"""
handlers/fsm_inputs.py — обработчики текстовых и callback-вводов в состояниях FSM:
  - Ввод телефона после подтверждения оплаты
  - Выбор способа доставки (callback)
  - Ввод адреса доставки
  - Приём фото чека
  - Сообщение администратору
  - Редактирование данных перед сохранением
  - Финальное подтверждение заказа с одной кнопкой «Изменить заказ»
"""

import logging
import aiomax
from aiomax import fsm, filters
from aiomax.buttons import KeyboardBuilder, CallbackButton

from maxbot.config import ADMIN_USER_ID, ADMIN_CHAT_ID
from maxbot.db import (
    get_session,
    get_or_create_user,
    get_order_with_items,
    OrderStatus,
)
from maxbot.keyboards import kb_main_menu, kb_back_to_menu, kb_admin_confirm_payment
from maxbot.states import UserStates
from maxbot.utils import format_order_for_admin

logger = logging.getLogger(__name__)


def register(bot: aiomax.Bot) -> None:

    # ── Ввод телефона после подтверждения оплаты ─────────────────────────
    @bot.on_message(filters.state(UserStates.AWAITING_PHONE))
    async def handle_phone(message: aiomax.Message, cursor: fsm.FSMCursor):
        from maxbot.validators import normalize_phone
        user_id = message.sender.user_id
        raw_phone = message.body.text if message.body and message.body.text else ""
        phone = normalize_phone(raw_phone)
        if not phone:
            await message.reply("❌ Введите корректный номер телефона (10–15 цифр, можно с +).")
            return

        data = cursor.get_data() or {}
        order_id = data.get("order_id")
        editing = data.get("editing", False)

        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order or order.user_id != user_id:
                cursor.clear()
                await message.reply("❌ Заказ не найден.")
                return
            order.contact_phone = phone
            user = await get_or_create_user(
                session, user_id,
                full_name=message.sender.name,
                username=getattr(message.sender, 'username', None),
                platform="MAX"
            )
            user.phone = phone
            await session.commit()

        cursor.change_state(UserStates.AWAITING_FULL_NAME)
        cursor.change_data({"order_id": order_id, "editing": editing})
        await message.reply("✏️ Введите ваше полное имя (ФИО) для заказа:")

    # ── Ввод ФИО (между телефоном и выбором доставки) ─────────────────────
    @bot.on_message(filters.state(UserStates.AWAITING_FULL_NAME))
    async def handle_full_name(message: aiomax.Message, cursor: fsm.FSMCursor):
        user_id = message.sender.user_id
        full_name = message.body.text.strip() if message.body and message.body.text else ""
        if not full_name:
            await message.reply("❌ Введите ваше полное имя (ФИО).")
            return

        data = cursor.get_data() or {}
        order_id = data.get("order_id")
        editing = data.get("editing", False)

        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order or order.user_id != user_id:
                cursor.clear()
                await message.reply("❌ Заказ не найден.")
                return
            order.full_name = full_name
            user = await get_or_create_user(
                session, user_id,
                full_name=message.sender.name,
                username=getattr(message.sender, 'username', None),
                platform="MAX"
            )
            user.full_name = full_name
            await session.commit()

        # Показываем выбор доставки
        kb = KeyboardBuilder()
        kb.add(CallbackButton("Озон", "delivery:ozon"))
        kb.row(CallbackButton("Яндекс", "delivery:yandex"))
        kb.row(CallbackButton("СДЭК до ПВЗ", "delivery:cdek"))
        kb.row(CallbackButton("🏠 Главное меню", "menu:main"))

        cursor.change_state(UserStates.AWAITING_DELIVERY_METHOD)
        cursor.change_data({"order_id": order_id, "editing": editing})
        await message.reply("✅ ФИО сохранено!\n\n🚚 Теперь выберите способ доставки:", keyboard=kb)
    # ── Обработчик выбора способа доставки (callback) ─────────────────────
    @bot.on_button_callback(lambda cb: cb.payload.startswith("delivery:"))
    async def handle_delivery_choice(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        data = cursor.get_data() or {}
        order_id = data.get("order_id")
        editing = data.get("editing", False)

        method = cb.payload.split(":")[1]
        delivery_names = {"ozon": "Озон", "yandex": "Яндекс", "cdek": "СДЭК до ПВЗ"}
        delivery_name = delivery_names.get(method, method)

        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if order and order.user_id == user_id:
                order.delivery_method = delivery_name
                await session.commit()

        cursor.change_state(UserStates.AWAITING_ADDRESS)
        cursor.change_data({"order_id": order_id, "editing": editing})
        await cb.answer(notification=" ")
        await cb.send(
            f"✅ Выбрана доставка: {delivery_name}\n📍 Теперь введите адрес доставки:",
            keyboard=kb_back_to_menu()
        )

    # ── Ввод адреса доставки (теперь НЕ сохраняет сразу) ────────────────
    @bot.on_message(filters.state(UserStates.AWAITING_ADDRESS))
    async def handle_address(message: aiomax.Message, cursor: fsm.FSMCursor):
        user_id = message.sender.user_id
        address = message.body.text.strip() if message.body and message.body.text else ""
        if not address:
            await message.reply("Пожалуйста, напишите адрес доставки текстом.")
            return

        data = cursor.get_data() or {}
        order_id = data.get("order_id")

        # Сохраняем адрес пока только в FSM, не пишем в базу
        cursor.change_data({"order_id": order_id, "address": address})
        cursor.change_state(UserStates.AWAITING_CONFIRMATION)

        # Получаем текущие данные заказа для отображения
        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order or order.user_id != user_id:
                cursor.clear()
                await message.reply("❌ Заказ не найден.")
                return
            phone = order.contact_phone or "не указан"
            delivery_method = order.delivery_method or "не выбран"
            items_lines = []
            for item in order.items:
                product_name = item.product.name if item.product else f"Товар #{item.product_id}"
                items_lines.append(f"• {product_name}: {item.quantity} шт. × {item.price_at_order:.0f} ₽")
            items_text = "\n".join(items_lines)

            kb = KeyboardBuilder()
            kb.add(CallbackButton("✏️ Изменить заказ", "edit:order"))
            kb.row(CallbackButton("✅ Всё верно, оформить", "confirm:final"))

            await message.reply(
                f"📋 **Проверьте данные заказа #{order_id}:**\n\n"
                f"📱 Телефон: {phone}\n"
                f"🚚 Доставка: {delivery_method}\n"
                f"📍 Адрес: {address}\n\n"
                f"🛒 **Товары:**\n{items_text}\n\n"
                f"💰 **Итого: {order.total_amount:.0f} ₽**\n\n"
                "Если всё верно, нажмите «✅ Всё верно, оформить».",
                keyboard=kb,
                format="markdown"
            )

    # ── Обработчик кнопки "Изменить заказ" ─────────────────────────────
    @bot.on_button_callback("edit:order")
    async def edit_order(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        """Возвращает пользователя к вводу телефона, чтобы он мог заново заполнить все данные."""
        cursor.change_state(UserStates.AWAITING_PHONE)
        await cb.answer(notification=" ")
        await cb.send("📱 Введите ваш номер телефона для связи:", keyboard=kb_back_to_menu())

    # ── Обработчик подтверждения "Всё верно, оформить" ──────────────────
    @bot.on_button_callback("confirm:final")
    async def confirm_final(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        data = cursor.get_data() or {}
        order_id = data.get("order_id")
        editing = data.get("editing", False)

        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order or order.user_id != user_id:
                cursor.clear()
                await cb.send("❌ Заказ не найден.")
                return

            if not editing:
                # Обычное оформление — уведомляем админа
                phone = order.contact_phone or "не указан"
                delivery_method = order.delivery_method or "не выбран"
                address = order.delivery_address or "не указан"
                items_lines = []
                for item in order.items:
                    product_name = item.product.name if item.product else f"Товар #{item.product_id}"
                    items_lines.append(f"• {product_name}: {item.quantity} шт. × {item.price_at_order:.0f} ₽")
                items_text = "\n".join(items_lines)

                admin_text = (
                    f"📦 **Заказ #{order_id} готов к отправке**\n\n"
                    f"👤 Клиент: {order.full_name or 'Без имени'} (ID {user_id})\n"
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
                    logger.error(f"Не удалось уведомить администратора: {e}")

            # Если редактирование — просто показываем успех
            cursor.clear()
            await cb.answer(notification=" ")
            if editing:
                await cb.send(
                    f"✅ Данные заказа #{order_id} обновлены!",
                    keyboard=kb_main_menu(is_admin=(user_id == ADMIN_USER_ID)),
                )
            else:
                await cb.send(
                    f"✅ Заказ #{order_id} оформлен! Данные сохранены.",
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

        # Уведомляем администратора с фото чека
        try:
            receipt_attachment = aiomax.PhotoAttachment(token=file_id)
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"💳 **Новый чек об оплате!**\n\n"
                    f"{order_info}\n\n"
                    "Проверьте оплату:"
                ),
                format="markdown",
                attachments=receipt_attachment,
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