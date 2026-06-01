"""
states.py — Состояния FSM.

Дерево состояний:
  CONSENT          → пользователь ещё не дал согласие на ПД
  IDLE             → главное меню / нет активного действия
  CART_CHANGE_QTY  → пользователь меняет количество позиции
  AWAITING_ADDRESS → ждём адрес доставки
  AWAITING_RECEIPT → ждём фото чека
  CONTACT_ADMIN    → пользователь пишет сообщение администратору
"""


class UserStates:
    CONSENT = "consent"
    IDLE = "idle"
    CART_CHANGE_QTY = "cart_change_qty"
    AWAITING_DELIVERY_METHOD = "awaiting_delivery_method"
    AWAITING_PHONE = "awaiting_phone"
    ADMIN_PAYMENT_QR = "admin_payment_qr"
    AWAITING_ADDRESS = "awaiting_address"
    AWAITING_RECEIPT = "awaiting_receipt"
    CONTACT_ADMIN = "contact_admin"
    EDITING_PHONE = "editing_phone"
    EDITING_DELIVERY = "editing_delivery"
    EDITING_ADDRESS = "editing_address"
    AWAITING_CONFIRMATION = "awaiting_confirmation"

