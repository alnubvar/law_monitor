# Backup and Restore

Production data lives outside the git checkout:

- DB: `/var/lib/ahstep-law-monitor/data/law_monitor.db`
- reports: `/var/lib/ahstep-law-monitor/reports`
- backups: `/var/lib/ahstep-law-monitor/backups`
- temp files: `/var/lib/ahstep-law-monitor/tmp`
- logs: `/var/log/ahstep-law-monitor`

Local development still defaults to `data/`, `reports/`, and `logs/` inside the
repository.

## Backup

Run before updates, risky maintenance, manual DB work, and restore attempts:

```bash
cd /opt/ahstep/law_monitor
sudo -u ahstep LAW_MONITOR_ENV_FILE=/etc/ahstep-law-monitor/law-monitor.env \
  bash scripts/backup_db.sh
```

The script uses `sqlite3 .backup` when available. If `sqlite3` is not installed,
it copies the DB and WAL/SHM sidecars. The copy fallback should be used only
after stopping services:

```bash
sudo systemctl stop ahstep-scheduler.service ahstep-telegram-bot.service
```

Backup files are timestamped as:

```text
/var/lib/ahstep-law-monitor/backups/law_monitor_YYYYMMDD_HHMMSS.db
```

## Restore

Always stop services first:

```bash
sudo systemctl stop ahstep-scheduler.service ahstep-telegram-bot.service
```

On systemd hosts, `scripts/restore_db.sh` enforces this and exits non-zero if
either service is still active. It also refuses to run while the app writer lock
exists. On non-systemd/manual deployments, the operator must verify no app
process is running before restore.

Restore requires an explicit `--confirm`:

```bash
cd /opt/ahstep/law_monitor
sudo -u ahstep LAW_MONITOR_ENV_FILE=/etc/ahstep-law-monitor/law-monitor.env \
  bash scripts/restore_db.sh /var/lib/ahstep-law-monitor/backups/law_monitor_YYYYMMDD_HHMMSS.db --confirm
```

The restore script creates a pre-restore backup of the current DB before it
overwrites anything.

Validate and start services:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py smoke-check'
sudo systemctl start ahstep-scheduler.service ahstep-telegram-bot.service
sudo journalctl -u ahstep-scheduler.service -n 100 --no-pager
sudo journalctl -u ahstep-telegram-bot.service -n 100 --no-pager
```

## Reports and Logs

DB scripts cover the SQLite database only. For a wider operational snapshot,
copy these directories separately:

```bash
sudo tar -C /var/lib -czf /var/lib/ahstep-law-monitor/backups/reports_$(date +%Y%m%d_%H%M%S).tar.gz ahstep-law-monitor/reports
sudo tar -C /var/log -czf /var/lib/ahstep-law-monitor/backups/logs_$(date +%Y%m%d_%H%M%S).tar.gz ahstep-law-monitor
```
