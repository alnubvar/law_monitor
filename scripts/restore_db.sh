#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage: scripts/restore_db.sh /path/to/law_monitor_backup.db --confirm

Stop ahstep-scheduler.service and ahstep-telegram-bot.service before restore.
On systemd hosts, this script refuses to run while either service is active.
The script creates a pre-restore backup of the current DB before overwriting it.
USAGE
}

require_services_stopped() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found; ensure no law_monitor app process is running before restore." >&2
    return
  fi

  local active_services=()
  local service
  for service in ahstep-scheduler.service ahstep-telegram-bot.service; do
    if systemctl is-active --quiet "$service"; then
      active_services+=("$service")
    fi
  done

  if [ "${#active_services[@]}" -gt 0 ]; then
    echo "Refusing to restore while services are active: ${active_services[*]}" >&2
    echo "Stop them first: sudo systemctl stop ahstep-scheduler.service ahstep-telegram-bot.service" >&2
    exit 1
  fi
}

BACKUP_SOURCE="${1:-}"
CONFIRM="${2:-}"
if [ -z "$BACKUP_SOURCE" ] || [ "$CONFIRM" != "--confirm" ]; then
  usage
  exit 2
fi

ENV_FILE="${LAW_MONITOR_ENV_FILE:-/etc/ahstep-law-monitor/law-monitor.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

DATA_DIR="${LAW_MONITOR_DATA_DIR:-${APP_DATA_DIR:-/var/lib/ahstep-law-monitor/data}}"
RUNTIME_DIR="${LAW_MONITOR_RUNTIME_DIR:-$DATA_DIR/runtime}"
DB_PATH="${LAW_MONITOR_DB_PATH:-$DATA_DIR/law_monitor.db}"
BACKUP_DIR="${LAW_MONITOR_BACKUP_DIR:-/var/lib/ahstep-law-monitor/backups}"
WRITER_LOCK_PATH="$RUNTIME_DIR/writer.lock"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
PRE_RESTORE_BACKUP="$BACKUP_DIR/pre_restore_$TIMESTAMP.db"

require_services_stopped

if [ ! -f "$BACKUP_SOURCE" ]; then
  echo "Backup file not found: $BACKUP_SOURCE" >&2
  exit 1
fi

if [ -e "$WRITER_LOCK_PATH" ]; then
  echo "Refusing to restore while app writer lock exists: $WRITER_LOCK_PATH" >&2
  echo "Ensure no write operation is running. If this is a stale lock, remove it only after verifying all app processes are stopped." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
mkdir -p "$(dirname "$DB_PATH")"

if [ -f "$DB_PATH" ]; then
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DB_PATH" ".backup '$PRE_RESTORE_BACKUP'"
  else
    echo "sqlite3 not found; creating pre-restore copy fallback." >&2
    cp -p -- "$DB_PATH" "$PRE_RESTORE_BACKUP"
    if [ -f "$DB_PATH-wal" ]; then
      cp -p -- "$DB_PATH-wal" "$PRE_RESTORE_BACKUP-wal"
    fi
    if [ -f "$DB_PATH-shm" ]; then
      cp -p -- "$DB_PATH-shm" "$PRE_RESTORE_BACKUP-shm"
    fi
  fi
  echo "Pre-restore backup created: $PRE_RESTORE_BACKUP"
fi

rm -f -- "$DB_PATH-wal" "$DB_PATH-shm"
cp -p -- "$BACKUP_SOURCE" "$DB_PATH"

if [ -f "$BACKUP_SOURCE-wal" ]; then
  cp -p -- "$BACKUP_SOURCE-wal" "$DB_PATH-wal"
fi
if [ -f "$BACKUP_SOURCE-shm" ]; then
  cp -p -- "$BACKUP_SOURCE-shm" "$DB_PATH-shm"
fi

echo "Database restored to: $DB_PATH"
