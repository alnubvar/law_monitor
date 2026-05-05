# AHSTEP GR-monitoring Deployment

## Purpose

Этот документ описывает минимальный production-ready контур для запуска AHSTEP GR-monitoring как долгоживущего сервиса без изменения бизнес-логики.

Цель MVP:
- безопасно запускать `collect/analyze/report/notify/scheduler`
- принимать входящие Telegram-команды через long polling listener
- регулярно проверять состояние через `smoke-check`
- хранить SQLite и логи на диске
- иметь понятный backup/recovery plan

Базовые требования runtime:
- Python `>=3.12`
- VPS-путь проекта: `/opt/ahstep/gr-monitor`
- systemd services:
  - `ahstep-gr-monitor` (scheduler)
  - `ahstep-gr-telegram-bot` (interactive Telegram command bot)

## Recommended MVP Variant

Рекомендуемый MVP-вариант сейчас:
- Windows host
- локальная `SQLite`
- запуск через `PowerShell` scripts
- `Task Scheduler` для `run-scheduler --once` по расписанию

Почему так:
- уже соответствует текущей среде проекта
- не требует Docker/PostgreSQL
- не меняет Telegram business logic
- проще всего поддерживать до первого реального 24/7 цикла

## Launch Options

### 1. Local Manual Run

Подходит для ручной эксплуатации и первой проверки после deploy.

Команды:
```powershell
.\.venv\Scripts\python.exe main.py smoke-check
.\.venv\Scripts\python.exe main.py run-scheduler --once
.\.venv\Scripts\python.exe main.py run-telegram-bot
```

### 2. Windows Task Scheduler

Рекомендуемый текущий MVP-вариант.

Подход:
- `run_smoke_check.ps1` по требованию или перед вводом в эксплуатацию
- `run_scheduler_once.ps1` по расписанию, например каждые 30-60 минут
- `run_scheduler.ps1` только если нужен долгоживущий scheduler process
- для интерактивного Telegram listener использовать отдельную задачу `python main.py run-telegram-bot`

### 3. Linux VPS / systemd

Подходит для следующего шага после Windows MVP.

Процессы:
- `python main.py run-scheduler`
- `python main.py run-telegram-bot`

## Environment Variables

Минимально важные переменные:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_PROXY_URL=
TELEGRAM_API_TIMEOUT=30
LAW_MONITOR_DB_PATH=data/law_monitor.db
LAW_MONITOR_LOG_LEVEL=INFO
LAW_MONITOR_DAILY_REPORT_HOUR=9
LAW_MONITOR_HOURLY_INTERVAL_MINUTES=60
```

## Data, DB And Logs

### SQLite

Рекомендуемое хранение:
- рабочая БД: `data/law_monitor.db`
- backup copies: отдельная папка вне runtime cleanup, например `data/backups/`

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

Linux VPS:

```bash
cd /opt/ahstep/gr-monitor
source .venv/bin/activate
python main.py run-scheduler
```

## Telegram Command Bot Run

Интерактивный listener запускается отдельным процессом:

```bash
cd /opt/ahstep/gr-monitor
source .venv/bin/activate
python main.py run-telegram-bot
```

Listener:
- использует `getUpdates` long polling и offset
- вызывает `setMyCommands` на старте
- показывает reply keyboard после `/start` и после каждой команды
- принимает входящие сообщения только из `TELEGRAM_CHAT_ID`

## Telegram Check

Проверка production-контура без отправки:

```powershell
.\.venv\Scripts\python.exe main.py smoke-check
```

Проверка реальной доставки тестового сообщения:

```powershell
.\.venv\Scripts\python.exe main.py telegram-check
```

## systemd Units (Linux VPS)

### 1) Scheduler

`/etc/systemd/system/ahstep-gr-monitor.service`

```ini
[Unit]
Description=AHSTEP GR Monitor Scheduler
After=network.target

[Service]
Type=simple
User=ahstep
WorkingDirectory=/opt/ahstep/gr-monitor
EnvironmentFile=/opt/ahstep/gr-monitor/.env
ExecStart=/opt/ahstep/gr-monitor/.venv/bin/python main.py run-scheduler
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 2) Telegram Bot

`/etc/systemd/system/ahstep-gr-telegram-bot.service`

```ini
[Unit]
Description=AHSTEP GR Telegram Bot Listener
After=network.target

[Service]
Type=simple
User=ahstep
WorkingDirectory=/opt/ahstep/gr-monitor
EnvironmentFile=/opt/ahstep/gr-monitor/.env
ExecStart=/opt/ahstep/gr-monitor/.venv/bin/python main.py run-telegram-bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Включение:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ahstep-gr-monitor.service
sudo systemctl enable --now ahstep-gr-telegram-bot.service
```

## Stop / Restart

Остановка:

```bash
sudo systemctl stop ahstep-gr-monitor.service
sudo systemctl stop ahstep-gr-telegram-bot.service
```

Перезапуск:

```bash
sudo systemctl restart ahstep-gr-monitor.service
sudo systemctl restart ahstep-gr-telegram-bot.service
```

Проверка статуса и логов:

```bash
sudo systemctl status ahstep-gr-monitor.service
sudo systemctl status ahstep-gr-telegram-bot.service
journalctl -u ahstep-gr-monitor.service -f --no-pager
journalctl -u ahstep-gr-telegram-bot.service -f --no-pager
```

## SQLite Backup

Windows-first backup script:

```powershell
.\scripts\backup_sqlite.ps1
```

## Pre-Go-Live Checklist

- `.venv` создан и зависимости установлены
- `.env` заполнен
- `python main.py init-db` проходит
- `python main.py smoke-check` проходит
- `python -m unittest` проходит
- `python main.py diagnostics --days 7` проходит
- `python main.py report --days 7` проходит
- `python main.py run-telegram-bot` стартует без ошибок при корректном Telegram env
- рабочая БД лежит на постоянном диске
- log directory writable
- backup script протестирован
- Telegram proxy проверен через `telegram-check` или `notify-test`
- scheduler и telegram-bot сервисы включены
