# AHSTEP GR Monitor Deployment

## Purpose

This guide describes a practical MVP deployment setup for AHSTEP GR Monitor without changing core business logic.

Scope:

- stable `collect -> analyze -> report -> notify`;
- interactive Telegram command listener;
- smoke-check based operations;
- SQLite persistence + local backups.

## Recommended Runtime Variant (Current MVP)

Windows-first setup:

- Python `>=3.12`
- SQLite (`data/law_monitor.db`)
- Task Scheduler for periodic runs
- optional long-running Telegram bot process

Why this variant:

- aligned with current repo workflow;
- low operational overhead;
- no Docker/PostgreSQL requirements; OCR runtime is optional and disabled by default.

## Environment

Required `.env` fields:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_PROXY_URL=
TELEGRAM_API_TIMEOUT=30
LAW_MONITOR_DB_PATH=data/law_monitor.db
LAW_MONITOR_LOG_LEVEL=INFO
LAW_MONITOR_DAILY_REPORT_HOUR=9
LAW_MONITOR_HOURLY_INTERVAL_MINUTES=60
LAW_MONITOR_OCR_ENABLED=false
LAW_MONITOR_OCR_LANGUAGE=rus+eng
LAW_MONITOR_OCR_MAX_PAGES=5
LAW_MONITOR_OCR_TIMEOUT=120
LAW_MONITOR_OCR_TESSDATA_PATH=
```

Windows local OCR example:

```env
LAW_MONITOR_OCR_ENABLED=true
LAW_MONITOR_OCR_LANGUAGE=rus+eng
LAW_MONITOR_OCR_TESSDATA_PATH=C:\Program Files\Tesseract-OCR\tessdata
```

## First-Time Bring-Up

```powershell
.\.venv\Scripts\python.exe main.py init-db
.\.venv\Scripts\python.exe main.py smoke-check
.\.venv\Scripts\python.exe main.py run-scheduler --once
.\.venv\Scripts\python.exe main.py telegram-check
```

## Operations Commands

### Scheduler cycle (safe cron-style)

```powershell
.\scripts\run_scheduler_once.ps1
```

### Long-running scheduler

```powershell
.\scripts\run_scheduler.ps1
```

### Telegram listener

```powershell
.\.venv\Scripts\python.exe main.py run-telegram-bot
```

### OCR triage review

```powershell
.\.venv\Scripts\python.exe main.py ocr-queue
.\.venv\Scripts\python.exe main.py ocr-queue --priority high
.\.venv\Scripts\python.exe main.py ocr-check
.\.venv\Scripts\python.exe main.py ocr-backfill --source "Нормативные акты Краснодарского края" --limit 10
.\.venv\Scripts\python.exe main.py ocr-run --source "Нормативные акты Краснодарского края" --limit 1
```

## Data and Backups

- primary DB: `data/law_monitor.db`
- logs: `logs/app.log`
- backup script:

```powershell
.\scripts\backup_sqlite.ps1
```

## Linux / systemd (Optional)

If you deploy to Linux later, run two services:

1. `python main.py run-scheduler`
2. `python main.py run-telegram-bot`

The repo can use standard `systemd` units with:

- `WorkingDirectory=/opt/ahstep/gr-monitor`
- `EnvironmentFile=/opt/ahstep/gr-monitor/.env`
- `ExecStart=/opt/ahstep/gr-monitor/.venv/bin/python main.py <command>`

## Pre-Go-Live Checklist

1. `.env` completed (no secrets in git).
2. `python -m unittest -v` is green.
3. `python main.py smoke-check` passes.
4. `python main.py diagnostics --days 7` looks healthy.
5. `python main.py report --days 7` generated.
6. `python main.py telegram-check` delivered test message.
7. scheduled jobs configured (`run-scheduler --once` cadence).
