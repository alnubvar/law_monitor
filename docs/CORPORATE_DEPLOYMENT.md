# Руководство по корпоративному развертыванию

Документ предназначен для развертывания AHSTEP `law_monitor` на корпоративном
Linux-сервере. Это эксплуатационная инструкция: без изменения архитектуры,
парсеров, логики `action_level` и без добавления новых инфраструктурных
компонентов.

## Целевая схема размещения

Используйте отдельного системного пользователя приложения. Runtime-данные должны
храниться вне git checkout.

```text
/opt/ahstep/law_monitor                    # git checkout and virtualenv
/etc/ahstep-law-monitor/law-monitor.env    # production env, secrets included
/var/lib/ahstep-law-monitor/data           # SQLite DB and runtime state
/var/lib/ahstep-law-monitor/reports        # generated reports
/var/lib/ahstep-law-monitor/backups        # DB backups
/var/lib/ahstep-law-monitor/tmp            # temp files
/var/log/ahstep-law-monitor                # rotating app log
```

Рекомендуемая учетная запись Linux:

```text
user:  ahstep
group: ahstep
shell: /usr/sbin/nologin
home:  /opt/ahstep
```

## Системные требования

- Linux-сервер с исходящим HTTPS-доступом к настроенным публичным нормативным источникам.
- Python 3.12 или новее.
- `git`.
- `sqlite3` рекомендуется для безопасных online-backup через `.backup`.
- `systemd`.
- Корпоративный proxy или настройка CA, если этого требует сеть.
- OCR runtime только если IT осознанно включает OCR позднее. По умолчанию OCR выключен.

## Создание пользователя, директорий и прав

```bash
sudo useradd --system --home /opt/ahstep --shell /usr/sbin/nologin ahstep || true

sudo install -d -o ahstep -g ahstep -m 0755 /opt/ahstep
sudo install -d -o ahstep -g ahstep -m 0750 /var/lib/ahstep-law-monitor/data
sudo install -d -o ahstep -g ahstep -m 0750 /var/lib/ahstep-law-monitor/reports
sudo install -d -o ahstep -g ahstep -m 0750 /var/lib/ahstep-law-monitor/backups
sudo install -d -o ahstep -g ahstep -m 0750 /var/lib/ahstep-law-monitor/tmp
sudo install -d -o ahstep -g ahstep -m 0750 /var/log/ahstep-law-monitor
sudo install -d -o root -g ahstep -m 0750 /etc/ahstep-law-monitor
```

Права на production env:

```bash
sudo chown root:ahstep /etc/ahstep-law-monitor/law-monitor.env
sudo chmod 0640 /etc/ahstep-law-monitor/law-monitor.env
```

Редактировать env-файл должен только `root`. Сервисный пользователь `ahstep`
получает доступ на чтение через права группы.

## Клонирование или обновление репозитория

Первичная установка:

```bash
cd /opt/ahstep
sudo -u ahstep git clone <REPO_URL> law_monitor
cd /opt/ahstep/law_monitor
```

Обновление существующего checkout:

```bash
sudo systemctl stop ahstep-scheduler.service ahstep-telegram-bot.service
cd /opt/ahstep/law_monitor
sudo -u ahstep git pull --ff-only
```

Сгенерированные БД, отчеты, backups, логи и временные файлы должны оставаться в
`/var/lib` и `/var/log`, а не внутри репозитория.

## Python virtualenv

```bash
cd /opt/ahstep/law_monitor
sudo -u ahstep python3.12 -m venv .venv
sudo -u ahstep .venv/bin/python -m pip install --upgrade pip
sudo -u ahstep .venv/bin/python -m pip install -e .
```

После каждого обновления кода повторно выполните:

```bash
cd /opt/ahstep/law_monitor
sudo -u ahstep .venv/bin/python -m pip install -e .
```

## Production env-файл

Реальный env-файл должен находиться здесь:

```text
/etc/ahstep-law-monitor/law-monitor.env
```

Используйте [PRODUCTION_ENV_TEMPLATE.md](PRODUCTION_ENV_TEMPLATE.md) как
чеклист. Не размещайте реальные секреты в `.env`, `.env.example`, README,
истории shell, тикетах или git-коммитах.

Минимальные production paths:

```env
LAW_MONITOR_DATA_DIR=/var/lib/ahstep-law-monitor/data
LAW_MONITOR_REPORTS_DIR=/var/lib/ahstep-law-monitor/reports
LAW_MONITOR_BACKUP_DIR=/var/lib/ahstep-law-monitor/backups
LAW_MONITOR_TMP_DIR=/var/lib/ahstep-law-monitor/tmp
LAW_MONITOR_LOG_DIR=/var/log/ahstep-law-monitor
LAW_MONITOR_DB_PATH=/var/lib/ahstep-law-monitor/data/law_monitor.db
LAW_MONITOR_LOG_FILE=/var/log/ahstep-law-monitor/app.log
```

