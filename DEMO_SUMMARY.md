# Кратко о демо

AHSTEP GR Monitor показывает компактный production-style цикл мониторинга для
GR-команд:

1. сбор материалов из источников;
2. извлечение текста (`html/pdf/docx`);
3. присвоение action level через rule-based анализ;
4. генерация Markdown-отчёта;
5. отправка Telegram-сводок и обслуживание интерактивных команд.

## Что показать в демо

- блок `requires_attention` остаётся сфокусированным и actionable;
- сигналы watchlist / background отделены от шума;
- диагностика источников подсвечивает пробелы в извлечении и покрытии;
- OCR scan-candidates обрабатываются через triage queue (`/ocr`,
  `ocr-queue`, `ocr-mark`);
- команды трекинга и refresh поддерживают ежедневные операции GR.

## Сценарий запуска демо

```bash
python main.py smoke-check
python main.py diagnostics --days 7
python main.py report --days 7 --action-level requires_attention watchlist --max-items 25
python main.py ocr-queue
```

## Ключевые ограничения

- OCR runtime не используется в основном pipeline (только triage);
- логика action level в этой фазе не меняется;
- инфраструктура без тяжёлых требований.
