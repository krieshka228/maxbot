FROM python:3.10-slim

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код в ПОДДИРЕКТОРИЮ maxbot, чтобы получился пакет
COPY . /app/maxbot/

# Python должен видеть пакет maxbot
ENV PYTHONPATH=/app

# Непривилегированный пользователь
RUN groupadd -r maxbot && useradd -r -g maxbot -d /app -s /usr/sbin/nologin maxbot \
    && chown -R maxbot:maxbot /app
USER maxbot

EXPOSE 8080

# Запуск модуля maxbot.main
CMD ["python", "-m", "maxbot.main"]
