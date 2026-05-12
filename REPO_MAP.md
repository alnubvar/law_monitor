# Repo Map

Compact architecture map for AI assistants working in `law_monitor`.

Use this for quick repo orientation. Use `CLAUDE.md` for working rules and `PROJECT_CONTEXT.md` for business context.

## Top Level

- `main.py` - CLI entry point and command router.
- `app/` - main application package.
- `config/` - YAML source and keyword config.
- `tests/` - unit, regression, smoke, and operational tests.
- `docs/` - deployment and production runbooks.
- `scripts/` - helper PowerShell scripts.
- `reports/` - generated Markdown reports.
- `data/` - SQLite DB and runtime artifacts.

## Fast Entry Points

Start with these files:

- `main.py` - what commands exist.
- `app/pipeline/collect.py` - collection, extraction, dedup, OCR queue sync.
- `app/pipeline/analyze.py` - analysis and business-field updates.
- `app/pipeline/digest.py` - report generation entry.
- `app/reports/markdown_report.py` - report layout, filtering, and buckets.
- `app/notify/telegram.py` - Telegram user-facing messages and command responses.
- `app/notify/telegram_bot.py` - interactive bot polling and `/refresh`.
- `app/scheduler.py` - hourly/daily automation.
- `app/storage.py` - DB schema and persistence.

`main.py` also wraps write commands with `writer_lock`.

## Main CLI Commands

Defined in `main.py`:

- `collect`
- `audit-extraction`
- `analyze`
- `report`
- `demo-report`
- `run`
- `run-scheduler`
- `run-telegram-bot`
- `notify-test`
- `check-tracked`
- `ocr-*`
- `diagnostics`
- `smoke-check`

## app/ Directory

### Core modules

- `app/config.py` - env loading, paths, logging, source/keyword loading.
- `app/models.py` - Pydantic models for source configs, documents, analysis, digest items.
- `app/storage.py` - SQLite schema, queries, runtime events, OCR queue, tracking state.
- `app/run_lock.py` - write lock used by CLI, scheduler, and bot refresh.
- `app/periods.py` - today/yesterday/rolling period logic.
- `app/operational_health.py` - operational notices for reports and Telegram.
- `app/user_facing.py` - GR-facing titles, reasons, actions, wording helpers.
- `app/visibility.py` - user-visible filtering, buckets, and display sections.
- `app/scheduler.py` - automation cycles and Telegram alert flow.

### `app/pipeline/`

Pipeline orchestration lives here.

- `run.py` - full pipeline: `init_db -> collect -> analyze -> report`.
- `collect.py` - source iteration, extraction, dedup, extraction audit, OCR queue updates.
- `analyze.py` - analysis, `action_level` updates, optional enrichment persistence.
- `digest.py` - loads recent docs and saves Markdown reports.
- `deduplicate.py` - content-hash dedup helpers.
- `diagnostics.py` - diagnostics snapshot builders.
- `smoke.py` - safe smoke checks.
- `tracking.py` - tracked document refresh/check logic.
- `ocr_runtime.py` - OCR queue processing and backfill.

### `app/sources/`

Source adapters and parser-specific fetch logic.

- `base.py` - shared source interface.
- `generic_html_source.py` - generic HTML listing fallback.
- `government_source.py` - government-specific fetch logic.
- `regional_law_source.py` - regional law/NPA fetch logic.
- `krasnodar_source.py` - Krasnodar source logic.
- `donland_source.py` - Rostov/Donland source logic.
- `stavropol_source.py` - Stavropol source logic.

The parser registry is in `app/pipeline/collect.py`.

### `app/extractors/`

Raw content extraction:

- `html_extractor.py`
- `pdf_extractor.py`
- `docx_extractor.py`
- `ocr_extractor.py`
- `date_extractor.py`
- `site_extractors.py`

Look here for missing text, bad dates, scan candidates, or OCR behavior.

### `app/rules/`

Deterministic classification logic.

Key files:

- `business_signal_rules.py`
- `support_measure_rules.py`
- `regional_npa_rules.py`
- `source_role_rules.py`
- `page_type_rules.py`
- `news_rules.py`
- `news_background_guard.py`
- `noise_rules.py`
- `title_normalization.py`
- `ahstep_domain_rules.py`

