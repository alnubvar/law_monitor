#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${LAW_MONITOR_ENV_FILE:-/etc/ahstep-law-monitor/law-monitor.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

DATA_DIR="${LAW_MONITOR_DATA_DIR:-${APP_DATA_DIR:-/var/lib/ahstep-law-monitor/data}}"
DB_PATH="${LAW_MONITOR_DB_PATH:-$DATA_DIR/law_monitor.db}"
BACKUP_DIR="${LAW_MONITOR_BACKUP_DIR:-/var/lib/ahstep-law-monitor/backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_PATH="$BACKUP_DIR/law_monitor_$TIMESTAMP.db"

if [ ! -f "$DB_PATH" ]; then
  echo "SQLite database not found: $DB_PATH" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB_PATH" ".backup '$BACKUP_PATH'"
  echo "Backup created with sqlite3 .backup: $BACKUP_PATH"
else
  echo "sqlite3 not found; using copy fallback. Stop services before relying on fallback backups." >&2
  cp -p -- "$DB_PATH" "$BACKUP_PATH"
  if [ -f "$DB_PATH-wal" ]; then
    cp -p -- "$DB_PATH-wal" "$BACKUP_PATH-wal"
  fi
  if [ -f "$DB_PATH-shm" ]; then
    cp -p -- "$DB_PATH-shm" "$BACKUP_PATH-shm"
  fi
  echo "Backup created with copy fallback: $BACKUP_PATH"
fi
