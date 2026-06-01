import asyncio
import logging
import aiomax
from aiomax.bot import Bot

from config import BOT_TOKEN
from db import init_db
from reminders import reminder_loop
from handlers import start, cart, checkout, fsm_inputs, posts, admin, orders, catalog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ------------------- Патч методов Bot для авторизации через заголовок -------------------
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

# ------------------- Патч для сообщений канала (без sender) -------------------
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
# ------------------------------------------------------------------------------

async def main():
    logger.info("Инициализация базы данных...")
    await init_db()

    bot = aiomax.Bot(BOT_TOKEN, default_format="markdown")

    # Регистрируем обработчики
    start.register(bot)
    cart.register(bot)
    checkout.register(bot)
    admin.register(bot)
    orders.register(bot)
    fsm_inputs.register(bot)
    posts.register(bot)
    catalog.register(bot)

    # Фоновая задача напоминаний
    asyncio.create_task(reminder_loop(bot))
    logger.info("Фоновая задача напоминаний запущена.")

    logger.info("Бот запускается (Long Polling)...")
    try:
        await bot.start_polling()
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(main())