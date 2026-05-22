# Краткий handoff для IT — публикация AHSTEP law_monitor

Документ для корпоративного IT и системных администраторов. Объясняет, что
такое `law_monitor`, какие требования к серверу, какие сервисы запускать и
что делать при основных сбоях.

## 1. Что это за проект

AHSTEP `law_monitor` — backend-система GR-мониторинга для агрохолдинга STEP.
Она:

- собирает публичные нормативные акты, новости и меры господдержки из
  государственных и отраслевых источников;
- классифицирует документы по бизнес-приоритету (`requires_attention`,
  `watchlist`, `background`, `irrelevant`);
- генерирует ежедневный markdown-отчёт для GR-команды;
- отправляет ежедневную сводку approved пользователям в личные Telegram-чаты
  (короткое сообщение + полный отчёт во вложении `.txt`);
- хранит историю в локальной SQLite БД.

Классификация документов выполняется детерминированными правилами.
LLM (Google Gemma через OpenAI-совместимый endpoint) опционально обогащает
карточки документов краткими бизнес-выводами. LLM не управляет
классификацией: если он недоступен, отчёт всё равно строится.

## 2. Чем `law_monitor` НЕ является

- Это **не публичный сайт**. Inbound HTTP-трафик не нужен. Открывать сервер
  наружу не требуется.
- Это **не внутренняя обработка корпоративных данных AHSTEP**. Через LLM
  отправляется только текст из публичных источников.
- Это **не data-pipeline на постоянное хранение конфиденциальных данных**.
  В БД сохраняются ссылки на публичные документы и их извлечённый текст.

Использует только публичные источники РФ и Telegram Bot API. Конфиденциальные
данные через систему не проходят.

## 3. Требования к серверу

| Параметр | Значение |
| --- | --- |
| OS | Ubuntu Server 22.04 LTS или 24.04 LTS |
| Python | 3.12+ |
| Дополнительно | `git`, `systemd`, желательно `sqlite3` |
| CPU | 2-4 vCPU |
| RAM | 4-8 GB |
| Диск | 40+ GB SSD (рост в основном за счёт логов и backups БД) |
| Сеть | Исходящий доступ в интернет (HTTPS) |
| Доступ | SSH; статический IP опционально |
| Inbound | Не требуется |

## 4. Необходимый сетевой доступ

Исходящий доступ нужен к:

- **Публичные нормативные источники РФ** (HTTP/HTTPS):
  - `government.ru`, `regulation.gov.ru`
  - `mcx.gov.ru`, `mcx.donland.ru`, `mshsk.ru`, `msh.krasnodar.ru`,
    `admkrai.krasnodar.ru`
  - `pravo.donland.ru`, `pravo.stavregion.ru`
  - `publication.pravo.gov.ru`
  - `promote.budget.gov.ru`
  - `gisp.gov.ru`
  - `zol.ru`
- **Telegram Bot API**: `api.telegram.org`.
  Если прямой доступ закрыт — через `TELEGRAM_PROXY_URL` (http / https /
  socks5 / socks5h). Telegram-прокси предназначен только для Telegram Bot API.
- **LLM provider** (если включён enrichment): по умолчанию
  `generativelanguage.googleapis.com` (Google Gemma через
  OpenAI-совместимый endpoint). На российском сервере доступ требует
  отдельного маршрута через `LLM_PROXY_URL` (отдельный от Telegram-прокси).

## 5. Runtime-директории

```text
/opt/ahstep/law_monitor                    # git checkout + .venv
/var/lib/ahstep-law-monitor/data           # SQLite БД и runtime state
/var/lib/ahstep-law-monitor/reports        # сгенерированные отчёты
/var/lib/ahstep-law-monitor/backups        # backups БД
/var/lib/ahstep-law-monitor/tmp            # временные файлы
/var/log/ahstep-law-monitor                # ротация логов
/etc/ahstep-law-monitor/law-monitor.env    # production env (root:ahstep 0640)
```

Пользователь и группа: `ahstep:ahstep`. Все runtime-данные хранятся **вне**
git-checkout. Подробности — [CORPORATE_DEPLOYMENT.md](CORPORATE_DEPLOYMENT.md).

## 6. Обязательные env переменные

Production env-файл располагается только здесь:

```text
/etc/ahstep-law-monitor/law-monitor.env
```

