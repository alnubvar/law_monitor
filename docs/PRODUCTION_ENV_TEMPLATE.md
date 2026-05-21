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
LAW_MONITOR_HOURLY_INTERVAL_MINUTES=360

# Telegram
TELEGRAM_BOT_TOKEN=<set-by-IT>
TELEGRAM_CHAT_ID=<set-by-IT>
TELEGRAM_PROXY_URL=

# LLM document enrichment
LLM_ENRICHMENT_ENABLED=false
LLM_PROVIDER=mock
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
LLM_TIMEOUT_SECONDS=60
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

`TELEGRAM_OPERATOR_CHAT_ID` — отдельный chat ID для оперативных уведомлений
(например, об отсутствии сбора при катастрофическом сбое всех источников).
Если значение пустое, оперативные уведомления будут отправлены в основной
`TELEGRAM_CHAT_ID` с префиксом «⚙️ Системное уведомление». На production
рекомендуется выделить отдельный chat ID, чтобы оперативные алерты не смешивались
с GR-сводками для пользователей.

## Чеклист

- [ ] Env-файл существует по пути `/etc/ahstep-law-monitor/law-monitor.env`.
- [ ] Owner/group/mode: `root:ahstep 0640`.
- [ ] Все пути `LAW_MONITOR_*_DIR` указывают на `/var/lib` или `/var/log`, а не на repo.
- [ ] `LAW_MONITOR_DB_PATH` указывает на `/var/lib/ahstep-law-monitor/data/law_monitor.db`.
- [ ] `LAW_MONITOR_LOG_FILE` указывает на `/var/log/ahstep-law-monitor/app.log`.
- [ ] `LAW_MONITOR_TIMEZONE` равен `Europe/Moscow`, если IT явно не выбрал другой timezone.
- [ ] `LAW_MONITOR_DAILY_REPORT_HOUR` согласован с business owner.
- [ ] `LAW_MONITOR_HOURLY_INTERVAL_MINUTES` согласован с business owner.
- [ ] Telegram token и chat ID заданы только в production env-файле.
- [ ] `TELEGRAM_PROXY_URL` пустой, если доступ к Telegram не требует proxy.
- [ ] `TELEGRAM_OPERATOR_CHAT_ID` задан, если нужен отдельный канал для оперативных алертов (рекомендуется на production).
- [ ] `LLM_ENRICHMENT_ENABLED=false` для первичного корпоративного handoff, если owner/IT явно не одобрили LLM enrichment.
- [ ] Если LLM включен, `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY` и `LLM_MODEL` согласованы с IT.
- [ ] В git checkout нет production `.env`.
- [ ] `.gitignore` по-прежнему исключает `.env`, `.env.*`, DBs, logs, data и generated reports.

## Примечание по LLM provider

Если LLM enrichment включен, приложение отправляет только текст, извлеченный из
публичных нормативных/source-документов, для формирования document-card
enrichment. Этот режим нельзя использовать для конфиденциальных внутренних
документов AHSTEP, если project owner и IT не согласовали provider, условия
обработки данных и сетевой маршрут.

## Примечания по proxy и CA

`TELEGRAM_PROXY_URL` применяется только к Telegram Bot API. Он не проксирует
запросы к государственным или региональным источникам.

Если корпоративная сеть требует proxy для источников, IT может задать
стандартные env vars `HTTPS_PROXY`, `HTTP_PROXY` и `NO_PROXY` в production
env-файле.

Некоторые источники сейчас используют `verify_ssl: false` в `config/sources.yaml`,
потому что TLS-поведение публичных источников нестабильно. По возможности
предпочитайте настройку corporate CA bundle вместо добавления новых отключений
TLS verification. Не меняйте SSL-политику источников во время deployment без
явной необходимости.

