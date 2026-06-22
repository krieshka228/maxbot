import asyncio
import aiomax
from maxbot.config import settings

async def main():
    bot = aiomax.Bot(settings.bot_token)

    # Инициализируем сессию (без этого send_message не сработает)
    # run() запускает бесконечный поллинг, поэтому просто делаем start_polling и сразу закрываем
    await bot.start_polling()   # создаёт сессию
    await asyncio.sleep(0.5)    # даём сессии создаться

    user_id = 198956043
    video_token = "f9LHodD0cOL0dbliqoXha3O7JdYGeFPKNAPwcpQSkl22nNrEPR3k8HhSBP7H3vZuOijYql7a7NI1Nke_aUyw"

    print(f"Пытаюсь отправить видео с токеном {video_token}")
    try:
        msg = await bot.send_message(
            text="Тестовое видео",
            user_id=user_id,
            attachments=[aiomax.VideoAttachment(token=video_token)]
        )
        print(f"Успех! Сообщение отправлено, id={msg.id}")
    except Exception as e:
        print(f"Ошибка: {e}")

    # Закрываем сессию и останавливаем поллинг (если нужно)
    await bot.session.close()
    await bot.stop_polling()  # если есть такой метод, иначе просто close

if __name__ == "__main__":
    asyncio.run(main())