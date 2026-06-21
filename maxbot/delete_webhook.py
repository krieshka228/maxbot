import asyncio
import aiohttp
from maxbot.config import BOT_TOKEN

async def delete_webhook():
    url = "https://platform-api.max.ru/subscriptions"
    headers = {"Authorization": BOT_TOKEN}
    async with aiohttp.ClientSession() as session:
        # Получаем список подписок
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                subscriptions = data.get("subscriptions", [])
                if not subscriptions:
                    print("Нет активных подписок")
                    return
                for sub in subscriptions:
                    sub_url = sub.get("url")
                    if sub_url:
                        print(f"Удаляю подписку: {sub_url}")
                        async with session.delete(f"{url}?url={sub_url}", headers=headers) as del_resp:
                            if del_resp.status == 200:
                                print("Успешно удалена")
                            else:
                                print(f"Ошибка удаления: {del_resp.status}")
            else:
                print(f"Ошибка получения подписок: {resp.status}")

if __name__ == "__main__":
    asyncio.run(delete_webhook())