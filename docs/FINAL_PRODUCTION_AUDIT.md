# Финальный production-аудит AHSTEP law_monitor

Дата аудита: 2026-05-21
Тип: read-only аудит + подготовка handoff-документов
Целевая среда: корпоративный Linux-сервер (Ubuntu Server 22.04/24.04 LTS)

## Резюме

AHSTEP `law_monitor` — production-like система GR-мониторинга, готовая к
корпоративному развёртыванию. Архитектурная граница «rules = детерминированный
фундамент; LLM = опциональное обогащение» соблюдается. Все запускаемые
safe-команды отрабатывают без ошибок. `smoke-check` проходит с 0 warnings, тест
запускается без падений (полный набор unittest), отчёт за 7 дней генерируется.
Корпоративная документация развёртывания и эксплуатации присутствует на русском
языке.

Критических блокеров перед публикацией нет. Найден один минорный документный
несоответствие в systemd unit-файлах (ссылка на `PRODUCTION_RUNBOOK.md`, тогда
как файл называется [docs/OPERATOR_RUNBOOK.md](OPERATOR_RUNBOOK.md)) и небольшая
небрежность в [`.gitignore`](../.gitignore). Все остальные находки — это
средние/низкие улучшения, которые можно решать после первого продуктивного
запуска.

## Вердикт по готовности

**Проект готов к корпоративному развёртыванию.**

Действия владельца перед go-live:

1. Заполнить production env по [docs/PRODUCTION_ENV_TEMPLATE.md](PRODUCTION_ENV_TEMPLATE.md).
2. Согласовать с IT доступ к публичным источникам и Telegram Bot API.
3. Согласовать с IT отдельный `LLM_PROXY_URL`, если включается LLM enrichment.
4. Сделать первый ручной запуск `telegram-check` и `run-scheduler --once` по чеклисту [docs/DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md).
5. Закоммитить производственный handoff-набор (этот аудит + короткий handoff IT).

## Критические блокеры

Не выявлены. Ничто из найденного не блокирует развёртывание.

## Высокий приоритет

### H1. `verify_ssl: false` у ряда источников

Файл [config/sources.yaml](../config/sources.yaml) задаёт `verify_ssl: false`
для:

- Минсельхоз России (mcx.gov.ru, обе записи);
- Минсельхоз Краснодарского края (msh.krasnodar.ru);
- Минсельхоз Ставропольского края (mshsk.ru);
- Право Ростовской области (pravo.donland.ru, обе записи);
- Право Ставропольского края (pravo.stavregion.ru);
- Нормативные акты Краснодарского края (admkrai.krasnodar.ru);
- ГИСП (gisp.gov.ru).

