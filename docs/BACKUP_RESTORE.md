# Backup and Restore

## Когда делать backup

Backup обязателен:

- перед ручными изменениями в базе
- перед production-like demo
- перед handover
- перед любым risky maintenance запуском
- перед массовыми collect/analyze/OCR операциями, если нужна точка отката

## Что сохранять

Минимальный набор:

- `data/law_monitor.db`
- `reports/`
- при необходимости `logs/`

Если нужны вложения и локальные документы:

- `data/documents/`

## Где лежат данные

- SQLite DB: `data/law_monitor.db`
- runtime state: `data/runtime/`
- local documents: `data/documents/`
- reports: `reports/`
- logs: `logs/`

## Safe backup procedure

1. Остановите write-heavy операции:

- `run-scheduler`
- `run`
- `collect`
- `analyze`
- `report`
- `ocr-run`
- `check-tracked`
- Telegram `/refresh`

2. Убедитесь, что нет активного writer lock в `data/runtime/`, либо дождитесь завершения процесса.

3. Создайте backup directory, если его ещё нет:

```powershell
New-Item -ItemType Directory -Force -Path "data\backups" | Out-Null
```

4. Скопируйте базу в backup location.

Пример:

```powershell
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item -LiteralPath "data\law_monitor.db" -Destination "data\backups\law_monitor_$ts.db"
```

5. Скопируйте reports:

```powershell
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item -LiteralPath "reports" -Destination "data\backups\reports_$ts" -Recurse
```

6. Если нужен расширенный backup:

```powershell
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item -LiteralPath "data\documents" -Destination "data\backups\documents_$ts" -Recurse
```

## Restore procedure

1. Остановите scheduler, bot и все write-heavy команды.
2. Сохраните текущий broken state в отдельный backup, даже если он кажется плохим.
3. Восстановите нужный `.db` файл поверх `data/law_monitor.db`.

Пример:

```powershell
Copy-Item -LiteralPath "data\backups\law_monitor_20260507_150000.db" -Destination "data\law_monitor.db" -Force
```

4. При необходимости верните reports:

```powershell
Copy-Item -LiteralPath "data\backups\reports_20260507_150000\*" -Destination "reports" -Recurse -Force
```

5. Если восстанавливались локальные документы, верните и их:

```powershell
Copy-Item -LiteralPath "data\backups\documents_20260507_150000\*" -Destination "data\documents" -Recurse -Force
```

6. После restore обязательно выполните:

```powershell
.\.venv\Scripts\python.exe main.py smoke-check
.\.venv\Scripts\python.exe main.py diagnostics --days 7
```

## Minimal backup checklist

- [ ] write-heavy процессы остановлены
- [ ] `data/law_monitor.db` скопирован
- [ ] `reports/` скопирован
- [ ] backup path записан в acceptance notes
- [ ] после restore выполнен `smoke-check`
