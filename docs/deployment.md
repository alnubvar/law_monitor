# AHSTEP GR-monitoring Deployment

## Purpose

Этот документ описывает минимальный production-ready контур для запуска AHSTEP GR-monitoring как долгоживущего сервиса без изменения бизнес-логики.

Цель MVP:
- безопасно запускать `collect/analyze/report/notify/scheduler`
- регулярно проверять состояние через `smoke-check`
- хранить SQLite и логи на диске
- иметь понятный backup/recovery plan

## Recommended MVP Variant

Рекомендуемый MVP-вариант сейчас:
- Windows host
- локальная `SQLite`
- запуск через `PowerShell` scripts
- `Task Scheduler` для `run-scheduler --once` по расписанию

Почему так:
- уже соответствует текущей среде проекта
- не требует Docker/PostgreSQL
- не меняет Telegram behavior
- проще всего поддерживать до первого реального 24/7 цикла

## Launch Options

### 1. Local Manual Run

Подходит для ручной эксплуатации и первой проверки после deploy.

Команды:
```powershell
.\.venv\Scripts\python.exe main.py smoke-check
.\.venv\Scripts\python.exe main.py run-scheduler --once
```

### 2. Windows Task Scheduler

Рекомендуемый текущий MVP-вариант.

Подход:
- `run_smoke_check.ps1` по требованию или перед вводом в эксплуатацию
- `run_scheduler_once.ps1` по расписанию, например каждые 30-60 минут
- `run_scheduler.ps1` только если нужен долгоживущий foreground/background PowerShell process

Практика для MVP:
- hourly or every-30-minutes запускать `run-scheduler --once`
- отдельно 1 раз в день запускать `report` или полагаться на встроенный daily cycle scheduler

### 3. Linux VPS / systemd

Подходит для следующего шага после Windows MVP.

Варианты:
- `systemd` service для долгоживущего `python main.py run-scheduler`
- либо `systemd timer` / `cron` для `python main.py run-scheduler --once`

Предпочтительнее для устойчивости:
- `systemd timer` + `--once`

### 4. Railway / Container Later

Не рекомендуется как первый production path прямо сейчас.

Причины:
- проект пока ориентирован на file-based SQLite и local logs
- нужен отдельный этап для persistent volumes и operational hardening

## Environment Variables

Минимально важные переменные:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_PROXY_URL=
LAW_MONITOR_DB_PATH=data/law_monitor.db
LAW_MONITOR_LOG_LEVEL=INFO
LAW_MONITOR_DAILY_REPORT_HOUR=9
LAW_MONITOR_HOURLY_INTERVAL_MINUTES=60
```

В `.env.example` также оставлены deployment-friendly ключи:

```env
APP_DATA_DIR=data
LOG_LEVEL=INFO
DAILY_REPORT_HOUR=
TIMEZONE=
```

Они полезны как ops-подсказка, даже если runtime сейчас фактически использует `LAW_MONITOR_*`.

## Data, DB And Logs

### SQLite

Рекомендуемое хранение:
- рабочая БД: `data/law_monitor.db`
- backup copies: отдельная папка вне runtime cleanup, например `data/backups/`

Не храните SQLite на:
- сетевом диске с нестабильной блокировкой
- временной системной директории

### Logs

Рекомендуемое хранение:
- `logs/app.log`
- rotation уже поддерживается приложением

## Smoke Check

Перед первым запуском и после изменений:

```powershell
.\scripts\run_smoke_check.ps1
```

Или напрямую:

```powershell
.\.venv\Scripts\python.exe main.py smoke-check
```

Smoke-check:
- не запускает collect по сети
- не отправляет Telegram
- не меняет `notified`
- не ломает БД

## Scheduler Run

### Single Cycle

Для безопасного production cron-style запуска:

```powershell
.\scripts\run_scheduler_once.ps1
```

### Long-Running Scheduler

Если нужен долгоживущий процесс:

```powershell
.\scripts\run_scheduler.ps1
```

Или напрямую:

```powershell
.\.venv\Scripts\python.exe main.py run-scheduler
```

## Telegram Check

Проверить конфиг Telegram без отправки production digest:

```powershell
.\.venv\Scripts\python.exe main.py smoke-check
```

Проверить реальную доставку тестового сообщения вручную:

```powershell
.\.venv\Scripts\python.exe main.py telegram-check
```

Важно:
- `smoke-check` не отправляет сообщение
- `telegram-check` отправляет тестовое сообщение

## Stop / Restart

### Manual PowerShell Run

Остановка:
- `Ctrl+C` в консоли, где запущен scheduler

Перезапуск:
```powershell
.\scripts\run_smoke_check.ps1
.\scripts\run_scheduler.ps1
```

### Windows Task Scheduler

Остановка:
- Disable task в `Task Scheduler`

Перезапуск:
- Run task manually
- либо re-enable task

### Linux systemd

Остановка:
```bash
sudo systemctl stop ahstep-gr-monitor.service
```

Перезапуск:
```bash
sudo systemctl restart ahstep-gr-monitor.service
```

## SQLite Backup

Windows-first backup script:

```powershell
.\scripts\backup_sqlite.ps1
```

Рекомендуется:
- как минимум ежедневный backup
- хранить несколько последних копий
- периодически проверять, что backup-файлы реально создаются

## Recovery Plan

Если runtime сломался:

1. Остановить scheduler.
2. Запустить `smoke-check`.
3. Проверить `logs/app.log`.
4. Проверить наличие и размер `data/law_monitor.db`.
5. Если БД повреждена, восстановить последнюю backup-копию.
6. Запустить `run-scheduler --once`.
7. Убедиться, что `diagnostics` и `report` снова строятся.

Если Telegram перестал работать:

1. Проверить `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_PROXY_URL`.
2. Запустить `python main.py telegram-check`.
3. Проверить логи по proxy/connect timeout.

## Pre-Go-Live Checklist

- `.venv` создан и зависимости установлены
- `.env` заполнен
- `python main.py smoke-check` проходит
- `python -m unittest discover -s tests -v` проходит
- рабочая БД лежит на постоянном диске
- log directory writable
- backup script протестирован
- Telegram proxy проверен через `telegram-check`
- scheduler выбран: `Task Scheduler` или `systemd`
