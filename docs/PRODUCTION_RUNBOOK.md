# Production Runbook

## Назначение

Этот runbook нужен для аккуратного запуска AHSTEP law_monitor как production-like internal GR system:

- с предсказуемым запуском через `.venv\Scripts\python.exe`
- с проверяемым Telegram-каналом
- с понятной диагностикой источников, OCR и scheduler
- с безопасной операционной процедурой перед показом, передачей или деплоем

## Базовое правило запуска

Все команды запускайте через project virtualenv:

```powershell
.\.venv\Scripts\python.exe <команда>
```

Примеры:

```powershell
.\.venv\Scripts\python.exe -m unittest
.\.venv\Scripts\python.exe main.py smoke-check
.\.venv\Scripts\python.exe main.py report
```

Не смешивайте запуск через системный `python` и `.venv\Scripts\python.exe`.

## Где настраивать `.env`

Файл `.env` держите в корне проекта рядом с `main.py`.

Минимальный production-like набор:

```env
LAW_MONITOR_DB_PATH=data/law_monitor.db
LAW_MONITOR_LOG_LEVEL=INFO

TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_PROXY_URL=
TELEGRAM_API_TIMEOUT=30

LAW_MONITOR_OCR_ENABLED=false
LAW_MONITOR_OCR_LANGUAGE=rus+eng
LAW_MONITOR_OCR_MAX_PAGES=5
LAW_MONITOR_OCR_TIMEOUT=120
LAW_MONITOR_OCR_TESSDATA_PATH=

LAW_MONITOR_DAILY_REPORT_HOUR=9
LAW_MONITOR_HOURLY_INTERVAL_MINUTES=60
```

Дополнительные env vars, если нужно:

- `LAW_MONITOR_REQUEST_TIMEOUT`
- `LAW_MONITOR_REQUEST_RETRIES`
- `LAW_MONITOR_REQUEST_BACKOFF_FACTOR`
- `LAW_MONITOR_USER_AGENT`
- `LAW_MONITOR_LOG_FILE`
- `LAW_MONITOR_LOG_MAX_BYTES`
- `LAW_MONITOR_LOG_BACKUP_COUNT`

LLM enrichment layer:

- по умолчанию отключён: `LLM_ENRICHMENT_ENABLED=false`
- для локальной безопасной проверки можно оставить `LLM_PROVIDER=mock`
- OpenAI-compatible endpoint example:
  - LM Studio: `LLM_BASE_URL=http://127.0.0.1:1234/v1`
  - Ollama: `LLM_BASE_URL=http://127.0.0.1:11434/v1`
- дополнительные поля:
  - `LLM_API_KEY=`
  - `LLM_MODEL=`

## Обязательные директории и артефакты

Проект использует:

- `data/law_monitor.db` — основная SQLite база
- `data/documents/` — локальные документы и вложения
- `data/runtime/` — runtime state и writer lock
- `reports/` — generated reports
- `logs/` — application logs
- `config/sources.yaml` — sources
- `config/keywords.yaml` — keywords

## Быстрый preflight

Перед показом, передачей или production-like запуском:

```powershell
.\.venv\Scripts\python.exe main.py init-db
.\.venv\Scripts\python.exe main.py smoke-check
.\.venv\Scripts\python.exe main.py diagnostics --days 7
.\.venv\Scripts\python.exe main.py report
```

Если `smoke-check` не зелёный, сначала разберите warning/error и только потом продолжайте.

## Как проверять Telegram

Проверка конфигурации и доставки:

```powershell
.\.venv\Scripts\python.exe main.py telegram-check
```

Что считается нормой:

- `Telegram configured: yes`
- `Send result: success`

Если нужен отдельный тестовый пинг (`telegram-check` и `notify-test` — алиасы одной команды):

```powershell
.\.venv\Scripts\python.exe main.py notify-test
```

## Как запускать Telegram bot

Интерактивный bot:

```powershell
.\.venv\Scripts\python.exe main.py run-telegram-bot
```

После запуска проверьте:

- `/status`
- `/report`
- `/sources`
- `/search экспорт`

## Как запускать scheduler

One-shot цикл:

```powershell
.\.venv\Scripts\python.exe main.py run-scheduler --once
```

Это лучший production acceptance запуск перед постоянным scheduler.

Постоянный scheduler:

```powershell
.\.venv\Scripts\python.exe main.py run-scheduler
```

## Как запускать one-shot pipeline вручную

Полный единичный прогон:

```powershell
.\.venv\Scripts\python.exe main.py run
```

Отдельные шаги:

```powershell
.\.venv\Scripts\python.exe main.py collect
.\.venv\Scripts\python.exe main.py analyze
.\.venv\Scripts\python.exe main.py report
```

Форсированный re-analyze используйте только осознанно:

```powershell
.\.venv\Scripts\python.exe main.py analyze --force
```

## Как смотреть diagnostics

Диагностика за 7 дней:

```powershell
.\.venv\Scripts\python.exe main.py diagnostics --days 7
```

Смотрите в первую очередь:

