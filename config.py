import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_USER_ID: int = int(os.getenv("ADMIN_USER_ID", "0"))
ADMIN_CHAT_ID: int = int(os.getenv("ADMIN_CHAT_ID", "0"))
CHANNEL_ID: int = int(os.getenv("CHANNEL_ID", "0"))
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///orders.db")
PAYMENT_DETAILS: str = os.getenv(
    "PAYMENT_DETAILS",
    "Карта Сбербанк: 4276 0000 0000 0000\nПолучатель: Роман И."
)

# Webhook
WEBHOOK_PATH: str = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "8080"))

