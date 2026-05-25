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

- Убедиться, что daily digest пришел approved пользователям в личные Telegram-чаты.
- Убедиться, что daily digest содержит DOCX-вложение.
- Убедиться, что актуальный отчет есть в `/var/lib/ahstep-law-monitor/reports`.
- Проверить ручной `/report` в личном чате администратора, если bot включен и согласован ручной smoke.
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

1. Проверить `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_USER_IDS`, active allowlist и `TELEGRAM_PROXY_URL` в production env-файле.
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

## Управление доступом к личному Telegram-боту

Нормальная корпоративная модель — каждый approved сотрудник пишет боту в
личный чат, пользуется `/report`, `/refresh`, кнопками и получает daily digest
лично. Видимое меню команд содержит только `/start`, `/myid`, `/help`,
`/report`, `/refresh`; поиск доступен через нижнюю кнопку «🔎 Поиск». Срочные
пункты включены в основной отчёт, отдельная кнопка «Срочное» не показывается.
Общий групповой чат не требуется.

`TELEGRAM_CHAT_ID` остается optional legacy/fallback destination для scheduled
delivery, если бизнесу нужен дополнительный общий адрес. Он не является списком
личных пользователей. Личный доступ сотрудников к командам бота управляется по
числовому Telegram `user_id`.

Администраторы задаются в production env:

```env
TELEGRAM_ADMIN_USER_IDS=100000001,100000002
TELEGRAM_ALLOWED_USER_IDS=
TELEGRAM_URGENT_ALERTS_ENABLED=false
```

`TELEGRAM_ADMIN_USER_IDS` — аварийный механизм замены администратора. Если
ответственный сотрудник ушел из компании, IT меняет эту переменную в
`/etc/ahstep-law-monitor/law-monitor.env` и перезапускает только Telegram bot:

```bash
sudo systemctl restart ahstep-telegram-bot.service
```

Дальше администратор управляет пользователями из Telegram без изменения env:

```text
/admin_users
/admin_add_user <user_id>
/admin_remove_user <user_id>
/admin_whoami
```

Сотрудник, у которого доступа еще нет, пишет боту в личный чат и получает
профессиональное сообщение с Telegram ID. Этот ID нужно передать администратору
бота внутри компании. Команда `/myid` работает даже без доступа и показывает
`user_id`, `chat_id`, username и текущий статус доступа.

Не используйте username как идентификатор доступа: usernames могут меняться.
Добавлять нужно только числовой Telegram `user_id`.

Daily digest отправляется всем env-администраторам из `TELEGRAM_ADMIN_USER_IDS`,
всем активным пользователям allowlist и, если задан, в `TELEGRAM_CHAT_ID`.
Inactive/removed users и unknown users не получают отчеты и scheduled digest.
Если отправка одному получателю не прошла, scheduler логирует сбой безопасно и
продолжает отправку остальным.

Серверный отчёт сохраняется в `.md` как архивный формат. Telegram daily digest
и ручной `/report` отправляют редактируемый `.docx`-файл для business users;
`.txt` больше не является основным форматом Telegram-вложения.

## Production schedule и ночная тишина

Рекомендуемый режим:

```env
LAW_MONITOR_COLLECTION_TIMES=08:30,12:00,18:00
LAW_MONITOR_DAILY_REPORT_HOUR=9
TELEGRAM_URGENT_ALERTS_ENABLED=false
```

Scheduler и Telegram bot остаются отдельными systemd-сервисами. Scheduler
тихо собирает и анализирует данные в 08:30, 12:00 и 18:00; daily digest
отправляется в 09:00. Автоматические urgent-алерты на каждом цикле выключены,
но документы продолжают классифицироваться, сохраняться и попадать в daily
digest и `/report`.

`LAW_MONITOR_HOURLY_INTERVAL_MINUTES` сохранен как compatibility fallback,
если `LAW_MONITOR_COLLECTION_TIMES` пустой. Immediate urgent notifications
можно включить позднее через `TELEGRAM_URGENT_ALERTS_ENABLED=true`, если
business owner явно попросит такой режим.

## Обработка сбоев LLM

На первичном corporate handoff enrichment должен быть выключен, если owner/IT
явно не согласовали его включение:

```env
LLM_ENRICHMENT_ENABLED=false
LLM_DOCUMENT_ENRICHMENT_ENABLED=false
```