- source coverage
- долю noisy материалов
- missing published dates
- перекос по `requires_attention` / `watchlist`

## Как проверять OCR

Проверка runtime:

```powershell
.\.venv\Scripts\python.exe main.py ocr-check
```

Очередь OCR:

```powershell
.\.venv\Scripts\python.exe main.py ocr-queue
.\.venv\Scripts\python.exe main.py ocr-queue --status pending
.\.venv\Scripts\python.exe main.py ocr-queue --priority high
```

## Как запускать OCR backlog cleanup

Backfill OCR queue из extraction audit:

```powershell
.\.venv\Scripts\python.exe main.py ocr-backfill --limit 50
```

Selective backfill по источнику:

```powershell
.\.venv\Scripts\python.exe main.py ocr-backfill --source "Нормативные акты Краснодарского края" --limit 20
```

Запуск OCR runtime по pending queue:

```powershell
.\.venv\Scripts\python.exe main.py ocr-run --limit 10
```

Ручное закрытие/triage элемента:

```powershell
.\.venv\Scripts\python.exe main.py ocr-mark <url> --status done
.\.venv\Scripts\python.exe main.py ocr-mark <url> --status skipped --notes "duplicate or low value"
```

## Как понимать operational warnings

В `/report`, daily digest и `/status` operational notices означают:

- `источник не обновлялся N дней` — источник не даёт новых публикаций дольше ожидаемого окна
- `были ошибки доступа за последние 24 часа` — были ошибки источника, нужен review
- `OCR queue: в очереди N документов на обработку` — OCR backlog начал накапливаться

Это не обязательно поломка, но это всегда повод на короткую операционную проверку.

Практический порядок чтения:

1. Сначала откройте `/status` и проверьте, нет ли stale/error warnings.
2. Затем откройте `/sources`, чтобы понять, какой именно источник даёт проблему.
3. После этого проверьте `diagnostics --days 7`, если нужна детализация по качеству данных.

## Что делать, если source не работает

1. Запустите:

```powershell
.\.venv\Scripts\python.exe main.py diagnostics --days 7
.\.venv\Scripts\python.exe main.py collect --source "<точное имя источника>"
```

2. Проверьте:

- источник не блокирует доступ по `403/429`
- не изменился HTML/listing path
- не требуется другой proxy/network route
- в `config/sources.yaml` источник всё ещё включён и корректен

3. Если ошибка повторяется:

- зафиксируйте source name
- сохраните текст ошибки и дату
- не меняйте rules/classification без отдельного решения

## Что делать, если OCR backlog растёт

1. Проверьте runtime:

```powershell
.\.venv\Scripts\python.exe main.py ocr-check
```

2. Проверьте pending queue:

```powershell
.\.venv\Scripts\python.exe main.py ocr-queue --status pending --limit 20
```

3. Если backlog объясним:

- зафиксируйте причину в acceptance notes
- при необходимости обработайте high-priority queue first

4. Если backlog не объясним:

- проверьте `LAW_MONITOR_OCR_ENABLED`
- проверьте `LAW_MONITOR_OCR_TESSDATA_PATH`
- проверьте доступность Tesseract и языков

5. Для controlled cleanup используйте:

```powershell
.\.venv\Scripts\python.exe main.py ocr-backfill --limit 50
.\.venv\Scripts\python.exe main.py ocr-run --limit 10
```

## Что делать, если Telegram не отправляет сообщения

1. Запустите:

```powershell
.\.venv\Scripts\python.exe main.py telegram-check
```

2. Проверьте:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_PROXY_URL`, если нужен proxy
- сетевую доступность Telegram API

3. Если bot polling не стартует:

- проверьте логи в `logs/`
- проверьте proxy
- убедитесь, что token не отозван

## Что делать, если команда пишет "Another write operation is already running"

Это single-writer protection для SQLite.

Что делать:

1. Дождаться завершения текущего write-heavy процесса
2. Не запускать одновременно:
   - `collect`
   - `analyze`
   - `report`
   - `run`
   - `ocr-run`
   - `check-tracked`
   - `run-scheduler --once`
3. Если есть сомнение, проверьте `data/runtime/`

Если блокировка пришла из Telegram `/refresh`, user-facing ответ должен быть:

- `Обновление уже выполняется, попробуйте позже.`

## Recommended acceptance command sequence

Минимальная production-like проверка:

```powershell
.\.venv\Scripts\python.exe -m unittest
.\.venv\Scripts\python.exe main.py smoke-check
.\.venv\Scripts\python.exe main.py telegram-check
.\.venv\Scripts\python.exe main.py diagnostics --days 7
.\.venv\Scripts\python.exe main.py ocr-check
.\.venv\Scripts\python.exe main.py ocr-queue --status pending
.\.venv\Scripts\python.exe main.py run-scheduler --once
.\.venv\Scripts\python.exe main.py report
```

## Scope note

В этой фазе проект остаётся docs-first.

Отдельная CLI команда вроде `backup-db` могла бы быть полезна позже, но в этой фазе она намеренно не добавляется: текущая задача закрывается безопасной документированной процедурой backup/restore.
