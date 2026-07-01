"""
handlers/checkout.py — оформление заказа, счёт, чек, подтверждение.
"""

import logging
from datetime import datetime, timezone

import aiomax
from aiomax import fsm

from config import ADMIN_CHAT_ID, ADMIN_USER_ID, PAYMENT_DETAILS
from db import get_session, get_draft_order, get_order_with_items, OrderStatus, get_bot_setting
from keyboards import kb_payment, kb_admin_confirm_payment, kb_back_to_menu, kb_unavailable
from utils import format_cart, format_order_for_admin
from cache import invalidate_catalog_cache
from states import UserStates
from utils import check_payment_qr
from sqlalchemy import text

logger = logging.getLogger(__name__)


def register(bot: aiomax.Bot) -> None:
    # Примечание: обработчик "checkout:confirm:" был удалён — на него не
    # ссылалась ни одна клавиатура (мёртвый код), а главное — он не
    # списывал остаток товара, в отличие от настоящего пути оформления
    # заказа через "cart:checkout:" (см. handlers/cart.py::cart_checkout).
    # Оставлять его было небезопасно: случайное переиспользование кнопки
    # позволило бы оформить заказ без проверки и списания остатков.

    @bot.on_button_callback(lambda cb: cb.payload.startswith("payment:receipt:"))
    async def payment_receipt_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return
        order_id = int(cb.payload.split(":")[-1])
        cursor.change_state(UserStates.AWAITING_RECEIPT)
        cursor.change_data({"order_id": order_id})
        await cb.answer(notification=" ")
        await cb.send("📷 Пришлите фото или скриншот чека об оплате:")

    @bot.on_button_callback(lambda cb: cb.payload.startswith("payment:cancel:"))
    async def payment_cancel(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        if cb.user.user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return

        order_id = int(cb.payload.split(":")[-1])
        await cb.answer(notification=" ")

        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order or order.user_id != cb.user.user_id:
                await cb.send("❌ Заказ не найден.")
                return

            # Отмена доступна только для заказов в статусе "pending",
            # созданных менее 5 минут назад
            if order.status != OrderStatus.pending:
                await cb.send(
                    "⚠️ Заказ уже оплачен или подтверждён, отмена невозможна.",
                    keyboard=kb_back_to_menu(),
                )
                return

            created_at = order.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - created_at).total_seconds()
            if age_seconds >= 300:
                await cb.send(
                    "⚠️ С момента оформления заказа прошло более 5 минут. Отмена невозможна.",
                    keyboard=kb_back_to_menu(),
                )
                return

            # 1. Атомарно меняем статус на cancelled, если он ещё pending
            result = await session.execute(
                text("UPDATE orders SET status = :new_status WHERE id = :id AND status = :pending_status"),
                {"new_status": OrderStatus.cancelled, "id": order_id, "pending_status": OrderStatus.pending}
            )
            if result.rowcount == 0:
                await cb.send("❌ Этот заказ уже отменён или его статус изменился.")
                return

            # 2. Возвращаем остатки и восстанавливаем видимость товара в каталоге
            for item in order.items:
                if item.product and item.product.stock is not None:
                    await session.execute(
                        text(
                            "UPDATE products SET stock = stock + :qty, "
                            "is_active = (stock + :qty) > 0, in_stock = (stock + :qty) > 0 "
                            "WHERE id = :id"
                        ),
                        {"qty": item.quantity, "id": item.product.id}
                    )

            await session.commit()
            invalidate_catalog_cache()

            # Уведомляем администратора об отмене
            fio = order.full_name or (order.user.full_name if order.user else None)
            admin_text = f"❌ Клиент отменил заказ #{order_id} на {order.total_amount:.0f} ₽."
            if fio:
                admin_text += f"\nФИО: {fio}"
            try:
                await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text)
            except Exception as e:
                logger.warning(f"Не удалось уведомить администратора об отмене: {e}")

            
            order_info = format_order_for_admin(order)
            text = f"❌ **Заказ #{order_id} отменён.**\n\n{order_info}"
            await cb.send(
                text,
                keyboard=kb_back_to_menu(),
                format="markdown"
            )

    # ── Администратор: подтверждение оплаты ──────────────────────────────
    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:pay_ok:"))
    async def admin_pay_ok(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        logger.info(f"CALLBACK admin:pay_ok user_id={cb.user.user_id}")
        # Сразу подтверждаем получение callback, чтобы избежать повторов
        await cb.answer(notification=" ")

        if cb.user.user_id != ADMIN_USER_ID:
            await cb.send("❌ Нет доступа.", keyboard=kb_back_to_menu())
            return
        order_id = int(cb.payload.split(":")[-1])
        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order:
                await cb.send("❌ Заказ не найден.", keyboard=kb_back_to_menu())
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
        # Отправляем подтверждение админу (редактируем текущее сообщение или отправляем новое)
        await cb.send(f"✅ Заказ #{order_id} подтверждён. Ожидаем телефон от клиента.")

    @bot.on_button_callback(lambda cb: cb.payload.startswith("admin:pay_fail:"))
    async def admin_pay_fail(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        logger.info(f"CALLBACK admin:pay_fail user_id={cb.user.user_id}")
        await cb.answer(notification=" ")

        if cb.user.user_id != ADMIN_USER_ID:
            await cb.send("❌ Нет доступа.", keyboard=kb_back_to_menu())
            return
        order_id = int(cb.payload.split(":")[-1])
        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order:
                await cb.send("❌ Заказ не найден.", keyboard=kb_back_to_menu())
                return
            order.status = OrderStatus.pending
            await session.commit()
            client_id = order.user_id

        await bot.send_message(
            user_id=client_id,
            text=(
                f"❌ Оплата заказа #{order_id} не подтверждена.\n"
                "Проверьте реквизиты и попробуйте снова или напишите администратору."
            ),
            keyboard=kb_payment(order_id),
        )
        await cb.send(f"❌ Оплата заказа #{order_id} отклонена.")
