import logging

import aiomax
from aiomax import fsm, filters
from aiomax.buttons import KeyboardBuilder, CallbackButton
from sqlalchemy import text, select

from config import PAYMENT_DETAILS
from db import (
    get_session,
    get_or_create_user,
    get_draft_order,
    remove_item_from_order,
    recalculate_total,
    OrderStatus,
    Product,
    User,
    get_order_with_items
)
from cache import invalidate_catalog_cache
from keyboards import (
    kb_cart_actions,
    kb_cart_items_remove,
    kb_back_to_menu,
    kb_unavailable,
)
from config import ADMIN_USER_ID
from utils import format_cart, check_payment_qr
from db import get_bot_setting

logger = logging.getLogger(__name__)


def register(bot: aiomax.Bot) -> None:
    @bot.on_message(filters.state("order_bonus_input"))
    async def handle_order_bonus_input(message: aiomax.Message, cursor: fsm.FSMCursor):
        data = cursor.get_data()
        order_id = data["order_id"]
        bonus_balance = data["bonus_balance"]
        order_total = data["order_total"]
        user_id = message.sender.user_id

        try:
            bonus_amount = int(message.body.text.strip())
            if bonus_amount < 0:
                raise ValueError
        except ValueError:
            await message.reply("❌ Введите целое неотрицательное число.", keyboard=kb_back_to_menu())
            return

        max_bonus = min(bonus_balance, int(order_total * 0.2))
        if bonus_amount > max_bonus:
            await message.reply(
                f"❌ Вы можете списать максимум {max_bonus} бонусов.\nВведите сумму до {max_bonus}:",
                keyboard=kb_back_to_menu()
            )
            return

        cursor.clear()
        await _finalize_order(message, order_id, user_id, bonus_amount)
    # ── Просмотр корзины ──────────────────────────────────────────────────────
    @bot.on_button_callback("cart:view")
    async def view_cart(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id

        if user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return

        await cb.answer(notification=" ")

        async for session in get_session():
            order = await get_draft_order(session, user_id)

        if order is None or not order.items:
            # Редактируем текущее сообщение, а не создаём новое
            await bot.edit_message(
                message_id=cb.message.id,
                text="🛒 Ваша корзина пуста.\n\nПерейдите в каталог и добавьте товары.",
                keyboard=kb_back_to_menu(),
            )
            return

        await bot.edit_message(
            message_id=cb.message.id,
            text=format_cart(order),
            format="markdown",
            keyboard=kb_cart_actions(order.id, has_items=True),
        )


    # ── Удалить позицию (выбор) ───────────────────────────────────────────────
    @bot.on_button_callback(lambda cb: cb.payload.startswith("cart:remove:"))
    async def cart_remove_choose(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        await cb.answer(notification=" ")
        async for session in get_session():
            order = await get_draft_order(session, user_id)
        if not order or not order.items:
            await bot.edit_message(
                message_id=cb.message.id,
                text="🛒 Корзина пуста.",
                keyboard=kb_back_to_menu()
            )
            return
        await bot.edit_message(
            message_id=cb.message.id,
            text="Выберите позицию для удаления:",
            keyboard=kb_cart_items_remove(order)
        )

    @bot.on_button_callback(lambda cb: cb.payload.startswith("cart:del_item:"))
    async def cart_delete_item(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        item_id = int(cb.payload.split(":")[-1])
        user_id = cb.user.user_id
        await cb.answer(notification=" ")
        async for session in get_session():
            order = await get_draft_order(session, user_id)
            if not order:
                await bot.edit_message(
                    message_id=cb.message.id,
                    text="🛒 Корзина пуста.",
                    keyboard=kb_back_to_menu()
                )
                return

            removed = await remove_item_from_order(session, order, item_id)
            if removed:
                await session.refresh(order)
                if order.items:
                    await bot.edit_message(
                        message_id=cb.message.id,
                        text="✅ Удалено.\n\n" + format_cart(order),
                        keyboard=kb_cart_actions(order.id),
                        format="markdown"
                    )
                else:
                    await bot.edit_message(
                        message_id=cb.message.id,
                        text="✅ Удалено. Корзина пуста.",
                        keyboard=kb_back_to_menu(),
                        format="markdown"
                    )
            else:
                await bot.edit_message(
                    message_id=cb.message.id,
                    text="❌ Позиция не найдена.",
                    keyboard=kb_back_to_menu()
                )

    # ── Изменить количество ───────────────────────────────────────────────────
    @bot.on_button_callback(lambda cb: cb.payload.startswith("cart:edit:"))
    async def cart_edit_choose(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        async for session in get_session():
            order = await get_draft_order(session, user_id)
        if not order or not order.items:
            await cb.answer(notification="Корзина пуста.")
            return

        kb = KeyboardBuilder()
        for item in order.items:
            name = item.product.name if item.product else f"Товар #{item.product_id}"
            kb.add(CallbackButton(f"{name} (x{item.quantity})", f"cart:change_qty:{item.id}"))
            kb.row()
        kb.add(CallbackButton("↩️ Назад", "cart:view"))
        await bot.edit_message(
            message_id=cb.message.id,
            text="Выберите позицию для изменения:",
            keyboard=kb,
            format="markdown"
        )

    @bot.on_button_callback(lambda cb: cb.payload.startswith("cart:change_qty:"))
    async def cart_change_qty_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        item_id = int(cb.payload.split(":")[-1])
        user_id = cb.user.user_id
        cursor.change_state("cart_change_qty")
        cursor.change_data({"item_id": item_id})

        async for session in get_session():
            order = await get_draft_order(session, user_id)
            if not order:
                await cb.answer(notification="Корзина пуста.")
                return
            item = next((i for i in order.items if i.id == item_id), None)
            if not item:
                await cb.answer(notification="Позиция не найдена.")
                return
            current_qty = item.quantity

        kb = KeyboardBuilder()
        kb.row(
            CallbackButton("-5", f"cart:delta:{item_id}:-5"),
            CallbackButton("-1", f"cart:delta:{item_id}:-1"),
            CallbackButton("+1", f"cart:delta:{item_id}:+1"),
            CallbackButton("+5", f"cart:delta:{item_id}:+5"),
        )
        kb.row(CallbackButton("🔢 Ввести число", f"cart:input:{item_id}"))
        kb.row(CallbackButton("↩️ Назад", "cart:view"))
        await bot.edit_message(
            message_id=cb.message.id,
            text=f"Количество: **{current_qty}**\nВыберите действие:",
            keyboard=kb,
            format="markdown"
        )

    # Обработчик кнопок +/- N
    @bot.on_button_callback(lambda cb: cb.payload.startswith("cart:delta:"))
    async def cart_delta(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        _, _, item_id, delta = cb.payload.split(":")
        item_id = int(item_id)
        delta = int(delta)
        user_id = cb.user.user_id

        async for session in get_session():
            order = await get_draft_order(session, user_id)
            if not order:
                await cb.answer(notification="Корзина пуста.")
                return
            item = next((i for i in order.items if i.id == item_id), None)
            if not item:
                await cb.answer(notification="Позиция не найдена.")
                return

            new_qty = item.quantity + delta
            if new_qty <= 0:
                order.items.remove(item)
                await session.delete(item)
            else:
                item.quantity = new_qty
            await recalculate_total(session, order)
            await session.commit()

        async for session in get_session():
            order = await get_draft_order(session, user_id)
        if not order or not order.items:
            await bot.edit_message(
                message_id=cb.message.id,
                text="🛒 Корзина пуста.",
                keyboard=kb_back_to_menu(),
                format="markdown"
            )
            cursor.clear()
            return

        await bot.edit_message(
            message_id=cb.message.id,
            text=format_cart(order),
            keyboard=kb_cart_actions(order.id),
            format="markdown"
        )
        cursor.clear()

    # Обработчик кнопки «Ввести число» – переводит в FSM для ввода
    @bot.on_button_callback(lambda cb: cb.payload.startswith("cart:input:"))
    async def cart_input_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        item_id = int(cb.payload.split(":")[-1])
        user_id = cb.user.user_id
        cursor.change_state("cart_change_qty")
        cursor.change_data({"item_id": item_id})
        await cb.answer(notification=" ")
        await cb.send("✏️ Введите новое количество (целое число):", keyboard=kb_back_to_menu())

    # Обработчик ввода числа
    @bot.on_message(filters.state("cart_change_qty"))
    async def handle_cart_qty_input(message: aiomax.Message, cursor: fsm.FSMCursor):
        data = cursor.get_data() or {}
        item_id = data.get("item_id")
        if not item_id:
            await message.reply("❌ Ошибка. Попробуйте снова.")
            cursor.clear()
            return

        try:
            new_qty = int(message.body.text.strip())
            if new_qty <= 0:
                raise ValueError
        except ValueError:
            await message.reply("❌ Введите целое положительное число.", keyboard=kb_back_to_menu())
            return

        user_id = message.sender.user_id
        async for session in get_session():
            order = await get_draft_order(session, user_id)
            if not order:
                await message.reply("❌ Корзина не найдена.", keyboard=kb_back_to_menu())
                cursor.clear()
                return
            item = next((i for i in order.items if i.id == item_id), None)
            if not item:
                await message.reply("❌ Позиция не найдена.", keyboard=kb_back_to_menu())
                cursor.clear()
                return

            item.quantity = new_qty
            await recalculate_total(session, order)
            await session.commit()

        async for session in get_session():
            order = await get_draft_order(session, user_id)
        await message.reply(
            format_cart(order),
            keyboard=kb_cart_actions(order.id),
            format="markdown"
        )
        cursor.clear()

    async def _finalize_order(ctx, order_id, user_id, bonus_amount):
        """Списывает бонусы, меняет статус и отправляет QR с информацией."""
        async for session in get_session():
            order = await get_order_with_items(session, order_id)
            if not order or order.user_id != user_id:
                await ctx.send("❌ Заказ не найден.")
                return

            if order.status != OrderStatus.draft:
                await ctx.send("❌ Заказ уже нельзя изменить.")
                return

            # Применяем бонусы
            if bonus_amount > 0:
                user = await session.get(User, user_id)
                user.bonus_balance -= bonus_amount
                order.bonus_used = bonus_amount
                order.total_amount -= bonus_amount

            order.status = OrderStatus.pending
            await session.commit()
            invalidate_catalog_cache()

            # QR-код
            qr_token = await get_bot_setting(session, "payment_qr_token")
            attachments = []
            if qr_token and not qr_token.startswith("AgACAgI"):
                attachments.append(aiomax.PhotoAttachment(token=qr_token))

            cart_text = format_cart(order)
            bonus_text = f"\n💎 Списано бонусов: {bonus_amount}" if bonus_amount > 0 else ""
            msg_text = (
                f"✅ **Заказ #{order.id} оформлен!**\n\n"
                f"{cart_text}"
                f"{bonus_text}\n\n"
                "После оплаты нажмите кнопку ниже и пришлите фото чека."
            )

            kb = KeyboardBuilder()
            kb.add(CallbackButton("💳 Я оплатил — отправить чек", f"payment:receipt:{order.id}", intent='default'))
            kb.row(CallbackButton("❌ Отменить заказ", f"payment:cancel:{order.id}", intent='default'))
            kb.row(CallbackButton("🏠 Главное меню", "menu:main", intent='default'))

            await ctx.send(
                msg_text,
                keyboard=kb,
                attachments=attachments if attachments else None,
                format="markdown"
            )
    # ── Оформить заказ (атомарное резервирование + проверка QR) ───────────────
    @bot.on_button_callback(lambda cb: cb.payload.startswith("cart:checkout:"))
    async def cart_checkout(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id

        if user_id != ADMIN_USER_ID and not await check_payment_qr():
            await cb.answer(
                text="⚠️ Бот временно недоступен. Приносим извинения.",
                keyboard=kb_unavailable(),
                format="markdown"
            )
            return

        if user_id == ADMIN_USER_ID and not await check_payment_qr():
            try:
                await cb.message.delete()
            except Exception:
                pass
            kb = KeyboardBuilder()
            kb.add(CallbackButton("💳 Реквизиты", "admin:payment_qr", intent='default'))
            kb.row(CallbackButton("🏠 Главное меню", "menu:main", intent='default'))
            await cb.send(
                text="⚠️ **Реквизиты не указаны.**\n\nЗагрузите QR‑код в разделе «Реквизиты» админ‑меню.",
                keyboard=kb,
                format="markdown"
            )
            return

        await cb.answer(notification=" ")
        try:
            await cb.message.delete()
        except Exception:
            pass

        async for session in get_session():
            order = await get_draft_order(session, user_id)
            if not order or not order.items:
                await cb.send("🛒 Корзина пуста.")
                return

            # --- Проверка бонусного баланса ---
            user = await session.get(User, user_id)
            bonus_balance = user.bonus_balance if user else 0
            if bonus_balance > 0 and order.total_amount > 0:
                cursor.change_state("order_bonus_input")
                cursor.change_data({
                    "order_id": order.id,
                    "bonus_balance": bonus_balance,
                    "order_total": order.total_amount
                })
                max_bonus = min(bonus_balance, int(order.total_amount * 0.2))
                await cb.send(
                    f"💎 **У вас {bonus_balance} бонусов!**\n\n"
                    f"Вы можете оплатить до 20% стоимости заказа.\n"
                    f"💰 Максимум: {max_bonus} бонусов.\n\n"
                    f"Введите сумму бонусов для списания (или 0, чтобы не использовать):",
                    keyboard=kb_back_to_menu()
                )
                return

            # Бонусов нет – оформляем без них
            await _finalize_order(cb, order.id, user_id, 0)