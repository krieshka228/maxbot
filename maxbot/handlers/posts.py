"""
handlers/posts.py — Max-бот НЕ парсит посты для добавления товаров.
Товары добавляются только через Telegram-бота из канала Telegram.
Max-бот только читает товары из общей БД.

Этот модуль оставлен для совместимости и будущего расширения,
но не регистрирует обработчики синхронизации.
"""

import logging
import aiomax

logger = logging.getLogger(__name__)


def register(bot: aiomax.Bot) -> None:
    """Max-бот не обрабатывает посты канала для синхронизации товаров.
    Товары берутся из общей БД, куда их добавляет Telegram-бот."""
    @bot.on_message()
    async def debug_all_messages(message: aiomax.Message, cursor: fsm.FSMCursor):
        logger.info(
            f"DEBUG MESSAGE: chat_id={message.recipient.chat_id if hasattr(message.recipient, 'chat_id') else '?'}, "
            f"sender_id={message.sender.user_id}, text={message.body.text[:100] if message.body and message.body.text else ''}"
        )
    logger.info(
        "posts.register: синхронизация через канал отключена — "
        "товары читаются из общей БД (добавляются через Telegram-бота)"
    )