Права: `root:ahstep 0640`. Не коммитить в git, не пересылать в тикетах.

Минимальный шаблон (без реальных значений):

```env
# Пути (production)
LAW_MONITOR_DATA_DIR=/var/lib/ahstep-law-monitor/data
LAW_MONITOR_REPORTS_DIR=/var/lib/ahstep-law-monitor/reports
LAW_MONITOR_BACKUP_DIR=/var/lib/ahstep-law-monitor/backups
LAW_MONITOR_TMP_DIR=/var/lib/ahstep-law-monitor/tmp
LAW_MONITOR_LOG_DIR=/var/log/ahstep-law-monitor
LAW_MONITOR_DB_PATH=/var/lib/ahstep-law-monitor/data/law_monitor.db
LAW_MONITOR_LOG_FILE=/var/log/ahstep-law-monitor/app.log

# Scheduler
LAW_MONITOR_TIMEZONE=Europe/Moscow
LAW_MONITOR_DAILY_REPORT_HOUR=9
LAW_MONITOR_COLLECTION_TIMES=08:30,12:00,18:00
LAW_MONITOR_HOURLY_INTERVAL_MINUTES=360

# Telegram
TELEGRAM_BOT_TOKEN=<выдаёт владелец проекта>
TELEGRAM_CHAT_ID=
TELEGRAM_ADMIN_USER_IDS=<числовой Telegram user_id администратора>
TELEGRAM_ALLOWED_USER_IDS=
TELEGRAM_URGENT_ALERTS_ENABLED=false
TELEGRAM_PROXY_URL=

# LLM (стартовое значение — выключен)
LLM_ENRICHMENT_ENABLED=false
LLM_DOCUMENT_ENRICHMENT_ENABLED=false
LLM_PROVIDER=mock
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
LLM_TIMEOUT_SECONDS=60
LLM_PROXY_URL=
LLM_RESPONSE_FORMAT=auto
LLM_MAX_RETRIES=2
LLM_RETRY_BACKOFF_SECONDS=2
LLM_RETRY_MAX_BACKOFF_SECONDS=10
LLM_MAX_DOCUMENT_CHARS=12000
LLM_ENRICHMENT_LIMIT=20
```

Полная таблица переменных — [PRODUCTION_ENV_TEMPLATE.md](PRODUCTION_ENV_TEMPLATE.md).

Нормальная корпоративная UX-модель — approved сотрудники общаются с ботом в
личном чате. Общий групповой чат не требуется. Daily digest отправляется всем
env-администраторам из `TELEGRAM_ADMIN_USER_IDS`, всем активным пользователям
SQLite allowlist и, если задан, в optional legacy/fallback `TELEGRAM_CHAT_ID`.

`TELEGRAM_CHAT_ID` не является списком пользователей и может оставаться пустым,
если доставка нужна только в личные чаты. Администраторы задаются в
`TELEGRAM_ADMIN_USER_IDS`; это аварийный механизм замены администратора, который
IT меняет в env с перезапуском `ahstep-telegram-bot.service`. Обычных
пользователей администратор добавляет и удаляет в Telegram командами
`/admin_add_user <user_id>` и `/admin_remove_user <user_id>`. Unknown users в
личном чате получают свой Telegram ID и передают его администратору.

Рекомендуемый production schedule: тихий collect/analyze в 08:30, 12:00 и
18:00, daily digest в 09:00, без автоматических urgent-алертов ночью или на
каждом цикле. `LAW_MONITOR_HOURLY_INTERVAL_MINUTES` остается fallback, если
`LAW_MONITOR_COLLECTION_TIMES` пустой.

## 7. Рекомендованные production-значения для LLM (Google/Gemma)

Включать только после согласования с владельцем и IT. Сетевой маршрут до
`generativelanguage.googleapis.com` — отдельный, через `LLM_PROXY_URL`.

```env
LLM_ENRICHMENT_ENABLED=true
LLM_DOCUMENT_ENRICHMENT_ENABLED=true
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_MODEL=gemma-4-31b-it
LLM_RESPONSE_FORMAT=none
LLM_PROXY_URL=socks5h://USER:PASSWORD@HOST:PORT
LLM_MAX_DOCUMENT_CHARS=1000
LLM_TIMEOUT_SECONDS=300
LLM_ENRICHMENT_LIMIT=3
LLM_MAX_RETRIES=2
LLM_RETRY_BACKOFF_SECONDS=2
LLM_RETRY_MAX_BACKOFF_SECONDS=10
```

