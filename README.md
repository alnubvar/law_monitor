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
- OCR triage queue (without OCR runtime);
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

### Why OCR runtime is not enabled yet

Current phase keeps runtime lightweight and deterministic:

- no heavy OCR dependencies in production path;
- no new infrastructure or cloud spend before ROI is validated.

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
python main.py ocr-queue
python main.py ocr-queue --status pending
python main.py ocr-queue --priority high
python main.py ocr-queue --status pending --limit 20
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
python main.py report --days 7 --action-level requires_attention watchlist --max-items 30
python main.py run-telegram-bot
python main.py check-tracked
python -m unittest -v
```

## Deployment

Deployment guide: [docs/deployment.md](docs/deployment.md)

This repository currently recommends Windows-first MVP deployment (`Task Scheduler` + SQLite), with optional Linux `systemd` examples.

## Screenshots / Examples

- Demo summary: [DEMO_SUMMARY.md](DEMO_SUMMARY.md)
- Safe demo report artifact: [docs/demo_report.md](docs/demo_report.md)
- Example generated reports: `reports/gr_monitoring_*.md`

## Roadmap

- selective OCR runtime for high-priority queue items;
- optional LLM-assisted summaries on top of clean extracted text;
- retrieval and historical analysis (RAG-style workflows);
- extraction robustness improvements for source-specific edge cases.

## Constraints (Current Phase)

- no OCR runtime in production pipeline;
- no changes to `action_level` business logic in triage/docs phase;
- no heavy dependency additions;
- no overengineering of infra for MVP stage.
