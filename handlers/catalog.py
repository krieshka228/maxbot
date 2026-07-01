"""
handlers/catalog.py — Каталог для покупателей: категории, товары с фото, поиск, заказ.
Показываются только активные товары (is_active == True).
Реализовано атомарное резервирование только при оформлении заказа.
"""

import logging
import asyncio
import aiomax
from aiomax import fsm, filters
from aiomax.buttons import KeyboardBuilder, CallbackButton
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from db import (
    get_session, Product, get_or_create_user, get_or_create_draft, add_item_to_order,
    Order, OrderItem, get_active_categories, get_active_products_in_category,
    get_all_active_products,
)
from keyboards import kb_cart_actions, kb_back_to_menu, kb_unavailable
from utils import (
    format_cart, parse_quantity, check_payment_qr, get_max_attachments,
    build_catalog_card_text
)
from config import ADMIN_USER_ID
from cache import invalidate_catalog_cache

logger = logging.getLogger(__name__)
ITEMS_PER_PAGE = 3

_catalog_messages: dict[int, list[str]] = {}   # карточки товаров
_nav_messages: dict[int, str] = {}             # ID навигационного сообщения
_category_messages: dict[int, str] = {}         # ID сообщения со списком подкатегорий


async def delete_catalog_messages(user_id: int, bot: aiomax.Bot, also_delete_message_id: str | None = None):
    """Удаляет все сохранённые карточки товаров для пользователя."""
    ids_to_delete = _catalog_messages.pop(user_id, [])[:]
    if also_delete_message_id and also_delete_message_id not in ids_to_delete:
        ids_to_delete.append(also_delete_message_id)
    if not ids_to_delete:
        return

    async def _safe_delete(mid: str):
        try:
            await bot.delete_message(mid)
        except Exception:
            pass

    await asyncio.gather(*(_safe_delete(mid) for mid in ids_to_delete))


