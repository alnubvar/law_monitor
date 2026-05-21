# Операторский runbook

Этот runbook предназначен для ежедневной эксплуатации AHSTEP `law_monitor` на
корпоративном Linux-сервере.

Ожидаемые пути:

```text
/opt/ahstep/law_monitor
/etc/ahstep-law-monitor/law-monitor.env
/var/lib/ahstep-law-monitor/data/law_monitor.db
/var/lib/ahstep-law-monitor/reports
/var/lib/ahstep-law-monitor/backups
/var/log/ahstep-law-monitor/app.log
```

## Ежедневные проверки

```bash
sudo systemctl status ahstep-scheduler.service
sudo systemctl status ahstep-telegram-bot.service
sudo journalctl -u ahstep-scheduler.service -n 50 --no-pager
sudo journalctl -u ahstep-telegram-bot.service -n 50 --no-pager
sudo tail -n 100 /var/log/ahstep-law-monitor/app.log
```

Бизнес-проверки:

- Убедиться, что daily digest пришел в ожидаемый Telegram chat.
- Убедиться, что актуальный отчет есть в `/var/lib/ahstep-law-monitor/reports`.
- Проверить Telegram `/status` и `/sources`, если bot включен.
- Проверить, показывает ли diagnostics stale key sources или recent source errors.

Безопасная read-only diagnostics:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py diagnostics --days 7'
```

## Еженедельные проверки

- Убедиться, что backups создаются в `/var/lib/ahstep-law-monitor/backups`.
- Запустить безопасную генерацию отчета по существующим данным БД.
- Проверить OCR queue status, если используется OCR triage.
- Проверить disk usage для `/var/lib/ahstep-law-monitor` и `/var/log/ahstep-law-monitor`.
- Просмотреть предупреждения о свежести источников и согласовать, какие stale sources ожидаемы.

Команды:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py report --days 7'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py ocr-check'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py ocr-queue --status pending'
df -h /var/lib/ahstep-law-monitor /var/log/ahstep-law-monitor
```

## Обработка сбоев источников

Симптомы:

- `diagnostics --days 7` показывает recent source errors.
- Telegram `/sources` показывает сбои источников.
- В отчетах меньше ожидаемых материалов.
- В логах есть timeout, connection, proxy, HTTP или extraction errors.

Первичные действия:

1. Проверить, доступен ли сайт источника с корпоративного сервера.
2. Проверить изменения корпоративных DNS/proxy/TLS inspection.
3. Проверить категории ошибок через `diagnostics --days 7`.
4. Не менять parser logic во время эксплуатации.
5. Если один публичный источник недоступен, зафиксировать это и продолжить мониторинг остальных источников.

Полезные команды:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py diagnostics --days 7'
sudo journalctl -u ahstep-scheduler.service -n 200 --no-pager
sudo tail -n 200 /var/log/ahstep-law-monitor/app.log
```

## Выявление stale sources

`diagnostics --days 7` включает блок operational freshness и предупреждения о
stale source. Такое предупреждение означает, что приложение давно не видело
успешный collect для ключевого источника в ожидаемом окне.

Действия оператора:

- Если на сайте источника нет новых публикаций, отметить это как ожидаемое состояние в handoff log.
- Если сайт вручную доступен, но приложение падает, эскалировать на parser/source investigation.
- Если сервер не может открыть источник, эскалировать в IT/network.
- Если stale сразу много источников, проверить scheduler status, DNS, proxy и outbound HTTPS.

## Обработка сбоев Telegram

Симптомы:

- Daily digest не приходит в Telegram.
- `telegram-check` падает во время ручной IT-проверки.
- Telegram bot service постоянно перезапускается.

Первичные действия:

1. Проверить `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` и `TELEGRAM_PROXY_URL` в production env-файле.
2. Проверить proxy credentials и firewall rules.
3. Перезапустить Telegram bot после изменений env.
4. Запускать ручную проверку Telegram только когда owner/IT ожидают тестовое сообщение.

Команды:

```bash
sudo systemctl status ahstep-telegram-bot.service
sudo journalctl -u ahstep-telegram-bot.service -n 200 --no-pager
sudo systemctl restart ahstep-telegram-bot.service
```

Manual-only проверки Telegram:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py telegram-check'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py notify-test'
```

