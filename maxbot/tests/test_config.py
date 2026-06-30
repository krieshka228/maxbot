"""Тесты конфигурации Max-бота (config.Settings) — fail-fast проверка
обязательных полей перед стартом, см. promt.md, раздел «Общая
инфраструктура»: «Проверка обязательных полей при старте (fail-fast)»."""

import pytest

from maxbot.config import Settings

# _env_file=None — игнорируем реальный .env, чтобы тесты были детерминированы
# и не зависели от секретов разработчика.
_BASE = dict(_env_file=None)


def _settings(**kwargs) -> Settings:
    return Settings(**_BASE, **kwargs)


def test_assert_production_ready_missing_everything():
    s = _settings(bot_token="", admin_user_id=0, channel_id=0)
    with pytest.raises(RuntimeError) as exc_info:
        s.assert_production_ready()
    msg = str(exc_info.value)
    assert "BOT_TOKEN" in msg
    assert "ADMIN_USER_ID" in msg
    assert "CHANNEL_ID" in msg


def test_assert_production_ready_missing_token_only():
    s = _settings(bot_token="", admin_user_id=1, channel_id=-100)
    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        s.assert_production_ready()


def test_assert_production_ready_ok():
    s = _settings(bot_token="abc123", admin_user_id=42, channel_id=-1001234567890)
    s.assert_production_ready()  # не должно бросать исключение


def test_is_sqlite_true_for_sqlite_dsn():
    assert _settings(database_url="sqlite+aiosqlite:///orders.db").is_sqlite is True


def test_is_sqlite_false_for_postgres_dsn():
    assert _settings(database_url="postgresql+asyncpg://user@host/db").is_sqlite is False


def test_defaults_are_safe_placeholders():
    """По умолчанию (без .env) бот не должен считаться готовым к продакшену —
    значения по умолчанию обязаны провалить fail-fast проверку."""
    s = _settings()
    with pytest.raises(RuntimeError):
        s.assert_production_ready()


def test_telegram_bot_token_optional():
    """TELEGRAM_BOT_TOKEN опционален — нужен только для перезаливки медиа
    из Telegram в Max (см. utils.get_max_attachments), его отсутствие НЕ
    должно блокировать запуск Max-бота."""
    s = _settings(bot_token="abc", admin_user_id=1, channel_id=-1, telegram_bot_token="")
    s.assert_production_ready()  # не должно бросать