`TELEGRAM_PROXY_URL` и `LLM_PROXY_URL` — независимые переменные. Telegram
proxy не даёт доступа к Google и наоборот.

Для SOCKS-проксей требуется extras `requests[socks]` (уже в
[pyproject.toml](../pyproject.toml)).

## 8. Сервисы для запуска

Шаблоны находятся в `deploy/systemd/`:

```text
/etc/systemd/system/ahstep-scheduler.service
/etc/systemd/system/ahstep-telegram-bot.service
```

**Важно:** scheduler и Telegram-бот — это **разные процессы и сервисы**.
Они не должны запускаться одной командой и не должны жить в одном процессе.

Установка:

```bash
sudo cp deploy/systemd/ahstep-scheduler.service /etc/systemd/system/
sudo cp deploy/systemd/ahstep-telegram-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ahstep-telegram-bot.service
sudo systemctl enable --now ahstep-scheduler.service
```

Перезапуск после изменения env:

```bash
sudo systemctl restart ahstep-scheduler.service ahstep-telegram-bot.service
```

## 9. Безопасные команды для проверки

Эти команды не отправляют сообщения в Telegram и не дёргают LLM/сеть live:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py smoke-check'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py report --days 7'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py diagnostics --days 7'
```

Запускать без боязни. Используются IT и владельцем как health-check.

## 10. Команды только для ручного запуска

Эти команды отправляют сообщения в Telegram, обращаются к live-источникам
или к LLM. Запускает только владелец проекта или IT в согласованное окно:

```bash
sudo -u ahstep bash -lc '... .venv/bin/python main.py telegram-check'
sudo -u ahstep bash -lc '... .venv/bin/python main.py notify-test'
sudo -u ahstep bash -lc '... .venv/bin/python main.py run-scheduler --once --force-daily-digest'
```

`run-scheduler --once` — это один ручной цикл (collect → analyze → optional
urgent notify → daily digest). При `TELEGRAM_URGENT_ALERTS_ENABLED=false`
urgent notify пропускается, но collect/analyze и daily digest выполняются.
Команда используется для go-live теста и для разовой реактивации digest, если
он не ушёл в назначенный час.

## 11. Backup и restore

Создать backup БД:

```bash
cd /opt/ahstep/law_monitor
sudo -u ahstep LAW_MONITOR_ENV_FILE=/etc/ahstep-law-monitor/law-monitor.env \
  bash scripts/backup_db.sh
```

Backup сохраняется в `/var/lib/ahstep-law-monitor/backups/law_monitor_YYYYMMDD_HHMMSS.db`.
По возможности используется `sqlite3 .backup` (атомарный). Если sqlite3 не
установлен — copy-fallback (нужно сначала остановить сервисы).

Restore:

```bash
sudo systemctl stop ahstep-scheduler.service ahstep-telegram-bot.service
cd /opt/ahstep/law_monitor
sudo -u ahstep LAW_MONITOR_ENV_FILE=/etc/ahstep-law-monitor/law-monitor.env \
  bash scripts/restore_db.sh /var/lib/ahstep-law-monitor/backups/law_monitor_YYYYMMDD_HHMMSS.db --confirm
