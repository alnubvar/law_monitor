# AHSTEP GR Monitor MVP

Production-like MVP для GR-мониторинга НПА, господдержки и отраслевых сигналов в АПК.

Система собирает документы из федеральных и региональных источников, извлекает текст, применяет rule-based анализ с `document facts`, присваивает `action_level`, формирует markdown-отчет и отправляет уведомления в Telegram.

## Что делает система

- собирает документы и новости по АПК;
- анализирует страницы мер поддержки, новостей, приказов, реестров и протоколов;
- извлекает `document facts`:
  - статус меры;
  - режим подачи;
  - `NPA` / номер акта;
  - `deadline_text`;
  - `terms_text`;
  - `business_signal`;
- относит документы к одному из уровней приоритета;
- формирует ежедневный markdown-report;
- отправляет Telegram-уведомления;
- поддерживает scheduler для hourly/daily запуска.

## Архитектура

- `collect`
  - загрузка источников из `config/sources.yaml`
  - сбор ссылок и первичных метаданных
  - извлечение текста из `html/pdf/docx`
- `analyze`
  - rule-based классификация документа
  - извлечение `document facts`
  - присвоение `action_level`
- `report`
  - bucket-based markdown report
  - разделение на main GR, support/reference, background
- `notify`
  - Telegram notifications
  - поддержка proxy только для Telegram API
- `scheduler`
  - hourly collect/analyze/notify
  - daily report + digest

## Action Level

- `requires_attention`
  - документ требует реакции GR-команды
- `watchlist`
  - документ важен для наблюдения, но без срочного действия
- `background`
  - полезный фон, но без прямого GR-сигнала
- `irrelevant`
  - нерелевантный или служебный документ

## Структура проекта

```text
app/
config/
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

## Быстрый запуск

```bash
python main.py analyze --force
python main.py report
python main.py run-scheduler --once
```

## Основные команды

Инициализация БД:

```bash
python main.py init-db
```

Сбор документов:

```bash
python main.py collect
```

Переанализ документов:

```bash
python main.py analyze --force
```

Построение отчета:

```bash
python main.py report --days 7 --action-level requires_attention watchlist --max-items 30
```

Один полный scheduler cycle:

```bash
python main.py run-scheduler --once
```

Проверка Telegram:

```bash
python main.py telegram-check
python main.py notify-test
```

Запуск тестов:

```bash
python -m unittest discover -s tests -v
```

## Telegram и Proxy

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
- `data/`, `logs/`, отчеты;
- `.env`;
- служебные IDE-файлы.

Не исключаются:

- `app/`
- `tests/`
- `config/`
- `pyproject.toml`
- `README.md`

## Текущее состояние MVP

- `collect / analyze / report / notify / scheduler` работают;
- `document facts` внедрены;
- `action_level` стабилизирован;
- Telegram через proxy работает;
- SQLite, logging и markdown-reporting работают;
- тесты проходят.
