# Локальная / demo-приёмка на Windows

Чеклист для локальной разработческой и demo-проверки на Windows-рабочей
станции. Это не production-приёмка Linux/VPS — для развертывания на сервере
используйте [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md).

Используйте этот checklist перед demo, handover или production-like прогоном
на локальной машине.

## Core acceptance

- [ ] `.venv\Scripts\python.exe -m unittest` завершился с `OK`
- [ ] `.venv\Scripts\python.exe main.py smoke-check` завершился без critical issues
- [ ] `.venv\Scripts\python.exe main.py telegram-check` завершился с `Send result: success`
- [ ] `diagnostics --days 7` просмотрен вручную
- [ ] OCR pending = `0` или backlog документирован и объяснён
- [ ] backup SQLite DB создан до изменений или перед показом

## Runtime acceptance

- [ ] `.venv\Scripts\python.exe main.py run-scheduler --once` отработал успешно
- [ ] daily digest отправлен или dry-run подтверждён
- [ ] report file сгенерирован в `reports/`
- [ ] writer lock не остаётся зависшим после завершения команд

## Telegram acceptance

- [ ] `/status` работает
- [ ] `/report` работает
- [ ] `/sources` работает
- [ ] Поиск работает через нижнюю кнопку «🔎 Поиск»
- [ ] `/today` открывается без ошибок
- [ ] Отдельной кнопки «Срочное» нет; срочные пункты включены в `/report`
- [ ] `/watchlist` открывается без ошибок
- [ ] Видимое меню команд содержит только `/start`, `/myid`, `/help`, `/report`, `/refresh`
- [ ] Вложения отчётов приходят как редактируемые `.docx`; `.md` остаётся в `reports/`

## Data quality acceptance

- [ ] operational warnings просмотрены и понятны
- [ ] stale sources либо ожидаемы, либо разобраны
- [ ] source errors за последние 24 часа либо отсутствуют, либо объяснены
- [ ] report summary выглядит human-readable
- [ ] OCR placeholders в user-facing output не видны

## Recommended command sequence

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

После `run-scheduler --once` проверьте, что:

- daily digest дошёл в Telegram или есть понятная причина, почему он не должен был уйти
- новый `.md` report file появился в `reports/`, а Telegram-вложение отправлено как `.docx`
- `data/runtime/writer.lock` не остался после завершения команды

## Release note block

Перед handover полезно коротко зафиксировать:

- дата acceptance
- кто запускал проверку
- есть ли source warnings
- есть ли OCR backlog
- какой report file был проверен
- где лежит backup