sudo -u ahstep bash -lc '... .venv/bin/python main.py smoke-check'
sudo systemctl start ahstep-scheduler.service ahstep-telegram-bot.service
```

Restore-скрипт сам блокируется, если сервисы активны или есть writer lock.
Подробности — [BACKUP_RESTORE.md](BACKUP_RESTORE.md).

## 12. Чеклист первого запуска

1. Создан системный пользователь `ahstep:ahstep`.
2. Созданы директории `/opt/ahstep`, `/var/lib/ahstep-law-monitor/*`,
   `/var/log/ahstep-law-monitor`, `/etc/ahstep-law-monitor`.
3. Клонирован репозиторий в `/opt/ahstep/law_monitor`.
4. Создан virtualenv: `python3.12 -m venv .venv`, `pip install -e .`.
5. Создан `/etc/ahstep-law-monitor/law-monitor.env` (root:ahstep 0640) с
   реальными значениями.
6. Выполнено `python main.py init-db`.
7. Прошёл `python -m unittest discover tests`.
8. Прошёл `python main.py smoke-check`.
9. Прошёл `python main.py report --days 7`.
10. IT/владелец вручную запустили `telegram-check` — пришло тестовое сообщение.
11. IT/владелец вручную запустили `run-scheduler --once` — успешно.
12. Включены systemd сервисы.
13. На следующее утро в `LAW_MONITOR_DAILY_REPORT_HOUR` daily digest пришёл в личные чаты approved users/admins.
14. Создан первый production backup БД.

## 13. Что делать при сбоях

### Telegram не отправляется

1. Проверить переменные `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_USER_IDS`,
   active allowlist, optional `TELEGRAM_CHAT_ID` и `TELEGRAM_PROXY_URL` в env-файле.
2. Проверить proxy credentials и доступ до `api.telegram.org` с сервера.
3. Перезапустить Telegram-бот: `sudo systemctl restart ahstep-telegram-bot.service`.
4. Запустить вручную `python main.py telegram-check` и посмотреть результат.
5. Посмотреть свежие логи:
   `sudo journalctl -u ahstep-telegram-bot.service -n 200 --no-pager`.

Token в логах редактируется автоматически — секрет не утечёт через journald.

### LLM падает (если включён)

1. Проверить `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`.
2. Проверить `LLM_PROXY_URL` (доступ до `generativelanguage.googleapis.com`).
   `TELEGRAM_PROXY_URL` для LLM не используется.
3. Для Google/Gemma поставить `LLM_RESPONSE_FORMAT=none`, если provider
   возвращает 400 на `response_format`.
4. Снизить `LLM_ENRICHMENT_LIMIT=1` или временно `LLM_ENRICHMENT_ENABLED=false`.
5. Отчёт всё равно будет выходить: LLM-сбой деградирует только детализацию
   карточек документов, classification и доставка остаются.

API-ключ и proxy credentials в логах редактируются — секрет не утечёт.

### Источник не отдаёт документы

1. Запустить `python main.py diagnostics --days 7` и посмотреть блок
   "Recent source errors" и "Source freshness".
2. Проверить, открывается ли сайт источника с самого сервера
   (`curl -I` или `wget`). Российские гос-источники могут быть нестабильны.
3. Если несколько источников — проверить корпоративный DNS/proxy/TLS
   inspection. Возможно, IT изменил сетевую политику.
4. Сбой одного источника не останавливает остальные. Отчёт продолжит
   приходить с пометкой о недоступном источнике в блоке "На что обратить
   внимание по системе".

### Daily digest не пришёл

1. Проверить, что scheduler работает:
   `sudo systemctl status ahstep-scheduler.service`.
2. Посмотреть свежие логи:
   `sudo journalctl -u ahstep-scheduler.service --since "24 hours ago"`.
3. Проверить heartbeat-файл:
   `ls -la /var/lib/ahstep-law-monitor/data/last_success.daily`.
4. Если scheduler не выполнил daily cycle — можно повторно запустить:
   ```bash
   sudo -u ahstep bash -lc '... .venv/bin/python main.py run-scheduler --once --force-daily-digest'
   ```
5. Если digest был отправлен, но получатель не получил его — см. раздел про сбои
   Telegram.

## 14. Важное напоминание

**Scheduler и Telegram-бот — это два разных процесса и два разных systemd
сервиса.** Не запускать оба в одном process group. Не запускать сторонним cron
или tmux/screen параллельно: применяется writer-lock, лишний процесс упадёт
или подождёт.

Автоматические urgent-алерты можно включить позднее через
`TELEGRAM_URGENT_ALERTS_ENABLED=true`, если business owner попросит immediate
notifications. По умолчанию production schedule тихий: urgent/requires_attention
остаются в daily digest, `/urgent` и `/report`.

Полная инструкция корпоративного развёртывания —
[CORPORATE_DEPLOYMENT.md](CORPORATE_DEPLOYMENT.md). Операторская рутина —
[OPERATOR_RUNBOOK.md](OPERATOR_RUNBOOK.md). Чеклист приёмки —
[DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md). Заметки по безопасности —
[SECURITY_NOTES.md](SECURITY_NOTES.md).
