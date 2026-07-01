"""
handlers/catalog.py — Каталог для покупателей: категории, товары с фото, поиск, заказ.
Показываются только активные товары (is_active == True).

Структура навигации (3 уровня):
    🏠 Главная → 📦 Категория → 📱 Подкатегория

* Категории и подкатегории — это ОДНО текстовое сообщение, которое
  редактируется на месте (cb.answer()), поэтому переходы между
  ними не плодят дубли.
* Товары показываются медиа-карточками: каждый товар — отдельное сообщение
  с фото/видео и подписью и кнопкой «🛒 Заказать».
* При переключении страницы все ранее отправленные карточки и навигационное
  сообщение удаляются перед отправкой новых.
"""

import logging
import asyncio
import aiomax
from aiomax import fsm, filters
from aiomax.buttons import KeyboardBuilder, CallbackButton
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from db import (
    get_session,
    Product,
    get_or_create_user,
    get_or_create_draft,
    add_item_to_order,
    Order,
    OrderItem,
    get_active_categories,
    get_active_products_in_category,
    get_all_active_products,
)
from keyboards import (
    kb_cart_actions,
    kb_back_to_menu,
    kb_unavailable,
    kb_main_menu,
)
from utils import (
    format_cart,
    parse_quantity,
    check_payment_qr,
    get_max_attachments,
    build_catalog_card_text,
)
from config import ADMIN_USER_ID
from cache import invalidate_catalog_cache

logger = logging.getLogger(__name__)

PRODUCTS_PER_PAGE = 3
LIST_PER_PAGE = 8

# Глобальные словари для хранения ID сообщений
_catalog_messages: dict[int, list[str]] = {}   # карточки товаров
_nav_messages: dict[int, str] = {}             # ID навигационного сообщения
_category_messages: dict[int, str] = {}         # ID сообщения со списком категорий


async def safe_edit_or_send(cb: aiomax.Callback, text: str, keyboard, format: str = "markdown"):
    """Пытается отредактировать сообщение, если не получается – отправляет новое."""
    try:
        await cb.answer(text=text, keyboard=keyboard, format=format)
        return cb.message.id
    except Exception as e:
        logger.warning(f"Не удалось отредактировать сообщение: {e}, отправляем новое")
        try:
            await bot.delete_message(cb.message.id)
        except Exception:
            pass
        msg = await cb.send(text=text, keyboard=keyboard, format=format)
        return msg.id


async def delete_catalog_messages(user_id: int, bot: aiomax.Bot, keep_current: bool = False):
    """Удаляет все сохранённые карточки товаров для пользователя."""
    ids_to_delete = _catalog_messages.pop(user_id, [])[:]
    nav_id = _nav_messages.pop(user_id, None)
    if nav_id:
        ids_to_delete.append(nav_id)
    if not ids_to_delete:
        return

    async def _safe_delete(mid: str):
        try:
            await bot.delete_message(mid)
        except Exception:
            pass

    await asyncio.gather(*(_safe_delete(mid) for mid in ids_to_delete))


def _crumbs(category: str | None = None, subcategory: str | None = None) -> str:
    """«Хлебные крошки»: 🏠 Главная → 📦 Категория → 📱 Подкатегория."""
    parts = ["🏠 Главная"]
    if category:
        parts.append(f"📦 {category}")
    if subcategory:
        parts.append(f"📱 {subcategory}")
    return " → ".join(parts)


def _subcategory_of(product) -> str:
    """Подкатегория = часть названия до первой запятой."""
    name = product.name or ""
    return name.split(",")[0].strip() if "," in name else name.strip()


