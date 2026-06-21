"""
middlewares.py — антифлуд и логирование обновлений для aiomax.

В aiomax нет TypeHandler, поэтому антифлуд реализован как обёртка-патч
над handle_update бота: вызывается ДО передачи апдейта в роутер.
"""

import logging
import time
from collections import defaultdict, deque
from typing import Deque

from config import ADMIN_USER_ID, settings

logger = logging.getLogger(__name__)

# Параметры антифлуда берутся из .env (RATE_LIMIT_MESSAGES / RATE_LIMIT_WINDOW),
# по умолчанию — не более 20 запросов за 10 секунд.
RATE_LIMIT_WINDOW = settings.rate_limit_window
RATE_LIMIT_MESSAGES = settings.rate_limit_messages

_hits: dict[int, Deque[float]] = defaultdict(deque)
_last_warned: dict[int, float] = {}


def is_rate_limited(user_id: int) -> bool:
    """Возвращает True, если пользователь превысил лимит запросов."""
    if user_id == ADMIN_USER_ID:
        return False  # Администратора не лимитируем

    now = time.monotonic()
    hits = _hits[user_id]
    # Очищаем устаревшие отметки
    while hits and (now - hits[0]) > RATE_LIMIT_WINDOW:
        hits.popleft()

    if len(hits) >= RATE_LIMIT_MESSAGES:
        last = _last_warned.get(user_id, 0.0)
        if (now - last) > RATE_LIMIT_WINDOW:
            _last_warned[user_id] = now
            logger.warning(
                "rate limit exceeded",
                extra={"event": "rate_limit", "user_id": user_id},
            )
        return True

    hits.append(now)
    return False


def patch_bot_antiflood(bot) -> None:
    """Патчит bot.handle_update для добавления антифлуда."""
    import aiomax
    _original_handle = bot.handle_update

    async def antiflood_handle_update(update: dict):
        # Извлекаем user_id из апдейта
        user_id = None
        try:
            msg = update.get("message") or update.get("callback") or {}
            sender = msg.get("sender") or {}
            user_id = sender.get("user_id")
        except Exception:
            pass

        if user_id and is_rate_limited(user_id):
            return  # Молча отбрасываем апдейт

        # Структурированное логирование
        kind = "message" if "message" in update else "callback" if "callback" in update else "other"
        logger.info(
            "update received",
            extra={"event": "update", "kind": kind, "user_id": user_id},
        )

        # Глобальный обработчик ошибок: одно упавшее обновление не должно
        # ронять весь polling-цикл бота — логируем с трейсбеком и продолжаем.
        try:
            await _original_handle(update)
        except Exception:
            logger.error(
                "unhandled exception while processing update",
                extra={"event": "update_error", "kind": kind, "user_id": user_id},
                exc_info=True,
            )

    bot.handle_update = antiflood_handle_update
