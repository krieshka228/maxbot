"""
utils.py — Вспомогательные функции.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


_QTY_RE = re.compile(r"^\s*(\d+)\s*(?:шт\.?|штук[аи]?|ед\.?)?\s*$", re.IGNORECASE)


def parse_quantity(text: str) -> Optional[int]:
    """Возвращает целое число из строки, иначе None."""
    m = _QTY_RE.match(text.strip())
    if m:
        qty = int(m.group(1))
        return qty if qty > 0 else None
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
    """Форматирует заказ для уведомления администратора."""
    user = order.user
    user_info = f"@{user.username}" if user.username else user.full_name or f"ID {user.id}"
    lines = [f"📦 *Заказ #{order.id}* от {user_info}"]
    for item in order.items:
        name = item.product.name if item.product else f"Товар #{item.product_id}"
        lines.append(f"  • {name}: {item.quantity} шт. × {item.price_at_order:.0f} ₽")
    lines.append(f"💰 Итого: {order.total_amount:.0f} ₽")
    if order.delivery_address:
        lines.append(f"🚚 Адрес: {order.delivery_address}")
    return "\n".join(lines)


def build_post_text(product) -> str:
    """Собирает текст поста для канала на основе данных товара."""
    lines = [product.name]
    if product.article:
        lines.append(f"Артикул {product.article}")
    if product.stock is not None:
        lines.append(f"На складе: {product.stock}")
    lines.append("")  # пустая строка перед ценой
    if product.price:
        lines.append(f"Цена {product.price:.0f}")
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