If a document got the wrong `action_level`, inspect `app/rules/` plus `app/llm/mock_client.py`.

### `app/reports/`

- `markdown_report.py` - main GR report builder, visible section layout, filtering, bucketing.

### `app/notify/`

- `telegram.py` - digest sending, command responses, short user-facing Telegram text.
- `telegram_bot.py` - polling bot, keyboards, report attachments, `/refresh`.
- `telegram_formatter.py` - digest formatting helpers.

### `app/llm/`

Optional enrichment layer, not the primary classifier.

- `mock_client.py` - current analysis entry used by `run_analyze`.
- `enrichment.py` - optional enrichment interface and provider selection.
- `facts_extractor.py` - fact extraction helpers.
- `prompts.py` - prompt text.
- `base.py` - base abstractions.

## Main Runtime Flows

### Collect

`main.py collect` -> `app/pipeline/collect.py`

1. load enabled sources from `config/sources.yaml`
2. create source parser from registry
3. fetch items from source
4. extract text via `app/extractors/`
5. write extraction audit
6. deduplicate by URL and content hash
7. save raw document to SQLite
8. sync OCR queue for scan-candidate PDFs

### Analyze

`main.py analyze` -> `app/pipeline/analyze.py`

1. load unanalyzed documents from storage
2. analyze through `app/llm/mock_client.py`
3. update business fields in `documents`
4. optionally persist enrichment
5. reprioritize OCR queue for high-value documents

### Report

`main.py report` -> `app/pipeline/digest.py` -> `app/reports/markdown_report.py`

1. load recent documents
2. backfill missing published dates
3. filter visible docs
4. bucket by user-facing visibility logic
5. generate Markdown report
6. save file into `reports/`

### Notify

Scheduler alerts live in `app/scheduler.py`.

On-demand bot responses live in:

- `app/notify/telegram.py`
- `app/notify/telegram_bot.py`

Scheduler path:

1. collect
2. analyze
3. notify new `requires_attention`
4. build/send daily digest

Bot path:

1. poll Telegram updates
2. map buttons/commands
3. build response from DB state
4. optionally generate report attachment

### Scheduler

`main.py run-scheduler` -> `app/scheduler.py`

- hourly cycle: collect -> analyze -> notify urgent items
- daily cycle: generate report -> send digest
- uses `writer_lock`
- uses APScheduler if available, otherwise loop fallback

## Where To Look

- collect logic: `app/pipeline/collect.py`, `app/sources/`, `config/sources.yaml`
- parser behavior: `app/sources/`, `app/extractors/site_extractors.py`
- extraction and OCR: `app/extractors/`, `app/pipeline/ocr_runtime.py`
- analyze logic: `app/pipeline/analyze.py`, `app/llm/mock_client.py`, `app/rules/`
- report logic: `app/pipeline/digest.py`, `app/reports/markdown_report.py`
- user-facing wording: `app/user_facing.py`, `app/notify/telegram.py`
- visibility and filtering: `app/visibility.py`
- notify and bot logic: `app/notify/`, `app/scheduler.py`
- scheduler logic: `app/scheduler.py`
- storage and DB schema: `app/storage.py`
- source and keyword config: `config/sources.yaml`, `config/keywords.yaml`
- tracking logic: `app/pipeline/tracking.py`, `app/storage.py`, `app/notify/telegram.py`

## Test Anchors

Useful starting points in `tests/`:

- pipeline and CLI: `test_main_cli.py`, `test_smoke.py`, `test_run_lock.py`
- storage and config: `test_storage.py`, `test_config.py`
- source parsers: `test_government_source.py`, `test_krasnodar_source.py`, `test_donland_source.py`, `test_stavropol_source.py`
- extraction/date/OCR: `test_site_extractors.py`, `test_date_extractor.py`, `test_ocr_extractor.py`, `test_ocr_runtime.py`
- analysis/business logic: `test_analyze.py`, `test_business_value.py`, `test_mock_analyze.py`
- reports/visibility/user-facing: `test_report_generation.py`, `test_visibility.py`, `test_user_facing.py`
- Telegram/tracking/ops: `test_telegram_notify.py`, `test_telegram_bot.py`, `test_tracking.py`, `test_operational_health.py`
- regression fixtures: `tests/fixtures/regression/`
