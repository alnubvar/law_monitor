# AHSTEP GR Monitor MVP

Production-style MVP for GR monitoring of agricultural policy signals: regional/federal regulations, support measures, and industry news.

## Project Overview

AHSTEP GR Monitor is built for GR and policy teams that need daily visibility into:

- what requires immediate action;
- what should stay on watchlist;
- what is useful background but not actionable.

The system continuously collects source documents, extracts text, applies rule-based analysis, stores results in SQLite, and delivers reports + Telegram summaries.

## Architecture

Pipeline flow:

1. `collect` -> source fetch + link filtering + extraction audit
2. `extract` -> `html/pdf/docx` text extraction
3. `analyze` -> rule-based classification + `action_level` + business facts
4. `report` -> markdown GR report
5. `notify` -> Telegram digest and interactive commands

Core entrypoint: [main.py](main.py)

## Current Capabilities

- source collection from federal and regional endpoints;
- extraction for `html`, `pdf`, `docx`;
- extraction diagnostics (`diagnostics`, extraction quality, source coverage);
- OCR triage queue + optional local OCR runtime (disabled by default);
- interactive Telegram bot with commands and reply keyboard;
- document tracking (`/track`, `/untrack`, `/tracked`);
- archive search (`/search`);
- manual refresh flow (`/refresh`) with cooldown;
- smoke checks and regression test suite.

## Action Levels

- `requires_attention`: requires GR action now.
- `watchlist`: important to monitor, no immediate action.
- `background`: useful context without direct action signal.
- `irrelevant`: noisy/service/non-target material.

## OCR Triage Workflow

### Why scan-candidates exist

Some PDFs are effectively scans or have weak/no text layer. They are detected as `scan_candidate` during extraction audit.

### OCR runtime mode

Local OCR runtime is supported, but remains opt-in:

- `LAW_MONITOR_OCR_ENABLED=false` by default;
- manual-first triage remains primary workflow;
- cloud OCR stays out of scope until ROI is validated.

### Why OCR queue is useful

OCR triage prevents scan-heavy documents from being lost:

- keeps a managed queue of OCR candidates;
- gives GR team visibility into pending work;
- supports manual status updates (`pending`, `in_review`, `done`, `skipped`).

### Current strategy

- manual-first triage now;
- optional local OCR later (selective, low-risk rollout);
- cloud OCR only after ROI validation.

### Priority model

- `high`: visible policy signals (`requires_attention` / `watchlist`) or source `Нормативные акты Краснодарского края`.
- `medium`: default for unknown/neutral candidates.
- `low`: non-actionable (`irrelevant`) candidates.

### Commands

```bash
python main.py ocr-check
python main.py ocr-queue
python main.py ocr-queue --status pending
python main.py ocr-queue --priority high
python main.py ocr-queue --status pending --limit 20
python main.py ocr-backfill --source "Нормативные акты Краснодарского края" --limit 10
python main.py ocr-run --source "Нормативные акты Краснодарского края" --limit 1
python main.py ocr-mark <url> --status done
python main.py ocr-mark <url> --status in_review --notes "checking text quality"
```

Telegram:

```text
/ocr
```

## Telegram Commands

| Command | Purpose |
| --- | --- |
| `/start` | open menu |
| `/help` | show command list |
| `/status` | system status and freshness |
| `/today` | visible documents for today |
| `/urgent [days]` | `requires_attention` documents |
| `/watchlist [days]` | watchlist documents |
| `/report [days]` | short summary + `.txt` attachment |
| `/sources` | source health summary |
| `/ocr` | OCR triage queue summary |
| `/search <query>` | archive search |
| `/track <url>` | add document to tracking |
| `/untrack <url>` | remove from tracking |
| `/tracked` | list active tracked documents |
| `/refresh` | manual collect/analyze/report run |

## Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
python main.py init-db
python main.py smoke-check
python main.py run-scheduler --once
```

## Common CLI Commands

```bash
python main.py collect
python main.py audit-extraction
python main.py analyze --force
python main.py diagnostics --days 7
python main.py ocr-check
python main.py ocr-backfill --limit 50
python main.py ocr-run --limit 10
python main.py report --days 7 --action-level requires_attention watchlist --max-items 30
python main.py run-telegram-bot
python main.py check-tracked
python -m unittest -v
```

## Deployment

Deployment guide: [docs/deployment.md](docs/deployment.md)

Operations docs:

- [docs/PRODUCTION_RUNBOOK.md](docs/PRODUCTION_RUNBOOK.md)
- [docs/ACCEPTANCE_CHECKLIST.md](docs/ACCEPTANCE_CHECKLIST.md)
- [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md)

This repository currently recommends Windows-first MVP deployment (`Task Scheduler` + SQLite), with optional Linux `systemd` examples.

Windows OCR note (optional local runtime):

- Tesseract binary example: `C:\Program Files\Tesseract-OCR\tesseract.exe`
- Tessdata path: `C:\Program Files\Tesseract-OCR\tessdata`
- env:
  - `LAW_MONITOR_OCR_ENABLED=true`
  - `LAW_MONITOR_OCR_LANGUAGE=rus+eng`
  - `LAW_MONITOR_OCR_TESSDATA_PATH=C:\Program Files\Tesseract-OCR\tessdata`

## Screenshots / Examples

- Demo summary: [DEMO_SUMMARY.md](DEMO_SUMMARY.md)
- Safe demo report artifact: [docs/demo_report.md](docs/demo_report.md)
- Example generated reports: `reports/gr_monitoring_*.md`

## Roadmap

- optional LLM-assisted summaries on top of clean extracted text;
- retrieval and historical analysis (RAG-style workflows);
- extraction robustness improvements for source-specific edge cases.

## Constraints (Current Phase)

- no cloud OCR in production pipeline;
- no changes to `action_level` business logic in triage/docs phase;
- no heavy dependency additions;
- no overengineering of infra for MVP stage.
