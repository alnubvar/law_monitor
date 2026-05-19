# AHSTEP law_monitor Deployment

This guide prepares a clean Linux corporate deployment without changing parser,
classification, report, Medialogia ontology, or Telegram UX behavior.

## Recommended Linux Layout

```text
/opt/ahstep/law_monitor                    # git checkout / application code
/etc/ahstep-law-monitor/law-monitor.env    # real env, not in git
/var/lib/ahstep-law-monitor/data           # SQLite DB and runtime state
/var/lib/ahstep-law-monitor/reports        # generated reports
/var/lib/ahstep-law-monitor/backups        # DB backups
/var/lib/ahstep-law-monitor/tmp            # temp attachments/files
/var/log/ahstep-law-monitor                # service logs
```

Operational rule: `git pull` must only update code under
`/opt/ahstep/law_monitor`. The SQLite DB, generated reports, backups, logs, and
temp files live outside the git checkout and must not be committed.

## Server Prerequisites

- Linux server with outbound access to the required government/regional sources.
- Python 3.12 or newer.
- `git`.
- `sqlite3` recommended for online-safe DB backups. The backup script has a copy
  fallback, but fallback backups should be made only with services stopped.
- System user and group, for example `ahstep`.
- Optional OCR runtime only if OCR will be enabled later. OCR is disabled by
  default.

## First-Time Install

```bash
sudo useradd --system --home /opt/ahstep --shell /usr/sbin/nologin ahstep || true

sudo install -d -o ahstep -g ahstep /opt/ahstep
sudo install -d -o ahstep -g ahstep /var/lib/ahstep-law-monitor/data
sudo install -d -o ahstep -g ahstep /var/lib/ahstep-law-monitor/reports
sudo install -d -o ahstep -g ahstep /var/lib/ahstep-law-monitor/backups
sudo install -d -o ahstep -g ahstep /var/lib/ahstep-law-monitor/tmp
sudo install -d -o ahstep -g ahstep /var/log/ahstep-law-monitor
sudo install -d -o root -g ahstep -m 0750 /etc/ahstep-law-monitor

cd /opt/ahstep
sudo -u ahstep git clone <REPO_URL> law_monitor
cd /opt/ahstep/law_monitor

sudo -u ahstep python3.12 -m venv .venv
sudo -u ahstep .venv/bin/python -m pip install --upgrade pip
sudo -u ahstep .venv/bin/python -m pip install -e .
```

If the repository is already cloned, use the existing checkout instead of
cloning again.

## Environment File

Place the real env file at:

```text
/etc/ahstep-law-monitor/law-monitor.env
```

Use `.env.example` as a template, but do not commit the real file. Minimal Linux
production values:

```env
LAW_MONITOR_DATA_DIR=/var/lib/ahstep-law-monitor/data
LAW_MONITOR_REPORTS_DIR=/var/lib/ahstep-law-monitor/reports
LAW_MONITOR_BACKUP_DIR=/var/lib/ahstep-law-monitor/backups
LAW_MONITOR_TMP_DIR=/var/lib/ahstep-law-monitor/tmp
LAW_MONITOR_LOG_DIR=/var/log/ahstep-law-monitor
LAW_MONITOR_DB_PATH=/var/lib/ahstep-law-monitor/data/law_monitor.db
LAW_MONITOR_LOG_FILE=/var/log/ahstep-law-monitor/app.log

LAW_MONITOR_TIMEZONE=Europe/Moscow
LAW_MONITOR_DAILY_REPORT_HOUR=9
LAW_MONITOR_HOURLY_INTERVAL_MINUTES=360

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_PROXY_URL=
TELEGRAM_API_TIMEOUT=30
```

`LAW_MONITOR_HOURLY_INTERVAL_MINUTES` and
`LAW_MONITOR_DAILY_REPORT_HOUR` are canonical. Compatibility aliases
`SCHEDULER_INTERVAL_MINUTES` and `SCHEDULER_DAILY_REPORT_HOUR` are also accepted.

