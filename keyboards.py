"""
keyboards.py — Клавиатуры для бота (aiomax KeyboardBuilder + CallbackButton).
"""

import aiomax
from aiomax import buttons
from aiomax.buttons import KeyboardBuilder, CallbackButton


def kb_consent() -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    kb.add(buttons.CallbackButton("✅ Согласен", "consent:yes"))
    kb.row(buttons.CallbackButton("❌ Отказываюсь", "consent:no"))
    return kb


def kb_main_menu(is_admin: bool = False) -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    kb.add(buttons.CallbackButton("🛍 Каталог", "catalog:show"))
    kb.row(buttons.CallbackButton("🔎 Поиск по артикулу", "search:article"))
    kb.row(buttons.CallbackButton("🛒 Моя корзина", "cart:view"))
    kb.row(buttons.CallbackButton("📋 Мои заказы", "orders:list"))
    kb.row(buttons.CallbackButton("✉️ Написать администратору", "contact:admin"))
    if is_admin:
        kb.row(buttons.CallbackButton("⚙️ Админ-меню", "admin:menu"))
    return kb


def kb_cart_actions(order_id: int, has_items: bool = True) -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    if has_items:
        kb.add(buttons.CallbackButton("✏️ Изменить кол-во", f"cart:edit:{order_id}"))
        kb.row(buttons.CallbackButton("🗑️ Удалить позицию", f"cart:remove:{order_id}"))
        kb.row(buttons.CallbackButton("✅ Оформить заказ", f"cart:checkout:{order_id}"))
    kb.row(buttons.CallbackButton("🏠 Главное меню", "menu:main"))
    return kb


def kb_cart_items_remove(order) -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    for item in order.items:
        name = (item.product.name[:28] if item.product else f"Позиция #{item.id}")
        kb.add(buttons.CallbackButton(f"🗑️ {name}", f"cart:del_item:{item.id}"))
        kb.row()
    kb.add(buttons.CallbackButton("↩️ Назад", "cart:view"))
    return kb


def kb_cart_items_edit(order) -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    for item in order.items:
        name = (item.product.name[:22] if item.product else f"Позиция #{item.id}")
        kb.add(buttons.CallbackButton(f"✏️ {name} (x{item.quantity})", f"cart:change_qty:{item.id}"))
        kb.row()
    kb.add(buttons.CallbackButton("↩️ Назад", "cart:view"))
    return kb


def kb_checkout_confirm(order_id: int) -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    kb.add(buttons.CallbackButton("✅ Подтвердить", f"checkout:confirm:{order_id}"))
    kb.row(buttons.CallbackButton("↩️ Назад", "cart:view"))
    return kb


def kb_payment(order_id: int) -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    kb.add(buttons.CallbackButton("💳 Я оплатил — отправить чек", f"payment:receipt:{order_id}"))
    kb.row(buttons.CallbackButton("❌ Отменить заказ", f"payment:cancel:{order_id}"))
    return kb


def kb_admin_menu() -> KeyboardBuilder:
    kb = KeyboardBuilder()
    kb.add(CallbackButton("📊 Заказы за месяц", "admin:excel:summary"))
    kb.row(CallbackButton("👥 База клиентов", "admin:excel:clients"))
    kb.row(CallbackButton("✅ Заказы к подтверждению", "admin:confirm_list"))
    kb.row(CallbackButton("🔄 Обновить категории из канала", "admin:refresh_categories"))
    kb.row(CallbackButton("🔄 Синхронизировать товары", "admin:sync"))
    kb.row(CallbackButton("📦 Изменить остаток", "admin:set_stock_list"))
    kb.row(CallbackButton("🏠 Главное меню", "menu:main"))
    return kb


def kb_admin_confirm_payment(order_id: int) -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    kb.add(buttons.CallbackButton("✅ Подтвердить оплату", f"admin:pay_ok:{order_id}"))
    kb.row(buttons.CallbackButton("❌ Отклонить", f"admin:pay_fail:{order_id}"))
    return kb


def kb_back_to_menu() -> aiomax.buttons.KeyboardBuilder:
    kb = buttons.KeyboardBuilder()
    kb.add(buttons.CallbackButton("🏠 Главное меню", "menu:main"))
    return kb
