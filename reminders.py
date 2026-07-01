"""
reminders.py — фоновая задача: напоминания об оплате каждый день в 09:00.
"""

import asyncio
import logging
from datetime import datetime

import aiomax

from  db import get_session, get_unpaid_orders_for_reminder

logger = logging.getLogger(__name__)


async def _seconds_until_nine() -> float:
    """Секунды до 06:00 UTC (= 09:00 MSK)."""
    now = datetime.utcnow()
    target = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now >= target:
        from datetime import timedelta
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def reminder_loop(bot: aiomax.Bot) -> None:
    logger.info("Задача напоминаний запущена.")
    while True:
        wait = await _seconds_until_nine()
        logger.info(f"Следующая рассылка напоминаний через {wait/3600:.1f} ч.")
        await asyncio.sleep(wait)
        try:
            await send_reminders(bot)
        except Exception as e:
            logger.error(f"Ошибка при рассылке напоминаний: {e}")
        await asyncio.sleep(70)  # пауза чтобы не сработать дважды


async def send_reminders(bot: aiomax.Bot) -> None:
    async for session in get_session():
        orders = await get_unpaid_orders_for_reminder(session)
        logger.info(f"Напоминаний к отправке: {len(orders)}")

        for order in orders:
            user = order.user
            if not user:
                continue
            count = order.reminder_sent_count + 1
            text = (
                f"⏰ **Напоминание #{count}:** у вас есть неоплаченный заказ "
                f"#{order.id} на {order.total_amount:.0f} ₽.\n\n"
                "Оплатите или отмените его. Напишите /start для открытия меню."
            )
            try:
                await bot.send_message(chat_id=user.id, text=text, format="markdown")
                order.reminder_sent_count += 1
                await session.commit()
            except Exception as e:
                logger.warning(f"Не удалось отправить напоминание {user.id}: {e}")