`TELEGRAM_PROXY_URL` is used only for Telegram Bot API calls. It does not force
government/regional source requests through that proxy. If IT wants source
requests to use a network proxy, configure standard source proxy envs such as
`HTTPS_PROXY` or `HTTP_PROXY` explicitly in the real env file.

Set permissions:

```bash
sudo chown root:ahstep /etc/ahstep-law-monitor/law-monitor.env
sudo chmod 0640 /etc/ahstep-law-monitor/law-monitor.env
```

If a value contains shell-special characters, quote it in the env file.

## Initialize and Validate

Run commands as the service user from the git checkout:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py init-db'

sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py smoke-check'

sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py telegram-check'

sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py notify-test'

sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py run-scheduler --once --force-daily-digest'
```

`telegram-check` and `notify-test` send Telegram test messages when credentials
are configured. Run them only when IT is ready to validate delivery.

## systemd Services

Templates are in `deploy/systemd/`:

- `ahstep-scheduler.service`
- `ahstep-telegram-bot.service`

Install and enable:

```bash
sudo cp deploy/systemd/ahstep-scheduler.service /etc/systemd/system/
sudo cp deploy/systemd/ahstep-telegram-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ahstep-scheduler.service
sudo systemctl enable --now ahstep-telegram-bot.service
```

Run only one scheduler instance. Do not also run cron, screen/tmux, or a second
systemd unit that calls `main.py run-scheduler`.

Check status and logs:

```bash
sudo systemctl status ahstep-scheduler.service
sudo systemctl status ahstep-telegram-bot.service
sudo journalctl -u ahstep-scheduler.service -n 100 --no-pager
sudo journalctl -u ahstep-telegram-bot.service -n 100 --no-pager
sudo tail -n 100 /var/log/ahstep-law-monitor/app.log
```

## Changing Telegram Proxy

```bash
sudoedit /etc/ahstep-law-monitor/law-monitor.env
# change TELEGRAM_PROXY_URL=...
sudo systemctl restart ahstep-telegram-bot.service ahstep-scheduler.service
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py telegram-check'
```

Do not commit real proxy credentials. Restart at least the Telegram bot after
changing proxy settings; restart both services when in doubt because the
scheduler also sends Telegram digests.

## Backup and Restore

Create a DB backup:

```bash
cd /opt/ahstep/law_monitor
sudo -u ahstep LAW_MONITOR_ENV_FILE=/etc/ahstep-law-monitor/law-monitor.env \
  bash scripts/backup_db.sh
```

Restore requires stopping both services first:

```bash
sudo systemctl stop ahstep-scheduler.service ahstep-telegram-bot.service
cd /opt/ahstep/law_monitor
sudo -u ahstep LAW_MONITOR_ENV_FILE=/etc/ahstep-law-monitor/law-monitor.env \
  bash scripts/restore_db.sh /var/lib/ahstep-law-monitor/backups/law_monitor_YYYYMMDD_HHMMSS.db --confirm
sudo systemctl start ahstep-scheduler.service ahstep-telegram-bot.service
```

The restore script creates a pre-restore backup before overwriting the DB. On
systemd hosts, it also exits non-zero if either service is still active. It
refuses to run while the app writer lock exists. For non-systemd/manual process
runs, the operator must verify that no app process is running before restore.

## Safe Update Flow

```bash
sudo systemctl stop ahstep-scheduler.service ahstep-telegram-bot.service

cd /opt/ahstep/law_monitor
sudo -u ahstep LAW_MONITOR_ENV_FILE=/etc/ahstep-law-monitor/law-monitor.env \
  bash scripts/backup_db.sh

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
2. Restore the previous git revision or branch.
3. Reinstall dependencies with `.venv/bin/python -m pip install -e .`.
4. Restore the DB backup only if the update changed DB state and rollback needs
   that older state.
5. Run `init-db`, `smoke-check`, and `telegram-check`.
6. Start both services and inspect logs.

## Known Caveat

Source network behavior can differ by server network, VPN, DNS, TLS inspection,
and proxy policy. A source that works from a developer workstation can fail from
the internal server, or the reverse. Validate from the target server network
before go-live.
