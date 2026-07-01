"""
config.py — конфигурация Max-бота через переменные окружения.

Использует ``pydantic-settings`` (тот же подход, что и в Telegram-боте —
см. telegram_bot/bot/config.py) — типобезопасность и fail-fast проверка
обязательных полей перед стартом.

ВАЖНО: DATABASE_URL должен указывать на ТУ ЖЕ базу, что и у Telegram-бота —
боты используют одну общую БД (см. promt.md, раздел "Главное требование:
единая БД").

TELEGRAM_BOT_TOKEN — опционален. Используется только для того, чтобы
скачать фото/видео товара, сохранённые Telegram-ботом как Telegram
file_id (Product.photo_file_ids/video_file_ids), и перезалить их в Max
(см. utils.get_max_attachments). Без этого токена карточки каталога
в Max будут отправляться без медиа.
"""

from __future__ import annotations
from pathlib import Path
from typing import ClassVar

import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    BASE_DIR: ClassVar[Path] = Path(__file__).parent
    ENV_FILE: ClassVar[Path] = BASE_DIR / ".env"

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: str = Field(default="", description="Токен Max-бота из @MasterBot")
    admin_user_id: int = Field(default=0, description="user_id администратора в Max")
    admin_chat_id: int = Field(default=0, description="Чат для уведомлений админа")
    channel_id: int = Field(default=0, description="ID канала Max для публикации товаров")
    database_url: str = Field(
        default="sqlite+aiosqlite:///orders.db",
        description="DSN базы данных (та же, что у Telegram-бота)",
    )
    payment_details: str = Field(
        default="Карта Сбербанк: 4276 0000 0000 0000\nПолучатель: Роман И.",
        description="Реквизиты для оплаты, показываемые покупателю",
    )

    # Опционально: для перезаливки фото/видео из Telegram в Max (см. docstring).
    telegram_bot_token: str = Field(
        default="", description="Токен Telegram-бота (только для скачивания медиа)"
    )

    # Webhook (используется опционально вместо long polling)
    webhook_path: str = Field(default="/webhook")
    webhook_host: str = Field(default="0.0.0.0")
    webhook_port: int = Field(default=8080)

    # Эксплуатационные настройки
    log_level: str = Field(default="INFO")
    log_json: bool = Field(default=True)

    # Антифлуд: не более N сообщений за окно секунд на пользователя.
    rate_limit_messages: int = Field(default=20, ge=1)
    rate_limit_window: float = Field(default=10.0, gt=0)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def assert_production_ready(self) -> None:
        """Fail-fast проверка обязательных значений перед стартом бота."""
        problems: list[str] = []
        if not self.bot_token:
            problems.append("BOT_TOKEN не задан")
        if not self.admin_user_id:
            problems.append("ADMIN_USER_ID не задан")
        if not self.channel_id:
            problems.append("CHANNEL_ID не задан")
        if problems:
            raise RuntimeError(
                "Некорректная конфигурация Max-бота: " + "; ".join(problems)
            )


settings = Settings()

# --- Обратная совместимость: модуль-уровневые константы (как раньше) ---
BOT_TOKEN: str = settings.bot_token
ADMIN_USER_ID: int = settings.admin_user_id
ADMIN_CHAT_ID: int = settings.admin_chat_id
CHANNEL_ID: int = settings.channel_id
DATABASE_URL: str = settings.database_url
PAYMENT_DETAILS: str = settings.payment_details
TELEGRAM_BOT_TOKEN: str = settings.telegram_bot_token

WEBHOOK_PATH: str = settings.webhook_path
WEBHOOK_HOST: str = settings.webhook_host
WEBHOOK_PORT: int = settings.webhook_port
