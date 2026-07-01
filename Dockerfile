FROM python:3.10-slim

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Копируем ВСЁ текущее содержимое (не вложенную папку) в /app
COPY . /app/

RUN groupadd -r maxbot && useradd -r -g maxbot -d /app -s /usr/sbin/nologin maxbot \
    && chown -R maxbot:maxbot /app
USER maxbot

EXPOSE 8080

CMD ["python", "main.py"]
