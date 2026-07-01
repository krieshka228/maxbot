"""
channel_publisher.py — публикация товаров из общей БД в канал Max.

Max-бот не синхронизирует каталог из канала Telegram (см. promt.md) —
товары туда кладёт Telegram-бот. Эта функция — обратное направление:
берёт товары из общей БД, у которых ещё нет Product.max_post_id, и
публикует их постом в канал Max (CHANNEL_ID). Используется как из
ручной кнопки в админ-меню (handlers/admin.py), так и из фоновой задачи
auto_publish_loop ниже (запускается из main.py, если нужна автопубликация
без участия администратора).
"""

import asyncio
import logging

import aiomax

from  config import CHANNEL_ID
from  db import get_session, get_products_without_max_post, mark_product_published,get_bot_setting, Product
from  utils import build_post_text, get_max_attachments
from sqlalchemy import select
logger = logging.getLogger(__name__)

# Как часто фоновая задача проверяет БД на новые товары для автопубликации.
AUTO_PUBLISH_INTERVAL = 300  # 5 минут


async def publish_pending_products(bot: aiomax.Bot) -> tuple[int, int, bool]:
    """Публикует все ещё не опубликованные активные товары в канал Max.

    Возвращает (published, failed, had_products).
    """
    logger.info("Вызвана publish_pending_products")
    if not CHANNEL_ID:
        logger.warning("CHANNEL_ID не задан — публикация в канал Max пропущена.")
        return 0, 0, False

    published, failed = 0, 0
    async for session in get_session():
        products = await get_products_without_max_post(session)
        if not products:
            return 0, 0, False

        for product in products:
            try:
                text = build_post_text(product)
                # Хештег категории больше не добавляем, так как категория уже есть в тексте
                attachments = await get_max_attachments(bot, session, product)
                msg = await bot.send_message(
                    text=text,
                    chat_id=CHANNEL_ID,
                    format="markdown",
                    attachments=attachments or None,
                )
                await mark_product_published(session, product, str(msg.id))
                published += 1
                logger.info(f"Товар #{product.id} опубликован в канал Max (post {msg.id}).")
            except Exception as e:
                failed += 1
                logger.error(
                    f"Не удалось опубликовать товар #{product.id} в канал Max: {e}",
                    exc_info=True,
                )
        return published, failed, True
    return 0, 0, False


async def auto_publish_loop(bot: aiomax.Bot):
    logger.info("Задача автопубликации в канал Max запущена.")
    while True:
        try:
            # Проверяем глобальный флаг автопубликации
            async for session in get_session():
                enabled = await get_bot_setting(session, "auto_publish_enabled")
                break

            if enabled != "true":
                await asyncio.sleep(10)
                continue

            # Получаем список товаров для публикации
            async for session in get_session():
                products = (await session.execute(
                    select(Product).where(
                        Product.is_active == True,
                        Product.max_post_id == None
                    )
                )).scalars().all()

                if not products:
                    break

                logger.info(f"Найдено {len(products)} товаров для автопубликации")

                for product in products:
                    # Перед каждой публикацией проверяем флаг
                    enabled_now = await get_bot_setting(session, "auto_publish_enabled")
                    if enabled_now != "true":
                        logger.info("Автопубликация выключена пользователем")
                        break

                    # Пауза 60 секунд перед каждым постом
                    logger.info("Пауза 60 секунд перед публикацией следующего товара")
                    await asyncio.sleep(60)

                    post_id = await publish_product_to_max(bot, product, CHANNEL_ID)
                    if post_id:
                        await mark_product_published(session, product, str(post_id))
                        logger.info(f"Товар #{product.id} опубликован (post {post_id})")
                    else:
                        logger.warning(f"Товар #{product.id} не опубликован")

        except Exception as e:
            logger.error(f"Ошибка в автопубликации: {e}", exc_info=True)
            await asyncio.sleep(10)
async def publish_product_to_max(bot: aiomax.Bot, product: Product, channel_id: int) -> str | None:
    """Публикует один товар в канал Max, возвращает post_id или None."""
    text = build_post_text(product)

    attachments = []
    if product.max_photo_ids:
        for token in product.max_photo_ids.split(","):
            attachments.append(aiomax.PhotoAttachment(token=token))
    if product.max_video_ids:
        for token in product.max_video_ids.split(","):
            attachments.append(aiomax.VideoAttachment(token=token))

    try:
        msg = await bot.send_message(
            chat_id=channel_id,
            text=text,
            attachments=attachments,
            format="markdown"  # или "html", но в тексте нет разметки
        )
        return msg.id
    except Exception as e:
        logger.error(f"Ошибка публикации товара #{product.id}: {e}")
        return None