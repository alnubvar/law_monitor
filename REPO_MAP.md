# Карта репозитория

Компактная карта архитектуры `law_monitor` для AI-ассистентов.

Используйте этот файл для быстрой ориентации. Для рабочих правил см.
`CLAUDE.md`, для бизнес-контекста — `PROJECT_CONTEXT.md`.

## Верхний уровень

- `main.py` — CLI entry point и роутер команд.
- `app/` — основной пакет приложения.
- `config/` — YAML с источниками и ключевыми словами.
- `tests/` — unit, regression, smoke и операционные тесты.
- `docs/` — deployment и production runbooks.
- `scripts/` — вспомогательные PowerShell- и bash-скрипты.
- `reports/` — сгенерированные Markdown-отчёты.
- `data/` — SQLite БД и runtime-артефакты.

## Точки быстрого входа

Начинать имеет смысл здесь:

- `main.py` — какие команды существуют.
- `app/pipeline/collect.py` — collect, extraction, dedup, OCR queue sync.
- `app/pipeline/analyze.py` — анализ и обновление бизнес-полей.
- `app/pipeline/digest.py` — генерация отчёта.
- `app/reports/markdown_report.py` — лейаут отчёта, фильтрация, buckets.
- `app/notify/telegram.py` — user-facing сообщения Telegram и ответы команд.
- `app/notify/telegram_bot.py` — поллинг бота и `/refresh`.
- `app/scheduler.py` — hourly/daily автоматизация.
- `app/storage.py` — схема БД и персистентность.

`main.py` оборачивает write-команды в `writer_lock`.

## Основные команды CLI

Определены в `main.py`:

- `collect`
- `audit-extraction`
- `analyze`
- `report`
- `demo-report`
- `run`
- `run-scheduler`
- `run-telegram-bot`
- `notify-test`
- `check-tracked`
- `ocr-*`
- `diagnostics`
- `smoke-check`

## Каталог `app/`

### Основные модули

- `app/config.py` — загрузка env, путей, логирования, источников и keyword.
- `app/models.py` — Pydantic-модели source-конфигов, документов, анализа,
  digest items.
- `app/storage.py` — схема SQLite, запросы, runtime events, OCR queue,
  tracking state.
- `app/run_lock.py` — write-lock, используемый CLI, scheduler и bot refresh.
- `app/periods.py` — логика today / yesterday / rolling periods.
- `app/operational_health.py` — операционные notices для отчётов и Telegram.
- `app/user_facing.py` — GR-facing заголовки, причины, действия, формулировки.
- `app/visibility.py` — фильтрация для пользователей, buckets, секции
  отображения.
- `app/scheduler.py` — циклы автоматизации и Telegram-алёрты.

### `app/pipeline/`

Оркестрация pipeline:

- `run.py` — полный pipeline: `init_db -> collect -> analyze -> report`.
- `collect.py` — итерация по источникам, extraction, dedup, extraction audit,
  обновления OCR queue.
- `analyze.py` — анализ, обновление `action_level`, опциональная
  персистенция enrichment.
- `digest.py` — загрузка свежих документов и запись Markdown-отчёта.
- `deduplicate.py` — content-hash dedup helpers.
- `diagnostics.py` — построение диагностических snapshots.
- `smoke.py` — безопасные smoke-проверки.
- `tracking.py` — refresh и проверка tracked-документов.
- `ocr_runtime.py` — обработка OCR queue и backfill.

### `app/sources/`

Source-адаптеры и parser-specific fetch:

- `base.py` — общий интерфейс источника.
- `generic_html_source.py` — generic HTML listing fallback.
- `government_source.py` — government-specific fetch.
- `regional_law_source.py` — fetch для региональных правовых актов / НПА.
- `krasnodar_source.py` — Краснодар.
- `donland_source.py` — Ростов / Donland.
- `stavropol_source.py` — Ставрополь.
- `mcx_source.py` — МСХ России.
- `publication_pravo_stav_source.py` — JSON API publication.pravo.gov.ru для
  ставропольских НПА.
- `regulation_gov_source.py` — XML API regulation.gov.ru для федеральных НПА
  проектов.

Реестр парсеров — в `app/pipeline/collect.py`.

### `app/extractors/`

Извлечение сырого контента:

- `html_extractor.py`
- `pdf_extractor.py`
- `docx_extractor.py`
- `ocr_extractor.py`
- `date_extractor.py`
- `site_extractors.py`

Сюда смотрят при пропавшем тексте, плохих датах, scan-кандидатах или странном
поведении OCR.

### `app/rules/`

Детерминированная логика классификации.

Ключевые файлы:

- `business_signal_rules.py`
- `support_measure_rules.py`
- `regional_npa_rules.py`
- `source_role_rules.py`
- `page_type_rules.py`
- `news_rules.py`
- `news_background_guard.py`
- `noise_rules.py`
- `title_normalization.py`
- `ahstep_domain_rules.py`

