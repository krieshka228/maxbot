"""
handlers/start.py — /start, on_bot_start, согласие на ПД, прямой заказ из ЛС.
"""

import logging
from datetime import datetime
from .catalog import delete_catalog_messages
import aiomax
from aiomax import fsm
from utils import parse_quantity, format_cart
from config import ADMIN_USER_ID
from db import get_session, get_or_create_user, get_or_create_draft, add_item_to_order
from keyboards import kb_consent, kb_main_menu, kb_cart_actions, kb_back_to_menu


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


async def _direct_order(message: aiomax.Message, cursor: fsm.FSMCursor, bot: aiomax.Bot):
    """Добавляет товар в корзину по ссылке/артикулу и количеству."""
    user_id = message.user_id
    text = message.body.text.strip() if message.body and message.body.text else ""
    if not text:
        return

    # Пытаемся разделить на две части: [ссылка/артикул] [количество]
    parts = text.split(maxsplit=1)
    post_id = None
    qty = None
    if len(parts) == 2:
        post_id = _parse_post_link(parts[0])
        qty = parse_quantity(parts[1])
    # Если одна часть, смотрим, может это только количество (тогда не заказ)
    if post_id is None or qty is None:
        # Не подходит под формат заказа — тихо выходим
        return

    async for session in get_session():
        user = await get_or_create_user(session, user_id)
        if not user.consented:
            await message.reply("❌ Сначала нужно дать согласие на обработку данных. Нажмите /start")
            return

        from sqlalchemy import select
        from db import Product
        product = (await session.execute(
            select(Product).where(Product.post_id == post_id, Product.is_active == True)
        )).scalar_one_or_none()

        if not product:
            await message.reply("⚠️ Товар с таким артикулом/постом не найден.")
            return

        order = await get_or_create_draft(session, user_id)
        from sqlalchemy.orm import selectinload
        from db import Order, OrderItem
        # Перечитываем заказ с товарами
        stmt = select(Order).where(Order.id == order.id).options(
            selectinload(Order.items).selectinload(OrderItem.product)
        )
        order = (await session.execute(stmt)).scalar_one()
        await add_item_to_order(session, order, product, qty)
        order = (await session.execute(stmt)).scalar_one()  # обновляем

        cart_text = format_cart(order)
        await message.reply(
            f"✅ **{product.name}** × {qty} шт. добавлен в корзину!\n\n{cart_text}",
            format="markdown",
            keyboard=kb_cart_actions(order.id)
        )


def register(bot: aiomax.Bot) -> None:

    @bot.on_command("products")
    async def list_products(ctx: aiomax.CommandContext, cursor: fsm.FSMCursor):
        user_id = ctx.sender.user_id
        # Лучше открыть только админу, но для теста можно всем
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

    # Обработчик прямых заказов (ЛС) — должен идти до всех состояний, но с проверкой, что не в FSM
    @bot.on_message(lambda msg: not getattr(msg.recipient, "chat_type", None) == "channel")
    async def direct_order_handler(message: aiomax.Message, cursor: fsm.FSMCursor):
        # Если пользователь уже в каком-то состоянии (например, меняет количество), не мешаем
        current_state = bot.storage.get_state(message.sender.user_id)
        if current_state is not None and current_state not in ("idle", None):
            return
        await _direct_order(message, cursor, bot)

    # ── Кнопка "Начать" в ЛС ────────────────────────────────────────────────
    @bot.on_bot_start()
    async def on_bot_start(payload: aiomax.BotStartPayload, cursor: fsm.FSMCursor):
        user_id = payload.user.user_id
        async for session in get_session():
            user = await get_or_create_user(
                session, user_id,
                full_name=payload.user.name,
                username=getattr(payload.user, "username", None),
            )
        if not user.consented:
            cursor.change_state("consent")
            await payload.send(
                "👋 Привет! Для работы с ботом нам нужно ваше согласие на обработку "
                "персональных данных (имя, телефон, адрес доставки).\n\n"
                "Данные используются исключительно для оформления и доставки заказов.",
                keyboard=kb_consent(),
            )
        else:
            cursor.clear()
            await payload.send(
                "👋 С возвращением! Выберите действие:",
                keyboard=kb_main_menu(is_admin=(user_id == ADMIN_USER_ID)),
            )

    # ── Команда /start ───────────────────────────────────────────────────────
    @bot.on_command("start")
    async def cmd_start(ctx: aiomax.CommandContext, cursor: fsm.FSMCursor):
        logger.info("Обработчик /start вызван")
        user_id = ctx.sender.user_id
        async for session in get_session():
            user = await get_or_create_user(
                session, user_id,
                full_name=ctx.sender.name,
                username=getattr(ctx.sender, "username", None),
            )
        if not user.consented:
            cursor.change_state("consent")
            logger.info("Попытка отправить главное меню")
            await ctx.reply(
                "👋 Привет! Для продолжения нужно ваше согласие на обработку "
                "персональных данных.",
                keyboard=kb_consent(),
            )
        else:
            logger.info("Попытка отправить главное меню")
            cursor.clear()
            await ctx.reply(
                "✅ Главное меню:",
                keyboard=kb_main_menu(is_admin=(user_id == ADMIN_USER_ID)),
            )

    # ── Согласие ─────────────────────────────────────────────────────────────
    @bot.on_button_callback("consent:yes")
    async def consent_yes(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        async for session in get_session():
            user = await get_or_create_user(session, user_id)
            user.consented = True
            user.consented_at = datetime.utcnow()
            await session.commit()
        cursor.clear()
        await cb.answer(
            text="✅ Спасибо! Теперь вы можете делать заказы.",
            keyboard=kb_main_menu(is_admin=(user_id == ADMIN_USER_ID)),
            format="markdown"
        )

    @bot.on_button_callback("consent:no")
    async def consent_no(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        await cb.answer(
            text="❌ Без согласия на обработку персональных данных работа с ботом невозможна.\n"
                 "Если передумаете — нажмите /start.",
            keyboard=kb_back_to_menu()
        )

    # ── Главное меню (кнопка «Назад») ────────────────────────────────────────

    @bot.on_button_callback("menu:main")
    async def back_to_menu(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        cursor.clear()
        # Очищаем каталог (если были открыты товары)
        await delete_catalog_messages(user_id, bot, also_delete_message_id=cb.message.id)

        # Всегда редактируем текущее сообщение, убирая фото и ставя меню
        await cb.answer(
            text="🏠 Главное меню:",
            keyboard=kb_main_menu(is_admin=(user_id == ADMIN_USER_ID)),
            attachments=[],  # <-- обязательно убираем вложения (фото)
            format="markdown"
        )
    @bot.on_command("myid")
    async def cmd_myid(ctx: aiomax.CommandContext, cursor: fsm.FSMCursor):
        await ctx.reply(f"Ваш user_id: {ctx.sender.user_id}")