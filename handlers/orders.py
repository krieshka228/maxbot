"""
handlers/orders.py — история заказов и контакт с администратором.
"""

import logging
from .catalog import delete_catalog_messages
import aiomax
from aiomax import fsm

from config import ADMIN_USER_ID
from db import get_session, OrderStatus
from keyboards import kb_back_to_menu, kb_main_menu
from states import UserStates

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
        user_id = cb.user.user_id
        await cb.answer(notification=" ")
        # Удаляем все сообщения каталога
        await delete_catalog_messages(user_id, bot, also_delete_message_id=cb.message.id)

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
            await cb.send("📋 У вас пока нет оформленных заказов.", keyboard=kb_back_to_menu())
            return

        lines = ["📋 **Ваши заказы:**\n"]
        for order in orders:
            label = STATUS_LABEL.get(order.status.value, order.status.value)
            qty = sum(i.quantity for i in order.items)
            lines.append(
                f"• Заказ #{order.id} — {label}\n"
                f"  {qty} поз. на {order.total_amount:.0f} ₽"
                + (f"\n  Адрес: {order.delivery_address}" if order.delivery_address else "")
            )

        await cb.send("\n".join(lines), format="markdown", keyboard=kb_back_to_menu())
    @bot.on_button_callback("contact:admin")
    async def contact_admin_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        cursor.change_state(UserStates.CONTACT_ADMIN)
        await cb.answer(notification=" ")
        await cb.send("✉️ Напишите ваш вопрос — передадим администратору:")
