# Max магазин-бот (maxbot)

Демон для Max: публикует карточки товаров из общей PostgreSQL-базы в Max-канал, перезаливает фото и видео из Telegram и отправляет напоминания.

## Что делает

- читает товары из PostgreSQL;
- публикует карточки в Max-канал;
- перезаливает фото/видео из Telegram в Max;
- работает по long polling или webhook;
- интегрирован с Telegram-ботом через общую базу данных.

## Структура

- `main.py` — точка входа;
- `channel_publisher.py` — автопубликация товаров;
- `db.py` — доступ к PostgreSQL;
- `handlers/` — обработчики Max;
- `Dockerfile` — production-образ;
- `.env.example` — шаблон окружения.

## Быстрый запуск локально

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# заполните BOT_TOKEN, ID чатов и DATABASE_URL

python main.py
```

## Запуск в Docker

```bash
cp .env.example .env
docker build -t maxbot .
docker run -d --name maxbot --restart unless-stopped \
  --network host --env-file .env maxbot
```

## Обязательное окружение

| Переменная | Назначение |
|---|---|
| `BOT_TOKEN` | токен Max-бота |
| `ADMIN_USER_ID` | Max user_id администратора |
| `ADMIN_CHAT_ID` | чат уведомлений |
| `CHANNEL_ID` | Max-канал для публикации |
| `DATABASE_URL` | общая PostgreSQL-база с Telegram-ботом |
| `TELEGRAM_BOT_TOKEN` | токен Telegram-бота для скачивания медиа |

Опционально: `WEBHOOK_PATH`, `WEBHOOK_HOST`, `WEBHOOK_PORT`.

## Эксплуатация

```bash
docker logs -f maxbot
docker restart maxbot
```

Автопубликация выполняется через `channel_publisher.py`, интервал между постами задаётся в коде публикатора.

## Безопасность

`.env` и секреты не попадают в Git. Токены не выводятся в логах.