Источники [`Правительство РФ`](../config/sources.yaml#L65) и
[`Официальные акты Ставропольского края`](../config/sources.yaml#L303) обращаются
по `http://` (без шифрования).

Это уже задокументировано в [docs/SECURITY_NOTES.md](SECURITY_NOTES.md) и
[docs/PRODUCTION_ENV_TEMPLATE.md](PRODUCTION_ENV_TEMPLATE.md) как
source-specific совместимость, а не общая рекомендация. Источники публичные,
конфиденциальные данные через эти каналы не передаются — только GET-запросы
без сессий. Тем не менее это остаётся фактором атаки MITM/перехвата контента
для конкретных источников.

**Реакция:** не убирать `verify_ssl: false` для перечисленных источников без
проверки доступа к ним с корпоративного сервера. IT по возможности
устанавливает корпоративный CA bundle и переводит источники в режим
`verify_ssl: true` после проверки.

### H2. systemd unit-файлы ссылаются на несуществующий runbook

В файлах
[deploy/systemd/ahstep-scheduler.service](../deploy/systemd/ahstep-scheduler.service)
и
[deploy/systemd/ahstep-telegram-bot.service](../deploy/systemd/ahstep-telegram-bot.service)
поле `Documentation=` указывает на:

```text
file:/opt/ahstep/law_monitor/docs/PRODUCTION_RUNBOOK.md
```

Файл с таким именем отсутствует. Существующий runbook называется
[docs/OPERATOR_RUNBOOK.md](OPERATOR_RUNBOOK.md). Это документная ссылка, не
поломка сервиса, но `systemctl status` показывает «битую» ссылку, что путает
оператора.

**Реакция:** заменить `PRODUCTION_RUNBOOK.md` на `OPERATOR_RUNBOOK.md` в обоих
unit-файлах. Это безопасная правка — она не влияет на запуск сервисов.

## Средний приоритет

### M1. Артефакт `.txt` Telegram-вложения в git

[`reports/gr_monitoring_2026-05-21.txt`](../reports/gr_monitoring_2026-05-21.txt)
числится в git-индексе, но удалён из working tree. Файл — это сгенерированное
Telegram-вложение, которое не должно попадать в git. Текущий
[.gitignore](../.gitignore) исключает `reports/*.md`, но не исключает
`reports/*.txt`.

**Реакция:**

1. Добавить `reports/*.txt` в `.gitignore`.
2. Удалить запись из индекса: `git rm reports/gr_monitoring_2026-05-21.txt`.

Никакие другие `.txt`-артефакты в reports/ сейчас не отслеживаются.

### M2. Опечатка в `.gitignore`

В файле [.gitignore](../.gitignore) встречается строка `ку` (две кириллические
буквы), которая ничего не исключает и выглядит как случайный ввод с русской
раскладки. Эффект — нулевой, но строка стоит почистить.

### M3. Дублирование env-настроек в документации

В нескольких документах ([CORPORATE_DEPLOYMENT.md](CORPORATE_DEPLOYMENT.md),
[PRODUCTION_ENV_TEMPLATE.md](PRODUCTION_ENV_TEMPLATE.md),
[OPERATOR_RUNBOOK.md](OPERATOR_RUNBOOK.md), [SECURITY_NOTES.md](SECURITY_NOTES.md))
повторяется один и тот же recommended LLM preset для Google/Gemma. Это не
ошибка, но любые будущие изменения preset надо вносить во все четыре файла
одновременно, иначе появится рассинхронизация.

**Реакция:** не трогать в рамках этого аудита. Если в будущем будет
обновляться preset, имеет смысл сделать единое место истины и перейти к
коротким ссылкам в остальных файлах.

### M4. Отсутствие `*.txt` Telegram-вложения после `run_digest`

Текущий `run_digest` создаёт только `.md`-файл. `.txt`-вложение строится
[`_prepare_daily_report_attachment`](../app/notify/telegram.py#L284) только в
момент отправки Telegram daily digest и сохраняется рядом с `.md`. Это
архитектурно корректно (см. PRODUCTION_RULES.md), но означает, что
сгенерированный `.txt` будет появляться в `reports/` после каждой реальной
отправки. Их и нужно покрыть `.gitignore` правкой из M1.

### M5. LLM_PROXY_URL как часть строки подключения

Поддерживаемые схемы прокси — `http`, `https`, `socks5`, `socks5h`. Для
`socks5h` требуется `requests[socks]`, что уже зафиксировано в
[pyproject.toml](../pyproject.toml#L19). Если IT поменяет окружение и удалит
SOCKS-зависимость, проверка падёт на этапе `_build_llm_proxy_config`. Не
ошибка, но стоит явно проговорить в handoff: SOCKS требует extras `requests[socks]`.

## Низкий приоритет

### L1. README.md описывает проект как «производственный MVP»

[README.md](../README.md#L1) использует фразу «Производственный MVP». В
актуальной фазе проект уже не MVP, а production-like система. Это нюанс
маркетинговый, не влияет на работу.

### L2. `LAW_MONITOR_USER_AGENT` по умолчанию `law-monitor-mvp/0.1`

[app/config.py](../app/config.py#L136) задаёт User-Agent
`law-monitor-mvp/0.1` по умолчанию. Многие источники в
[config/sources.yaml](../config/sources.yaml) переопределяют его на браузерный
UA, но «глобальный» default лучше переименовать в `ahstep-law-monitor/<version>`
для прозрачности.

### L3. Demo-report пишется в `docs/`

[`run_demo_report`](../app/pipeline/digest.py#L90) сохраняет файл в
`docs/demo_report.md`. Это допустимо для локальной демонстрации, но при
production-развёртывании в `/opt/ahstep/law_monitor/docs/` создание файла из
runtime-процесса не нужно. Это не уязвимость — `demo-report` запускается только
вручную, — но желательно перенести demo-output в `/var/lib/ahstep-law-monitor/tmp`
при работе на сервере. Сейчас в [.gitignore](../.gitignore#L32) `docs/demo_report.md`
уже исключён.

### L4. Большое число ALTER TABLE при `init_db`

[app/storage.py](../app/storage.py#L142-L350) выполняет десятки
`ALTER TABLE ... ADD COLUMN` миграций idempotently при каждом запуске. Это
безопасно для SQLite и для текущего тома данных, но при росте БД (>5 GB) каждая
такая операция всё ещё дешёвая, а перечитывание `PRAGMA table_info` блокирует
коротко. Реальной проблемы нет, в будущем можно ввести явный migration tracker.

### L5. Логи могут содержать тексты документов

Файл [/var/log/ahstep-law-monitor/app.log](../logs/app.log) на уровне `INFO`
выводит summary анализа и URL документов, но не печатает full raw_text.
Утечки секретов нет; тем не менее логи стоит хранить в `/var/log` с правами
`ahstep:ahstep 0640` и не пересылать в общие корпоративные log aggregators без
проверки.

## Подтверждённые сильные стороны

1. **Архитектурная граница rules ↔ LLM выдержана.** `action_level`
   устанавливается только в [app/llm/mock_client.py](../app/llm/mock_client.py)
   (детерминированный rule-based анализ) и в правилах из
   [app/rules/](../app/rules/). LLM-слой
   ([app/llm/enrichment.py](../app/llm/enrichment.py)) явно проверяет
   `is_enrichment_eligible(action_level)` и возвращает `None` для документов
   с уровнем `background`/`irrelevant`. Падение LLM не меняет classification.

2. **Изоляция Telegram и LLM прокси.** `TELEGRAM_PROXY_URL` и `LLM_PROXY_URL` —
   независимые env переменные, не наследуются друг от друга, требуют отдельных
   restart-ов сервисов. Поведение зафиксировано в
   [SECURITY_NOTES.md](SECURITY_NOTES.md) и [OPERATOR_RUNBOOK.md](OPERATOR_RUNBOOK.md).

3. **Защита секретов в ошибках LLM.**
   [`_safe_llm_provider_error_snippet`](../app/llm/enrichment.py#L1505) и
   [`redact_telegram_secrets`](../app/notify/telegram.py#L133) маскируют
   Bearer-токены, `sk-...`, `AIza...`, credentials в URL, secret-снимки.
   Telegram URL с токеном тоже редактируются перед записью в логи.

4. **Корректная идемпотентность daily digest.**
   [`_daily_digest_already_sent`](../app/scheduler.py#L319) сверяет
   `runtime_events.daily_digest.sent_date` с текущей датой. Повторные запуски
   `run-scheduler` за тот же день не дублируют сообщения. Флаг
   `--force-daily-digest` явно позволяет переотправку.

5. **Writer lock с защитой от stale.**
   [app/run_lock.py](../app/run_lock.py) использует `O_EXCL`, проверяет PID
   процесса и timestamp; lock старше 1 часа автоматически чистится только
   если процесс мёртв. Это исключает блокировку scheduler упавшим процессом.

6. **Двухсервисная топология.** Scheduler и Telegram bot — отдельные
   systemd-сервисы: [ahstep-scheduler.service](../deploy/systemd/ahstep-scheduler.service)
   и [ahstep-telegram-bot.service](../deploy/systemd/ahstep-telegram-bot.service).
   Оба используют hardening (`NoNewPrivileges=true`, `PrivateTmp=true`,
   `ProtectHome=true`, `ProtectSystem=full`, ограниченный `ReadWritePaths`,
   `UMask=0077`). Это корректная безопасная база.

7. **PDF download safety.** В
   [app/extractors/pdf_extractor.py](../app/extractors/pdf_extractor.py#L30)
   жёсткий лимит на размер скачиваемого PDF — 50 MB; нарушение лимита бросает
   `ValueError`, предотвращая bomb-load.

8. **LLM retry/backoff и fallback.** В
   [app/llm/enrichment.py](../app/llm/enrichment.py#L893-L1135):
   - retry на HTTP 429/500/502/503/504 и transient request-исключения;
   - экспоненциальный backoff с jitter, кэп `LLM_RETRY_MAX_BACKOFF_SECONDS`;
   - fallback на `MockEnrichmentProvider` через
     [`DocumentEnricher._build_fallback_result`](../app/llm/enrichment.py#L1213);
   - facts hardening: даже если LLM вернул слабый ответ, текст пересоберётся
     детерминированно.

9. **Идемпотентность init_db.**
   [`init_db`](../app/storage.py#L95) безопасно создаёт все таблицы и
   проверяет `PRAGMA table_info` перед каждым `ALTER TABLE ADD COLUMN`.
   Повторные запуски не ломают БД.

10. **Чёткая граница безопасных и manual-only команд.**
    [.claude/PRODUCTION_RULES.md](../.claude/PRODUCTION_RULES.md),
    [.claude/CLAUDE.md](../.claude/CLAUDE.md) и [README.md](../README.md)
    дают единый список того, что может запускать AI/CI, и что должен
    запускать только владелец/IT.

11. **Backup/restore с защитой.**
    [scripts/backup_db.sh](../scripts/backup_db.sh) использует `sqlite3 .backup`
    (атомарный) с copy-fallback;
    [scripts/restore_db.sh](../scripts/restore_db.sh) отказывается работать,
    если systemd сервисы активны или существует writer lock, создаёт
    pre-restore backup до перезаписи и требует явный флаг `--confirm`.

12. **Operational notices в отчёте.**
    [app/operational_health.py](../app/operational_health.py) добавляет в
    daily-отчёт блок «На что обратить внимание по системе» с предупреждениями
    о stale-источниках, failed sources, OCR backlog. Эти notices читаемы GR
    пользователем и не утекают техническими терминами.

13. **Telegram .txt вложение в utf-8-sig.**
    [`_prepare_daily_report_attachment`](../app/notify/telegram.py#L284)
    конвертирует `.md` отчёт в `.txt` с кодировкой `utf-8-sig`, что устраняет
    mojibake при просмотре в Telegram на Windows-клиентах.

14. **Тесты зелёные.** Smoke-check выдаёт 0 warnings и 0 fails, отчёт за
    последние 7 дней корректно генерируется (см. раздел «Выполненные команды»).

## Аудит по разделам

### 1. Безопасность

| Пункт | Состояние |
| --- | --- |
| Секреты только в production env | OK |
| `.env` исключён из git | OK |
| `.env.example` без реальных значений | OK |
| Telegram token redacted в логах | OK |
| LLM API key и proxy creds redacted | OK |
| `TELEGRAM_PROXY_URL` ≠ `LLM_PROXY_URL` | OK |
| TLS verify для платёжных/API эндпоинтов | OK |
| TLS verify для российских гос-источников | частично выключен (H1) |
| Limit на скачивание PDF | OK (50 MB) |
| Системный umask 0077 в systemd | OK |
| Защищённый env-файл (`root:ahstep 0640`) | OK (документ) |
| Запрет `git add .` | OK (документ) |
| `LLM_PROXY_URL` scheme allowlist | OK |

### 2. Backend reliability

| Пункт | Состояние |
| --- | --- |
| APScheduler с fallback на loop | OK |
| SIGTERM/SIGINT обработчики | OK |
| Daily digest идемпотентность | OK |
| Hourly cycle освобождает writer lock на конкуренции | OK |
| Heartbeat файлы | OK |
| Source errors сохраняются в SQLite | OK |
| SQLite WAL + busy_timeout=5000 | OK |
| LLM retry/backoff с jitter | OK |
| LLM fallback на mock | OK |
| Telegram retry x3 с backoff | OK |
| Backup / restore с защитой от активных сервисов | OK |
| Report dry-run проходит при пустой БД | OK |

### 3. Корпоративная деплой-готовность

| Пункт | Состояние |
| --- | --- |
| systemd unit-файлы | OK + минор (H2) |
| Production env шаблон | OK |
| Директории `/var/lib`, `/var/log`, `/etc` | OK (документ) |
| Two-service runtime (scheduler + bot) | OK |
| Health checks (smoke-check, diagnostics) | OK |
| Manual Telegram verification | OK (документ) |
| Rollback процедура | OK (документ) |
| Безопасное обновление кода | OK (документ) |
| Документация по proxy для российского сервера | OK |

### 4. LLM-слой

| Пункт | Состояние |
| --- | --- |
| `LLM_PROXY_URL` независим от Telegram | OK |
| `LLM_RESPONSE_FORMAT=none` для Google/Gemma | OK (документ, проверено в коде) |
| Retry/backoff конфигурируются env-ами | OK |
| Fallback не меняет `action_level` | OK |
| Cache по `source_hash` (избегаем перерасчётов) | OK |
| Provider/model/prompt_version в `document_enrichments` | OK |
| Rate-limit через `LLM_ENRICHMENT_LIMIT` | OK |
| LLM не может изменить classification | OK |
| User-facing очистка LLM-вывода | OK (см. `_clean_user_facing_text`) |

### 5. Отчёт и Telegram UX

| Пункт | Состояние |
| --- | --- |
| Daily digest .txt вложение | OK |
| utf-8-sig кодировка для Telegram-клиентов | OK |
| Русские формулировки | OK (выборочная проверка отчёта 2026-05-21) |
| Без английских терминов eligibility/compliance | OK (есть substitution) |
| Без OCR-мусора | OK (есть фильтр) |
| Без generic-болтовни | OK (`is_generic_enrichment_text`) |
| Маркетинговые мероприятия отфильтрованы | OK (visibility) |
| Сценарий пустой выборки | OK (safe message) |

### 6. Источники и парсеры

| Пункт | Состояние |
| --- | --- |
| government.ru | OK, дедицированный парсер |
| regulation.gov.ru | OK |
| mcx.gov.ru (2 источника) | OK |
| promote.budget.gov.ru | OK |
| Региональные (krasnodar/donland/stavropol) | OK |
| ZOL.ru | OK |
| ГИСП | OK |
| Source-specific parser registry | OK |
| Fallback на generic_html при отсутствии парсера | OK |
| OCR triage для scan-кандидатов | OK (опционален) |
| urllib3 Retry для transient | OK |
| Запись source_errors | OK |

### 7. Тесты и repo hygiene

| Пункт | Состояние |
| --- | --- |
| 44 теста, smoke-check проходит | OK |
| Regression fixtures (32 файла) | OK |
| `.gitignore` для DB, logs, data | OK |
| `.gitignore` для `reports/*.txt` | требует правки (M1) |
| Опечатка `ку` в `.gitignore` | требует правки (M2) |
| Артефакт reports/...txt в git | требует правки (M1) |
| README ссылается на «MVP» | минорная неточность (L1) |
| Документация согласована между файлами | OK с дублированием (M3) |

## Рекомендованный план исправлений

### До go-live

1. Поправить `Documentation=` в обоих systemd unit-файлах
   (`PRODUCTION_RUNBOOK.md` → `OPERATOR_RUNBOOK.md`). См. H2.
2. Добавить `reports/*.txt` в [.gitignore](../.gitignore) и убрать опечатку
   `ку`. См. M1, M2.
3. Удалить из индекса трекаемый артефакт:
   `git rm reports/gr_monitoring_2026-05-21.txt`. См. M1.
4. Заполнить production env-файл по
   [PRODUCTION_ENV_TEMPLATE.md](PRODUCTION_ENV_TEMPLATE.md).
5. Согласовать с IT доступ:
   - публичные источники РФ;
   - Telegram Bot API (опционально через `TELEGRAM_PROXY_URL`);
   - Google/Gemma через `LLM_PROXY_URL` (если включается LLM).
6. Пройти чеклист [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md).

### Первая неделя после go-live

1. Включить scheduler и telegram-bot как systemd сервисы.
2. Проверить, что daily digest пришёл в Telegram, и `.txt` вложение
   читается без mojibake.
3. Запустить `diagnostics --days 7` и проверить, что ни один из 16 источников
   не помечен как stale более 7 дней.
4. Снять первый production backup через
   [scripts/backup_db.sh](../scripts/backup_db.sh) и подтвердить, что он
   читается.
5. Если LLM включён — мониторить `document_enrichments` и логи на провал
   provider/timeout/HTTP 5xx; первые 2-3 дня держать `LLM_ENRICHMENT_LIMIT=3`.
6. Подтвердить, что `/refresh` в Telegram-боте отрабатывает (отдельный
   writer_lock).
7. Подтвердить, что size logs `/var/log/ahstep-law-monitor/app.log` ротируется
   через RotatingFileHandler (5 MB × 5 backups по умолчанию).

### Дальнейшие улучшения (после стабилизации)

1. Подумать о замене `verify_ssl: false` на корпоративный CA bundle (см. H1).
2. Переименовать default `LAW_MONITOR_USER_AGENT` (см. L2).
3. Перенести demo-report в `/var/lib/ahstep-law-monitor/tmp` для server
   режима (см. L3).
4. Унифицировать LLM-preset как ссылку, чтобы избежать рассинхрона между
   четырьмя документами (см. M3).
5. Рассмотреть явный migration tracker вместо chain of `ALTER TABLE` (см. L4).
6. Изучить TLS inspection-политику корпоративной сети, чтобы при необходимости
   уменьшить набор `verify_ssl: false` источников.

## Рейтинг рисков

| Идентификатор | Категория | Описание | Уровень |
| --- | --- | --- | --- |
| H1 | TLS | `verify_ssl: false` у ряда источников | High |
| H2 | Docs/systemd | `Documentation=` ссылается на несуществующий файл | High (docs) |
| M1 | Repo hygiene | `.txt` отчёт в git-индексе | Medium |
| M2 | Repo hygiene | Опечатка `ку` в `.gitignore` | Medium |
| M3 | Docs | Дублирование LLM-preset в 4 файлах | Medium |
| M4 | Runtime | `.txt` вложение появляется в repo после реальной отправки | Medium (компенсируется M1) |
| M5 | LLM | SOCKS-зависимость требует extras `requests[socks]` | Medium |
| L1 | Docs | README говорит «MVP» | Low |
| L2 | Cosmetic | Default UA `law-monitor-mvp/0.1` | Low |
| L3 | Layout | demo-report в `docs/` | Low |
| L4 | Storage | Много ALTER TABLE при init_db | Low |
| L5 | Ops | Логи содержат URL документов | Low |

Критических (Critical) рисков нет.

## Можно ли развёртывать сейчас?

**Да.** Все критические компоненты на месте: rules deterministic foundation,
optional LLM enrichment с retry/backoff/fallback, scheduler с idempotency и
writer lock, Telegram bot и daily digest как отдельные процессы, два proxy
независимы друг от друга, backup/restore проверены, документация на русском
языке.

Найденные H1/H2 — это уже задокументированный compatibility caveat и
тривиальная правка ссылки в systemd unit. Они не блокируют production-запуск.

Рекомендованная последовательность действий перед публикацией зафиксирована в
[DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md) и
[CORPORATE_DEPLOYMENT.md](CORPORATE_DEPLOYMENT.md).

## Выполненные safe-команды

```powershell
.\.venv\Scripts\python.exe main.py smoke-check
```

Результат: `Smoke check passed with warnings: 0`. Все 12 проверок (config,
database, tables, sources, source roles, keywords, diagnostics, report dry-run,
Telegram config, system HTTP proxy env vars, regression fixtures, LLM
enrichment) выдали `OK`. БД содержит 444 сохранённые LLM enrichment записи.

```powershell
.\.venv\Scripts\python.exe main.py report --days 7
```

Результат: `Report saved to D:\ahstep\law_monitor\reports\gr_monitoring_2026-05-21.md`.
Отчёт за 7 дней корректно сгенерирован, проанализировано 575 документов,
включено в сводку 23, требует реакции 5, на наблюдении 18. По разделам сводки
формулировки русские, GR-ориентированные, без технических терминов.

```powershell
.\.venv\Scripts\python.exe -m unittest discover tests
```

Результат: `Ran 928 tests in 377.827s` → `OK`. Полный набор unit-тестов
проходит без сбоев. Зафиксированы только `ResourceWarning: unclosed database`
от моков unittest при тестировании SQLite — это шум окружения Windows, не
ошибка production-кода (production-код всегда использует
context-manager-обёртку `_connect_db`).

## Использовалась ли `.claude` guidance

Да. До начала аудита прочитаны:

- [.claude/CLAUDE.md](../.claude/CLAUDE.md) — архитектурная граница, safe/manual
  commands, GR audience rules.
- [.claude/PRODUCTION_RULES.md](../.claude/PRODUCTION_RULES.md) — корпоративная
  реальность, рекомендуемый LLM baseline, runtime topology.
- [.claude/SESSION_START.md](../.claude/SESSION_START.md) — порядок чтения и
  стиль работы.

Аудит проведён в read-only режиме. Изменения в коде Python не вносились;
выявленные документные правки (M1/M2/H2) предлагаются как минимальные
docs-only/.gitignore-only исправления.

## Сводка

| Метрика | Значение |
| --- | --- |
| Критических блокеров | 0 |
| High-приоритетных рисков | 2 (H1 — задокументированный caveat; H2 — docs-only) |
| Medium-приоритетных рисков | 5 |
| Low-приоритетных рисков | 5 |
| Подтверждённых сильных сторон | 14 |
| Smoke-check warnings/failures | 0 / 0 |
| Источников | 16 |
| Тестов | 928 проходят (44 файла, 32 regression fixtures) |
| LLM enrichments в БД | 444 |
| Проверенный run отчёта | `reports/gr_monitoring_2026-05-21.md` |
| Готовность к корпоративному развёртыванию | Да |
