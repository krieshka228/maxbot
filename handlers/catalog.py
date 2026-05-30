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
from keyboards import kb_cart_actions, kb_back_to_menu
from utils import format_cart, parse_quantity

logger = logging.getLogger(__name__)
ITEMS_PER_PAGE = 3

_catalog_messages: dict[int, list[str]] = {}


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
    @bot.on_button_callback("catalog:show")
    async def catalog_show_categories(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        await delete_catalog_messages(user_id, bot)
        await show_categories(cb)

    async def show_categories(cb: aiomax.Callback):
        async for session in get_session():
            categories = (await session.execute(
                select(Product.category).where(
                    Product.is_active == True,
                    Product.category != None
                ).distinct()
            )).scalars().all()

        kb = KeyboardBuilder()
        if not categories:
            kb.add(CallbackButton("🏠 Главное меню", "menu:main"))
            await cb.answer(text="📭 В каталоге пока нет категорий.", keyboard=kb, format="markdown")
            return

        for cat in categories:
            kb.add(CallbackButton(cat, f"catalog:category:{cat}"))
            kb.row()
        kb.add(CallbackButton("🏠 Главное меню", "menu:main"))
        await cb.answer(text="**Выберите категорию:**", keyboard=kb, format="markdown")

    # ------------------- Товары в категории (пагинация) -----------------------
    @bot.on_button_callback(lambda cb: cb.payload.startswith("catalog:category:"))
    async def catalog_category_page(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        category = cb.payload.split(":", 2)[2]
        user_id = cb.user.user_id
        await delete_catalog_messages(user_id, bot, also_delete_message_id=cb.message.id)
        await show_category_page(bot, cb, category, 0)

    @bot.on_button_callback(lambda cb: cb.payload.startswith("catalog:catpage:"))
    async def catalog_catpage(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        parts = cb.payload.split(":")
        category = parts[2]
        page = int(parts[3])
        user_id = cb.user.user_id
        await delete_catalog_messages(user_id, bot)
        await show_category_page(bot, cb, category, page)

    async def show_category_page(bot: aiomax.Bot, cb: aiomax.Callback, category: str, page: int):
        user_id = cb.user.user_id
        async for session in get_session():
            total = (await session.execute(
                select(func.count(Product.id)).where(
                    Product.is_active == True,
                    Product.stock > 0,
                    Product.category == category
                )
            )).scalar()
            products = (await session.execute(
                select(Product).where(
                    Product.is_active == True,
                    Product.stock > 0,
                    Product.category == category
                ).order_by(Product.id)
                .offset(page * ITEMS_PER_PAGE).limit(ITEMS_PER_PAGE)
            )).scalars().all()

        if not products:
            await cb.answer(
                text=f"В категории «{category}» пока нет товаров.",
                keyboard=kb_back_to_menu(),
                format="markdown"
            )
            return

        total_pages = (total - 1) // ITEMS_PER_PAGE + 1

        # Удаляем все предыдущие сообщения каталога (товары + навигацию)
        await delete_catalog_messages(user_id, bot)

        # Отправляем каждый товар отдельным сообщением с фото
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
            nav_row.append(CallbackButton("← Назад", f"catalog:catpage:{category}:{page-1}"))
        if page < total_pages - 1:
            nav_row.append(CallbackButton("Вперёд →", f"catalog:catpage:{category}:{page+1}"))
        if nav_row:
            nav_kb.row(*nav_row)
        nav_kb.row(CallbackButton("↩️ К категориям", "catalog:show"))
        nav_kb.row(CallbackButton("🏠 Главное меню", "menu:main"))

        nav_text = f"**{category}** (стр. {page+1}/{total_pages})"
        nav_msg = await cb.send(nav_text, keyboard=nav_kb, format="markdown")
        new_msgs.append(nav_msg.id)

        # Сохраняем id всех сообщений (товаров и навигации)
        _catalog_messages[user_id] = new_msgs

    # Гарантированное удаление каталога при нажатии "Главное меню"
    @bot.on_button_callback("menu:main")
    async def catalog_menu_main(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        await delete_catalog_messages(user_id, bot, also_delete_message_id=cb.message.id)

    # ------------------- Заказ (без резервирования в корзине) -----------------------
    @bot.on_button_callback(lambda cb: cb.payload.startswith("order:start:"))
    async def start_order(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        product_id = int(cb.payload.split(":")[-1])

        product = None
        async for session in get_session():
            product = await session.get(Product, product_id)
            break
        if not product or not product.is_active:
            await cb.answer(notification="❌ Товар недоступен.")
            return

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

        # Редактируем текущее сообщение (товар) -> карточка с фото и полем ввода
        await cb.answer(
            text=text,
            attachments=attachments,
            keyboard=kb_back_to_menu(),
            format="markdown"
        )
        cursor.change_state("order_qty")
        cursor.change_data({"product_id": product_id, "card_msg_id": cb.message.id})

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
            await message.reply("🔎 Товар с таким артикулом не найден.", keyboard=kb_back_to_menu())
            cursor.clear()
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