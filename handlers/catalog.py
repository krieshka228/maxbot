"""
handlers/catalog.py — Каталог для покупателей: категории, товары с фото/видео, поиск, заказ.
Показываются только активные товары (is_active == True).
Реализовано атомарное резервирование только при оформлении заказа.
"""

import logging
import aiomax
from aiomax import fsm, filters
from aiomax.buttons import KeyboardBuilder, CallbackButton
from sqlalchemy import select, func, text
from db import get_session, Product, get_or_create_user, get_or_create_draft, add_item_to_order
from keyboards import kb_cart_actions, kb_back_to_menu, kb_unavailable
from utils import format_cart, parse_quantity, check_payment_qr
from config import ADMIN_USER_ID


logger = logging.getLogger(__name__)
ITEMS_PER_PAGE = 3

_catalog_messages: dict[int, list[str]] = {}
_nav_messages: dict[int, str] = {}


async def delete_catalog_messages(user_id: int, bot: aiomax.Bot, also_delete_message_id: str | None = None):
    """Удаляет все сохранённые сообщения каталога для пользователя."""
    ids_to_delete = _catalog_messages.pop(user_id, [])[:]
    if also_delete_message_id and also_delete_message_id not in ids_to_delete:
        ids_to_delete.append(also_delete_message_id)
    for mid in ids_to_delete:
        try:
            await bot.delete_message(mid)
        except Exception:
            pass


