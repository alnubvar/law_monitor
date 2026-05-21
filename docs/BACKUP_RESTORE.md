# Backup и restore

Production-данные располагаются вне git checkout:

- DB: `/var/lib/ahstep-law-monitor/data/law_monitor.db`
- reports: `/var/lib/ahstep-law-monitor/reports`
- backups: `/var/lib/ahstep-law-monitor/backups`
- временные файлы: `/var/lib/ahstep-law-monitor/tmp`
- logs: `/var/log/ahstep-law-monitor`

Локальная разработка по-прежнему использует `data/`, `reports/` и `logs/`
внутри репозитория по умолчанию.

## Backup

Запускать перед обновлениями, рискованным сопровождением, ручной работой с БД и
перед попытками restore:

```bash
cd /opt/ahstep/law_monitor
sudo -u ahstep LAW_MONITOR_ENV_FILE=/etc/ahstep-law-monitor/law-monitor.env \
  bash scripts/backup_db.sh
```

Скрипт использует `sqlite3 .backup`, если он доступен. Если `sqlite3` не
установлен, копируются файл БД и WAL/SHM-сайдкары. Copy fallback использовать
только при остановленных сервисах:

```bash
sudo systemctl stop ahstep-scheduler.service ahstep-telegram-bot.service
```

Файлы backup получают timestamp:

```text
/var/lib/ahstep-law-monitor/backups/law_monitor_YYYYMMDD_HHMMSS.db
```

## Restore

Сначала остановить сервисы:

```bash
sudo systemctl stop ahstep-scheduler.service ahstep-telegram-bot.service
```

На системах с systemd `scripts/restore_db.sh` сам проверяет это и
возвращает non-zero, если один из сервисов всё ещё активен. Скрипт также
отказывается работать при существующем writer lock. На не-systemd или ручных
deployment оператор должен сам убедиться, что ни один процесс приложения не
запущен.

Restore требует явного `--confirm`:

```bash
cd /opt/ahstep/law_monitor
sudo -u ahstep LAW_MONITOR_ENV_FILE=/etc/ahstep-law-monitor/law-monitor.env \
  bash scripts/restore_db.sh /var/lib/ahstep-law-monitor/backups/law_monitor_YYYYMMDD_HHMMSS.db --confirm
```

Restore-скрипт создаёт pre-restore backup текущей БД до того, как что-либо
перезапишется.

Проверить и запустить сервисы:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py smoke-check'
sudo systemctl start ahstep-scheduler.service ahstep-telegram-bot.service
sudo journalctl -u ahstep-scheduler.service -n 100 --no-pager
sudo journalctl -u ahstep-telegram-bot.service -n 100 --no-pager
```

## Reports и logs

DB-скрипты охватывают только SQLite-базу. Для более широкого снимка
эксплуатации копируйте каталоги отдельно:

```bash
sudo tar -C /var/lib -czf /var/lib/ahstep-law-monitor/backups/reports_$(date +%Y%m%d_%H%M%S).tar.gz ahstep-law-monitor/reports
sudo tar -C /var/log -czf /var/lib/ahstep-law-monitor/backups/logs_$(date +%Y%m%d_%H%M%S).tar.gz ahstep-law-monitor
```
