"""
keyboards.py — Клавиатуры для бота (aiomax KeyboardBuilder + CallbackButton).
"""

import aiomax
from aiomax import buttons
from aiomax.buttons import KeyboardBuilder, CallbackButton
from config import ADMIN_USER_ID


def kb_consent() -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    kb.add(buttons.CallbackButton("✅ Согласен", "consent:yes", intent='default'))
    kb.row(buttons.CallbackButton("❌ Отказываюсь", "consent:no", intent='default'))
    return kb


def kb_main_menu(is_admin: bool = False, has_qr: bool = True) -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    if is_admin or has_qr:
        kb.add(buttons.CallbackButton("🛍 Каталог", "catalog:show", intent='default'))
        kb.row(buttons.CallbackButton("🔎 Поиск по артикулу", "search:article", intent='default'))
        kb.row(buttons.CallbackButton("🔍 Поиск по названию", "search:name", intent='default'))
        kb.row(buttons.CallbackButton("🛒 Моя корзина", "cart:view", intent='default'))
        kb.row(buttons.CallbackButton("📋 Мои заказы", "orders:list", intent='default'))
    # Обычная кнопка, которая вызовет отправку упоминания
    kb.row(buttons.CallbackButton("✉️ Написать администратору", "contact:admin", intent='default'))
    if is_admin:
        kb.row(buttons.CallbackButton("⚙️ Админ-меню", "admin:menu", intent='default'))
    return kb


def kb_cart_actions(order_id: int, has_items: bool = True) -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    if has_items:
        kb.add(buttons.CallbackButton("✏️ Изменить кол-во", f"cart:edit:{order_id}", intent='default'))
        kb.row(buttons.CallbackButton("🗑️ Удалить позицию", f"cart:remove:{order_id}", intent='default'))
        kb.row(buttons.CallbackButton("✅ Оформить заказ", f"cart:checkout:{order_id}", intent='default'))
    kb.row(buttons.CallbackButton("🏠 Главное меню", "menu:main", intent='default'))
    return kb


def kb_cart_items_remove(order) -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    for item in order.items:
        name = (item.product.name[:28] if item.product else f"Позиция #{item.id}")
        kb.add(buttons.CallbackButton(f"🗑️ {name}", f"cart:del_item:{item.id}", intent='default'))
        kb.row()
    kb.add(buttons.CallbackButton("↩️ Назад", "cart:view", intent='default'))
    return kb


def kb_cart_items_edit(order) -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    for item in order.items:
        name = (item.product.name[:22] if item.product else f"Позиция #{item.id}")
        kb.add(buttons.CallbackButton(f"✏️ {name} (x{item.quantity})", f"cart:change_qty:{item.id}", intent='default'))
        kb.row()
    kb.add(buttons.CallbackButton("↩️ Назад", "cart:view", intent='default'))
    return kb


def kb_checkout_confirm(order_id: int) -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    kb.add(buttons.CallbackButton("✅ Подтвердить", f"checkout:confirm:{order_id}", intent='default'))
    kb.row(buttons.CallbackButton("↩️ Назад", "cart:view", intent='default'))
    return kb


def kb_payment(order_id: int) -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    kb.add(buttons.CallbackButton("💳 Я оплатил — отправить чек", f"payment:receipt:{order_id}", intent='default'))
    kb.row(buttons.CallbackButton("❌ Отменить заказ", f"payment:cancel:{order_id}", intent='default'))
    return kb


def kb_admin_menu() -> KeyboardBuilder:
    kb = KeyboardBuilder()
    kb.add(CallbackButton("📊 Заказы за месяц", "admin:excel:summary", intent='default'))
    kb.row(CallbackButton("👥 База клиентов", "admin:excel:clients", intent='default'))
    kb.row(CallbackButton("🔄 Синхронизировать товары", "admin:sync", intent='default'))
    kb.row(CallbackButton("💳 Реквизиты", "admin:payment_qr", intent='default'))
    kb.row(CallbackButton("📦 Изменить остаток", "admin:set_stock_list", intent='default'))
    kb.row(CallbackButton("🏠 Главное меню", "menu:main", intent='default'))
    return kb


def kb_admin_confirm_payment(order_id: int) -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    kb.add(buttons.CallbackButton("✅ Подтвердить оплату", f"admin:pay_ok:{order_id}", intent='default'))
    kb.row(buttons.CallbackButton("❌ Отклонить", f"admin:pay_fail:{order_id}", intent='default'))
    return kb


def kb_back_to_menu() -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    kb.add(buttons.CallbackButton("🏠 Главное меню", "menu:main", intent='default'))
    return kb


def kb_unavailable() -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    kb.row(buttons.CallbackButton("✉️ Написать администратору", "contact:admin", intent='default'))
    kb.row(buttons.CallbackButton("🏠 Главное меню", "menu:main", intent='default'))
    return kb