## Обработка сбоев LLM

На первичном corporate handoff enrichment должен быть выключен, если owner/IT
явно не согласовали его включение:

```env
LLM_ENRICHMENT_ENABLED=false
LLM_DOCUMENT_ENRICHMENT_ENABLED=false
```

Если LLM enrichment позднее включен и появляются сбои:

- Проверить `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` и `LLM_TIMEOUT_SECONDS`.
- Убедиться, что сервер может открыть approved provider endpoint.
- Проверить provider quota и rate limits.
- Выключить enrichment, если он блокирует эксплуатацию.
- Не менять rule-based логику `action_level`.

Отключение enrichment:

```bash
sudoedit /etc/ahstep-law-monitor/law-monitor.env
sudo systemctl restart ahstep-scheduler.service
```

## Backups

Создать DB backup:

```bash
cd /opt/ahstep/law_monitor
sudo -u ahstep LAW_MONITOR_ENV_FILE=/etc/ahstep-law-monitor/law-monitor.env \
  bash scripts/backup_db.sh
```

По возможности используйте `sqlite3` на сервере. Если `sqlite3` недоступен,
скрипт использует copy fallback; перед использованием fallback backups
остановите сервисы.

## Restore

Restore является ручной maintenance-операцией. Сначала остановите сервисы:

```bash
sudo systemctl stop ahstep-scheduler.service ahstep-telegram-bot.service
```

Запустить restore:

```bash
cd /opt/ahstep/law_monitor
sudo -u ahstep LAW_MONITOR_ENV_FILE=/etc/ahstep-law-monitor/law-monitor.env \
  bash scripts/restore_db.sh /var/lib/ahstep-law-monitor/backups/law_monitor_YYYYMMDD_HHMMSS.db --confirm
```

Проверить и перезапустить:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py smoke-check'
sudo systemctl start ahstep-scheduler.service ahstep-telegram-bot.service
```

Restore script создает pre-restore backup перед перезаписью БД. Он также
отказывается выполняться, если systemd services активны или существует writer
lock.

## Безопасные команды для AI или CI

Эти команды не отправляют Telegram messages и не выполняют live collect/analyze
источников:

```bash
python -m unittest discover tests
python main.py smoke-check
python main.py report --days 7
python main.py diagnostics --days 7
python main.py ocr-check
python main.py ocr-queue --status pending
```

## Manual-only команды

Эти команды могут отправлять Telegram messages, обращаться к live sources,
изменять состояние анализа или выполнять scheduled production behavior.
Запускать только владельцу проекта или IT:

```bash
python main.py telegram-check
python main.py notify-test
python main.py run-scheduler --once
python main.py analyze --force
python main.py collect
python main.py enrich-docs
```

Также считайте `python main.py run`, `python main.py run-scheduler`, `python
main.py run-telegram-bot`, `python main.py ocr-run` и `python main.py
ocr-backfill` ручными operational commands.

## Просмотр свежих логов

```bash
sudo journalctl -u ahstep-scheduler.service -n 100 --no-pager
sudo journalctl -u ahstep-telegram-bot.service -n 100 --no-pager
sudo tail -n 100 /var/log/ahstep-law-monitor/app.log
sudo tail -F /var/log/ahstep-law-monitor/app.log
```

За более длинный период:

```bash
sudo journalctl -u ahstep-scheduler.service --since "24 hours ago" --no-pager
sudo journalctl -u ahstep-telegram-bot.service --since "24 hours ago" --no-pager
```

## Правила перезапуска сервисов

Перезапускайте оба сервиса после изменений env, которые влияют на paths,
Telegram, scheduler timing, source networking или LLM settings:

```bash
sudo systemctl restart ahstep-scheduler.service ahstep-telegram-bot.service
```

Перезапускайте только Telegram bot для bot-only изменений, если env scheduler
не менялся:

```bash
sudo systemctl restart ahstep-telegram-bot.service
```
