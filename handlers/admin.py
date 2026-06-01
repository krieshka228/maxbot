"""
handlers/admin.py — Административные функции: Excel, синхронизация, подтверждение оплаты, управление остатками.
"""

import io
import logging
from datetime import datetime, timedelta
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
import aiohttp
import aiomax
from aiomax import fsm, filters
from aiomax.buttons import KeyboardBuilder, CallbackButton


from config import ADMIN_USER_ID, CHANNEL_ID
from db import (
    get_session,
    get_all_users,
    OrderStatus,
    Product,
    User,
    upsert_product,
    set_bot_setting,
    get_bot_setting,
)
from excel_reports import build_monthly_report, build_clients_excel
from keyboards import (
    kb_admin_menu,
    kb_back_to_menu,
    kb_admin_confirm_payment,
)
from api import fetch_channel_messages
from utils import parse_post_product
from .catalog import delete_catalog_messages
from states import UserStates

logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 5           # товаров на странице
CATEGORIES_PER_PAGE = 5      # категорий на странице

def register(bot: aiomax.Bot) -> None:

    # ------------------- Админ-меню -----------------------
    @bot.on_button_callback("admin:menu")
    async def admin_menu(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        user_id = cb.user.user_id
        await delete_catalog_messages(user_id, bot, also_delete_message_id=cb.message.id)
        await cb.answer(
            text="⚙️ **Админ‑меню:**",
            keyboard=kb_admin_menu(),
            attachments=[],  # убираем возможные вложения
            format="markdown"
        )

    # ------------------- Заказы за месяц (Excel) -----------------------
    @bot.on_button_callback("admin:excel:summary")
    async def excel_monthly(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        await cb.answer(notification="Формирую отчёт за месяц...")

        now = datetime.utcnow()
        month_ago = now - timedelta(days=30)

        async for session in get_session():
            from db import Order, OrderItem

            stmt = (
                select(Order)
                .where(Order.created_at >= month_ago)
                .options(
                    selectinload(Order.items).selectinload(OrderItem.product),
                    selectinload(Order.user),
                )
                .order_by(Order.created_at.desc())
            )
            result = await session.execute(stmt)
            orders = result.scalars().all()

            user_ids = {order.user_id for order in orders if order.user_id}
            users = []
            if user_ids:
                user_stmt = select(User).where(User.id.in_(user_ids))
                user_result = await session.execute(user_stmt)
                users = user_result.scalars().all()

        if not orders:
            await cb.answer(
                text="📊 За последний месяц нет заказов.",
                keyboard=kb_admin_menu(),
                format="markdown"
            )
            return

        excel_bytes = build_monthly_report(orders, users)
        count_orders = len(orders)
        count_users = len(users)

        try:
            file_attachment = await bot.upload_file(excel_bytes, filename="monthly_report.xlsx")
            await cb.send(attachments=file_attachment)
            await cb.answer(
                text=f"📊 Отчёт за месяц: {count_orders} заказов, {count_users} клиентов.",
                keyboard=kb_admin_menu(),
                format="markdown"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
            await cb.answer(
                text=f"❌ Ошибка: {e}",
                keyboard=kb_admin_menu(),
                format="markdown"
            )

    # ------------------- База клиентов (Excel) -----------------------
    @bot.on_button_callback("admin:excel:clients")
    async def excel_clients(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        await cb.answer(notification="Формирую базу клиентов...")
        async for session in get_session():
            users = await get_all_users(session)
            if not users:
                await cb.answer(
                    text="👥 База клиентов пуста.",
                    keyboard=kb_admin_menu(),
                    format="markdown"
                )
                return
            excel_bytes = build_clients_excel(users)
            count = len(users)

        try:
            file_attachment = await bot.upload_file(excel_bytes, filename="clients.xlsx")
            await cb.send(attachments=file_attachment)
            await cb.answer(
                text=f"👥 База клиентов ({count} чел.) отправлена.",
                keyboard=kb_admin_menu(),
                format="markdown"
            )
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await cb.answer(
                text=f"❌ Ошибка: {e}",
                keyboard=kb_admin_menu(),
                format="markdown"
            )

    # ------------------- Заказы к подтверждению -----------------------
    @bot.on_button_callback("admin:confirm_list")
    async def confirm_list(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        await cb.answer(notification=" ")
        async for session in get_session():
            from db import Order, OrderItem

            stmt = (
                select(Order)
                .where(Order.status == OrderStatus.paid)
                .options(
                    selectinload(Order.items).selectinload(OrderItem.product),
                    selectinload(Order.user),
                )
            )
            result = await session.execute(stmt)
            orders = result.scalars().all()

        if not orders:
            await cb.answer(
                text="✅ Нет заказов, ожидающих подтверждения.",
                keyboard=kb_admin_menu(),
                format="markdown"
            )
            return

        for order in orders:
            user = order.user
            user_info = (
                f"@{user.username}"
                if (user and user.username)
                else (user.full_name if user else f"ID {order.user_id}")
            )
            lines = [f"📦 **Заказ #{order.id}** — {user_info}"]
            for item in order.items:
                name = item.product.name if item.product else f"ID {item.product_id}"
                lines.append(
                    f"  • {name}: {item.quantity} шт. × {item.price_at_order:.0f} ₽"
                )
            lines.append(f"💰 Итого: {order.total_amount:.0f} ₽")
            await cb.send(
                "\n".join(lines),
                format="markdown",
                keyboard=kb_admin_confirm_payment(order.id),
            )
        await cb.answer(
            text="⚙️ **Админ-меню:**",
            keyboard=kb_admin_menu(),
            format="markdown"
        )

    # ------------------- Синхронизация товаров -----------------------
    @bot.on_button_callback("admin:sync")
    async def admin_sync(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return

        await cb.answer(notification="⏳ Синхронизирую...")
        try:
            messages = await fetch_channel_messages(CHANNEL_ID, limit=100)
        except Exception as e:
            await cb.answer(
                text=f"❌ Ошибка получения постов: {e}",
                keyboard=kb_admin_menu(),
                format="markdown"
            )
            return

        added = 0
        for msg_data in messages:
            body = msg_data.get("body", {})
            text = body.get("text", "")
            sold_keywords = ["продано", "нет в наличии", "sold", "закончился", "продана", "продан"]
            in_stock = not any(word in text.lower() for word in sold_keywords)
            name, article, price, category, description, stock = parse_post_product(text)
            if not name:
                continue

            post_id = msg_data.get("id") or body.get("mid")

            photo_tokens = []
            video_tokens = []
            for att in body.get("attachments", []):
                if att.get("type") == "image" and "payload" in att and "url" in att["payload"]:
                    url = att["payload"]["url"]
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(url) as resp:
                                if resp.status == 200:
                                    img_data = io.BytesIO(await resp.read())
                                    attachment_obj = await bot.upload_image(img_data)
                                    photo_tokens.append(attachment_obj.token)
                    except Exception as e:
                        logger.warning(f"Ошибка загрузки фото: {e}")
                elif att.get("type") == "video" and "payload" in att and "url" in att["payload"]:
                    url = att["payload"]["url"]
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(url) as resp:
                                if resp.status == 200:
                                    video_data = io.BytesIO(await resp.read())
                                    attachment_obj = await bot.upload_video(video_data)
                                    video_tokens.append(attachment_obj.token)
                    except Exception as e:
                        logger.warning(f"Ошибка загрузки видео: {e}")

            photo_ids = ",".join(photo_tokens) if photo_tokens else None
            video_ids = ",".join(video_tokens) if video_tokens else None

            async for session in get_session():
                await upsert_product(
                    session, post_id, name, price, photo_ids, video_ids,
                    article, category, description, stock=stock, in_stock=in_stock
                )
                added += 1

        await cb.answer(
            text=f"✅ Синхронизация завершена. Добавлено/обновлено товаров: {added}",
            keyboard=kb_admin_menu(),
            format="markdown"
        )

    # ------------------- Обновить категории из канала -----------------------
    @bot.on_button_callback("admin:refresh_categories")
    async def refresh_categories(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        await cb.answer(notification="Обновляю категории…")
        async for session in get_session():
            products = (await session.execute(
                select(Product).where(Product.is_active == True)
            )).scalars().all()
            updated = 0
            for product in products:
                if "," in product.name:
                    new_cat = product.name.split(",")[0].strip()
                else:
                    new_cat = product.name.strip()
                if product.category != new_cat:
                    product.category = new_cat
                    updated += 1
            await session.commit()
        await cb.answer(
            text=f"✅ Категории обновлены. Изменено товаров: {updated}",
            keyboard=kb_admin_menu(),
            format="markdown"
        )

    # ------------------- Изменение остатка (stock) -----------------------
    ITEMS_PER_PAGE = 5

    @bot.on_button_callback("admin:set_stock_list")
    async def set_stock_list(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        await show_stock_categories(cb, page=0)

    async def show_stock_categories(cb: aiomax.Callback, page: int = 0):
        async for session in get_session():
            # Получаем общее количество категорий
            total_cats = (await session.execute(
                select(func.count(Product.category.distinct())).where(Product.category != None)
            )).scalar()
            # Получаем категории для текущей страницы
            categories = (await session.execute(
                select(Product.category).where(Product.category != None)
                .distinct()
                .order_by(Product.category)
                .offset(page * CATEGORIES_PER_PAGE).limit(CATEGORIES_PER_PAGE)
            )).scalars().all()

        kb = KeyboardBuilder()
        if not categories:
            kb.add(CallbackButton("⚙️ Админ-меню", "admin:menu"))
            await cb.answer(text="📭 Нет товаров.", keyboard=kb, format="markdown")
            return

        total_pages = (total_cats - 1) // CATEGORIES_PER_PAGE + 1
        header = f"**Категории** (стр. {page + 1}/{total_pages})"
        for cat in categories:
            kb.add(CallbackButton(cat, f"admin:stock_category:{cat}"))
            kb.row()

        # Навигационные кнопки для категорий
        nav = []
        if page > 0:
            nav.append(CallbackButton("← Назад", f"admin:stock_catlist_page:{page - 1}"))
        if page < total_pages - 1:
            nav.append(CallbackButton("Вперёд →", f"admin:stock_catlist_page:{page + 1}"))
        if nav:
            kb.row(*nav)
        kb.row(CallbackButton("⚙️ Админ-меню", "admin:menu"))
        await cb.answer(text=header, keyboard=kb, format="markdown")

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:stock_catlist_page:"))
    async def stock_catlist_page(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        page = int(cb.payload.split(":")[-1])
        await show_stock_categories(cb, page)

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:stock_category:"))
    async def stock_category_page(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        category = cb.payload.split(":", 2)[2]
        await show_stock_products_page(cb, category, 0)

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:stock_catpage:"))
    async def stock_catpage(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        parts = cb.payload.split(":")
        category = parts[2]
        page = int(parts[3])
        await show_stock_products_page(cb, category, page)

    async def show_stock_products_page(cb: aiomax.Callback, category: str, page: int):
        async for session in get_session():
            total = (await session.execute(
                select(func.count(Product.id)).where(Product.category == category)
            )).scalar()
            products = (await session.execute(
                select(Product).where(Product.category == category)
                .order_by(Product.id)
                .offset(page * ITEMS_PER_PAGE).limit(ITEMS_PER_PAGE)
            )).scalars().all()

        if not products:
            await cb.answer(
                text=f"В категории «{category}» нет товаров.",
                keyboard=kb_back_to_menu(),
                format="markdown"
            )
            return

        total_pages = (total - 1) // ITEMS_PER_PAGE + 1
        lines = [f"**Остатки: {category}** (стр. {page+1}/{total_pages})\n"]
        kb = KeyboardBuilder()

        for p in products:
            if p.stock is not None:
                stock_str = f"{p.stock} шт."
                if not p.is_active:
                    stock_str += " (скрыт)"
            else:
                stock_str = "∞"
            lines.append(f"• {p.name} — на складе: {stock_str}")
            kb.add(CallbackButton(f"✏️ {p.name[:25]}", f"admin:set_stock_select:{p.id}"))
            kb.row()

        nav = []
        if page > 0:
            nav.append(CallbackButton("← Назад", f"admin:stock_catpage:{category}:{page-1}"))
        if page < total_pages - 1:
            nav.append(CallbackButton("Вперёд →", f"admin:stock_catpage:{category}:{page+1}"))
        if nav:
            kb.row(*nav)
        kb.row(CallbackButton("↩️ К категориям", "admin:set_stock_list"))
        kb.row(CallbackButton("⚙️ Админ-меню", "admin:menu"))

        await cb.answer(
            text="\n".join(lines),
            keyboard=kb,
            format="markdown"
        )

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:set_stock_select:"))
    async def set_stock_select(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        product_id = int(cb.payload.split(":")[-1])
        cursor.change_state("admin_set_stock")
        cursor.change_data({"product_id": product_id})
        await cb.answer(notification=" ")
        await cb.send("✏️ Введите новое количество (целое число) или 0, чтобы скрыть товар:", keyboard=kb_back_to_menu())

    @bot.on_message(filters.state("admin_set_stock"))
    async def handle_set_stock(message: aiomax.Message, cursor: fsm.FSMCursor):
        if message.sender.user_id != ADMIN_USER_ID:
            return
        data = cursor.get_data() or {}
        product_id = data.get("product_id")
        try:
            new_stock = int(message.body.text.strip())
            if new_stock < 0:
                raise ValueError
        except ValueError:
            await message.reply("❌ Введите целое неотрицательное число.", keyboard=kb_back_to_menu())
            return

        async for session in get_session():
            product = await session.get(Product, product_id)
            if not product:
                await message.reply("❌ Товар не найден.", keyboard=kb_admin_menu())
                cursor.clear()
                return
            product.stock = new_stock
            product.is_active = new_stock > 0
            product.in_stock = new_stock > 0
            await session.commit()
            await message.reply(
                f"✅ Остаток товара «{product.name}» обновлён: {new_stock}",
                keyboard=kb_admin_menu()
            )
        cursor.clear()

    # ------------------- Управление QR-кодом оплаты -----------------------
    @bot.on_button_callback("admin:payment_qr")
    async def admin_payment_qr_menu(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        kb = KeyboardBuilder()
        kb.add(CallbackButton("🖼 Загрузить QR-код", "admin:upload_qr"))
        kb.row(CallbackButton("👀 Показать текущий", "admin:show_qr"))
        kb.row(CallbackButton("🗑 Удалить QR-код", "admin:delete_qr"))
        kb.row(CallbackButton("⚙️ Админ-меню", "admin:menu"))
        await cb.answer(
            text="**Управление QR-кодом для оплаты**",
            keyboard=kb,
            format="markdown"
        )

    @bot.on_button_callback("admin:upload_qr")
    async def admin_upload_qr_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        cursor.change_state(UserStates.ADMIN_PAYMENT_QR)
        await cb.answer(notification=" ")
        await cb.send("📷 Пришлите PNG-изображение с QR-кодом:", keyboard=kb_back_to_menu())

    @bot.on_message(filters.state(UserStates.ADMIN_PAYMENT_QR))
    async def admin_upload_qr_finish(message: aiomax.Message, cursor: fsm.FSMCursor):
        if message.sender.user_id != ADMIN_USER_ID:
            return
        file_id = None
        if message.body and hasattr(message.body, "attachments") and message.body.attachments:
            for att in message.body.attachments:
                if att.type == "image" and hasattr(att, "token") and att.token:
                    file_id = att.token
                    break
        if not file_id:
            await message.reply("❌ Пришлите изображение в формате PNG.", keyboard=kb_back_to_menu())
            return

        async for session in get_session():
            await set_bot_setting(session, "payment_qr_token", file_id)
        cursor.clear()
        await message.reply(
            "✅ QR-код сохранён. Теперь он будет показываться покупателям при оформлении заказа.",
            keyboard=kb_admin_menu()
        )

    @bot.on_button_callback("admin:show_qr")
    async def admin_show_qr(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        async for session in get_session():
            token = await get_bot_setting(session, "payment_qr_token")
        if not token:
            await cb.answer(notification="QR-код не задан.")
            return
        await cb.send(
            attachments=aiomax.PhotoAttachment(token=token),
            keyboard=kb_back_to_menu()
        )
        await cb.answer(notification=" ")

    @bot.on_button_callback("admin:delete_qr")
    async def admin_delete_qr(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        async for session in get_session():
            await set_bot_setting(session, "payment_qr_token", "")
        await cb.answer(notification="QR-код удалён.")