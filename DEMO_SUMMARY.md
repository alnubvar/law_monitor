# Demo Summary

AHSTEP GR Monitor demonstrates a compact production-style monitoring loop for GR teams:

1. collect source materials;
2. extract text (`html/pdf/docx`);
3. assign action levels with rule-based analysis;
4. generate markdown report;
5. send Telegram summaries and serve interactive commands.

## What To Show In Demo

- `requires_attention` items are kept focused and actionable;
- watchlist/background signals are separated from noise;
- source diagnostics highlight extraction and coverage gaps;
- OCR scan-candidates are managed through triage queue (`/ocr`, `ocr-queue`, `ocr-mark`);
- tracking and refresh commands support daily GR operations.

## Demo Run

```bash
python main.py smoke-check
python main.py diagnostics --days 7
python main.py report --days 7 --action-level requires_attention watchlist --max-items 25
python main.py ocr-queue
```

## Key Constraints

- no OCR runtime in core pipeline (triage only);
- no changes to action-level policy logic in this phase;
- no heavy infrastructure requirements.
