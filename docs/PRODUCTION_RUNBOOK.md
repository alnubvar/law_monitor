# Production Runbook

This runbook is for operating AHSTEP law_monitor on a corporate Linux server.
It assumes:

- code checkout: `/opt/ahstep/law_monitor`
- real env file: `/etc/ahstep-law-monitor/law-monitor.env`
- SQLite DB: `/var/lib/ahstep-law-monitor/data/law_monitor.db`
- reports: `/var/lib/ahstep-law-monitor/reports`
- backups: `/var/lib/ahstep-law-monitor/backups`
- temp files: `/var/lib/ahstep-law-monitor/tmp`
- logs: `/var/log/ahstep-law-monitor`

The DB, reports, backups, logs, and temp files are outside git. Production
reports generated under `/var/lib/ahstep-law-monitor/reports` should not be
copied back into the repository or committed.

## Command Wrapper

Use this pattern for manual app commands:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py smoke-check'
```

If an env value contains shell-special characters, quote it in
`law-monitor.env`.

## Daily Service Operations

Status:

```bash
sudo systemctl status ahstep-scheduler.service
sudo systemctl status ahstep-telegram-bot.service
```

Restart:

```bash
sudo systemctl restart ahstep-scheduler.service ahstep-telegram-bot.service
```

Logs:

```bash
sudo journalctl -u ahstep-scheduler.service -n 100 --no-pager
sudo journalctl -u ahstep-telegram-bot.service -n 100 --no-pager
sudo tail -n 100 /var/log/ahstep-law-monitor/app.log
```

Only one scheduler should run. Do not run `main.py run-scheduler` manually while
`ahstep-scheduler.service` is active.

## Manual Validation Checklist

Run after first deployment, after env/proxy changes, and after updates:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py init-db'

sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py smoke-check'

sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py telegram-check'

sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py notify-test'

sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py run-scheduler --once --force-daily-digest'
```

`telegram-check`, `notify-test`, and the forced daily digest can send Telegram
messages. Use them only when IT/ops expects test delivery.

Optional diagnostics:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py diagnostics --days 7'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py ocr-check'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py ocr-queue --status pending'
```

## Telegram Proxy Change

1. Edit the real env file:

```bash
sudoedit /etc/ahstep-law-monitor/law-monitor.env
```

2. Change only the env value:

```env
TELEGRAM_PROXY_URL=socks5://<user>:<password>@proxy.example.internal:1080
```

3. Restart services:

```bash
sudo systemctl restart ahstep-telegram-bot.service ahstep-scheduler.service
```

4. Validate:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py telegram-check'
```

Do not commit real proxy credentials. `TELEGRAM_PROXY_URL` applies to Telegram
Bot API calls only. Government/regional source requests use normal server
networking unless IT explicitly configures `HTTPS_PROXY`, `HTTP_PROXY`, or
similar source proxy envs.

## Backup

Preferred DB backup:

```bash
cd /opt/ahstep/law_monitor
sudo -u ahstep LAW_MONITOR_ENV_FILE=/etc/ahstep-law-monitor/law-monitor.env bash scripts/backup_db.sh
```

The script uses `sqlite3 .backup` when `sqlite3` is installed. If `sqlite3` is
missing, it falls back to copying the DB and WAL/SHM sidecars. The fallback is
safe only when services are stopped.

Backup before:

- every update;
- risky maintenance;
- manual DB inspection or repair;
- restore attempts.

## Restore

Stop services first:

```bash
sudo systemctl stop ahstep-scheduler.service ahstep-telegram-bot.service
```

On systemd hosts, `scripts/restore_db.sh` also checks these services and exits
non-zero if either is still active. It also refuses to run if the app writer lock
exists. For non-systemd or manual process runs, the operator must still verify
that no `main.py` app process is running before restore.

Restore with explicit confirmation:

```bash
cd /opt/ahstep/law_monitor
sudo -u ahstep LAW_MONITOR_ENV_FILE=/etc/ahstep-law-monitor/law-monitor.env \
  bash scripts/restore_db.sh /var/lib/ahstep-law-monitor/backups/law_monitor_YYYYMMDD_HHMMSS.db --confirm
```

The script creates a pre-restore backup before overwriting the current DB. After
restore:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py smoke-check'
sudo systemctl start ahstep-scheduler.service ahstep-telegram-bot.service
sudo journalctl -u ahstep-scheduler.service -n 100 --no-pager
sudo journalctl -u ahstep-telegram-bot.service -n 100 --no-pager
```

## Safe Update Procedure

```bash
sudo systemctl stop ahstep-scheduler.service ahstep-telegram-bot.service

cd /opt/ahstep/law_monitor
sudo -u ahstep LAW_MONITOR_ENV_FILE=/etc/ahstep-law-monitor/law-monitor.env bash scripts/backup_db.sh

sudo -u ahstep git pull --ff-only
sudo -u ahstep .venv/bin/python -m pip install -e .

sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py init-db'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py smoke-check'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py telegram-check'

sudo systemctl start ahstep-scheduler.service ahstep-telegram-bot.service
sudo journalctl -u ahstep-scheduler.service -n 100 --no-pager
sudo journalctl -u ahstep-telegram-bot.service -n 100 --no-pager
```

## Rollback Basics

1. Stop both services.
2. Check the last good git revision.
3. Restore code with git, for example `git checkout <known-good-sha>`.
4. Reinstall dependencies: `.venv/bin/python -m pip install -e .`.
5. Restore DB only if the failed update changed DB state and the previous DB
   state is required.
6. Run `init-db`, `smoke-check`, and `telegram-check`.
7. Start services and inspect logs.

## Source Network Caveat

Government/regional source behavior can differ by server network, VPN, DNS,
TLS inspection, and proxy routing. Validate source access from the target server
network before go-live and after network policy changes.
