"""
handlers/orders.py — история заказов и контакт с администратором.
"""

import logging
import aiomax
from aiomax import fsm, filters
from aiomax.buttons import KeyboardBuilder, CallbackButton, ChatButton

from config import ADMIN_USER_ID, ADMIN_CHAT_ID
from db import get_session, OrderStatus
from keyboards import kb_back_to_menu, kb_unavailable, kb_main_menu
from states import UserStates
from utils import check_payment_qr

logger = logging.getLogger(__name__)

STATUS_LABEL = {
    "pending":   "⏳ Ожидает оплаты",
    "paid":      "💳 Оплачен (проверяется)",
    "confirmed": "✅ Подтверждён",
    "exported":  "🚚 В обработке",
    "cancelled": "❌ Отменён",
}


def register(bot: aiomax.Bot) -> None:
    @bot.on_button_callback("orders:list")
    async def orders_list(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.message.sender.user_id

        if user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return

        await cb.answer(notification=" ")

        async for session in get_session():
            from sqlalchemy import select
            from db import Order
            from sqlalchemy.orm import selectinload
            from db import OrderItem
            stmt = (
                select(Order)
                .where(Order.user_id == user_id, Order.status != OrderStatus.draft)
                .options(selectinload(Order.items).selectinload(OrderItem.product))
                .order_by(Order.created_at.desc())
                .limit(10)
            )
            result = await session.execute(stmt)
            orders = result.scalars().all()

        if not orders:
            await cb.answer(
                text="📋 У вас пока нет оформленных заказов.",
                keyboard=kb_back_to_menu(),
                format="markdown"
            )
            return

        lines = ["📋 **Ваши заказы:**\n"]
        kb = KeyboardBuilder()
        for order in orders:
            label = STATUS_LABEL.get(order.status.value, order.status.value)
            qty = sum(i.quantity for i in order.items)
            lines.append(
                f"• Заказ #{order.id} — {label}\n"
                f"  {qty} поз. на {order.total_amount:.0f} ₽"
                + (f"\n  Адрес: {order.delivery_address}" if order.delivery_address else "")
            )

            if order.status == OrderStatus.pending:
                kb.add(CallbackButton(f"💳 Оплатить #{order.id}", f"payment:receipt:{order.id}", intent='default'))
                kb.row(CallbackButton(f"❌ Отменить #{order.id}", f"payment:cancel:{order.id}", intent='default'))
            # Для других статусов кнопок не добавляем

        kb.row(CallbackButton("🏠 Главное меню", "menu:main", intent='default'))
        await cb.answer(text="\n".join(lines), keyboard=kb, format="markdown")

    from aiomax import ContactAttachment  # добавьте в импорты

    @bot.on_button_callback("contact:admin")
    async def contact_admin_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        cursor.change_state(UserStates.CONTACT_ADMIN)
        await cb.answer(notification=" ")
        await cb.send("✉️ Напишите ваш вопрос — передадим администратору:")