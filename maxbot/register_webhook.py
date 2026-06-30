import asyncio
import logging
import aiohttp
from maxbot.config import BOT_TOKEN

logger = logging.getLogger(__name__)

WEBHOOK_URL = "https://fabric-overshot-baboon.ngrok-free.dev/webhook"

async def register_webhook():
    url = "https://platform-api.max.ru/subscriptions"
    headers = {"Authorization": BOT_TOKEN}
    payload = {"url": WEBHOOK_URL}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status == 200:
                logger.info("Webhook успешно зарегистрирован")
            else:
                body = await resp.text()
                logger.error(f"Ошибка регистрации webhook: {resp.status} {body}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(register_webhook())