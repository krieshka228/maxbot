"""
handlers/orders.py — история заказов и контакт с администратором.

Реализованы:
  - Пагинация (5 заказов на страницу)
  - Кнопка «Отменить» только для заказов моложе 5 минут в статусе pending
  - Кнопка «Дополнить данные» для confirmed-заказов без телефона/адреса
  - Контакт с администратором
"""

import logging
from datetime import datetime, timezone

import aiomax
from aiomax import fsm, filters
from aiomax.buttons import KeyboardBuilder, CallbackButton

from maxbot.config import ADMIN_USER_ID, ADMIN_CHAT_ID
from maxbot.db import get_session, OrderStatus, Order, OrderItem, get_order_with_items, get_bot_setting
from maxbot.keyboards import kb_back_to_menu, kb_unavailable, kb_main_menu
from maxbot.states import UserStates
from maxbot.utils import check_payment_qr

logger = logging.getLogger(__name__)

ORDERS_PER_PAGE = 5
CANCEL_TIMEOUT_SECONDS = 300  # 5 минут

STATUS_LABEL = {
    "pending":   "⏳ Ожидает оплаты",
    "paid":      "💳 Оплачен (проверяется)",
    "confirmed": "✅ Подтверждён",
    "exported":  "🚚 В обработке",
    "cancelled": "❌ Отменён",
}


async def _show_orders_page(cb: aiomax.Callback, user_id: int, page: int):
    from sqlalchemy import select, func
    from sqlalchemy.orm import selectinload

    async for session in get_session():
        total_stmt = select(Order).where(Order.user_id == user_id, Order.status != OrderStatus.draft)
        total = (await session.execute(select(func.count()).select_from(total_stmt.subquery()))).scalar()

        stmt = (
            select(Order)
            .where(Order.user_id == user_id, Order.status != OrderStatus.draft)
            .options(selectinload(Order.items).selectinload(OrderItem.product))
            .order_by(Order.created_at.desc())
            .offset(page * ORDERS_PER_PAGE)
            .limit(ORDERS_PER_PAGE)
        )
        orders = (await session.execute(stmt)).scalars().all()
        qr_token = await get_bot_setting(session, "payment_qr_token")

    if total == 0:
        await cb.answer(
            text="📋 У вас пока нет оформленных заказов.",
            keyboard=kb_back_to_menu(),
            format="markdown"
        )
        return

    total_pages = (total - 1) // ORDERS_PER_PAGE + 1
    lines = [f"📋 **Ваши заказы** (стр. {page + 1}/{total_pages})\n"]
    now = datetime.now(timezone.utc)

    for order in orders:
        label = STATUS_LABEL.get(order.status.value, order.status.value)
        qty = sum(i.quantity for i in order.items)
        address_line = f"\n  Адрес: {order.delivery_address}" if order.delivery_address else ""
        lines.append(
            f"• Заказ #{order.id} — {label}\n"
            f"  {qty} поз. на {order.total_amount:.0f} ₽"
            + address_line
        )

    kb = KeyboardBuilder()
    for order in orders:
        # Кнопка "Отменить" для pending (до 5 мин)
        if order.status == OrderStatus.pending:
            created_at = order.created_at.replace(tzinfo=timezone.utc)
            age = (now - created_at).total_seconds()
            if age < CANCEL_TIMEOUT_SECONDS:
                kb.add(CallbackButton(f"❌ Отменить #{order.id}", f"payment:cancel:{order.id}", intent='default'))
                kb.row()
            if qr_token:
                kb.add(CallbackButton(f"💳 Оплатить #{order.id}", f"payment:receipt:{order.id}", intent='default'))
                kb.row()
        # Новая кнопка "Изменить заказ" для всех, кроме отменённых
        if order.status != OrderStatus.cancelled:
            kb.add(CallbackButton(f"✏️ Изменить заказ #{order.id}", f"orders:edit:{order.id}", intent='default'))
            kb.row()

    # Навигация
    nav = []
    if page > 0:
        nav.append(CallbackButton("← Назад", f"orders:page:{page - 1}", intent='default'))
    if page < total_pages - 1:
        nav.append(CallbackButton("Вперёд →", f"orders:page:{page + 1}", intent='default'))
    if nav:
        kb.row(*nav)

    kb.row(CallbackButton("🏠 Главное меню", "menu:main", intent='default'))
    await cb.answer(text="\n".join(lines), keyboard=kb, format="markdown")


def register(bot: aiomax.Bot) -> None:
    @bot.on_button_callback(lambda cb: cb.payload.startswith("orders:edit:"))
    async def orders_edit_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        order_id = int(cb.payload.split(":")[-1])
        await cb.answer(notification=" ")

        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order or order.user_id != user_id:
                await cb.send("❌ Заказ не найден.", keyboard=kb_back_to_menu())
                return
            if order.status == OrderStatus.cancelled:
                await cb.send("❌ Отменённый заказ нельзя редактировать.", keyboard=kb_back_to_menu())
                return

        cursor.change_data({"order_id": order_id, "editing": True})
        cursor.change_state(UserStates.AWAITING_PHONE)
        await cb.send("📱 Введите ваш номер телефона для связи:", keyboard=kb_back_to_menu())
    @bot.on_button_callback("orders:list")
    async def orders_list(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id

        if user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return

        await cb.answer(notification=" ")
        await _show_orders_page(cb, user_id, page=0)

    @bot.on_button_callback(lambda cb: cb.payload.startswith("orders:page:"))
    async def orders_page(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        page = int(cb.payload.split(":")[-1])
        await cb.answer(notification=" ")
        await _show_orders_page(cb, user_id, page)

    @bot.on_button_callback(lambda cb: cb.payload.startswith("orders:complete:"))
    async def orders_complete_data(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        order_id = int(cb.payload.split(":")[-1])
        await cb.answer(notification=" ")

        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order or order.user_id != user_id:
                await cb.send("❌ Заказ не найден.", keyboard=kb_back_to_menu())
                return
            missing_full_name = not order.full_name
            missing_phone = not order.contact_phone
            missing_address = not order.delivery_address

        cursor.change_data({"order_id": order_id})

        if missing_full_name:
            cursor.change_state(UserStates.AWAITING_FULL_NAME)
            await cb.send("✏️ Введите ваше полное имя (ФИО):", keyboard=kb_back_to_menu())
        elif missing_phone:
            cursor.change_state(UserStates.AWAITING_PHONE)
            await cb.send("📱 Введите ваш номер телефона для связи:", keyboard=kb_back_to_menu())
        elif missing_address:
            cursor.change_state(UserStates.AWAITING_ADDRESS)
            await cb.send("📍 Введите адрес доставки:", keyboard=kb_back_to_menu())
        else:
            await cb.send("✅ Все данные заказа уже заполнены.", keyboard=kb_back_to_menu())

    @bot.on_button_callback("contact:admin")
    async def contact_admin_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        cursor.change_state(UserStates.CONTACT_ADMIN)
        await cb.answer(notification=" ")
        await cb.send("✉️ Напишите ваш вопрос — передадим администратору:")
