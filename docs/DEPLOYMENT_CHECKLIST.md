# Чеклист приемки deployment

Используйте этот чеклист для передачи корпоративного сервера, production-like
приемки и проверки после обновлений.

## Сервер и файловая система

- [ ] На Linux-сервере есть Python 3.12+, `git`, `systemd` и желательно `sqlite3`.
- [ ] Пользователь и группа `ahstep:ahstep` существуют.
- [ ] Code checkout расположен в `/opt/ahstep/law_monitor`.
- [ ] Runtime data находится в `/var/lib/ahstep-law-monitor`.
- [ ] Logs находятся в `/var/log/ahstep-law-monitor`.
- [ ] Production env расположен в `/etc/ahstep-law-monitor/law-monitor.env`.
- [ ] Права на env: `root:ahstep 0640`.
- [ ] В git checkout нет production `.env`.

## Env

- [ ] `LAW_MONITOR_DATA_DIR=/var/lib/ahstep-law-monitor/data`.
- [ ] `LAW_MONITOR_REPORTS_DIR=/var/lib/ahstep-law-monitor/reports`.
- [ ] `LAW_MONITOR_BACKUP_DIR=/var/lib/ahstep-law-monitor/backups`.
- [ ] `LAW_MONITOR_TMP_DIR=/var/lib/ahstep-law-monitor/tmp`.
- [ ] `LAW_MONITOR_LOG_DIR=/var/log/ahstep-law-monitor`.
- [ ] `LAW_MONITOR_DB_PATH=/var/lib/ahstep-law-monitor/data/law_monitor.db`.
- [ ] `LAW_MONITOR_LOG_FILE=/var/log/ahstep-law-monitor/app.log`.
- [ ] `LAW_MONITOR_TIMEZONE=Europe/Moscow`.
- [ ] Scheduler hour и interval согласованы.
- [ ] Telegram token и chat ID заданы только в env-файле.
- [ ] Telegram proxy задан только если он требуется.
- [ ] LLM enrichment выключен, если owner/IT явно не согласовали включение.

## Безопасные acceptance-команды

Запускать из checkout с загруженным production env:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python -m unittest discover tests'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py smoke-check'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py report --days 7'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py diagnostics --days 7'
```

Критерии приемки:

- [ ] Unit tests проходят.
- [ ] Smoke-check завершается успешно.
- [ ] Report generation создает отчет в `/var/lib/ahstep-law-monitor/reports`.
- [ ] Diagnostics output просмотрен.
- [ ] Source errors отсутствуют или объяснены.
- [ ] Stale sources отсутствуют, ожидаемы или назначены IT/dev на follow-up.

## Manual-only приемка

Эти действия выполняются owner/IT, потому что они могут отправлять Telegram
messages или запускать live scheduled behavior:

- [ ] `python main.py telegram-check` проходит успешно, тестовое сообщение видно.
- [ ] `python main.py notify-test` проходит успешно, если owner хочет отдельный notification test.
- [ ] Первый `python main.py run-scheduler --once` выполнен owner/IT.
- [ ] Первый daily digest пришел в целевой Telegram chat.
- [ ] Telegram `/status` работает.
- [ ] Telegram `/sources` работает.
- [ ] Telegram `/report` работает.

## Backup и rollback

- [ ] Initial DB backup создан через `scripts/backup_db.sh`.
- [ ] Backup path зафиксирован.
- [ ] Restore procedure просмотрена.
- [ ] Known-good git revision или release tag записан.
- [ ] Rollback owner определен.
- [ ] Services можно остановить и запустить через systemd.

Команда backup:

```bash
cd /opt/ahstep/law_monitor
sudo -u ahstep LAW_MONITOR_ENV_FILE=/etc/ahstep-law-monitor/law-monitor.env \
  bash scripts/backup_db.sh
```

Шаблон команды rollback:

```bash
sudo systemctl stop ahstep-scheduler.service ahstep-telegram-bot.service
cd /opt/ahstep/law_monitor
sudo -u ahstep git checkout <known-good-sha-or-tag>
sudo -u ahstep .venv/bin/python -m pip install -e .
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py smoke-check'
sudo systemctl start ahstep-scheduler.service ahstep-telegram-bot.service
```

## Список безопасных команд

Безопасно для AI/CI:

```bash
python -m unittest discover tests
python main.py smoke-check
python main.py report --days 7
python main.py diagnostics --days 7
python main.py ocr-check
python main.py ocr-queue --status pending
```

Только ручной запуск:

```bash
python main.py telegram-check
python main.py notify-test
python main.py run-scheduler --once
python main.py analyze --force
python main.py collect
python main.py enrich-docs
```
