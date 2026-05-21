# AHSTEP GR Monitor

Производственный MVP мониторинга GR-сигналов в сельском хозяйстве:
региональное и федеральное регулирование, меры поддержки, отраслевые новости.

## Назначение

AHSTEP GR Monitor предназначен для GR- и policy-команд, которым нужна
ежедневная видимость:

- что требует немедленной реакции;
- что должно оставаться на watchlist;
- что важно как фон, но не требует действий.

Система собирает документы из настроенных публичных источников, извлекает
текст, применяет rule-based анализ, сохраняет результаты в SQLite и
доставляет Markdown-отчёты и Telegram-сводки.

## Архитектура

Поток обработки:

1. `collect` — fetch источников, фильтрация ссылок, аудит извлечения.
2. `extract` — извлечение текста для `html`, `pdf`, `docx`.
3. `analyze` — rule-based классификация, `action_level`, бизнес-факты.
4. `report` — Markdown-отчёт для GR.
5. `notify` — Telegram digest и интерактивные команды.

Точка входа CLI: [main.py](main.py)

## Возможности

- сбор из федеральных и региональных источников;
- извлечение `html`, `pdf`, `docx`;
- диагностика извлечения (`diagnostics`, качество, покрытие источников);
- OCR triage queue и опциональный локальный OCR (по умолчанию выключен);
- интерактивный Telegram-бот с командами и reply-клавиатурой;
- трекинг документов (`/track`, `/untrack`, `/tracked`);
- архивный поиск (`/search`);
- ручной refresh (`/refresh`) с cooldown;
- smoke checks и регрессионные тесты.

## Уровни действий (`action_level`)

- `requires_attention` — требует действий GR сейчас.
- `watchlist` — важно мониторить, действия не требуются.
- `background` — полезный контекст без прямого сигнала.
- `irrelevant` — шум, служебные или нерелевантные материалы.

## OCR triage

### Зачем нужны scan-candidates

Часть PDF — фактически сканы или с очень слабым текстовым слоем. Они
помечаются как `scan_candidate` на этапе extraction audit.

### Режим OCR runtime

Локальный OCR поддерживается, но включается опционально:

- `LAW_MONITOR_OCR_ENABLED=false` по умолчанию;
- manual-first triage остаётся основным workflow;
- cloud OCR вне scope до подтверждения ROI.

### Зачем нужна очередь OCR

OCR triage не даёт потерять scan-heavy документы:

- ведёт управляемую очередь кандидатов;
- даёт GR-команде видимость pending-работы;
- поддерживает ручные статусы (`pending`, `in_review`, `done`, `skipped`).

### Приоритеты

- `high`: видимые policy-сигналы (`requires_attention` / `watchlist`) или
  источник `Нормативные акты Краснодарского края`.
- `medium`: default для нейтральных или неизвестных кандидатов.
- `low`: нерелевантные (`irrelevant`) кандидаты.

### Команды OCR

```bash
python main.py ocr-check
python main.py ocr-queue
python main.py ocr-queue --status pending
python main.py ocr-queue --priority high
python main.py ocr-queue --status pending --limit 20
python main.py ocr-backfill --source "Нормативные акты Краснодарского края" --limit 10
python main.py ocr-run --source "Нормативные акты Краснодарского края" --limit 1
python main.py ocr-mark <url> --status done
python main.py ocr-mark <url> --status in_review --notes "checking text quality"
```

В Telegram:

```text
/ocr
```

## Команды Telegram

| Команда | Назначение |
| --- | --- |
| `/start` | открыть меню |
| `/help` | список команд |
| `/status` | состояние системы и свежесть |
| `/today` | видимые документы за сегодня |
| `/urgent [days]` | документы `requires_attention` |
| `/watchlist [days]` | документы watchlist |
| `/report [days]` | короткая сводка + `.txt` вложение |
| `/sources` | состояние источников |
| `/ocr` | состояние OCR triage |
| `/search <query>` | поиск по архиву |
| `/track <url>` | добавить документ в трекинг |
| `/untrack <url>` | убрать из трекинга |
| `/tracked` | активные tracked-документы |
| `/refresh` | ручной collect/analyze/report |

