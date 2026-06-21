FROM python:3.10-slim

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .

# Устанавливаем зависимости (aiomax, aiohttp, redis и т.д.)
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код
COPY . .

# Непривилегированный пользователь — не запускаем бота от root
# (см. promt.md, раздел «Безопасность и продакшен»).
RUN groupadd -r maxbot && useradd -r -g maxbot -d /app -s /usr/sbin/nologin maxbot \
    && chown -R maxbot:maxbot /app
USER maxbot

# Порт, который слушает aiohttp (должен совпадать с WEBHOOK_PORT)
EXPOSE 8080

# Запуск вашего main.py
CMD ["python", "main.py"]