Если документ получил неверный `action_level`, смотреть в `app/rules/` плюс
`app/llm/mock_client.py`.

### `app/reports/`

- `markdown_report.py` — основной builder GR-отчёта, секции, фильтры, buckets.

### `app/notify/`

- `telegram.py` — отправка digest, ответы команд, короткий user-facing текст.
- `telegram_bot.py` — поллинг бота, клавиатуры, вложения отчётов, `/refresh`.
- `telegram_formatter.py` — форматирование digest.

### `app/llm/`

Опциональный enrichment-слой, не основной классификатор.

- `mock_client.py` — текущий entry для анализа, используется в `run_analyze`.
- `enrichment.py` — интерфейс enrichment и выбор провайдера.
- `facts_extractor.py` — извлечение фактов.
- `prompts.py` — тексты промптов.
- `base.py` — базовые абстракции.

## Основные runtime-сценарии

### Collect

`main.py collect` -> `app/pipeline/collect.py`

1. загрузить включённые источники из `config/sources.yaml`
2. создать source-парсер из реестра
3. fetch элементов источника
4. extract текста через `app/extractors/`
5. записать extraction audit
6. дедуп по URL и content hash
7. сохранить raw документ в SQLite
8. синхронизировать OCR queue для scan-кандидатов PDF

### Analyze

`main.py analyze` -> `app/pipeline/analyze.py`

1. загрузить unanalyzed-документы из storage
2. проанализировать через `app/llm/mock_client.py`
3. обновить бизнес-поля в `documents`
4. опционально записать enrichment
5. репориентация OCR queue для high-value документов

### Report

`main.py report` -> `app/pipeline/digest.py` -> `app/reports/markdown_report.py`

1. загрузить свежие документы
2. backfill пропущенных дат публикации
3. отфильтровать visible-документы
4. распределить по buckets с учётом видимости
5. сгенерировать Markdown
6. сохранить файл в `reports/`

### Notify

Алёрты scheduler живут в `app/scheduler.py`.

On-demand ответы бота:

- `app/notify/telegram.py`
- `app/notify/telegram_bot.py`

Путь scheduler:

1. collect
2. analyze
3. уведомить о новых `requires_attention`
4. собрать и отправить daily digest

Путь бота:

1. поллить Telegram updates
2. сопоставить кнопки/команды
3. собрать ответ из состояния БД
4. опционально сгенерировать вложение с отчётом

### Scheduler

`main.py run-scheduler` -> `app/scheduler.py`

- hourly: collect -> analyze -> notify urgent items
- daily: generate report -> send digest
- использует `writer_lock`
- использует APScheduler, если доступен, иначе fallback-loop

## Куда смотреть

- логика collect: `app/pipeline/collect.py`, `app/sources/`, `config/sources.yaml`
- поведение парсеров: `app/sources/`, `app/extractors/site_extractors.py`
- extraction и OCR: `app/extractors/`, `app/pipeline/ocr_runtime.py`
- логика анализа: `app/pipeline/analyze.py`, `app/llm/mock_client.py`, `app/rules/`
- логика отчёта: `app/pipeline/digest.py`, `app/reports/markdown_report.py`
- user-facing формулировки: `app/user_facing.py`, `app/notify/telegram.py`
- видимость и фильтрация: `app/visibility.py`
- notify и логика бота: `app/notify/`, `app/scheduler.py`
- логика scheduler: `app/scheduler.py`
- storage и схема БД: `app/storage.py`
- конфигурация источников и keywords: `config/sources.yaml`, `config/keywords.yaml`
- логика трекинга: `app/pipeline/tracking.py`, `app/storage.py`, `app/notify/telegram.py`

## Якоря тестов

Полезные стартовые точки в `tests/`:

- pipeline и CLI: `test_main_cli.py`, `test_smoke.py`, `test_run_lock.py`
- storage и config: `test_storage.py`, `test_config.py`
- source-парсеры: `test_government_source.py`, `test_krasnodar_source.py`,
  `test_donland_source.py`, `test_stavropol_source.py`
- extraction / даты / OCR: `test_site_extractors.py`, `test_date_extractor.py`,
  `test_ocr_extractor.py`, `test_ocr_runtime.py`
- анализ и бизнес-логика: `test_analyze.py`, `test_business_value.py`,
  `test_mock_analyze.py`
- отчёты, видимость, user-facing: `test_report_generation.py`,
  `test_visibility.py`, `test_user_facing.py`
- Telegram, трекинг, ops: `test_telegram_notify.py`, `test_telegram_bot.py`,
  `test_tracking.py`, `test_operational_health.py`
- регрессионные фикстуры: `tests/fixtures/regression/`
