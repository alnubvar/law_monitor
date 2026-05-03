from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config import DOCS_DIR, REPORTS_DIR, ensure_directories
from app.reports.markdown_report import generate_markdown_report, save_markdown_report
from app.storage import (
    backfill_missing_published_at,
    init_db,
    list_recent_documents,
    list_recent_source_errors,
)


def run_digest(
    days: int = 7,
    output_path: str | None = None,
    *,
    db_path: Path | str | None = None,
    relevant_only: bool = True,
    max_items: int | None = None,
    action_levels: list[str] | None = None,
    include_background: bool = False,
    include_section_pages: bool = False,
    include_registries: bool = False,
    include_market_background: bool = False,
    include_full_background: bool = False,
    report_title: str | None = None,
    intro_note: str | None = None,
) -> Path:
    ensure_directories()
    if db_path is None:
        init_db()
        backfill_missing_published_at()
        documents = list_recent_documents(
            days=days,
            relevant_only=False,
            action_levels=None,
        )
        source_errors = list_recent_source_errors(days=days)
    else:
        init_db(db_path)
        backfill_missing_published_at(db_path)
        documents = list_recent_documents(
            db_path=db_path,
            days=days,
            relevant_only=False,
            action_levels=None,
        )
        source_errors = list_recent_source_errors(db_path=db_path, days=days)
    report_date = datetime.now().strftime("%Y-%m-%d")
    markdown = generate_markdown_report(
        documents,
        report_date=report_date,
        period_days=days,
        report_title=report_title,
        intro_note=intro_note,
        relevant_only=relevant_only,
        max_items=max_items,
        source_errors=source_errors,
        action_levels=action_levels,
        include_background=include_background,
        include_section_pages=include_section_pages,
        include_registries=include_registries,
        include_market_background=include_market_background,
        include_full_background=include_full_background,
    )

    if output_path:
        path = Path(output_path)
    else:
        path = REPORTS_DIR / f"gr_monitoring_{report_date}.md"
    save_markdown_report(markdown, path)
    return path


def run_demo_report(
    days: int = 7,
    output_path: str | None = None,
    *,
    db_path: Path | str | None = None,
    max_items: int = 20,
) -> Path:
    intro_note = (
        "_Демонстрационный markdown-report для репозитория. "
        "Содержит только публичные названия документов, факты и ссылки на открытые источники._"
    )
    return run_digest(
        days=days,
        output_path=output_path or str(DOCS_DIR / "demo_report.md"),
        db_path=db_path,
        relevant_only=True,
        max_items=max_items,
        action_levels=["requires_attention", "watchlist"],
        include_market_background=False,
        include_full_background=False,
        report_title="# AHSTEP Demo Report",
        intro_note=intro_note,
    )
