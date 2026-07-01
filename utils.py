import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


_QTY_RE = re.compile(r"^\s*(\d+)\s*(?:шт\.?|штук[аи]?|ед\.?)?\s*$", re.IGNORECASE)


def parse_quantity(text: str) -> Optional[int]:
    """Возвращает целое число из строки (допускает суффикс «шт.»/«ед.»),
    иначе None. Верхняя граница — validators.MAX_QUANTITY (защита от
    переполнения/абьюза, см. validators.py)."""
    from  validators import MAX_QUANTITY
    m = _QTY_RE.match(text.strip())
    if m:
        qty = int(m.group(1))
        if 0 < qty <= MAX_QUANTITY:
            return qty
    return None


def parse_post_product(text: str) -> tuple[
    Optional[str], Optional[str], Optional[float], Optional[str], Optional[str], Optional[str], Optional[int]
]:
    """
    Возвращает (название, артикул, цена, level1_category, category, description, stock).
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return None, None, None, None, None, None, None

    name = lines[0]
    # Категория второго уровня – часть названия до первой запятой
    category = name.split(",")[0].strip() if "," in name else name.strip()

    article = None
    price = None
    level1_category = None
    stock = None

    # Ищем артикул и цену
    for idx, line in enumerate(lines):
        if line.lower().startswith("артикул"):
            parts = line.split(maxsplit=1)
            if len(parts) > 1:
                article = parts[1].strip()
        elif line.lower().startswith("цена"):
            parts = line.split(maxsplit=1)
            if len(parts) > 1:
                price_match = re.search(r"(\d+[\.,]?\d*)", parts[1])
                if price_match:
                    price = float(price_match.group(1).replace(",", "."))
            # Ищем level1_category: первая непустая строка после цены, не хештег
            for i in range(idx + 1, len(lines)):
                candidate = lines[i]
                if candidate.startswith("#"):
                    continue
                level1_category = candidate
                break
        elif "на складе:" in line.lower():
            try:
                stock = int(re.search(r"\d+", line).group())
            except:
                pass

    # Если level1_category не найдена, используем строку, следующую сразу за ценой (без учёта хештегов)
    if level1_category is None:
        for idx, line in enumerate(lines):
            if line.lower().startswith("цена"):
                for i in range(idx + 1, len(lines)):
                    if not lines[i].startswith("#"):
                        level1_category = lines[i]
                        break
                break

    description = text.strip()
    return name, article, price, level1_category, category, description, stock


def format_cart(order) -> str:
    """Форматирует корзину в читаемый вид."""
    if not order.items:
        return "🛒 Ваша корзина пуста."

    lines = ["🛒 **Ваша корзина:**\n"]
    for i, item in enumerate(order.items, 1):
        name = item.product.name if item.product else f"Товар #{item.product_id}"
        subtotal = item.quantity * item.price_at_order
        lines.append(f"{i}. {name} — {item.quantity} шт. × {item.price_at_order:.0f} ₽ = {subtotal:.0f} ₽")

    lines.append(f"\n💰 **Итого: {order.total_amount:.0f} ₽**")
    return "\n".join(lines)


async def check_payment_qr() -> bool:
    from db import get_session, get_bot_setting
    async for session in get_session():
        token = await get_bot_setting(session, "payment_qr_token")
        return bool(token)


def format_order_for_admin(order) -> str:
    """Форматирует заказ для уведомления администратора.

    Включает ФИО, телефон и адрес клиента (см. promt.md, раздел «Корзина
    и оформление заказа»: «админ получает уведомление с ФИО, телефоном,
    адресом»)."""
    user = order.user
    user_info = f"@{user.username}" if user and user.username else f"ID {order.user_id}"
    lines = [f"📦 *Заказ #{order.id}* от {user_info}"]
    for item in order.items:
        name = item.product.name if item.product else f"Товар #{item.product_id}"
        lines.append(f"  • {name}: {item.quantity} шт. × {item.price_at_order:.0f} ₽")
    lines.append(f"💰 Итого: {order.total_amount:.0f} ₽")
    full_name = order.full_name or (user.full_name if user else None)
    if full_name:
        lines.append(f"👤 ФИО: {full_name}")
    phone = order.contact_phone or (user.phone if user else None)
    if phone:
        lines.append(f"📱 Телефон: {phone}")
    if order.delivery_method:
        lines.append(f"🚚 Способ доставки: {order.delivery_method}")
    if order.delivery_address:
        lines.append(f"📍 Адрес: {order.delivery_address}")
    return "\n".join(lines)


# ----- ФУНКЦИИ ДЛЯ ФОРМИРОВАНИЯ ТЕКСТА ТОВАРОВ -----

def build_catalog_card_text(product) -> str:
    """
    Текст карточки товара для КАТАЛОГА (стиль Telegram-бота).
    Выводится название, артикул, остаток, цена.
    """
    lines = [product.name]
    if product.article:
        lines.append(f"▫️ Артикул: {product.article}")
    if product.stock is not None:
        lines.append(f"▫️ На складе: {product.stock} шт.")
    lines.append(f"▫️ Цена: {product.price:.0f} ₽")
    return "\n".join(lines)


def build_post_text(product) -> str:
    """
    Текст ПОСТА в канал Max (формат без остатка, с категорией).
    Используется в channel_publisher.py.
    """
    lines = [product.name]
    if product.article:
        lines.append(f"Артикул {product.article}")
    lines.append("")  # пустая строка
    if product.price:
        lines.append(f"Цена {product.price:.0f}")
    if product.category:
        lines.append("")  # пустая строка перед категорией
        lines.append(product.category)
    return "\n".join(lines)


async def update_channel_post(bot, product):
    """Обновляет сообщение в канале."""
    if not product.post_id:
        return
    new_text = build_post_text(product)
    try:
        await bot.edit_message(
            message_id=product.post_id,
            text=new_text,
            format="markdown"
        )
    except Exception as e:
        logger.warning(f"Не удалось обновить пост {product.post_id}: {e}")


async def _download_telegram_file(bot, file_id: str, tg_token: str) -> "bytes | None":
    """Скачивает файл из Telegram Bot API по file_id. Возвращает байты
    или None при ошибке (используется только internal-но, см. get_max_attachments)."""
    try:
        resp = await bot.session.get(
            f"https://api.telegram.org/bot{tg_token}/getFile",
            params={"file_id": file_id},
        )
        data = await resp.json()
        file_path = (data or {}).get("result", {}).get("file_path")
        if not file_path:
            logger.warning(f"Telegram getFile не вернул file_path для file_id={file_id}")
            return None
        file_resp = await bot.session.get(
            f"https://api.telegram.org/file/bot{tg_token}/{file_path}"
        )
        return await file_resp.read()
    except Exception as e:
        logger.warning(f"Ошибка скачивания файла {file_id} из Telegram: {e}")
        return None


async def get_max_attachments(bot, session, product) -> list:
    """Возвращает список вложений (фото/видео) для товара."""
    import aiomax
    attachments = []
    if product.max_photo_ids:
        for token in product.max_photo_ids.split(","):
            token = token.strip()
            if token:
                attachments.append(aiomax.PhotoAttachment(token=token))
    if product.max_video_ids:
        for token in product.max_video_ids.split(","):
            token = token.strip()
            if token:
                attachments.append(aiomax.VideoAttachment(token=token))
    return attachments
import aiohttp
from config import settings

MAX_API_BASE = "https://platform-api.max.ru"

async def send_video_direct(user_id: int, video_token: str, text: str = "📹") -> bool:
    """Отправляет видео напрямую через Max API, минуя aiomax."""
    headers = {"Authorization": settings.max_bot_token}
    payload = {
        "text": text,
        "attachments": [{
            "type": "video",
            "payload": {"token": video_token}
        }]
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{MAX_API_BASE}/messages?user_id={user_id}",
            headers=headers,
            json=payload
        ) as resp:
            if resp.status == 200:
                logger.info(f"Видео отправлено пользователю {user_id}")
                return True
            else:
                logger.error(f"Ошибка отправки видео: {resp.status} {await resp.text()}")
                return False