Если LLM enrichment позднее включен и появляются сбои:

- Проверить `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` и `LLM_TIMEOUT_SECONDS`.
- Если provider endpoint требует отдельный corporate proxy/VPN path, проверить `LLM_PROXY_URL`.
  `TELEGRAM_PROXY_URL` для LLM не используется и не дает доступ к Google/Gemini.
- Для Google Gemini OpenAI-compatible endpoint оставить `LLM_RESPONSE_FORMAT=auto`
  либо временно поставить `LLM_RESPONSE_FORMAT=none`, если provider возвращает
  400 на `response_format`.
- Проверить retry-параметры: `LLM_MAX_RETRIES=2`,
  `LLM_RETRY_BACKOFF_SECONDS=3`, `LLM_RETRY_MAX_BACKOFF_SECONDS=20`.
- Убедиться, что сервер может открыть approved provider endpoint.
- Проверить provider quota и rate limits.
- Если provider нестабилен, снизить `LLM_ENRICHMENT_LIMIT` до `1`-`2` или
  временно выключить `LLM_ENRICHMENT_ENABLED`.
- Не менять rule-based логику `action_level`.

`LLM_PROXY_URL` поддерживает `http`, `https`, `socks5` и `socks5h` proxy URLs.
Не вставляйте реальные credentials в tickets, screenshots или shell history.
Ошибки provider path должны сохранять API key и proxy credentials в redacted
виде.

Для первого корпоративного запуска с Google Gemini через proxy используйте
малый лимит enrichment (`2` или `3`) и повышайте его только после 2-3 стабильных
дней:

```env
LLM_ENRICHMENT_ENABLED=true
LLM_DOCUMENT_ENRICHMENT_ENABLED=true
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_MODEL=gemini-3.1-flash-lite
LLM_MODEL_FALLBACKS=gemini-2.5-flash,gemini-2.0-flash
LLM_RESPONSE_FORMAT=none
LLM_PROXY_URL=socks5h://USER:PASSWORD@HOST:PORT
LLM_MAX_DOCUMENT_CHARS=2000
LLM_TIMEOUT_SECONDS=300
LLM_ENRICHMENT_LIMIT=3
LLM_MAX_DOCS_PER_BATCH=10
LLM_REQUEST_DELAY_SECONDS=5
LLM_MAX_RETRIES=2
LLM_RETRY_BACKOFF_SECONDS=3
LLM_RETRY_MAX_BACKOFF_SECONDS=20
```

LLM failures должны деградировать только детализацию отчета. Collection,
deterministic `action_level`, report generation и Telegram sending должны
продолжать работать через fallback.

В нормальном production flow ручной `enrich-docs` не нужен. Если
`LLM_DOCUMENT_ENRICHMENT_ENABLED=true`, enrichment запускается автоматически
внутри `analyze` для новых документов, которые уже прошли deterministic rules
и имеют `requires_attention` / `watchlist`. Ручной `python main.py enrich-docs`
используется только как maintenance/debug operation:

- после изменения prompt version;
- после изменения схемы facts/cache;
- для точечной проверки owner/debug-сценариев.

Он не должен быть ежедневным production-шагом и не должен использоваться для
исправления `action_level`: видимость и срочность определяются rules/gates.

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

## Безопасное обновление кода в production

Выполняется при плановых обновлениях, после смены зависимостей и после
изменений в env, которые требуют peер-проверки smoke-check.

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

`telegram-check` отправит тестовое сообщение в Telegram, выполняйте его только
когда IT и владелец проекта готовы это увидеть.

## Базовый rollback

1. Остановить оба сервиса.
2. Определить последнюю стабильную git-ревизию.
3. Восстановить код, например `git checkout <known-good-sha>`.
4. Переустановить зависимости: `.venv/bin/python -m pip install -e .`.
5. Восстановить DB только если неудачное обновление изменило состояние БД и
   нужен предыдущий state.
6. Выполнить `init-db`, `smoke-check`, `telegram-check`.
7. Запустить сервисы, посмотреть логи.

## Сетевой caveat по источникам

Поведение государственных и региональных источников может отличаться в
зависимости от server network, VPN, DNS, TLS inspection и proxy routing.
Проверяйте доступ к источникам с целевой сети сервера до go-live и после
изменений сетевой политики.

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
