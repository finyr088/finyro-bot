#!/usr/bin/env bash
# Локальный запуск Финуро (бот + мини-апп) одной командой.
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "→ Создаю виртуальное окружение…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "→ Устанавливаю зависимости…"
pip install -q -r requirements.txt

if [ ! -f ".env" ]; then
  echo "⚠️  Файла .env нет — копирую из .env.example. Заполните BOT_TOKEN и ADMIN_IDS!"
  cp .env.example .env
fi

PORT="${PORT:-8080}"
echo "→ Запускаю на http://localhost:${PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
