# Project Context

This file gives AI assistants compact current-state context.

Use it together with `.claude/CLAUDE.md` and `REPO_MAP.md`.

## Current System Shape

The project already has the main product backbone in place:

- source collection from configured public sources;
- extraction for common document formats;
- SQLite-based storage and history;
- rule-based analysis and classification;
- Markdown report generation;
- Telegram notification and interaction flows;
- scheduled and manual run paths;
- tests, fixtures, and smoke-style operational checks.

This means most work should be treated as improving a running monitoring product, not inventing a new one.

## Current Constraints And Pain Points

Important things to remember about the current state:

- the product is judged by relevance and clarity, not by raw collection volume;
- noisy outputs are harmful because they reduce trust in monitoring;
- false positives create alert fatigue for GR users;
- user-facing text must avoid technical leakage;
- parsers and extraction flows need to stay defensive because source quality is inconsistent;
- reports, Telegram behavior, and scheduler flows are sensitive surfaces;
- some capabilities may exist in partial or optional form, so changes should be grounded in the current code, not assumed from generic patterns.

In short: the project benefits more from precision, stability, and cleaner communication than from ambitious new abstractions.
