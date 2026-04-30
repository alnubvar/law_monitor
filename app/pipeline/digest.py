from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config import REPORTS_DIR, ensure_directories
from app.reports.markdown_report import generate_markdown_report, save_markdown_report
from app.storage import init_db, list_recent_documents, list_recent_source_errors


def run_digest(
    days: int = 7,
    output_path: str | None = None,
    *,
    relevant_only: bool = True,
    max_items: int | None = None,
    action_levels: list[str] | None = None,
    include_background: bool = False,
    include_section_pages: bool = False,
    include_registries: bool = False,
    include_market_background: bool = False,
    include_full_background: bool = False,
) -> Path:
    ensure_directories()
    init_db()
    documents = list_recent_documents(
        days=days,
        relevant_only=False,
        action_levels=action_levels,
    )
    source_errors = list_recent_source_errors(days=days)
    report_date = datetime.now().strftime("%Y-%m-%d")
    markdown = generate_markdown_report(
        documents,
        report_date=report_date,
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
