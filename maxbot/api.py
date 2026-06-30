import aiohttp
from maxbot.config import BOT_TOKEN

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
from maxbot.config import BOT_TOKEN

BASE_URL = "https://platform-api.max.ru"

async def fetch_channel_messages(chat_id: int, limit: int = 200) -> list[dict]:
    """
    Загружает последние `limit` сообщений канала.
    Использует маркер для продолжения, если API возвращает меньше, чем запрошено.
    Останавливается, когда набирается `limit` сообщений или больше нет данных.
    """
    url = f"{BASE_URL}/messages"
    headers = {"Authorization": BOT_TOKEN}
    params = {"chat_id": chat_id, "count": min(limit, 100)}  # API может иметь ограничение на count
    all_messages = []

    async with aiohttp.ClientSession() as session:
        while len(all_messages) < limit:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"API error {resp.status}: {text}")
                data = await resp.json()
                messages = data.get("messages", [])
                if not messages:
                    break
                all_messages.extend(messages)
                marker = data.get("marker")
                if not marker:
                    break
                params["marker"] = marker
                # Обновляем count на оставшееся количество
                params["count"] = min(limit - len(all_messages), 200)

    return all_messages[:limit]