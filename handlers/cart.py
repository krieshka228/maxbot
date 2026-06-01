"""
handlers/cart.py — корзина: просмотр, удаление, изменение количества, оформление заказа.
Оформление происходит одним сообщением: сразу показываются реквизиты.
Атомарное резервирование выполняется при оформлении.
"""

import logging

import aiomax
from aiomax import fsm, filters
from aiomax.buttons import KeyboardBuilder, CallbackButton
from sqlalchemy import text

from config import PAYMENT_DETAILS
from db import (
    get_session,
    get_or_create_user,
    get_draft_order,
    remove_item_from_order,
    recalculate_total,
    OrderStatus,
)
from keyboards import (
    kb_cart_actions,
    kb_cart_items_remove,
    kb_back_to_menu,
)
from utils import format_cart
from db import get_bot_setting
logger = logging.getLogger(__name__)


def register(bot: aiomax.Bot) -> None:

    # ── Просмотр корзины ──────────────────────────────────────────────────────
    @bot.on_button_callback("cart:view")
    async def view_cart(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
        await cb.answer(notification=" ")

        async for session in get_session():
            user = await get_or_create_user(session, user_id)
            if not user.consented:
                await cb.answer(
                    text="❌ Сначала дайте согласие на обработку данных (/start).",
                    keyboard=kb_back_to_menu(),
                    format="markdown"
                )
                return
            order = await get_draft_order(session, user_id)

        if order is None or not order.items:
            await cb.answer(
                text="🛒 Ваша корзина пуста.\n\nПерейдите в каталог и добавьте товары.",
                keyboard=kb_back_to_menu(),
                format="markdown"
            )
            return

        await cb.answer(
            text=format_cart(order),
            keyboard=kb_cart_actions(order.id, has_items=True),
            format="markdown"
        )

    # ── Удалить позицию (выбор) ───────────────────────────────────────────────
    @bot.on_button_callback(lambda cb: cb.payload.startswith("cart:remove:"))
    async def cart_remove_choose(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        await cb.answer(notification=" ")
        async for session in get_session():
            order = await get_draft_order(session, cb.user.user_id)
        if not order or not order.items:
            await cb.answer(text="🛒 Корзина пуста.", keyboard=kb_back_to_menu())
            return
        await cb.answer(text="Выберите позицию для удаления:", keyboard=kb_cart_items_remove(order))

    @bot.on_button_callback(lambda cb: cb.payload.startswith("cart:del_item:"))
    async def cart_delete_item(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        item_id = int(cb.payload.split(":")[-1])
        await cb.answer(notification=" ")
        async for session in get_session():
            order = await get_draft_order(session, cb.user.user_id)
            if not order:
                await cb.answer(text="🛒 Корзина пуста.", keyboard=kb_back_to_menu())
                return

            removed = await remove_item_from_order(session, order, item_id)
            if removed:
                await session.refresh(order)
                if order.items:
                    await cb.answer(
                        text="✅ Удалено.\n\n" + format_cart(order),
                        keyboard=kb_cart_actions(order.id),
                        format="markdown"
                    )
                else:
                    await cb.answer(text="✅ Удалено. Корзина пуста.", keyboard=kb_back_to_menu(), format="markdown")
            else:
                await cb.answer(text="❌ Позиция не найдена.", keyboard=kb_back_to_menu())

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
        await cb.answer(text="Выберите позицию для изменения:", keyboard=kb, format="markdown")

    @bot.on_button_callback(lambda cb: cb.payload.startswith("cart:change_qty:"))
    async def cart_change_qty_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        item_id = int(cb.payload.split(":")[-1])
        cursor.change_state("cart_change_qty")
        cursor.change_data({"item_id": item_id})

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
        await cb.answer(
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

            product = item.product
            new_qty = item.quantity + delta

            # Проверка остатка (stock не трогаем — резервирование при оформлении)
            if product and product.stock is not None and new_qty > product.stock:
                await cb.answer(notification=f"❌ Доступно только {product.stock} шт.")
                return

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
            await cb.answer(text="🛒 Корзина пуста.", keyboard=kb_back_to_menu(), format="markdown")
            cursor.clear()
            return

        await cb.answer(
            text=format_cart(order),
            keyboard=kb_cart_actions(order.id),
            format="markdown"
        )
        cursor.clear()

    # Обработчик кнопки «Ввести число» – переводит в FSM для ввода
    @bot.on_button_callback(lambda cb: cb.payload.startswith("cart:input:"))
    async def cart_input_start(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        item_id = int(cb.payload.split(":")[-1])
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

            product = item.product
            # Проверка остатка
            if product and product.stock is not None and new_qty > product.stock:
                await message.reply(
                    f"❌ Недостаточно товара. В наличии: {product.stock} шт.",
                    keyboard=kb_back_to_menu()
                )
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

    # ── Оформить заказ (атомарное резервирование здесь) ─────────────────────────
    # В файле handlers/cart.py

    @bot.on_button_callback(lambda cb: cb.payload.startswith("cart:checkout:"))
    async def cart_checkout(cb: aiomax.Callback, cursor: fsm.FSMCursor):
        user_id = cb.user.user_id
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

            # Проверка остатков и атомарное резервирование
            for item in order.items:
                product = item.product
                if product and product.stock is not None:
                    if item.quantity > product.stock:
                        await cb.send(
                            f"❌ Товар «{product.name}» доступен в количестве {product.stock} шт. "
                            f"У вас в корзине {item.quantity} шт. Пожалуйста, измените количество.",
                            keyboard=kb_cart_actions(order.id),
                            format="markdown"
                        )
                        return
                    # Атомарное списание
                    result = await session.execute(
                        text("UPDATE products SET stock = stock - :qty WHERE id = :id AND stock >= :qty"),
                        {"qty": item.quantity, "id": product.id}
                    )
                    if result.rowcount == 0:
                        await cb.send(
                            f"❌ Товар «{product.name}» только что закончился.",
                            keyboard=kb_cart_actions(order.id),
                            format="markdown"
                        )
                        return
                    await session.refresh(product, attribute_names=["stock"])

            order.status = OrderStatus.pending
            await session.commit()

        # --- Блок добавления QR-кода ---
        attachments = []
        async for session in get_session():
            qr_token = await get_bot_setting(session, "payment_qr_token")
        if qr_token:
            attachments.append(aiomax.PhotoAttachment(token=qr_token))
        # -----------------------------

        cart_text = format_cart(order)
        msg_text = (
            f"✅ **Заказ #{order.id} оформлен!**\n\n"
            f"{cart_text}\n\n"
            f"💳 **Реквизиты для оплаты:**\n{PAYMENT_DETAILS}\n\n"
            "После оплаты нажмите кнопку ниже и пришлите фото чека."
        )

        kb = KeyboardBuilder()
        kb.add(CallbackButton("💳 Я оплатил — отправить чек", f"payment:receipt:{order.id}"))
        kb.row(CallbackButton("❌ Отменить заказ", f"payment:cancel:{order.id}"))
        kb.row(CallbackButton("🏠 Главное меню", "menu:main"))

        await cb.send(msg_text, keyboard=kb, attachments=attachments if attachments else None, format="markdown")