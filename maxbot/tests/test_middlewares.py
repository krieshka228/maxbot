"""Тесты антифлуда (middlewares.is_rate_limited) — см. promt.md,
раздел «Безопасность и продакшен»: «Антифлуд по user_id»."""

import pytest

import middlewares


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    middlewares._hits.clear()
    middlewares._last_warned.clear()
    yield
    middlewares._hits.clear()
    middlewares._last_warned.clear()


def test_allows_requests_under_limit(monkeypatch):
    monkeypatch.setattr(middlewares, "RATE_LIMIT_MESSAGES", 5)
    monkeypatch.setattr(middlewares, "RATE_LIMIT_WINDOW", 10.0)
    monkeypatch.setattr(middlewares, "ADMIN_USER_ID", 0)

    user_id = 111
    for _ in range(5):
        assert middlewares.is_rate_limited(user_id) is False


def test_blocks_requests_over_limit(monkeypatch):
    monkeypatch.setattr(middlewares, "RATE_LIMIT_MESSAGES", 3)
    monkeypatch.setattr(middlewares, "RATE_LIMIT_WINDOW", 10.0)
    monkeypatch.setattr(middlewares, "ADMIN_USER_ID", 0)

    user_id = 222
    for _ in range(3):
        assert middlewares.is_rate_limited(user_id) is False
    # 4-й запрос в то же окно — должен быть заблокирован
    assert middlewares.is_rate_limited(user_id) is True


def test_admin_is_never_rate_limited(monkeypatch):
    monkeypatch.setattr(middlewares, "RATE_LIMIT_MESSAGES", 1)
    monkeypatch.setattr(middlewares, "RATE_LIMIT_WINDOW", 10.0)
    monkeypatch.setattr(middlewares, "ADMIN_USER_ID", 999)

    for _ in range(10):
        assert middlewares.is_rate_limited(999) is False


def test_different_users_have_independent_limits(monkeypatch):
    monkeypatch.setattr(middlewares, "RATE_LIMIT_MESSAGES", 1)
    monkeypatch.setattr(middlewares, "RATE_LIMIT_WINDOW", 10.0)
    monkeypatch.setattr(middlewares, "ADMIN_USER_ID", 0)

    assert middlewares.is_rate_limited(1) is False
    assert middlewares.is_rate_limited(1) is True
    # У другого пользователя свой собственный лимит, не общий счётчик.
    assert middlewares.is_rate_limited(2) is False
