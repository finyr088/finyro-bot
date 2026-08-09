#!/usr/bin/env bash
# Восстановление базы Финуро из бэкапа.
# Использование:  bash deploy/restore.sh ~/finyro-backups/finyro-20260809-030000.db.gz
set -e

cd "$(dirname "$0")/.."
COMPOSE="docker compose -f deploy/docker-compose.yml"
CONTAINER="finyro-app-1"

FILE="$1"
[ -z "$FILE" ] && { echo "Укажите файл бэкапа: bash deploy/restore.sh <файл.db.gz>"; exit 1; }
[ -f "$FILE" ] || { echo "Файл не найден: $FILE"; exit 1; }

TMP="/tmp/finyro_restore_$$.db"
case "$FILE" in
  *.gz) gunzip -c "$FILE" > "$TMP" ;;
  *)    cp "$FILE" "$TMP" ;;
esac

echo "⚠️  Текущая база будет заменена содержимым $FILE"
read -rp "Продолжить? (yes/no): " ANS
[ "$ANS" = "yes" ] || { rm -f "$TMP"; echo "Отменено."; exit 0; }

echo "→ Останавливаю приложение…"
$COMPOSE stop app
echo "→ Заливаю базу…"
docker cp "$TMP" "$CONTAINER:/data/finyro.db"
echo "→ Запускаю…"
$COMPOSE start app
rm -f "$TMP"
echo "✓ База восстановлена из $FILE"