## Локальная разработка и demo (Windows)

Используйте виртуальное окружение проекта. Не предполагайте, что system Python
содержит все зависимости.

Базовый локальный workflow:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python main.py init-db
python main.py smoke-check
python main.py run-scheduler --once
```

Запускать команды через интерпретатор venv, без активации:

```powershell
.\.venv\Scripts\python.exe -m unittest
.\.venv\Scripts\python.exe main.py smoke-check
.\.venv\Scripts\python.exe main.py demo-report
```

Чеклист локальной и demo-приёмки: [docs/LOCAL_ACCEPTANCE_CHECKLIST.md](docs/LOCAL_ACCEPTANCE_CHECKLIST.md)

## Частые CLI-команды

```bash
python main.py collect
python main.py audit-extraction
python main.py analyze --force
python main.py diagnostics --days 7
python main.py backfill-dates
python main.py ocr-check
python main.py ocr-backfill --limit 50
python main.py ocr-run --limit 10
python main.py report --days 7 --action-level requires_attention watchlist --max-items 30
python main.py run-telegram-bot
python main.py check-tracked
python -m unittest -v
```

## Развертывание на Linux/VPS (production)

Эксплуатационные документы для передачи в корпоративный IT:

- [docs/CORPORATE_DEPLOYMENT.md](docs/CORPORATE_DEPLOYMENT.md) — полная
  инструкция по корпоративному развертыванию.
- [docs/PRODUCTION_ENV_TEMPLATE.md](docs/PRODUCTION_ENV_TEMPLATE.md) — шаблон
  production env и чеклист обязательных значений.
- [docs/OPERATOR_RUNBOOK.md](docs/OPERATOR_RUNBOOK.md) — операторский runbook,
  ежедневные/еженедельные проверки, обработка сбоев, обновление и rollback.
- [docs/DEPLOYMENT_CHECKLIST.md](docs/DEPLOYMENT_CHECKLIST.md) — чеклист
  приемки production Linux/VPS-развертывания.
- [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md) — backup и restore SQLite
  DB.
- [docs/SECURITY_NOTES.md](docs/SECURITY_NOTES.md) — заметки по безопасности
  для корпоративного IT.

## Примечание по Windows OCR (optional local runtime)

- бинарь Tesseract, пример: `C:\Program Files\Tesseract-OCR\tesseract.exe`
- tessdata: `C:\Program Files\Tesseract-OCR\tessdata`
- env:
  - `LAW_MONITOR_OCR_ENABLED=true`
  - `LAW_MONITOR_OCR_LANGUAGE=rus+eng`
  - `LAW_MONITOR_OCR_TESSDATA_PATH=C:\Program Files\Tesseract-OCR\tessdata`
  - `LLM_DOCUMENT_ENRICHMENT_ENABLED=false` по умолчанию
  - `LLM_ENRICHMENT_ENABLED=false` сохранён как compatibility alias
  - `LLM_PROVIDER=mock` для безопасного локального тестирования
  - `LLM_TIMEOUT_SECONDS=60`, `LLM_MAX_DOCUMENT_CHARS=12000`,
    `LLM_ENRICHMENT_LIMIT=20`
  - OpenAI-совместимый пример: `LLM_BASE_URL=http://127.0.0.1:1234/v1` для LM
    Studio или `http://127.0.0.1:11434/v1` для Ollama

## Демо

- Краткое описание демо: [DEMO_SUMMARY.md](DEMO_SUMMARY.md)
- Безопасный demo-артефакт: `python main.py demo-report` →
  `docs/demo_report.md` (в `.gitignore`, не коммитится)
- Примеры сгенерированных отчётов: `reports/gr_monitoring_*.md`

## Дорожная карта

- опциональные LLM-сводки поверх чистого извлечённого текста;
- retrieval и историческая аналитика (RAG-style workflows);
- улучшение устойчивости извлечения для source-specific edge cases.

## Ограничения текущей фазы

- нет cloud OCR в основном pipeline;
- не меняем бизнес-логику `action_level` в фазе triage и docs;
- без тяжёлых дополнительных зависимостей;
- без переусложнения инфраструктуры на MVP-стадии.
