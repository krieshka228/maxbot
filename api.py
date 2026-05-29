import aiohttp
from config import BOT_TOKEN

BASE_URL = "https://platform-api.max.ru"

async def fetch_all_channel_messages(chat_id: int, limit: int = 200) -> list[dict]:
    """
    Загружает ВСЕ сообщения канала с помощью маркера.
    limit — количество за один запрос (макс. 200? уточните в доке, поставим 200).
    """
    url = f"{BASE_URL}/messages"
    headers = {"Authorization": BOT_TOKEN}
    params = {"chat_id": chat_id, "count": limit}
    all_messages = []

    async with aiohttp.ClientSession() as session:
        while True:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"API error {resp.status}: {text}")
                data = await resp.json()
                messages = data.get("messages", [])
                all_messages.extend(messages)
                marker = data.get("marker")
                if not marker or len(messages) < limit:
                    break
                params["marker"] = marker

    return all_messages
"""
maxbot/api.py — Прямые запросы к API MAX.
"""

import aiohttp
from config import BOT_TOKEN

BASE_URL = "https://platform-api.max.ru"

async def fetch_channel_messages(chat_id: int, limit: int = 50) -> list[dict]:
    """Получает последние сообщения канала через GET /messages"""
    url = f"{BASE_URL}/messages"
    headers = {"Authorization": BOT_TOKEN}
    params = {"chat_id": chat_id, "count": limit}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("messages", [])
            else:
                text = await resp.text()
                raise Exception(f"API error {resp.status}: {text}")