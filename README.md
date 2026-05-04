# AHSTEP GR Monitor MVP

Production-like MVP для GR-мониторинга НПА, мер поддержки и отраслевых сигналов в АПК.

Система собирает документы из федеральных и региональных источников, извлекает текст, применяет rule-based анализ с `document facts`, присваивает `action_level`, формирует markdown-отчеты и отправляет уведомления в Telegram.

## Current MVP Status

- `collect / analyze / report / notify / scheduler` работают;
- rule-based `document facts` внедрены;
- `action_level` стабилизирован до ограниченного набора реальных GR-сигналов;
- hourly alert и daily digest работают раздельно;
- Telegram через proxy поддерживается для РФ-сервера;
- SQLite, file logging, markdown reporting и tests уже встроены.

## Что Делает Система

- собирает документы, новости, разделы мер поддержки и региональные НПА;
- извлекает текст из `html / pdf / docx`;
- классифицирует документы по business-значимости;
- вытаскивает прикладные факты для GR-аналитика;
- формирует daily markdown-report и Telegram digest;
- хранит историю в SQLite.

## Источники (MVP)

- `Правительство РФ - документы` (`government.ru/docs`)
- `Правительство РФ - новости` (`government.ru/news`)
- `Regulation.gov.ru`
- `ГИСП - меры поддержки АПК`
- `Минсельхоз Краснодарского края - субсидирование и финансирование`
- `Минсельхоз Ростовской области - господдержка`
- `Минсельхоз Ставропольского края - господдержка`
- `Нормативные акты Краснодарского края`
- `Право Ростовской области`
- `Право Ставропольского края`
- `ZOL.ru - зерновые новости`

## Архитектура

- `collect`
  загрузка источников из `config/sources.yaml`, сбор ссылок и первичных метаданных.
- `analyze`
  rule-based классификация, page type detection, `document facts`, `action_level`.
- `report`
  bucket-based markdown report с разделением на main GR, support/reference и background.
- `notify`
  Telegram notifications и безопасная отправка через proxy только для Telegram API.
- `scheduler`
  hourly collect/analyze/notify и daily report cycle.

## Business Logic

Система не считает документ срочным только потому, что в нем встречаются слова вроде `субсидия` или `господдержка`.

Для документов мер поддержки она дополнительно учитывает:

- статус меры: `active / inactive / unknown`;
- режим: `open / closed / regular / unknown`;
- target geography;
- page type: карточка меры, раздел, реестр, протокол, фон;
- business signal: короткое объяснение, почему документ попал в конкретный bucket.

## Document Facts

Для analyzed-документов сохраняются:

- `support_status`
- `is_active`
- `is_continuous`
- `application_status`
- `npa_number`
- `deadline_text`
- `terms_text`
- `business_signal`
- `risk_notes`

`deadline_text` хранит только реальные дедлайны реакции: прием заявок, срок подачи, конкурсный отбор, срок обсуждения.  
`terms_text` хранит условия меры: срок кредита, срок займа, размер поддержки и похожие параметры.

## Action Levels

- `requires_attention`
  документ требует реакции GR-команды.
- `watchlist`
  документ важен для наблюдения, но без срочного действия.
- `background`
  полезный отраслевой или региональный фон без прямого action signal.
- `irrelevant`
  нерелевантный, служебный или шумовой документ.

## Source Diagnostics

Добавлена отдельная диагностика качества источников:

```bash
python main.py diagnostics
python main.py diagnostics --days 7
```

Диагностика показывает:

- сколько документов пришло по каждому источнику;
- распределение по `requires_attention / watchlist / background / irrelevant`;
- сколько документов без `published_at`;
- сколько документов без `summary` или `raw_text`;
- сколько `reference_page / registry / measure_card`;
- топ источников по шуму.

## Telegram Behavior

- `hourly alert`
  отправляет только новые `requires_attention`, у которых `notified = 0`.
- `daily digest`
  отправляет visible-документы из основных report buckets.
- `notified flag`
  выставляется только после успешной отправки Telegram-сообщения.
- global / market background по умолчанию в Telegram digest не показывается.
- proxy используется только для Telegram API, а не для парсинга источников.