def register(bot: aiomax.Bot) -> None:

    # ------------------- Показ категорий -----------------------
    # ------------------- Показ категорий первого уровня -----------------------
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
        await show_level1_categories(cb)

    async def show_level1_categories(cb: aiomax.Callback):
        async for session in get_session():
            categories = (await session.execute(
                select(Product.level1_category).where(
                    Product.is_active == True,
                    Product.level1_category != None,
                    Product.stock > 0
                ).distinct()
            )).scalars().all()

        kb = KeyboardBuilder()
        if not categories:
            kb.add(CallbackButton("🏠 Главное меню", "menu:main"))
            # Удаляем текущее сообщение и отправляем новое
            try:
                await cb.message.delete()
            except Exception:
                pass
            await cb.send(text="📭 В каталоге пока нет товаров.", keyboard=kb)
            return

        for cat in categories:
            kb.add(CallbackButton(cat, f"catalog:level1:{cat}"))
            kb.row()
        kb.add(CallbackButton("🏠 Главное меню", "menu:main"))

        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.send(text="**Выберите категорию:**", keyboard=kb)

    # ------------------- Подкатегории (category) для выбранной level1 ---------
    @bot.on_button_callback(lambda cb: cb.payload.startswith("catalog:level1:"))
    async def catalog_level2_page(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        if user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return
        level1 = cb.payload.split(":", 2)[2]
        await delete_catalog_messages(user_id, bot, also_delete_message_id=cb.message.id)
        await show_level2_categories(cb, level1)

    async def show_level2_categories(cb: aiomax.Callback, level1: str):
        async for session in get_session():
            categories = (await session.execute(
                select(Product.category).where(
                    Product.is_active == True,
                    Product.level1_category == level1,
                    Product.stock > 0,
                    Product.category != None
                ).distinct()
            )).scalars().all()

        kb = KeyboardBuilder()
        if not categories:
            kb.add(CallbackButton("↩️ К категориям", "catalog:show",intent='default'))
            kb.add(CallbackButton("🏠 Главное меню", "menu:main",intent='default'))
            try:
                await cb.message.delete()
            except Exception:
                pass
            await cb.answer(text=f"В категории «{level1}» пока нет подкатегорий.", keyboard=kb)
            return

        for cat in categories:
            kb.add(CallbackButton(cat, f"catalog:category:{level1}:{cat}"))
            kb.row()
        kb.add(CallbackButton("↩️ К категориям", "catalog:show",intent='default'))
        kb.add(CallbackButton("🏠 Главное меню", "menu:main",intent='default'))

        try:
            await cb.message.delete()
        except Exception:
            pass
        await cb.send(text=f"**{level1}** — выберите подкатегорию:", keyboard=kb)

    # ------------------- Товары в подкатегории (пагинация) --------------------
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
        level1 = parts[2]
        category = parts[3]
        user_id = cb.user.user_id
        await delete_catalog_messages(user_id, bot, also_delete_message_id=cb.message.id)
        await show_category_page(bot, cb, level1, category, 0)

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
        level1 = parts[2]
        category = parts[3]
        page = int(parts[4])
        user_id = cb.user.user_id
        await delete_catalog_messages(user_id, bot)
        await show_category_page(bot, cb, level1, category, page)

    async def show_category_page(bot: aiomax.Bot, cb: aiomax.Callback, level1: str, category: str, page: int):
        user_id = cb.user.user_id
        async for session in get_session():
            total = (await session.execute(
                select(func.count(Product.id)).where(
                    Product.is_active == True,
                    Product.stock > 0,
                    Product.level1_category == level1,
                    Product.category == category
                )
            )).scalar()
            products = (await session.execute(
                select(Product).where(
                    Product.is_active == True,
                    Product.stock > 0,
                    Product.level1_category == level1,
                    Product.category == category
                ).order_by(Product.id)
                .offset(page * ITEMS_PER_PAGE).limit(ITEMS_PER_PAGE)
            )).scalars().all()

        if not products:
            try:
                await cb.message.delete()
            except Exception:
                pass
            await cb.answer(f"В подкатегории «{category}» пока нет товаров.", keyboard=kb_back_to_menu())
            return

        total_pages = (total - 1) // ITEMS_PER_PAGE + 1

        # Удаляем старое навигационное сообщение (если оно есть)
        prev_nav_id = _nav_messages.get(user_id)
        if prev_nav_id:
            try:
                await bot.delete_message(prev_nav_id)
            except Exception:
                pass
        # Удаляем само сообщение, на которое нажали
        try:
            await cb.message.delete()
        except Exception:
            pass

        # Отправляем товары
        new_msgs = []
        for product in products:
            attachments = []
            if product.photo_ids:
                for t in product.photo_ids.split(","):
                    attachments.append(aiomax.PhotoAttachment(token=t))
            if product.video_ids:
                for t in product.video_ids.split(","):
                    attachments.append(aiomax.VideoAttachment(token=t))

            text = product.name
            if product.article:
                text += f"\nАртикул {product.article}"
            if product.stock is not None:
                text += f"\nНа складе: {product.stock}"
            text += f"\n\nЦена {product.price:.0f}"

            kb = KeyboardBuilder()
            kb.add(CallbackButton("🛒 Заказать", f"order:start:{product.id}"))
            msg = await cb.send(text, attachments=attachments, keyboard=kb, format="markdown")
            new_msgs.append(msg.id)

        # Навигационное сообщение
        nav_kb = KeyboardBuilder()
        nav_row = []
        if page > 0:
            nav_row.append(CallbackButton("← Назад", f"catalog:catpage:{level1}:{category}:{page - 1}",intent='default'))
        if page < total_pages - 1:
            nav_row.append(CallbackButton("Вперёд →", f"catalog:catpage:{level1}:{category}:{page + 1}",intent='default'))
        if nav_row:
            nav_kb.row(*nav_row)
        nav_kb.row(CallbackButton("↩️ К подкатегориям", f"catalog:level1:{level1}",intent='default'))
        nav_kb.row(CallbackButton("🏠 Главное меню", "menu:main",intent='default'))

        nav_text = f"**{category}** (стр. {page + 1}/{total_pages})"
        nav_msg = await cb.send(nav_text, keyboard=nav_kb, format="markdown")
        _nav_messages[user_id] = nav_msg.id
        _catalog_messages[user_id] = new_msgs

    # ------------------- Заказ (без резервирования в корзине) -----------------------
    @bot.on_button_callback(lambda cb: cb.payload.startswith("order:start:"))
    async def start_order(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(notification="Функционал временно недоступен. Напишите администратору.")
            return
        product_id = int(cb.payload.split(":")[-1])

        product = None
        async for session in get_session():
            product = await session.get(Product, product_id)
            break
        if not product or not product.is_active:
            await cb.answer(notification="❌ Товар недоступен.")
            return

        user_id = cb.user.user_id

        # Удаляем навигационное сообщение, если оно есть
        nav_id = _nav_messages.pop(user_id, None)
        if nav_id:
            try:
                await bot.delete_message(nav_id)
            except Exception:
                pass

        # Удаляем предыдущее сообщение (карточку товара или навигацию)
        try:
            await cb.message.delete()
        except Exception:
            pass

        # Собираем фото / видео
        attachments = []
        if product.photo_ids:
            for t in product.photo_ids.split(","):
                attachments.append(aiomax.PhotoAttachment(token=t))
        if product.video_ids:
            for t in product.video_ids.split(","):
                attachments.append(aiomax.VideoAttachment(token=t))

        text = product.name
        if product.article:
            text += f"\nАртикул {product.article}"
        if product.stock is not None:
            text += f"\nНа складе: {product.stock}"
        text += f"\n\nЦена {product.price:.0f}"
        text += "\n\n✏️ Введите количество:"

        # Отправляем карточку и запоминаем её message_id
        msg = await cb.send(
            text,
            attachments=attachments,
            keyboard=kb_back_to_menu(),
            format="markdown"
        )
        cursor.change_state("order_qty")
        cursor.change_data({"product_id": product_id, "card_msg_id": msg.id})

    @bot.on_message(filters.state("order_qty"))
    async def handle_order_qty(message: aiomax.Message, cursor: fsm.FSMCursor):
        qty = parse_quantity(message.body.text or "")
        data = cursor.get_data()
        product_id = data["product_id"]
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
                username=getattr(message.sender, 'username', None)
            )
            if not user.consented:
                if card_msg_id:
                    await bot.edit_message(
                        message_id=card_msg_id,
                        text="❌ Сначала дайте согласие: /start",
                        keyboard=kb_back_to_menu()
                    )
                else:
                    await message.reply("❌ Сначала дайте согласие: /start", keyboard=kb_back_to_menu())
                cursor.clear()
                return

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

            # Получаем текущую корзину и смотрим, сколько этого товара уже есть
            order = await get_or_create_draft(session, user_id)
            existing_qty = 0
            from sqlalchemy.orm import selectinload
            from db import Order, OrderItem
            stmt = select(Order).where(Order.id == order.id).options(
                selectinload(Order.items).selectinload(OrderItem.product)
            )
            order = (await session.execute(stmt)).scalar_one()
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
            cart_text = f"✅ **{product.name}** × {qty} шт. добавлен в корзину!\n\n{format_cart(order)}"

        cursor.clear()

        if card_msg_id:
            # Убираем фото, показываем только корзину
            await bot.edit_message(
                message_id=card_msg_id,
                text=cart_text,
                keyboard=kb_cart_actions(order.id),
                attachments=[],  # <-- обязательно убираем фото
                format="markdown"
            )
        else:
            await message.reply(cart_text, keyboard=kb_cart_actions(order.id), format="markdown")
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
        async for session in get_session():
            product = (await session.execute(
                select(Product).where(
                    Product.is_active == True,
                    Product.article == article
                )
            )).scalar_one_or_none()
            break

        if not product:
            await message.reply("🔎 Товар с таким артикулом не найден. Попробуйте другой запрос.",
                                keyboard=kb_back_to_menu())
            # Не сбрасываем состояние
            return

        attachments = []
        if product.photo_ids:
            for t in product.photo_ids.split(","):
                attachments.append(aiomax.PhotoAttachment(token=t))
        if product.video_ids:
            for t in product.video_ids.split(","):
                attachments.append(aiomax.VideoAttachment(token=t))

        text = product.name
        if product.article:
            text += f"\nАртикул {product.article}"
        if product.stock is not None:
            text += f"\nНа складе: {product.stock}"
        text += f"\n\nЦена {product.price:.0f}"

        kb = KeyboardBuilder()
        kb.add(CallbackButton("🛒 Заказать", f"order:start:{product.id}"))
        kb.row(CallbackButton("🏠 Главное меню", "menu:main"))
        await message.reply(text, attachments=attachments, keyboard=kb, format="markdown")
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
            # Загружаем все активные товары (их обычно немного)
            all_products = (await session.execute(
                select(Product).where(Product.is_active == True)
            )).scalars().all()

        # Фильтруем по подстроке в нижнем регистре
        matched = [p for p in all_products if query in p.name.lower()]

        if not matched:
            await message.reply("🔎 Ничего не найдено. Попробуйте другой запрос.", keyboard=kb_back_to_menu())
            return

        kb = KeyboardBuilder()
        for p in matched[:20]:  # ограничиваем 20 результатами
            kb.add(CallbackButton(f"{p.name[:30]} ({p.price:.0f}₽)", f"order:start:{p.id}", intent='default'))
            kb.row()
        kb.add(CallbackButton("🏠 Главное меню", "menu:main", intent='default'))
        await message.reply("**Результаты поиска:**", keyboard=kb, format="markdown")
        cursor.clear()