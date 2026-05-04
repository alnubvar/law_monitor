# DEMO Summary (Production-like MVP)

## Что делает система

AHSTEP GR-monitoring автоматизирует цикл:
`collect -> analyze -> report -> telegram -> scheduler`

По федеральным/региональным НПА и мерам господдержки АПК система:

- собирает документы из `config/sources.yaml`;
- извлекает текст (`html/pdf/docx`);
- применяет rule-based анализ (`page_type`, `action_level`, business facts);
- формирует markdown-отчет;
- отправляет daily/hourly Telegram-уведомления;
- хранит историю в SQLite.

## Текущий статус demo

Состояние после PHASE 1-7 (актуальный прогон):

- tests: `180/180 OK`
- `requires_attention = 2` (стабильно)
- `visible = 12`
- `watchlist = 23`
- основной шум по ГИСП/листингам и service-страницам снижен

## Что показывает текущий demo-результат

- В `Требует внимания GR` остаются 2 реальные федеральные меры.
- В блоке `Объявленные меры / отборы` остаются только целевые активные меры без лишнего справочного шума.
- Inactive и non-target региональные карточки ГИСП уходят в `background`.
- Service/archive/navigation страницы не попадают в visible-отчет.

## Ограничения MVP

- Rule-based движок (без LLM в runtime).
- OCR не используется в production-потоке.
- SQLite (без PostgreSQL).
- Scheduler и Telegram transport фиксированы, без изменения контрактов.

## Быстрый сценарий demo

```bash
python main.py smoke-check
python main.py analyze --force
python main.py report --days 7 --action-level requires_attention watchlist --max-items 25
python main.py diagnostics --days 7
```

## Production checklist (short)

1. Настроить `.env` (token/chat/proxy).
2. `python main.py init-db`
3. `python main.py smoke-check`
4. `python main.py analyze --force`
5. `python main.py report --days 7 --action-level requires_attention watchlist --max-items 25`
6. `python main.py run-scheduler --once` (дальше по расписанию)
