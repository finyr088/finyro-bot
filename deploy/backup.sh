#!/usr/bin/env bash
# Бэкап базы Финуро (SQLite) — консистентный снимок через online backup API,
# копия на хост, сжатие и ротация (последние N копий).
#
# Запуск вручную:   bash deploy/backup.sh
# Восстановление:   см. deploy/restore.sh
set -e

cd "$(dirname "$0")/.."   # корень репозитория (~/finyro-bot)

COMPOSE="docker compose -f deploy/docker-compose.yml"
CONTAINER="finyro-app-1"
BACKUP_DIR="${BACKUP_DIR:-$HOME/finyro-backups}"
KEEP="${KEEP:-14}"        # сколько копий хранить

mkdir -p "$BACKUP_DIR"
TS="$(date +%Y%m%d-%H%M%S)"
TMP="/data/_backup_$TS.db"

echo "→ Снимок базы…"
# Online backup API SQLite — безопасно даже при работающем боте.
$COMPOSE exec -T app python -c "import sqlite3; s=sqlite3.connect('/data/finyro.db'); d=sqlite3.connect('$TMP'); s.backup(d); d.close(); s.close()"

echo "→ Копирую на хост…"
docker cp "$CONTAINER:$TMP" "$BACKUP_DIR/finyro-$TS.db"
$COMPOSE exec -T app rm -f "$TMP" || true

gzip -f "$BACKUP_DIR/finyro-$TS.db"
echo "✓ Бэкап: $BACKUP_DIR/finyro-$TS.db.gz"

# Ротация: оставляем последние $KEEP.
ls -1t "$BACKUP_DIR"/finyro-*.db.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
echo "✓ Всего копий: $(ls -1 "$BACKUP_DIR"/finyro-*.db.gz 2>/dev/null | wc -l) (храним $KEEP)"
