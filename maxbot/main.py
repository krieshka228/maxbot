import asyncio
import logging

import aiomax
from aiomax.bot import Bot
from maxbot.config import BOT_TOKEN
from maxbot.db import init_db, engine
from maxbot.reminders import reminder_loop
from maxbot.channel_publisher import auto_publish_loop

# ── Настройка логирования ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Патч CallbackButton.__init__ – автоматически добавляет intent='default' ──
import aiomax.buttons as _buttons
_original_cb_init = _buttons.CallbackButton.__init__

def _patched_cb_init(self, text, payload, intent='default'):
    _original_cb_init(self, text, payload, intent)

_buttons.CallbackButton.__init__ = _patched_cb_init

# ── Патч CallbackButton.from_json ──────────────────────────────────────────
from aiomax.buttons import CallbackButton as _CB
_original_cb_from_json = _CB.from_json

@classmethod
def patched_cb_from_json(cls, data: dict):
    if "intent" not in data:
        data = {**data, "intent": "default"}
    return _original_cb_from_json(data)

_CB.from_json = patched_cb_from_json

# ── Патч методов Bot для авторизации через заголовок ─────────────────────
_original_get = Bot.get
_original_post = Bot.post
_original_put = Bot.put

async def patched_get(self, url, **kwargs):
    params = kwargs.get("params", {})
    if isinstance(params, dict):
        params.pop("access_token", None)
    kwargs["params"] = params
    headers = kwargs.get("headers", {})
    headers["Authorization"] = self.access_token
    kwargs["headers"] = headers
    return await _original_get(self, url, **kwargs)

async def patched_post(self, url, **kwargs):
    params = kwargs.get("params", {})
    if isinstance(params, dict):
        params.pop("access_token", None)
    kwargs["params"] = params
    headers = kwargs.get("headers", {})
    headers["Authorization"] = self.access_token
    kwargs["headers"] = headers
    return await _original_post(self, url, **kwargs)

async def patched_put(self, url, **kwargs):
    params = kwargs.get("params", {})
    if isinstance(params, dict):
        params.pop("access_token", None)
    kwargs["params"] = params
    headers = kwargs.get("headers", {})
    headers["Authorization"] = self.access_token
    kwargs["headers"] = headers
    return await _original_put(self, url, **kwargs)

Bot.get = patched_get
Bot.post = patched_post
Bot.put = patched_put

# ── Патч handle_update: sender для канала ────────────────────────────────
_original_handle_update = Bot.handle_update

async def patched_handle_update(self, update: dict):
    if "message" in update and not update["message"].get("sender"):
        update["message"]["sender"] = {
            "user_id": 0,
            "name": "Channel",
            "first_name": "Channel",
            "last_name": "",
            "username": None,
            "is_bot": True,
            "last_activity_time": 0,
        }
    await _original_handle_update(self, update)

Bot.handle_update = patched_handle_update

# ── Импортируем обработчики ПОСЛЕ всех патчей ──────────────────────────────
from maxbot.handlers import start, cart, checkout, fsm_inputs, posts, admin, orders, catalog
from maxbot.middlewares import patch_bot_antiflood


async def main():
    from maxbot.config import settings
    settings.assert_production_ready()

    logger.info("Инициализация базы данных...")
    await init_db()

    bot = aiomax.Bot(BOT_TOKEN, default_format="markdown")

    # Подключаем антифлуд
    patch_bot_antiflood(bot)

    start.register(bot)
    cart.register(bot)
    checkout.register(bot)
    admin.register(bot)
    orders.register(bot)
    fsm_inputs.register(bot)
    posts.register(bot)
    catalog.register(bot)

    asyncio.create_task(reminder_loop(bot))
    logger.info("Фоновая задача напоминаний запущена.")

    asyncio.create_task(auto_publish_loop(bot))
    logger.info("Фоновая задача автопубликации в канал Max запущена.")

    logger.info("Бот запускается (Long Polling)...")
    try:
        await bot.start_polling()
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        # Graceful shutdown — закрываем соединения с БД
        logger.info("Закрытие соединений с БД...")
        await engine.dispose()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    asyncio.run(main())
