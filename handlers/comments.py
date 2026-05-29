"""
handlers/comments.py — комментарии под постами канала.

Пользователь пишет количество под постом → бот:
  1. Парсит количество.
  2. Находит товар по post_id (id родительского поста).
  3. Добавляет в корзину.
  4. Удаляет комментарий.
  5. Уведомляет пользователя в ЛС.
"""

import logging

import aiomax
from aiomax import fsm

from config import CHANNEL_ID
from db import get_session, get_or_create_user, get_or_create_draft, add_item_to_order
from keyboards import kb_cart_actions
from utils import parse_quantity, format_cart

logger = logging.getLogger(__name__)


def _is_channel_message(message: aiomax.Message) -> bool:
    """Сообщение пришло из нашего канала."""
    try:
        return message.recipient.chat_id == CHANNEL_ID
    except AttributeError:
        return False


def register(bot: aiomax.Bot) -> None:
    @bot.on_message(detect_commands=True)
    async def debug_log_all(message: aiomax.Message, cursor: fsm.FSMCursor):
        chat_id = getattr(message.recipient, 'chat_id', 'unknown')
        text_preview = (message.body.text[:50] + '...') if (message.body and message.body.text) else ''
        logger.info(f"DEBUG ALL: chat_id={chat_id}, text='{text_preview}'")

    @bot.on_message( detect_commands=True)
    async def handle_channel_comment(message: aiomax.Message, cursor: fsm.FSMCursor):
        """Обрабатываем все сообщения в канале."""
        # Игнорируем сообщения, у которых нет реального отправителя
        # (например, собственные посты канала, где мы подставили sender с user_id=0)
        try:
            chat_id = message.recipient.chat_id
        except AttributeError:
            chat_id = None
        logger.info(f"Получено сообщение: chat_id={chat_id}, text='{message.body.text if message.body else ''}'")
        if chat_id != CHANNEL_ID:
            return

        if message.sender is None or message.sender.user_id == 0:
            return

        text = message.body.text.strip() if (message.body and message.body.text) else ""
        if not text:
            return

        qty = parse_quantity(text)
        if qty is None:
            logger.debug(f"Комментарий не распознан как количество: '{text}'")
            return

        user_id = message.sender.user_id

        # Определяем post_id — из поля link_preview или parent message id
        post_id = None
        try:
            # Вариант 1: прямое поле parent с id
            if hasattr(message, "parent") and message.parent:
                post_id = message.parent.id
            # Вариант 2: reply_to (если комментарий является ответом)
            elif hasattr(message, "reply_to") and message.reply_to:
                post_id = message.reply_to.message_id
            # Вариант 3: topic_id (для сообщений в обсуждениях)
            elif hasattr(message, "topic_id") and message.topic_id:
                post_id = message.topic_id
        except Exception:
            pass

        if post_id is None:
            # На всякий случай попробуем старый способ (link_preview)
            try:
                if hasattr(message, "link_preview") and message.link_preview:
                    post_id = getattr(message.link_preview, "message_id", None)
            except Exception:
                pass

        if post_id is None:
            logger.warning(
                f"Не удалось определить post_id для комментария "
                f"message.id={getattr(message, 'id', '?')}"
            )

        async for session in get_session():
            user = await get_or_create_user(
                session, user_id,
                full_name=message.sender.name,
                username=getattr(message.sender, "username", None),
            )

            if not user.consented:
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=(
                            "👋 Вы пытаетесь сделать заказ, но сначала нужно согласие на "
                            "обработку персональных данных. Нажмите /start в боте."
                        ),
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить ЛС {user_id}: {e}")
                return

            # Ищем товар по post_id
            from sqlalchemy import select
            from db import Product
            product = None
            if post_id:
                stmt = select(Product).where(
                    Product.post_id == post_id, Product.is_active == True
                )
                result = await session.execute(stmt)
                product = result.scalar_one_or_none()

            if product is None:
                logger.warning(f"Товар с post_id={post_id} не найден в БД.")
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=(
                            "⚠️ Не удалось найти товар из этого поста.\n"
                            "Возможно, пост ещё не обработан ботом. Попробуйте чуть позже."
                        ),
                    )
                except Exception:
                    pass
                return

            # Добавляем в корзину
            from sqlalchemy.orm import selectinload
            from db import Order, OrderItem
            order = await get_or_create_draft(session, user_id)
            stmt2 = (
                select(Order)
                .where(Order.id == order.id)
                .options(selectinload(Order.items).selectinload(OrderItem.product))
            )
            res2 = await session.execute(stmt2)
            order = res2.scalar_one()

            await add_item_to_order(session, order, product, qty)

            # Перечитываем заказ с обновлёнными данными
            res3 = await session.execute(stmt2)
            order = res3.scalar_one()

            cart_text = format_cart(order)
            confirm_text = (
                f"✅ **{product.name}** × {qty} шт. добавлен в корзину!\n\n"
                f"{cart_text}"
            )

        try:
            await bot.send_message(
                chat_id=user_id,
                text=confirm_text,
                format="markdown",
                keyboard=kb_cart_actions(order.id),
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить ЛС {user_id}: {e}")

        # Удаляем комментарий (бот должен быть администратором канала)
        try:
            mid = message.id
            await bot.delete_message(chat_id=CHANNEL_ID, message_id=mid)
        except Exception as e:
            logger.debug(f"Не удалось удалить комментарий: {e}")