def register(bot: aiomax.Bot) -> None:

    # ------------------- Уровень 1: Категории -----------------------
    @bot.on_button_callback("catalog:show")
    async def catalog_show_level1(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        if user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return
        await delete_catalog_messages(user_id, bot)
        # Очищаем контекст
        cursor.change_data({})
        await show_level1_categories(cb)

    async def show_level1_categories(cb: aiomax.Callback):
        user_id = cb.user.user_id

        # Удаляем старое навигационное сообщение (пагинацию), если оно было
        nav_id = _nav_messages.pop(user_id, None)
        if nav_id:
            try:
                await bot.delete_message(nav_id)
            except Exception:
                pass

        async for session in get_session():
            categories = await get_active_categories(session)
            break

        kb = KeyboardBuilder()
        if not categories:
            kb.row(CallbackButton("🏠 Главное меню", "menu:main"))
            await cb.answer(text="📭 В каталоге пока нет товаров.", keyboard=kb)
            _category_messages[user_id] = cb.message.id
            return

        for cat in categories:
            kb.row(CallbackButton(cat, f"catalog:level1:{cat}"))
        kb.row(CallbackButton("🏠 Главное меню", "menu:main"))

        await cb.answer(text="**Выберите категорию:**", keyboard=kb, format="markdown")

        # Удаляем старое сообщение со списком категорий, если оно не совпадает с текущим
        prev_msg_id = _category_messages.pop(user_id, None)
        if prev_msg_id is not None and prev_msg_id != cb.message.id:
            try:
                await bot.delete_message(prev_msg_id)
            except Exception:
                pass

        _category_messages[user_id] = cb.message.id

    # ------------------- Уровень 2: Подкатегории -----------------------
    @bot.on_button_callback(lambda cb: cb.payload.startswith("catalog:level1:"))
    async def catalog_level2_page(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        await cb.answer(notification=" ")
        user_id = cb.user.user_id
        if user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return
        category = cb.payload.split(":", 2)[2]
        await delete_catalog_messages(user_id, bot)
        await show_level2_categories(cb, category)

    async def show_level2_categories(cb: aiomax.Callback, category: str):
        user_id = cb.user.user_id

        # Удаляем старое навигационное сообщение
        nav_id = _nav_messages.pop(user_id, None)
        if nav_id:
            try:
                await bot.delete_message(nav_id)
            except Exception:
                pass

        async for session in get_session():
            products = await get_active_products_in_category(session, category)
            break

        subcategories = {}
        for p in products:
            if ',' in p.name:
                sub = p.name.split(',')[0].strip()
            else:
                sub = p.name.strip()
            subcategories[sub] = subcategories.get(sub, 0) + 1

        kb = KeyboardBuilder()
        if not subcategories:
            kb.row(CallbackButton("↩️ К категориям", "catalog:show"))
            kb.row(CallbackButton("🏠 Главное меню", "menu:main"))
            await cb.answer(text=f"В категории «{category}» пока нет подкатегорий.", keyboard=kb)
            _category_messages[user_id] = cb.message.id
            return

        for sub in sorted(subcategories):
            kb.row(CallbackButton(f"{sub} ({subcategories[sub]})", f"catalog:category:{category}:{sub}"))
        kb.row(CallbackButton("↩️ К категориям", "catalog:show"))
        kb.row(CallbackButton("🏠 Главное меню", "menu:main"))

        await cb.answer(text=f"**{category}** — выберите подкатегорию:", keyboard=kb, format="markdown")

        # Удаляем старое сообщение со списком подкатегорий, если оно не совпадает с текущим
        prev_msg_id = _category_messages.pop(user_id, None)
        if prev_msg_id is not None and prev_msg_id != cb.message.id:
            try:
                await bot.delete_message(prev_msg_id)
            except Exception:
                pass

        _category_messages[user_id] = cb.message.id

    # ------------------- Уровень 3: Товары (с пагинацией) -----------------------
    @bot.on_button_callback(lambda cb: cb.payload.startswith("catalog:category:"))
    async def catalog_category_page(cb: aiomax.Callback, cursor: fsm.FSMCursor):
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
        await delete_catalog_messages(user_id, bot)

        # Сохраняем контекст в FSM
        cursor.change_data({
            "catalog_category": category,
            "catalog_subcategory": subcategory,
            "catalog_page": 0
        })

        await show_category_page(bot, cb, category, subcategory, 0)

    @bot.on_button_callback(lambda cb: cb.payload.startswith("catalog:catpage:"))
    async def catalog_catpage(cb: aiomax.Callback, cursor: fsm.FSMCursor):
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
        await delete_catalog_messages(user_id, bot)

        # Обновляем контекст с новой страницей
        cursor.change_data({
            "catalog_category": category,
            "catalog_subcategory": subcategory,
            "catalog_page": page
        })

        await show_category_page(bot, cb, category, subcategory, page)

    async def show_category_page(bot, ctx, category: str, subcategory: str, page: int):
        user_id = ctx.user.user_id

        # Удаляем старую навигацию (пагинацию), чтобы в чате не накапливались сообщения с кнопками
        nav_id = _nav_messages.pop(user_id, None)
        if nav_id:
            try:
                await bot.delete_message(nav_id)
            except Exception:
                pass

        # Удаляем сообщение со списком подкатегорий – оно больше не нужно
        prev_msg_id = _category_messages.pop(user_id, None)
        if prev_msg_id is not None:
            try:
                await bot.delete_message(prev_msg_id)
            except Exception:
                pass

        async for session in get_session():
            cat_products = await get_active_products_in_category(session, category)
            matched = [
                p for p in cat_products
                if p.name == subcategory
                   or p.name.startswith(subcategory + ",")
                   or p.name.startswith(subcategory + " ,")
            ]

            if not matched:
                kb = KeyboardBuilder()
                kb.row(CallbackButton("↩️ К подкатегориям", f"catalog:level1:{category}"))
                kb.row(CallbackButton("🏠 Главное меню", "menu:main"))
                await ctx.send(
                    text=f"В подкатегории «{subcategory}» пока нет товаров.",
                    keyboard=kb
                )
                return

            total = len(matched)
            total_pages = (total - 1) // ITEMS_PER_PAGE + 1
            page = max(0, min(page, total_pages - 1))
            products = matched[page * ITEMS_PER_PAGE: (page + 1) * ITEMS_PER_PAGE]

            new_msgs = []
            for product in products:
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

            nav_kb = KeyboardBuilder()
            nav_row = []
            if page > 0:
                nav_row.append(CallbackButton("← Назад", f"catalog:catpage:{category}:{subcategory}:{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(CallbackButton("Вперёд →", f"catalog:catpage:{category}:{subcategory}:{page + 1}"))
            if nav_row:
                nav_kb.row(*nav_row)
            nav_kb.row(CallbackButton("↩️ К подкатегориям", f"catalog:level1:{category}"))
            nav_kb.row(CallbackButton("🏠 Главное меню", "menu:main"))

            nav_text = f"{category} → **{subcategory}** (стр. {page + 1}/{total_pages}, товаров: {total})"
            nav_msg = await ctx.send(
                text=nav_text,
                format="markdown",
                keyboard=nav_kb
            )
            _nav_messages[user_id] = nav_msg.id
            break

    # ------------------- Заказ (без резервирования в корзине) -----------------------
    @bot.on_button_callback(lambda cb: cb.payload.startswith("order:start:"))
    async def start_order(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(notification="Функционал временно недоступен. Напишите администратору.")
            return
        product_id = int(cb.payload.split(":")[-1])

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

        user_id = cb.user.user_id

        nav_id = _nav_messages.pop(user_id, None)
        if nav_id:
            try:
                await bot.delete_message(nav_id)
            except Exception:
                pass

        await delete_catalog_messages(user_id, bot)

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

        # Читаем текущий контекст из FSM
        data = cursor.get_data() or {}
        cat = data.get("catalog_category")
        sub = data.get("catalog_subcategory")
        pg = data.get("catalog_page", 0)

        # Сохраняем всё вместе
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

        # Кнопка "Продолжить покупки" с контекстом
        cat = data.get("catalog_category")
        sub = data.get("catalog_subcategory")
        pg = data.get("catalog_page", 0)

        if cat and sub:
            kb.row(CallbackButton("📦 Продолжить покупки", f"catalog:continue:{cat}:{sub}:{pg}", intent='default'))
        else:
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

    @bot.on_button_callback(lambda cb: cb.payload.startswith("catalog:continue:"))
    async def catalog_continue(cb: aiomax.Callback, cursor: fsm.FSMCursor):
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

        async for session in get_session():
            # Проверяем, есть ли товары в этой подкатегории
            cat_products = await get_active_products_in_category(session, category)
            matched = [
                p for p in cat_products
                if p.name == subcategory or p.name.startswith(subcategory + ",")
            ]

            if matched:
                # Если есть – показываем страницу товаров (с сохранением контекста)
                cursor.change_data({
                    "catalog_category": category,
                    "catalog_subcategory": subcategory,
                    "catalog_page": page
                })
                await show_category_page(bot, cb, category, subcategory, page)
                return

            # Если товаров в подкатегории нет – проверяем, есть ли другие подкатегории в этой категории
            products = await get_active_products_in_category(session, category)
            subcategories = {}
            for p in products:
                if ',' in p.name:
                    sub = p.name.split(',')[0].strip()
                else:
                    sub = p.name.strip()
                subcategories[sub] = subcategories.get(sub, 0) + 1

            if subcategories:
                # Если подкатегории есть – показываем их (уровень 2)
                await show_level2_categories(cb, category)
            else:
                # Если и подкатегорий нет – показываем категории (уровень 1)
                await show_level1_categories(cb)
            break

    # ------------------- Поиск по артикулу -----------------------
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

    # ------------------- Поиск по названию -----------------------
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