Команды в Telegram:

- `/today` — visible документы за сегодня;
- `/urgent` — только `requires_attention`;
- `/sources` — статус источников;
- `/help` — список команд.

## Структура Проекта

```text
app/
config/
docs/
tests/
main.py
pyproject.toml
.env.example
README.md
```

## Требования

- Python 3.11+
- Windows / Linux
- Telegram bot token и chat id для уведомлений

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
```

Заполните `.env` по образцу `.env.example`.

## Быстрый Запуск

```bash
python main.py analyze --force
python main.py diagnostics
python main.py report --days 7 --action-level requires_attention watchlist --max-items 30
python main.py run-scheduler --once
```

## Основные Команды

```bash
python main.py init-db
python main.py collect
python main.py analyze --force
python main.py diagnostics
python main.py report --days 7 --action-level requires_attention watchlist --max-items 30
python main.py demo-report
python main.py run-scheduler --once
python main.py telegram-check
python main.py notify-test
python -m unittest discover -s tests -v
```

## Deployment / 24/7 Run

Для production-подготовки без изменения бизнес-логики добавлены:

- `python main.py smoke-check`
- Windows-first scripts в `scripts/`
- deployment guide: [docs/deployment.md](docs/deployment.md)

Быстрые команды:

```powershell
.\scripts\run_smoke_check.ps1
.\scripts\run_scheduler_once.ps1
.\scripts\run_scheduler.ps1
.\scripts\backup_sqlite.ps1
```

Рекомендуемый текущий MVP-вариант: Windows host + SQLite + Task Scheduler + регулярный `run-scheduler --once`.

## Production Checklist (Short)

1. Подготовить env: заполнить `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, опционально `TELEGRAM_PROXY_URL`).
2. Инициализировать БД: `python main.py init-db`.
3. Проверить контур: `python main.py smoke-check`.
4. Прогнать pipeline вручную:
   `python main.py analyze --force`
   `python main.py report --days 7 --action-level requires_attention watchlist --max-items 25`
5. Проверить Telegram доставку: `python main.py telegram-check`.
6. Включить scheduler: `python main.py run-scheduler --once` (через Task Scheduler по расписанию).

## Demo Report

Для репозитория можно безопасно собрать коммитируемый пример:

```bash
python main.py demo-report
```

По умолчанию он сохраняется в [docs/demo_report.md](docs/demo_report.md) и не требует Telegram или специальных env-переменных сверх доступа к локальной SQLite базе.

## Telegram И Proxy

Для серверов в РФ Telegram API может быть недоступен напрямую.

Поддерживаются переменные:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_PROXY_URL=
TELEGRAM_API_TIMEOUT=30
```

Поддерживаемые proxy-форматы:

```env
TELEGRAM_PROXY_URL=socks5://login:password@ip:port
TELEGRAM_PROXY_URL=http://login:password@ip:port
```

Важно:

- `collect` и парсинг сайтов идут напрямую;
- proxy используется только для Telegram;
- секреты не должны попадать в git или логи.

## Конфигурация

Основные настройки задаются через:

- `.env`
- `config/sources.yaml`
- `config/keywords.yaml`

В репозиторий коммитится только `.env.example`, но не рабочий `.env`.

## Git / Repo Hygiene

В `.gitignore` уже исключены:

- виртуальные окружения;
- локальные БД;
- `data/`, `logs/`, runtime-отчеты;
- `.env`;
- служебные IDE-файлы.

Не исключаются:

- `app/`
- `tests/`
- `config/`
- `docs/demo_report.md`
- `pyproject.toml`
- `README.md`

## Roadmap

- source-specific parsers
- published_at and deadline quality
- OCR для плохих PDF / сканов
- PostgreSQL для production deployment
- LLM summaries поверх очищенного source text
- RAG / archive search по историческим документам

## Ограничения MVP

- Анализ полностью rule-based (LLM не используется).
- OCR и PostgreSQL не используются в runtime.
- Scheduler/Telegram transport и schema стабилизированы и не меняются в рамках текущих фаз.
- Отчет ориентирован на ежедневную GR-работу, не на полноценный BI-дашборд.