def register(bot: aiomax.Bot) -> None:

    # ========================== УРОВЕНЬ 1: КАТЕГОРИИ ==========================

    @bot.on_button_callback("catalog:show")
    async def catalog_show_categories(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        """Показывает список категорий (редактирует текущее сообщение)."""
        user_id = cb.user.user_id
        if user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return

        # Удаляем карточки товаров и навигацию, но НЕ удаляем текущее сообщение
        await delete_catalog_messages(user_id, bot, keep_current=True)
        cursor.change_data({})

        async for session in get_session():
            products = await get_all_active_products(session)

        counts = {}
        for p in products:
            if p.category:
                counts[p.category] = counts.get(p.category, 0) + 1

        if not counts:
            kb = KeyboardBuilder()
            kb.row(CallbackButton("🏠 Главное меню", "menu:main"))
            msg_id = await safe_edit_or_send(cb, "📭 В каталоге пока нет товаров.", kb)
            _category_messages[user_id] = msg_id
            return

        kb = KeyboardBuilder()
        for cat in sorted(counts):
            kb.row(CallbackButton(f"📦 {cat} ({counts[cat]})", f"catalog:category:{cat}"))
        kb.row(CallbackButton("🏠 Главное меню", "menu:main"))

        msg_id = await safe_edit_or_send(
            cb,
            f"{_crumbs()}\n📂 **Категории**",
            kb,
            format="markdown"
        )
        _category_messages[user_id] = msg_id

    # ========================== УРОВЕНЬ 2: ПОДКАТЕГОРИИ ==========================

    @bot.on_button_callback(lambda cb: cb.payload.startswith("catalog:category:"))
    async def catalog_show_subcategories(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        """Показывает подкатегории выбранной категории."""
        if cb.user.user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return

        category = cb.payload.split(":", 2)[2]
        user_id = cb.user.user_id

        # Удаляем карточки, но НЕ удаляем текущее сообщение
        await delete_catalog_messages(user_id, bot, keep_current=True)

        # Сохраняем текущую категорию в FSM
        data = cursor.get_data() or {}
        data["catalog_category"] = category
        cursor.change_data(data)

        async for session in get_session():
            products = await get_active_products_in_category(session, category)

        counts = {}
        for p in products:
            sub = _subcategory_of(p)
            counts[sub] = counts.get(sub, 0) + 1

        if not counts:
            kb = KeyboardBuilder()
            kb.row(CallbackButton("↩️ К категориям", "catalog:show"))
            kb.row(CallbackButton("🏠 Главное меню", "menu:main"))
            msg_id = await safe_edit_or_send(
                cb,
                f"{_crumbs(category)}\n\nВ этой категории пока нет товаров 😔",
                kb
            )
            _category_messages[user_id] = msg_id
            return

        kb = KeyboardBuilder()
        for sub in sorted(counts):
            kb.row(CallbackButton(f"📱 {sub} ({counts[sub]})", f"catalog:subcategory:{category}:{sub}"))
        kb.row(CallbackButton("↩️ К категориям", "catalog:show"))
        kb.row(CallbackButton("🏠 Главное меню", "menu:main"))

        msg_id = await safe_edit_or_send(
            cb,
            f"{_crumbs(category)}\nВыберите подкатегорию:",
            kb,
            format="markdown"
        )
        _category_messages[user_id] = msg_id

    # ========================== УРОВЕНЬ 3: ТОВАРЫ ==========================

    @bot.on_button_callback(lambda cb: cb.payload.startswith("catalog:subcategory:"))
    async def catalog_show_products(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        """Показывает товары подкатегории (медиа-карточки + навигация)."""
        if cb.user.user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return

        parts = cb.payload.split(":")
        category = parts[2]
        subcategory = parts[3]
        user_id = cb.user.user_id

        # Удаляем карточки и навигацию
        await delete_catalog_messages(user_id, bot, keep_current=False)

        # Удаляем текущее сообщение (оно было с подкатегориями)
        try:
            await bot.delete_message(cb.message.id)
        except Exception:
            pass

        # Сохраняем контекст
        data = cursor.get_data() or {}
        data["catalog_category"] = category
        data["catalog_subcategory"] = subcategory
        data["catalog_page"] = 0
        cursor.change_data(data)

        await _show_products_page(bot, cb, category, subcategory, 0)

    @bot.on_button_callback(lambda cb: cb.payload.startswith("catalog:prodpage:"))
    async def catalog_prodpage(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        """Обработчик пагинации товаров."""
        if cb.user.user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return

        parts = cb.payload.split(":")
        category = parts[2]
        subcategory = parts[3]
        page = int(parts[4])
        user_id = cb.user.user_id

        # Удаляем старые карточки и навигацию
        await delete_catalog_messages(user_id, bot, keep_current=False)

        cursor.change_data({
            "catalog_category": category,
            "catalog_subcategory": subcategory,
            "catalog_page": page
        })

        await _show_products_page(bot, cb, category, subcategory, page)

    async def _show_products_page(bot, ctx, category: str, subcategory: str, page: int):
        """Внутренняя функция: отправляет карточки товаров и навигацию."""
        user_id = ctx.user.user_id

        async for session in get_session():
            all_products = await get_active_products_in_category(session, category)
            products = [
                p for p in all_products
                if p.name == subcategory or p.name.startswith(subcategory + ",")
            ]

            if not products:
                kb = KeyboardBuilder()
                kb.row(CallbackButton("↩️ К подкатегориям", f"catalog:category:{category}"))
                kb.row(CallbackButton("🏠 Главное меню", "menu:main"))
                await ctx.send(
                    text=f"{_crumbs(category, subcategory)}\n\nВ этой подкатегории пока нет товаров 😔",
                    keyboard=kb
                )
                return

            total = len(products)
            total_pages = (total - 1) // PRODUCTS_PER_PAGE + 1
            page = max(0, min(page, total_pages - 1))
            page_products = products[page * PRODUCTS_PER_PAGE: page * PRODUCTS_PER_PAGE + PRODUCTS_PER_PAGE]

            # 1) Отправляем карточки товаров
            new_msgs = []
            for product in page_products:
                text = build_catalog_card_text(product)
                kb = KeyboardBuilder()
                kb.row(CallbackButton("🛒 Заказать", f"order:start:{product.id}"))

                attachments = await get_max_attachments(bot, session, product)

                try:
                    msg = await bot.send_message(
                        text=text,
                        user_id=user_id,
                        format="markdown",
                        keyboard=kb,
                        attachments=attachments if attachments else None,
                    )
                    new_msgs.append(msg.id)
                except Exception:
                    photo_atts = [att for att in attachments if isinstance(att, aiomax.PhotoAttachment)]
                    if photo_atts:
                        msg = await bot.send_message(
                            text=text,
                            user_id=user_id,
                            format="markdown",
                            keyboard=kb,
                            attachments=photo_atts,
                        )
                        new_msgs.append(msg.id)
                    else:
                        msg = await bot.send_message(
                            text=text,
                            user_id=user_id,
                            format="markdown",
                            keyboard=kb,
                        )
                        new_msgs.append(msg.id)

            _catalog_messages[user_id] = new_msgs

            # 2) Навигационное сообщение
            nav_kb = KeyboardBuilder()
            nav_row = []
            if page > 0:
                nav_row.append(CallbackButton("◀️ Назад", f"catalog:prodpage:{category}:{subcategory}:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(CallbackButton("Вперёд ▶️", f"catalog:prodpage:{category}:{subcategory}:{page + 1}"))
            if nav_row:
                nav_kb.row(*nav_row)
            nav_kb.row(CallbackButton("↩️ К подкатегориям", f"catalog:category:{category}"))
            nav_kb.row(CallbackButton("🏠 Главное меню", "menu:main"))

            nav_text = (
                f"{_crumbs(category, subcategory)}\n"
                f"🔎 Найдено {total} товаров. Страница {page + 1} из {total_pages}"
            )
            nav_msg = await ctx.send(
                text=nav_text,
                format="markdown",
                keyboard=nav_kb
            )
            _nav_messages[user_id] = nav_msg.id

    # ========================== ЗАКАЗ ==========================

    @bot.on_button_callback(lambda cb: cb.payload.startswith("order:start:"))
    async def start_order(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        """Кнопка «Заказать» – запрашивает количество."""
        if cb.user.user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(notification="Функционал временно недоступен. Напишите администратору.")
            return

        product_id = int(cb.payload.split(":")[-1])
        user_id = cb.user.user_id

        product = None
        attachments = []
        async for session in get_session():
            product = await session.get(Product, product_id)
            if product and product.is_active:
                attachments = await get_max_attachments(bot, session, product)
            break

        if not product or not product.is_active:
            await cb.answer(notification="❌ Товар недоступен.")
            return

        # Удаляем карточки и навигацию
        await delete_catalog_messages(user_id, bot, keep_current=False)

        # Удаляем текущее сообщение (навигацию)
        try:
            await bot.delete_message(cb.message.id)
        except Exception:
            pass

        text = product.name
        if product.article:
            text += f"\nАртикул {product.article}"
        if product.stock is not None:
            text += f"\nНа складе: {product.stock}"
        text += f"\n\nЦена {product.price:.0f}"
        text += "\n\n✏️ Введите количество:"

        msg = await cb.send(
            text=text,
            keyboard=kb_back_to_menu(),
            format="markdown",
            attachments=attachments or None,
        )

        # Читаем контекст из FSM
        data = cursor.get_data() or {}
        cat = data.get("catalog_category")
        sub = data.get("catalog_subcategory")
        pg = data.get("catalog_page", 0)

        cursor.change_state("order_qty")
        cursor.change_data({
            "product_id": product_id,
            "card_msg_id": msg.id,
            "catalog_category": cat,
            "catalog_subcategory": sub,
            "catalog_page": pg
        })

    @bot.on_message(filters.state("order_qty"))
    async def handle_order_qty(message: aiomax.Message, cursor: fsm.FSMCursor):
        """Обработка ввода количества."""
        qty = parse_quantity(message.body.text or "")
        data = cursor.get_data()
        product_id = data.get("product_id")
        card_msg_id = data.get("card_msg_id")
        user_id = message.sender.user_id

        if not qty:
            if card_msg_id:
                await bot.edit_message(
                    message_id=card_msg_id,
                    text="❌ Введите целое положительное число.",
                    keyboard=kb_back_to_menu()
                )
            else:
                await message.reply("❌ Введите целое положительное число.", keyboard=kb_back_to_menu())
            return

        async for session in get_session():
            user = await get_or_create_user(
                session, user_id,
                full_name=message.sender.name,
                username=getattr(message.sender, 'username', None),
                platform="MAX"
            )
            product = await session.get(Product, product_id)
            if not product or not product.is_active:
                if card_msg_id:
                    await bot.edit_message(
                        message_id=card_msg_id,
                        text="❌ Товар недоступен.",
                        keyboard=kb_back_to_menu()
                    )
                else:
                    await message.reply("❌ Товар недоступен.", keyboard=kb_back_to_menu())
                cursor.clear()
                return

            order = await get_or_create_draft(session, user_id)
            stmt = select(Order).where(Order.id == order.id).options(
                selectinload(Order.items).selectinload(OrderItem.product)
            )
            order = (await session.execute(stmt)).scalar_one()

            existing_qty = 0
            for item in order.items:
                if item.product_id == product_id:
                    existing_qty = item.quantity
                    break
            total_qty = existing_qty + qty

            if product.stock is not None and total_qty > product.stock:
                msg = (f"❌ Недостаточно товара. В наличии: {product.stock} шт."
                       + (f", у вас в корзине уже {existing_qty} шт." if existing_qty else ""))
                if card_msg_id:
                    await bot.edit_message(message_id=card_msg_id, text=msg, keyboard=kb_back_to_menu())
                else:
                    await message.reply(msg, keyboard=kb_back_to_menu())
                cursor.clear()
                return

            await add_item_to_order(session, order, product, qty)
            order = (await session.execute(stmt)).scalar_one()
            invalidate_catalog_cache()

        cursor.clear()

        confirm_text = f"✅ **{product.name}** × {qty} шт. добавлен в корзину!"
        kb = KeyboardBuilder()
        kb.row(CallbackButton("🛒 Перейти в корзину", "cart:view", intent='default'))
        kb.row(CallbackButton("📦 Продолжить покупки", "catalog:show", intent='default'))
        kb.row(CallbackButton("🏠 Главное меню", "menu:main", intent='default'))

        if card_msg_id:
            await bot.edit_message(
                message_id=card_msg_id,
                text=confirm_text,
                keyboard=kb,
                attachments=[],
                format="markdown"
            )
        else:
            await message.reply(confirm_text, keyboard=kb, format="markdown")

    # ========================== ПОИСК ==========================

    @bot.on_button_callback("search:article")
    async def search_article_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return
        user_id = cb.user.user_id
        cursor.change_state("search_article")
        await cb.answer(notification=" ")
        await delete_catalog_messages(user_id, bot)
        await cb.answer(
            text="🔎 Введите артикул:",
            keyboard=kb_back_to_menu(),
            format="markdown"
        )

    @bot.on_message(filters.state("search_article"))
    async def search_article_result(message: aiomax.Message, cursor: fsm.FSMCursor):
        article = message.body.text.strip() if message.body and message.body.text else ""
        if not article:
            await message.reply("❌ Введите артикул.", keyboard=kb_back_to_menu())
            return

        product = None
        attachments = []
        async for session in get_session():
            products = (await session.execute(
                select(Product).where(
                    Product.is_active == True,
                    Product.article == article
                )
            )).scalars().all()
            if products:
                product = products[0]
                attachments = await get_max_attachments(bot, session, product)
            break

        if not product:
            await message.reply("🔎 Товар с таким артикулом не найден. Попробуйте другой запрос.",
                                keyboard=kb_back_to_menu())
            return

        user_id = message.sender.user_id
        photo_atts = [att for att in attachments if isinstance(att, aiomax.PhotoAttachment)]

        text = build_catalog_card_text(product)
        kb = KeyboardBuilder()
        kb.row(CallbackButton("🛒 Заказать", f"order:start:{product.id}"))
        kb.row(CallbackButton("🏠 Главное меню", "menu:main"))

        if not photo_atts:
            await message.reply(text, keyboard=kb, format="markdown")
        else:
            await message.reply(
                text, keyboard=kb, format="markdown",
                attachments=photo_atts,
            )

        cursor.clear()

    @bot.on_button_callback("search:name")
    async def search_name_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        if user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return
        cursor.change_state("search_name")
        await cb.answer(notification=" ")
        await cb.answer(
            text="🔍 Введите название или его часть:",
            keyboard=kb_back_to_menu(),
            format="markdown"
        )

    @bot.on_message(filters.state("search_name"))
    async def search_name_result(message: aiomax.Message, cursor: fsm.FSMCursor):
        query = message.body.text.strip().lower() if message.body and message.body.text else ""
        if not query:
            await message.reply("❌ Введите текст для поиска.", keyboard=kb_back_to_menu())
            return

        async for session in get_session():
            all_products = await get_all_active_products(session)
            break

        matched = [p for p in all_products if query in p.name.lower()]

        if not matched:
            await message.reply("🔎 Ничего не найдено. Попробуйте другой запрос.", keyboard=kb_back_to_menu())
            return

        kb = KeyboardBuilder()
        for p in matched[:20]:
            kb.row(CallbackButton(f"{p.name[:30]} ({p.price:.0f}₽)", f"order:start:{p.id}"))
        kb.row(CallbackButton("🏠 Главное меню", "menu:main"))

        await message.reply("**Результаты поиска:**", keyboard=kb, format="markdown")
        cursor.clear()