## Обертка для ручных команд

Используйте эту обертку для ручного запуска команд, чтобы окружение было
загружено так же, как его загружает systemd:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py smoke-check'
```

Меняйте только финальную часть `main.py ...`.

## Инициализация базы данных

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py init-db'
```

## Безопасные проверки до запуска сервисов

Эти команды не собирают live-источники и не отправляют сообщения в Telegram:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python -m unittest discover tests'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py smoke-check'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py report --days 7'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py diagnostics --days 7'
```

Перед включением сервисов просмотрите предупреждения. На новой или пустой БД
часть предупреждений о свежести источников может быть ожидаемой, но перед
передачей в эксплуатацию они должны быть понятны.

## Сервисы systemd

Шаблоны сервисов находятся в `deploy/systemd/`:

```text
deploy/systemd/ahstep-scheduler.service
deploy/systemd/ahstep-telegram-bot.service
```

Установка:

```bash
cd /opt/ahstep/law_monitor
sudo cp deploy/systemd/ahstep-scheduler.service /etc/systemd/system/
sudo cp deploy/systemd/ahstep-telegram-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Telegram bot включайте только после проверки token, chat ID и сетевого маршрута
со стороны IT. Scheduler включайте только после прохождения чеклиста первого
запуска.

```bash
sudo systemctl enable --now ahstep-telegram-bot.service
sudo systemctl enable --now ahstep-scheduler.service
```

Должен работать только один scheduler. Не запускайте параллельно cron,
`screen`, `tmux` или второй systemd unit, который вызывает `main.py run-scheduler`.

## Последовательность первого запуска

1. Создать директории и env-файл.
2. Склонировать repo и установить virtualenv.
3. Выполнить `init-db`.
4. Выполнить `python -m unittest discover tests`.
5. Выполнить `python main.py smoke-check`.
6. Выполнить `python main.py report --days 7`.
7. Выполнить `python main.py diagnostics --days 7` и проверить свежесть источников.
8. IT или владелец проекта вручную выполняет проверку Telegram.
9. IT или владелец проекта вручную выполняет первый dry run scheduler.
10. Включить systemd services.
11. Подтвердить получение первого daily digest.
12. Создать и зафиксировать DB backup.

## Ручная проверка Telegram

Эти команды могут отправлять сообщения в Telegram. Они относятся к manual-only
и выполняются IT или владельцем проекта, когда целевой чат готов к тесту:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py telegram-check'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py notify-test'
```

Если для Telegram нужен proxy, задайте `TELEGRAM_PROXY_URL` в
`/etc/ahstep-law-monitor/law-monitor.env`, перезапустите сервисы и повторите
ручную проверку.

## Первый dry run scheduler

Manual-only, потому что команда выполняет live collect/analyze/report/notify:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py run-scheduler --once'
```

Запускайте только когда IT или владелец проекта готовы к live-доступу к
источникам и возможной доставке Telegram.

## Генерация отчета

Безопасная генерация отчета по текущему содержимому БД:

```bash
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py report --days 7'
```

Отчеты записываются в:

```text
/var/lib/ahstep-law-monitor/reports
```

Не копируйте production reports в `reports/` внутри git checkout.

## Rollback

Rollback применяется, если обновление ломает smoke checks, запуск сервисов,
генерацию отчета, доставку Telegram или работу источников.

```bash
sudo systemctl stop ahstep-scheduler.service ahstep-telegram-bot.service

cd /opt/ahstep/law_monitor
sudo -u ahstep git checkout <known-good-sha-or-tag>
sudo -u ahstep .venv/bin/python -m pip install -e .

sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py init-db'
sudo -u ahstep bash -lc 'cd /opt/ahstep/law_monitor && set -a && . /etc/ahstep-law-monitor/law-monitor.env && set +a && .venv/bin/python main.py smoke-check'

sudo systemctl start ahstep-scheduler.service ahstep-telegram-bot.service
sudo journalctl -u ahstep-scheduler.service -n 100 --no-pager
sudo journalctl -u ahstep-telegram-bot.service -n 100 --no-pager
```

Восстанавливайте БД только если неудачное обновление изменило состояние БД и
для rollback требуется предыдущее состояние. См. [BACKUP_RESTORE.md](BACKUP_RESTORE.md).

