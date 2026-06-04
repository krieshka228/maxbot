"""
handlers/checkout.py — оформление заказа, счёт, чек, подтверждение.
"""

import logging

import aiomax
from aiomax import fsm

from config import ADMIN_CHAT_ID, ADMIN_USER_ID, PAYMENT_DETAILS
from db import get_session, get_draft_order, get_order_with_items, OrderStatus, get_bot_setting
from keyboards import kb_payment, kb_admin_confirm_payment, kb_back_to_menu
from utils import format_cart, format_order_for_admin
from states import UserStates
from utils import check_payment_qr

logger = logging.getLogger(__name__)


def register(bot: aiomax.Bot) -> None:
    # В файле handlers/checkout.py

    @bot.on_button_callback(lambda cb: cb.payload.startswith("checkout:confirm:"))
    async def checkout_confirm(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        await cb.answer(notification=" ")
        async for session in get_session():
            order = await get_draft_order(session, cb.user.user_id)
            if not order or not order.items:
                await cb.send("🛒 Корзина пуста.")
                return
            order.status = OrderStatus.pending
            await session.commit()

        # --- Блок добавления QR-кода ---
        attachments = []
        async for session in get_session():
            qr_token = await get_bot_setting(session, "payment_qr_token")
        if qr_token:
            attachments.append(aiomax.PhotoAttachment(token=qr_token))
        # -----------------------------

        cart_text = format_cart(order)
        msg_text = (
            f"✅ **Заказ #{order.id} оформлен!**\n\n"
            f"{cart_text}\n\n"
            f"💳 **Реквизиты для оплаты:**\n{PAYMENT_DETAILS}\n\n"
            "После оплаты нажмите кнопку ниже и пришлите фото чека."
        )

        await cb.send(
            msg_text,
            format="markdown",
            keyboard=kb_payment(order.id),
            attachments=attachments if attachments else None
        )

    @bot.on_button_callback(lambda cb: cb.payload.startswith("payment:receipt:"))
    async def payment_receipt_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(notification="Функционал временно недоступен. Напишите администратору.")
            return
        order_id = int(cb.payload.split(":")[-1])
        cursor.change_state(UserStates.AWAITING_RECEIPT)
        cursor.change_data({"order_id": order_id})
        await cb.answer(notification=" ")
        await cb.send("📷 Пришлите фото или скриншот чека об оплате:")

    @bot.on_button_callback(lambda cb: cb.payload.startswith("payment:cancel:"))
    async def payment_cancel(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(notification="Функционал временно недоступен. Напишите администратору.")
            return
        order_id = int(cb.payload.split(":")[-1])
        await cb.answer(notification=" ")
        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if order and order.user_id == cb.user.user_id:
                order.status = OrderStatus.cancelled
                # Возвращаем остатки на склад
                for item in order.items:
                    if item.product and item.product.stock is not None:
                        item.product.stock += item.quantity
                await session.commit()
                await cb.send(
                    f"❌ Заказ #{order_id} отменён.",
                    keyboard=kb_back_to_menu(),
                )
            else:
                await cb.send("❌ Заказ не найден.")

    # ── Администратор: подтверждение оплаты ──────────────────────────────
    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:pay_ok:"))
    async def admin_pay_ok(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        order_id = int(cb.payload.split(":")[-1])
        await cb.answer(notification="Оплата подтверждена!")
        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order:
                await cb.send("❌ Заказ не найден.")
                return
            order.status = OrderStatus.confirmed
            await session.commit()
            client_id = order.user_id

        # Уведомляем клиента и запрашиваем телефон
        bot.storage.change_state(client_id, UserStates.AWAITING_PHONE)
        bot.storage.change_data(client_id, {"order_id": order_id})
        await bot.send_message(
            user_id=client_id,
            text="📱 Введите ваш номер телефона для связи:"
        )
        await cb.send(f"✅ Заказ #{order_id} подтверждён. Ожидаем телефон от клиента.")
    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:pay_fail:"))
    async def admin_pay_fail(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID:
            await cb.answer(notification="❌ Нет доступа.")
            return
        order_id = int(cb.payload.split(":")[-1])
        await cb.answer(notification="Оплата отклонена.")
        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order:
                await cb.send("❌ Заказ не найден.")
                return
            order.status = OrderStatus.pending
            await session.commit()
            client_id = order.user_id

        await bot.send_message(
            user_id=client_id,  # было chat_id — исправлено
            text=(
                f"❌ Оплата заказа #{order_id} не подтверждена.\n"
                "Проверьте реквизиты и попробуйте снова или напишите администратору."
            ),
            keyboard=kb_payment(order_id),
        )
        await cb.send(f"❌ Оплата заказа #{order_id} отклонена.")