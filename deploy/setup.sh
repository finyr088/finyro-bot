#!/usr/bin/env bash
# Установка Финуро на чистый VPS (Ubuntu/Debian) одной командой.
# Запуск из корня репозитория:  bash deploy/setup.sh
set -e

cd "$(dirname "$0")/.."   # корень репозитория

echo "== Финуро: установка на сервер =="

# 1. Docker
if ! command -v docker >/dev/null 2>&1; then
  echo "→ Устанавливаю Docker…"
  curl -fsSL https://get.docker.com | sh
fi

# 2. Определяем бесплатный HTTPS-домен из внешнего IP (sslip.io)
IP="$(curl -fsS https://api.ipify.org || true)"
if [ -z "$IP" ]; then
  read -rp "Не удалось определить IP. Введите внешний IP сервера: " IP
fi
DOMAIN="${IP//./-}.sslip.io"
echo "→ HTTPS-домен: https://$DOMAIN"

# 3. Файл .env (секреты и настройки)
if [ ! -f .env ]; then
  echo "→ Настройка .env"
  read -rp "  BOT_TOKEN (от @BotFather): " BT
  read -rp "  ADMIN_IDS (ваш Telegram ID): " AI
  SECRET="$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 48)"
  cat > .env <<EOF
BOT_TOKEN=$BT
ADMIN_IDS=$AI
SECRET_KEY=$SECRET
ACCESS_DAYS=0
DOMAIN=$DOMAIN
MINIAPP_URL=https://$DOMAIN
DATABASE_URL=sqlite+aiosqlite:////data/finyro.db
EOF
  echo "  .env создан."
else
  echo "→ .env уже существует — использую его."
fi

# 4. Запуск
echo "→ Сборка и запуск (первый раз ~2–4 мин)…"
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build

echo ""
echo "======================================================"
echo " Готово! Мини-приложение:  https://$DOMAIN"
echo " Бот запущен. Напишите ему /start, затем /admin."
echo " Логи:   docker compose -f deploy/docker-compose.yml logs -f app"
echo " Стоп:   docker compose -f deploy/docker-compose.yml down"
echo "======================================================"
