# Security notes для корпоративного IT

## Секреты

Production-секреты должны находиться только здесь:

```text
/etc/ahstep-law-monitor/law-monitor.env
```

Обязательные права:

```text
root:ahstep 0640
```

Не храните реальные tokens, chat IDs, proxy credentials или LLM API keys в git,
`.env`, `.env.example`, README, tickets, screenshots или shell snippets.

`.gitignore` исключает `.env`, `.env.*`, DB files, logs, data directories и
generated reports. Это правило нужно сохранить.

## Размещение данных

Production runtime data должны храниться вне checkout:

```text
/var/lib/ahstep-law-monitor/data
/var/lib/ahstep-law-monitor/reports
/var/lib/ahstep-law-monitor/backups
/var/lib/ahstep-law-monitor/tmp
/var/log/ahstep-law-monitor
```

Git checkout в `/opt/ahstep/law_monitor` должен содержать только код,
configuration templates и документацию.

## Безопасность Telegram token

Telegram bot token позволяет отправлять сообщения от имени bot. Считайте его
секретом.

- Rotate token при любом раскрытии.
- Держите `TELEGRAM_CHAT_ID` ограниченным approved corporate destination.
- Используйте `TELEGRAM_PROXY_URL` только если это требуется corporate network policy.
- Перезапускайте сервисы после изменения token, chat или proxy.

## LLM provider и данные

На первичном handoff LLM enrichment должен быть выключен, если он не согласован:

```env
LLM_ENRICHMENT_ENABLED=false
LLM_DOCUMENT_ENRICHMENT_ENABLED=false
```

Если режим позднее включен, в настроенный LLM provider отправляется только
публичный regulatory/source text, извлеченный монитором, для document-card
enrichment. Не обрабатывайте через LLM path конфиденциальные материалы AHSTEP,
если owner/IT не согласовали provider, data handling terms, retention policy и
network route.

`LLM_PROXY_URL` задает отдельный proxy только для OpenAI-compatible LLM provider
requests. Он не наследуется из `TELEGRAM_PROXY_URL` и не применяется к Telegram
или source requests. Поддерживаются `http`, `https`, `socks5` и `socks5h`
proxy URLs, если SOCKS extras доступны через `requests[socks]`.

Telegram proxy и LLM proxy разделены намеренно: `TELEGRAM_PROXY_URL` помогает
только Telegram Bot API и не обеспечивает доступ к Google/Gemini. Для
Google/Gemma OpenAI-compatible endpoint через корпоративную сеть используйте
отдельный `LLM_PROXY_URL` и храните его только в production env-файле.

Не логируйте полный `LLM_PROXY_URL`. Если proxy URL содержит credentials, они
должны храниться только в production env-файле и попадать в diagnostics/errors
только в redacted виде. LLM API key redaction также должна сохраняться.

LLM provider failures, включая 429/5xx, timeout и proxy/network errors, не
должны останавливать deterministic pipeline. Они могут сделать report enrichment
более общим, но не должны менять `action_level`, collection, report generation
или Telegram delivery.

## TLS и corporate CA

Некоторые настроенные публичные источники используют `verify_ssl: false` в
`config/sources.yaml`, потому что TLS-поведение этих публичных источников может
быть нестабильным. Это source-specific compatibility caveat, а не общая
рекомендация по безопасности.

Корпоративному IT по возможности следует предпочитать установку или настройку
корректного corporate CA bundle для inspected HTTPS traffic. Не добавляйте новые
`verify_ssl: false` во время deployment без явной необходимости.

## Backups

Backups могут содержать collected public source text, report history,
operational metadata и Telegram-related runtime records. Храните backups в
утвержденном server backup path:

```text
/var/lib/ahstep-law-monitor/backups
```

Защищайте backups теми же правилами доступа, что и production DB. Не
прикладывайте DB backups к tickets или email без явного согласования.

## Эксплуатационные границы

Safe commands для автоматических проверок не должны отправлять Telegram messages
или запускать live collection/enrichment. Используйте список safe commands в
[DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md).

Manual-only commands включают Telegram checks, live collect, forced analyze, LLM
enrichment и scheduler one-shot runs. Их должен запускать только project
owner/IT во время плановой validation.
