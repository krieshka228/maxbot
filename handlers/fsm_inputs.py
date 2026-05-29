"""
handlers/fsm_inputs.py — обработчики текстовых и callback-вводов в состояниях FSM:
  - Ввод телефона после подтверждения оплаты
  - Выбор способа доставки (callback)
  - Ввод адреса доставки
  - Приём фото чека
  - Сообщение администратору
"""

import logging
import aiomax
from aiomax import fsm, filters
from aiomax.buttons import KeyboardBuilder, CallbackButton

from config import ADMIN_USER_ID, ADMIN_CHAT_ID
from db import (
    get_session,
    get_or_create_user,
    get_order_with_items,
    OrderStatus,
)
from keyboards import kb_main_menu, kb_back_to_menu, kb_admin_confirm_payment
from states import UserStates
from utils import format_order_for_admin

logger = logging.getLogger(__name__)


def register(bot: aiomax.Bot) -> None:

    # ── Ввод телефона после подтверждения оплаты ─────────────────────────
    @bot.on_message(filters.state(UserStates.AWAITING_PHONE))
    async def handle_phone(message: aiomax.Message, cursor: fsm.FSMCursor):
        user_id = message.sender.user_id
        phone = message.body.text.strip() if message.body and message.body.text else ""
        if not phone or not phone.replace("+", "").replace(" ", "").isdigit():
            await message.reply("❌ Введите корректный номер телефона (цифры, можно с +).")
            return

        data = cursor.get_data() or {}
        order_id = data.get("order_id")

        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order or order.user_id != user_id:
                cursor.clear()
                await message.reply("❌ Заказ не найден.")
                return
            order.contact_phone = phone
            user = await get_or_create_user(session, user_id)
            user.phone = phone
            await session.commit()

        # Переходим к выбору способа доставки
        cursor.change_state(UserStates.AWAITING_DELIVERY_METHOD)
        cursor.change_data({"order_id": order_id})

        kb = KeyboardBuilder()
        kb.add(CallbackButton("Озон", "delivery:ozon"))
        kb.row(CallbackButton("Яндекс", "delivery:yandex"))
        kb.row(CallbackButton("СДЭК до ПВЗ", "delivery:cdek"))
        kb.row(CallbackButton("🏠 Главное меню", "menu:main"))
        await message.reply("🚚 Выберите способ доставки:", keyboard=kb)

    # ── Обработчик выбора способа доставки (callback) ─────────────────────
    @bot.on_button_callback(lambda cb: cb.payload.startswith("delivery:"))
    async def handle_delivery_choice(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        data = cursor.get_data() or {}
        order_id = data.get("order_id")

        method = cb.payload.split(":")[1]  # ozon, yandex, cdek
        delivery_names = {
            "ozon": "Озон",
            "yandex": "Яндекс",
            "cdek": "СДЭК до ПВЗ",
        }
        delivery_name = delivery_names.get(method, method)

        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if order and order.user_id == user_id:
                order.delivery_method = delivery_name
                await session.commit()

        cursor.change_state(UserStates.AWAITING_ADDRESS)
        cursor.change_data({"order_id": order_id})
        await cb.answer(notification=" ")
        await cb.send(
            f"✅ Выбрана доставка: {delivery_name}\n📍 Теперь введите адрес доставки:",
            keyboard=kb_back_to_menu()
        )

    # ── Ввод адреса доставки ──────────────────────────────────────────────
    @bot.on_message(filters.state(UserStates.AWAITING_ADDRESS))
    async def handle_address(message: aiomax.Message, cursor: fsm.FSMCursor):
        user_id = message.sender.user_id
        address = message.body.text.strip() if message.body and message.body.text else ""
        if not address:
            await message.reply("Пожалуйста, напишите адрес доставки текстом.")
            return

        data = cursor.get_data() or {}
        order_id = data.get("order_id")

        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order or order.user_id != user_id:
                cursor.clear()
                await message.reply("❌ Заказ не найден.")
                return
            order.delivery_address = address
            user = await get_or_create_user(session, user_id)
            user.address = address
            await session.commit()

        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order or order.user_id != user_id:
                cursor.clear()
                await message.reply("❌ Заказ не найден.")
                return
            order.delivery_address = address
            user = await get_or_create_user(session, user_id)
            user.address = address
            await session.commit()

            # Формируем сообщение для администратора
            user_info = f"{user.full_name or 'Без имени'} (ID {user.id})"
            phone = user.phone or "не указан"
            delivery_method = order.delivery_method or "не выбран"
            items_lines = []
            for item in order.items:
                product_name = item.product.name if item.product else f"Товар #{item.product_id}"
                items_lines.append(f"• {product_name}: {item.quantity} шт. × {item.price_at_order:.0f} ₽")
            items_text = "\n".join(items_lines)

            admin_text = (
                f"📦 **Заказ #{order_id} готов к отправке**\n\n"
                f"👤 Клиент: {user_info}\n"
                f"📱 Телефон: {phone}\n"
                f"🚚 Доставка: {delivery_method}\n"
                f"📍 Адрес: {address}\n\n"
                f"🛒 **Товары:**\n{items_text}\n\n"
                f"💰 **Итого: {order.total_amount:.0f} ₽**"
            )

            try:
                await bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=admin_text,
                    format="markdown"
                )
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление администратору: {e}")

        cursor.clear()
        await message.reply(
            f"✅ Адрес сохранён!\n\nЗаказ #{order_id} принят в работу!",
            keyboard=kb_main_menu(is_admin=(user_id == ADMIN_USER_ID)),
        )

    # ── Приём фото чека ───────────────────────────────────────────────────
    @bot.on_message(filters.state(UserStates.AWAITING_RECEIPT))
    async def handle_receipt(message: aiomax.Message, cursor: fsm.FSMCursor):
        user_id = message.sender.user_id
        data = cursor.get_data() or {}
        order_id = data.get("order_id")

        # Ищем первое изображение во вложениях
        file_id = None
        if message.body and hasattr(message.body, "attachments") and message.body.attachments:
            for att in message.body.attachments:
                if att.type == "image" and hasattr(att, "token") and att.token:
                    file_id = att.token
                    break

        if file_id is None:
            await message.reply("📷 Пожалуйста, пришлите именно фото чека (изображение).")
            return

        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order or order.user_id != user_id:
                cursor.clear()
                await message.reply("❌ Заказ не найден.")
                return
            order.status = OrderStatus.paid
            order.receipt_file_id = file_id
            await session.commit()

            order_info = format_order_for_admin(order)

        # Уведомляем администратора
        try:
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"💳 **Новый чек об оплате!**\n\n"
                    f"{order_info}\n\n"
                    "Проверьте оплату:"
                ),
                format="markdown",
                keyboard=kb_admin_confirm_payment(order_id),
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить администратора: {e}")

        cursor.clear()
        await message.reply(
            f"✅ Чек получен! Ожидайте подтверждения оплаты по заказу #{order_id}.",
            keyboard=kb_main_menu(is_admin=(user_id == ADMIN_USER_ID)),
        )

    # ── Сообщение администратору ─────────────────────────────────────────
    @bot.on_message(filters.state(UserStates.CONTACT_ADMIN))
    async def handle_contact_admin(message: aiomax.Message, cursor: fsm.FSMCursor):
        user_id = message.sender.user_id
        text = message.body.text.strip() if message.body and message.body.text else ""
        if not text:
            await message.reply("Напишите ваш вопрос текстом.")
            return

        sender_name = message.sender.name or f"ID {user_id}"
        fwd_text = (
            f"✉️ **Сообщение от клиента**\n"
            f"Клиент: {sender_name} (ID: {user_id})\n\n"
            f"{text}"
        )
        try:
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=fwd_text,
                format="markdown"
            )
        except Exception as e:
            logger.error(f"Не удалось переслать сообщение: {e}")

        cursor.clear()
        await message.reply(
            "✅ Сообщение отправлено администратору!",
            keyboard=kb_main_menu(is_admin=(user_id == ADMIN_USER_ID)),
        )