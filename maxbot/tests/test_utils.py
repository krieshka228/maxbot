"""Unit-тесты для чистых функций-помощников в utils.py."""

from types import SimpleNamespace

import pytest

from utils import build_post_text, format_cart, format_order_for_admin, parse_quantity


# --------------------------- parse_quantity ---------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("5", 5),
        ("5 шт", 5),
        ("5 шт.", 5),
        (" 12 ", 12),
        ("3 единицы", None),  # суффикс не из списка -> не матчится regex'ом целиком
        ("3 ед", 3),
        ("3ед.", 3),
    ],
)
def test_parse_quantity_valid_and_units(raw, expected):
    assert parse_quantity(raw) == expected


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "", "1.5"])
def test_parse_quantity_invalid(raw):
    assert parse_quantity(raw) is None


def test_parse_quantity_rejects_overflow():
    from validators import MAX_QUANTITY
    assert parse_quantity(str(MAX_QUANTITY + 1)) is None
    assert parse_quantity(str(MAX_QUANTITY)) == MAX_QUANTITY


# --------------------------- format_cart ---------------------------

def _make_order(items=None, total=0.0, **kwargs):
    return SimpleNamespace(items=items or [], total_amount=total, **kwargs)


def _make_item(product_name, qty, price):
    return SimpleNamespace(
        product=SimpleNamespace(name=product_name),
        product_id=1,
        quantity=qty,
        price_at_order=price,
    )


def test_format_cart_empty():
    order = _make_order()
    assert "пуста" in format_cart(order)


def test_format_cart_with_items():
    order = _make_order(items=[_make_item("Товар А", 2, 100.0)], total=200.0)
    text = format_cart(order)
    assert "Товар А" in text
    assert "200" in text


# --------------------------- format_order_for_admin ---------------------------

def test_format_order_for_admin_includes_fio_and_phone():
    """Регрессия: ранее ФИО и телефон не попадали в уведомление админу
    (см. promt.md — обязательное требование)."""
    user = SimpleNamespace(username="ivan", full_name="Иванов Иван", phone="+79991234567")
    order = SimpleNamespace(
        id=1,
        user=user,
        user_id=42,
        items=[_make_item("Товар Б", 1, 50.0)],
        total_amount=50.0,
        full_name="Иванов Иван Иванович",
        contact_phone="+79991234567",
        delivery_method="СДЭК",
        delivery_address="г. Москва, ул. Ленина 1",
    )
    text = format_order_for_admin(order)
    assert "Иванов Иван Иванович" in text
    assert "+79991234567" in text
    assert "СДЭК" in text
    assert "г. Москва" in text


def test_format_order_for_admin_handles_missing_user():
    """Регрессия: format_order_for_admin падал с AttributeError, если
    order.user is None (selectinload не подгрузил пользователя)."""
    order = SimpleNamespace(
        id=2,
        user=None,
        user_id=99,
        items=[],
        total_amount=0.0,
        full_name=None,
        contact_phone=None,
        delivery_method=None,
        delivery_address=None,
    )
    text = format_order_for_admin(order)
    assert "ID 99" in text


# --------------------------- build_post_text ---------------------------

def test_build_post_text_includes_core_fields():
    product = SimpleNamespace(
        name="Тестовый товар",
        article="ABC123",
        stock=5,
        price=999.0,
        category="Категория",
    )
    text = build_post_text(product)
    assert "Тестовый товар" in text
    assert "ABC123" in text
    assert "999" in text
