# Production env template и чеклист

Production env-файл должен находиться только здесь:

```text
/etc/ahstep-law-monitor/law-monitor.env
```

Права:

```bash
sudo chown root:ahstep /etc/ahstep-law-monitor/law-monitor.env
sudo chmod 0640 /etc/ahstep-law-monitor/law-monitor.env
```

Не коммитьте реальные значения. Не размещайте production secrets в `.env`,
`.env.example`, README, issue comments, shell snippets или чатах.

## Обязательные production values

```env
# Persistent paths outside git
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
TELEGRAM_BOT_TOKEN=<set-by-IT>
TELEGRAM_CHAT_ID=
TELEGRAM_ADMIN_USER_IDS=<numeric-user-id>[,<numeric-user-id>]
TELEGRAM_ALLOWED_USER_IDS=
TELEGRAM_URGENT_ALERTS_ENABLED=false
TELEGRAM_PROXY_URL=

# LLM document enrichment
LLM_ENRICHMENT_ENABLED=false
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

## Рекомендуемые дополнительные значения

Эти параметры уже поддерживаются приложением. В production их полезно задавать
явно, если IT нужен предсказуемый режим работы:

```env
TELEGRAM_API_TIMEOUT=30
TELEGRAM_OPERATOR_CHAT_ID=
LAW_MONITOR_LOG_LEVEL=INFO
LAW_MONITOR_LOG_MAX_BYTES=5242880
LAW_MONITOR_LOG_BACKUP_COUNT=5
LAW_MONITOR_REQUEST_TIMEOUT=30
LAW_MONITOR_REQUEST_RETRIES=2
LAW_MONITOR_REQUEST_BACKOFF_FACTOR=1.0
LAW_MONITOR_USER_AGENT=law-monitor-mvp/0.1
LAW_MONITOR_OCR_ENABLED=false
LAW_MONITOR_OCR_LANGUAGE=rus+eng
LAW_MONITOR_OCR_MAX_PAGES=5
LAW_MONITOR_OCR_TIMEOUT=120
LAW_MONITOR_OCR_TESSDATA_PATH=
LLM_DOCUMENT_ENRICHMENT_ENABLED=false
```

`LLM_DOCUMENT_ENRICHMENT_ENABLED` является предпочтительным явным флагом для
document enrichment. `LLM_ENRICHMENT_ENABLED` сохранен как compatibility alias и
включен выше, потому что существующие handoff-чеклисты могут на него ссылаться.

## Рекомендуемый preset для Google/Gemma через LLM proxy

Используйте этот preset только после согласования owner/IT и только в реальном
production env-файле. Для первого корпоративного запуска держите
`LLM_ENRICHMENT_LIMIT=2` или `3`; увеличивайте лимит только после 2-3 стабильных
дней без quota/timeout/provider ошибок.

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

`TELEGRAM_PROXY_URL` и `LLM_PROXY_URL` - разные настройки. Telegram proxy
используется только для Telegram Bot API и не дает доступ к Google/Gemini.
Для Google/Gemma нужен отдельный `LLM_PROXY_URL` или другой согласованный
сетевой маршрут.

`TELEGRAM_OPERATOR_CHAT_ID` — отдельный chat ID для оперативных уведомлений
(например, об отсутствии сбора при катастрофическом сбое всех источников).
Если значение пустое, оперативные уведомления будут отправлены в основной
`TELEGRAM_CHAT_ID` с префиксом «⚙️ Системное уведомление». На production
рекомендуется выделить отдельный chat ID, чтобы оперативные алерты не смешивались
с GR-сводками для пользователей.

Рекомендуемый production schedule:

```env
LAW_MONITOR_COLLECTION_TIMES=08:30,12:00,18:00
LAW_MONITOR_DAILY_REPORT_HOUR=9
TELEGRAM_URGENT_ALERTS_ENABLED=false
```

При таком режиме scheduler выполняет тихий collect/analyze в 08:30, 12:00 и
18:00, а основная ежедневная сводка уходит в 09:00. `LAW_MONITOR_HOURLY_INTERVAL_MINUTES`
сохраняется как compatibility fallback, если `LAW_MONITOR_COLLECTION_TIMES`
пустой. Срочные документы при этом не теряются: они классифицируются,
сохраняются и попадают в ежедневный отчет и команды бота.

`TELEGRAM_ADMIN_USER_IDS` — аварийный механизм замены администратора бота.
Здесь задаются числовые Telegram user ID сотрудников, которые могут управлять
allowlist личных пользователей. Если владелец проекта меняется, IT обновляет
эту переменную в production env и перезапускает `ahstep-telegram-bot.service`.

`TELEGRAM_ALLOWED_USER_IDS` — необязательный первичный seed allowlist для личных
диалогов с ботом. После запуска активных пользователей следует добавлять и
удалять командами администратора в Telegram, без изменения env и без рестарта.
Нормальная корпоративная UX-модель — каждый approved user пишет боту в личном
чате, пользуется `/report`, `/refresh`, кнопками и получает daily digest лично.
Видимое меню команд содержит только `/start`, `/myid`, `/help`, `/report`,
`/refresh`; поиск доступен через нижнюю кнопку «🔎 Поиск». Срочные пункты
включены в основной отчёт, отдельная кнопка «Срочное» не показывается. Общий
групповой чат не требуется.

Отчёт на сервере сохраняется как `.md` — это архивный формат. Telegram daily
digest и ручной `/report` отправляют редактируемое вложение `.docx` для
business users; `.txt` больше не является основным Telegram-форматом.

Daily digest отправляется всем env-администраторам из `TELEGRAM_ADMIN_USER_IDS`,
всем активным пользователям из SQLite allowlist и, если задан, в legacy/fallback
`TELEGRAM_CHAT_ID`. `TELEGRAM_CHAT_ID` оставлен для совместимости и опциональной
дополнительной scheduled delivery; это не обязательный общий чат.

`TELEGRAM_URGENT_ALERTS_ENABLED=false` отключает автоматические urgent-алерты
на каждом collect/analyze цикле. Позднее их можно снова включить, если business
owner попросит immediate notifications.

## Чеклист

- [ ] Env-файл существует по пути `/etc/ahstep-law-monitor/law-monitor.env`.
- [ ] Owner/group/mode: `root:ahstep 0640`.
- [ ] Все пути `LAW_MONITOR_*_DIR` указывают на `/var/lib` или `/var/log`, а не на repo.
- [ ] `LAW_MONITOR_DB_PATH` указывает на `/var/lib/ahstep-law-monitor/data/law_monitor.db`.
- [ ] `LAW_MONITOR_LOG_FILE` указывает на `/var/log/ahstep-law-monitor/app.log`.
- [ ] `LAW_MONITOR_TIMEZONE` равен `Europe/Moscow`, если IT явно не выбрал другой timezone.
- [ ] `LAW_MONITOR_COLLECTION_TIMES=08:30,12:00,18:00`.
- [ ] `LAW_MONITOR_DAILY_REPORT_HOUR=9` или согласован с business owner.
- [ ] `LAW_MONITOR_HOURLY_INTERVAL_MINUTES` оставлен как fallback, если collection times пустой.
- [ ] Telegram token задан только в production env-файле.
- [ ] `TELEGRAM_CHAT_ID` пустой или содержит legacy/fallback scheduled destination.
- [ ] `TELEGRAM_ADMIN_USER_IDS` содержит хотя бы одного актуального администратора бота.
- [ ] `TELEGRAM_ALLOWED_USER_IDS` пустой или содержит только первичных approved user IDs.
- [ ] `TELEGRAM_URGENT_ALERTS_ENABLED=false` для тихого production schedule.
- [ ] Видимое меню Telegram: `/start`, `/myid`, `/help`, `/report`, `/refresh`; поиск только через кнопку.
- [ ] Telegram report attachments приходят как `.docx`, а серверный архив отчётов остаётся `.md`.
- [ ] `TELEGRAM_PROXY_URL` пустой, если доступ к Telegram не требует proxy.
- [ ] `TELEGRAM_OPERATOR_CHAT_ID` задан, если нужен отдельный канал для оперативных алертов (рекомендуется на production).
- [ ] `LLM_ENRICHMENT_ENABLED=false` для первичного корпоративного handoff, если owner/IT явно не одобрили LLM enrichment.
- [ ] Если LLM включен, `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY` и `LLM_MODEL` согласованы с IT.
- [ ] `LLM_PROXY_URL` пустой, если LLM provider endpoint не требует отдельный proxy/VPN path.
- [ ] Если Google/Gemma включен через proxy, `LLM_MAX_RETRIES=2`, `LLM_RETRY_BACKOFF_SECONDS=2`, `LLM_RETRY_MAX_BACKOFF_SECONDS=10`.
- [ ] Для первого запуска с LLM задан `LLM_ENRICHMENT_LIMIT=2` или `3`.
- [ ] В git checkout нет production `.env`.
- [ ] `.gitignore` по-прежнему исключает `.env`, `.env.*`, DBs, logs, data и generated reports.

## Примечание по LLM provider

Если LLM enrichment включен, приложение отправляет только текст, извлеченный из
публичных нормативных/source-документов, для формирования document-card
enrichment. Этот режим нельзя использовать для конфиденциальных внутренних
документов AHSTEP, если project owner и IT не согласовали provider, условия
обработки данных и сетевой маршрут.

Если provider endpoint требует отдельный сетевой маршрут, задайте
`LLM_PROXY_URL`. Он применяется только к OpenAI-compatible LLM provider
requests и не наследует `TELEGRAM_PROXY_URL`. Поддерживаются схемы `http`,
`https`, `socks5` и `socks5h`; SOCKS требует установленной зависимости
`requests[socks]`. Не размещайте proxy credentials в git, tickets или логах.

LLM enrichment является только обогащением отчета. Сбой provider, timeout,
429/5xx или proxy error должны ухудшать детализацию карточек, но не должны
останавливать collection, deterministic classification, report generation или
Telegram sending. Если provider нестабилен, снизьте `LLM_ENRICHMENT_LIMIT` до
`1`-`2` или временно установите `LLM_ENRICHMENT_ENABLED=false`.

## Примечания по proxy и CA

`TELEGRAM_PROXY_URL` применяется только к Telegram Bot API. Он не проксирует
запросы к государственным или региональным источникам и не используется для
LLM provider requests.

Если корпоративная сеть требует proxy для источников, IT может задать
стандартные env vars `HTTPS_PROXY`, `HTTP_PROXY` и `NO_PROXY` в production
env-файле.

Некоторые источники сейчас используют `verify_ssl: false` в `config/sources.yaml`,
потому что TLS-поведение публичных источников нестабильно. По возможности
предпочитайте настройку corporate CA bundle вместо добавления новых отключений
TLS verification. Не меняйте SSL-политику источников во время deployment без
явной необходимости.
