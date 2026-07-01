"""
handlers/start.py — /start, on_bot_start, согласие на ПД, прямой заказ из ЛС.
"""

import logging
from datetime import datetime
import aiomax
from aiomax import fsm
from utils import parse_quantity, format_cart, check_payment_qr
from config import ADMIN_USER_ID
from db import get_session, get_or_create_user, get_or_create_draft, add_item_to_order, get_bot_setting
from keyboards import kb_main_menu, kb_cart_actions, kb_back_to_menu, kb_unavailable

logger = logging.getLogger(__name__)


def _parse_post_link(text: str) -> int | None:
    """Извлекает post_id из текста: просто число или из URL /post/123."""
    import re
    text = text.strip()
    if text.isdigit():
        return int(text)
    m = re.search(r'/post/(\d+)', text)
    if m:
        return int(m.group(1))
    return None


def register(bot: aiomax.Bot) -> None:

    @bot.on_command("products")
    async def list_products(ctx: aiomax.CommandContext, cursor: fsm.FSMCursor):
        user_id = ctx.sender.user_id
        async for session in get_session():
            from sqlalchemy import select
            from db import Product
            products = (await session.execute(
                select(Product).where(Product.is_active == True)
            )).scalars().all()
        if not products:
            await ctx.reply("Товаров нет.")
            return
        lines = ["**Активные товары:**"]
        for p in products:
            lines.append(f"• {p.name} — post_id={p.post_id}")
        await ctx.reply("\n".join(lines), format="markdown")

    @bot.on_command("start")
    async def cmd_start(ctx: aiomax.CommandContext, cursor: fsm.FSMCursor):
        logger.info("Обработчик /start вызван")
        user_id = ctx.sender.user_id
        has_qr = await check_payment_qr()
        async for session in get_session():
            user = await get_or_create_user(
                session, user_id,
                full_name=ctx.sender.name,
                username=getattr(ctx.sender, "username", None),
                platform="MAX"
            )
        if user_id == ADMIN_USER_ID:
            cursor.clear()
            await ctx.reply(
                "✅ Главное меню:",
                keyboard=kb_main_menu(is_admin=True, has_qr=True),
            )
            return

        if not has_qr:
            cursor.clear()
            await ctx.reply(
                "⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
            )
            return

        cursor.clear()
        await ctx.reply(
            "✅ Главное меню:",
            keyboard=kb_main_menu(is_admin=False, has_qr=True),
        )

    @bot.on_bot_start()
    async def on_bot_start(payload: aiomax.BotStartPayload, cursor: fsm.FSMCursor):
        user_id = payload.user.user_id
        has_qr = await check_payment_qr()
        async for session in get_session():
            user = await get_or_create_user(
                session, user_id,
                full_name=payload.user.name,
                username=getattr(payload.user, "username", None),
                platform="MAX"
            )
        if user_id == ADMIN_USER_ID:
            cursor.clear()
            await payload.send(
                "👋 С возвращением! Выберите действие:",
                keyboard=kb_main_menu(is_admin=True, has_qr=True),
            )
            return

        if not has_qr:
            cursor.clear()
            await payload.send(
                "⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
            )
            return

        cursor.clear()
        await payload.send(
            "👋 С возвращением! Выберите действие:",
            keyboard=kb_main_menu(is_admin=False, has_qr=True),
        )

    @bot.on_button_callback("menu:main")
    async def back_to_menu(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        is_admin = (user_id == ADMIN_USER_ID)

        logger.info(f"BACK_TO_MENU user_id={user_id}, ADMIN_USER_ID={ADMIN_USER_ID}")

        from handlers.catalog import delete_catalog_messages, _category_messages, safe_edit_or_send

        cursor.clear()

        # 1. Удаляем карточки товаров и навигацию
        await delete_catalog_messages(user_id, bot, keep_current=True)

        # 2. Проверяем доступность
        if is_admin:
            has_qr = True
        else:
            has_qr = await check_payment_qr()

        if not has_qr and not is_admin:
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return

        # 3. Редактируем или отправляем новое сообщение
        kb = kb_main_menu(is_admin=is_admin, has_qr=has_qr)
        msg_id = await safe_edit_or_send(
            cb,
            "🏠 **Главное меню**\n\nВыберите действие:",
            kb,
            format="markdown"
        )
        _category_messages[user_id] = msg_id

    @bot.on_command("myid")
    async def cmd_myid(ctx: aiomax.CommandContext, cursor: fsm.FSMCursor):
        await ctx.reply(f"Ваш user_id: {ctx.sender.user_id}")