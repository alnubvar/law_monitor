# Final Production Audit

Дата обновления: 2026-05-25

Этот документ заменяет устаревшую ревизию от 2026-05-21. Он фиксирует текущую
production baseline после стабилизации Gemini Flash Lite, private-only Telegram
UX и DOCX-доставки отчетов.

## Executive Verdict

Статус: ready with minor fixes.

Система готова к короткому корпоративному pilot при условии, что production env
заполняется по актуальному шаблону, Telegram bot и scheduler запускаются
отдельными systemd-сервисами, а LLM используется только как enrichment layer.

## Зафиксированная Production Baseline

```env
LLM_MODEL=gemini-3.1-flash-lite
LLM_MODEL_FALLBACKS=gemini-2.5-flash,gemini-2.0-flash
LLM_MAX_DOCUMENT_CHARS=2000
LLM_MAX_DOCS_PER_BATCH=10
LLM_REQUEST_DELAY_SECONDS=5
LLM_MAX_RETRIES=2
LLM_RETRY_BACKOFF_SECONDS=3
LLM_RETRY_MAX_BACKOFF_SECONDS=20
LLM_RESPONSE_FORMAT=none
```

LLM остается необязательным обогащением карточек документов. Видимость,
приоритет и `action_level` определяются deterministic rules/gates; LLM-сбой не
должен ломать сбор, анализ, генерацию отчета или доставку Telegram digest.

## Telegram И Scheduler

- Telegram bot работает в private-only corporate mode: approved users пишут
  боту в личный чат.
- `TELEGRAM_ADMIN_USER_IDS` задает env-администраторов, которые всегда получают
  daily digest и могут управлять allowlist.
- `TELEGRAM_CHAT_ID` является optional legacy/fallback scheduled destination и
  не требуется для private-only запуска.
- Daily digest уходит env-администраторам, активным allowed users и, если задан,
  в legacy/fallback `TELEGRAM_CHAT_ID`, с deduplication.
- Unknown users получают только access-denied и свой Telegram ID.
- Daily digest и ручной `/report` отправляют DOCX-вложение; Markdown остается
  серверным архивным форматом.
- Scheduler и Telegram bot являются разными процессами и systemd-сервисами.

## Актуальные Операционные Риски

1. Medium: отдельный `LLM_PROXY_URL` и provider quota должны быть подтверждены
   IT до включения enrichment в production.
2. Medium: российские государственные источники могут давать timeout или
   временную недоступность; diagnostics должны проверяться после первых циклов.
3. Medium: `verify_ssl: false` для отдельных источников является compatibility
   caveat и не должен расширяться без явной необходимости.
4. Low: watchlist может сохранять часть широких федеральных/export/market
   сигналов, чтобы не потерять важные темы для GR.

## Do Not Touch

- Не менять deterministic `action_level` без отдельного business approval.
- Не расширять Telegram visible menu и нижнюю keyboard без отдельного product
  решения.
- Не объединять scheduler и Telegram bot в один сервис.
- Не возвращать старые production-рекомендации по нестабильным моделям.
- Не запускать live collect/analyze/enrich/scheduler/Telegram команды во время
  read-only или docs-only аудита.

## Recommended Owner Commands

Безопасные проверки после деплоя env и до включения systemd:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py smoke-check'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py report --days 7'
```

Live-команды (`telegram-check`, `notify-test`, `run-scheduler --once`) выполнять
только владельцу проекта или IT в согласованное окно.
