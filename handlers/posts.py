"""
handlers/posts.py — синхронизация постов канала с базой данных.
"""

import logging
import io
import aiohttp
import aiomax
from aiomax import fsm

from config import CHANNEL_ID
from db import get_session, upsert_product, Product
from utils import parse_post_product

logger = logging.getLogger(__name__)


def _is_channel_post(msg: aiomax.Message) -> bool:
    try:
        return msg.recipient.chat_id == CHANNEL_ID
    except AttributeError:
        return False


async def _sync_post(bot: aiomax.Bot, message: aiomax.Message) -> None:
    if not message.body or not message.body.text:
        return

    text = message.body.text
    name, article, price, category, description, stock = parse_post_product(text)
    if name is None:
        return

    post_id = message.id

    # Проверяем, не написали ли "Продано"
    sold_keywords = ["продано", "нет в наличии", "sold", "закончился", "продана", "продан"]
    text_lower = text.lower()
    in_stock = not any(word in text_lower for word in sold_keywords)

    # Загружаем фото и видео
    photo_tokens = []
    video_tokens = []

    if hasattr(message.body, "attachments") and message.body.attachments:
        for att in message.body.attachments:
            if att.type == "image" and hasattr(att, "url") and att.url:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(att.url) as resp:
                            if resp.status == 200:
                                img_data = io.BytesIO(await resp.read())
                                attachment = await bot.upload_image(img_data)
                                photo_tokens.append(attachment.token)
                except Exception as e:
                    logger.warning(f"Ошибка загрузки фото: {e}")

            elif att.type == "video" and hasattr(att, "url") and att.url:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(att.url) as resp:
                            if resp.status == 200:
                                video_data = io.BytesIO(await resp.read())
                                attachment = await bot.upload_video(video_data)
                                video_tokens.append(attachment.token)
                except Exception as e:
                    logger.warning(f"Ошибка загрузки видео: {e}")

    photo_ids = ",".join(photo_tokens) if photo_tokens else None
    video_ids = ",".join(video_tokens) if video_tokens else None

    async for session in get_session():
        await upsert_product(
            session, post_id, name, price, photo_ids, video_ids,
            article, category, description, stock=stock, in_stock=in_stock
        )
        logger.info(
            f"Товар обновлён: id={post_id} '{name}' {price}₽ "
            f"арт={article} кат={category} в_наличии={in_stock} "
            f"фото={len(photo_tokens)} видео={len(video_tokens)}"
        )


def register(bot: aiomax.Bot) -> None:

    @bot.on_message(lambda msg: _is_channel_post(msg), detect_commands=True)
    async def new_post(message: aiomax.Message, cursor: fsm.FSMCursor):
        await _sync_post(bot, message)

    @bot.on_message_edit(lambda msg: _is_channel_post(msg))
    async def edited_post(before: aiomax.Message, after: aiomax.Message, cursor: fsm.FSMCursor):
        await _sync_post(bot, after)

    # ── Обработка удаления поста – теперь товар удаляется полностью ──────
    @bot.on_message_delete(lambda payload: payload.chat_id == CHANNEL_ID)
    async def on_post_deleted(payload: aiomax.MessageDeletePayload, cursor: fsm.FSMCursor):
        message_id = payload.message_id
        if not message_id:
            return
        async for session in get_session():
            from sqlalchemy import select, delete
            product = (await session.execute(
                select(Product).where(Product.post_id == message_id)
            )).scalar_one_or_none()
            if product:
                await session.delete(product)
                await session.commit()
                logger.info(f"Товар удалён из базы: {product.name}")