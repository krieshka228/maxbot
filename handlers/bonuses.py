"""
handlers/bonuses.py — Бонусная система Max‑бота:
  - просмотр баланса
  - активация промокода (одноразовая, с проверкой срока и лимита)
"""

import logging
from datetime import datetime, timezone

import aiomax
from aiomax import fsm, filters
from aiomax.buttons import KeyboardBuilder, CallbackButton
from sqlalchemy import select

from config import ADMIN_USER_ID
from db import get_session, User, PromoCode, PromoUsage
from keyboards import kb_main_menu, kb_back_to_menu

logger = logging.getLogger(__name__)


def register(bot: aiomax.Bot) -> None:
    # ── Меню бонусов (просмотр баланса) ────────────────────────────────
    @bot.on_button_callback("bonus:menu")
    async def bonus_menu(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        is_admin = (user_id == ADMIN_USER_ID)

        async for session in get_session():
            user = await session.get(User, user_id)
            bonus = user.bonus_balance if user else 0

        text = (
            f"💎 **Ваши бонусы**\n\n"
            f"💰 Баланс: {bonus} бонусов.\n"
            "Вы можете оплатить до 20% стоимости заказа бонусами."
        )
        kb = KeyboardBuilder()
        kb.row(CallbackButton("🎁 Ввести промокод", "promo:enter"))
        kb.row(CallbackButton("🏠 Главное меню", "menu:main"))
        await cb.answer(text=text, keyboard=kb, format="markdown")

    # ── Начало ввода промокода ─────────────────────────────────────────
    @bot.on_button_callback("promo:enter")
    async def promo_enter_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        cursor.change_state("client_promo_enter")
        await cb.answer(notification=" ")
        await cb.send("🎁 Введите промокод:", keyboard=kb_back_to_menu())

    # ── Обработка введённого промокода ─────────────────────────────────
    @bot.on_message(filters.state("client_promo_enter"))
    async def process_client_promo(message: aiomax.Message, cursor: fsm.FSMCursor):
        code = message.body.text.strip().upper() if message.body and message.body.text else ""
        user_id = message.sender.user_id
        is_admin = (user_id == ADMIN_USER_ID)

        if not code:
            await message.reply("❌ Введите промокод.", keyboard=kb_main_menu(is_admin=is_admin))
            cursor.clear()
            return

        async for session in get_session():
            promo = (await session.execute(
                select(PromoCode).where(PromoCode.code == code)
            )).scalar_one_or_none()

            if not promo or not promo.is_active:
                await message.reply("❌ Промокод не найден или неактивен.",
                                    keyboard=kb_main_menu(is_admin=is_admin))
                cursor.clear()
                return

            # Проверка срока действия
            if promo.expires_at:
                expires_dt = promo.expires_at.replace(tzinfo=timezone.utc) if promo.expires_at.tzinfo is None else promo.expires_at
                if datetime.now(timezone.utc) > expires_dt:
                    await message.reply("❌ Срок действия промокода истёк.",
                                        keyboard=kb_main_menu(is_admin=is_admin))
                    cursor.clear()
                    return

            # Лимит использований
            if promo.max_uses is not None and promo.used_count >= promo.max_uses:
                await message.reply("❌ Промокод больше не действует (достигнут лимит).",
                                    keyboard=kb_main_menu(is_admin=is_admin))
                cursor.clear()
                return

            # Проверка повторного использования
            already = (await session.execute(
                select(PromoUsage).where(
                    PromoUsage.promo_code == code,
                    PromoUsage.user_id == user_id
                )
            )).scalar_one_or_none()
            if already:
                await message.reply("❌ Вы уже использовали этот промокод.",
                                    keyboard=kb_main_menu(is_admin=is_admin))
                cursor.clear()
                return

            # Начисляем бонусы
            user = await session.get(User, user_id)
            if not user:
                user = User(id=user_id, bonus_balance=0)
                session.add(user)
            user.bonus_balance = (user.bonus_balance or 0) + promo.bonus_amount
            promo.used_count += 1
            session.add(PromoUsage(promo_code=code, user_id=user_id))
            await session.commit()
            new_balance = user.bonus_balance

        cursor.clear()
        await message.reply(
            f"✅ Промокод активирован! Вам начислено {promo.bonus_amount} бонусов.\n"
            f"💰 Ваш баланс: {new_balance} бонусов.",
            keyboard=kb_main_menu(is_admin=is_admin)
        )