FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# Один процесс: FastAPI (API + мини-апп) и Telegram-бот (polling) вместе.
# Порт фиксирован на 8080 — его же Timeweb определяет из EXPOSE и проверяет healthcheck'ом.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
