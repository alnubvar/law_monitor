from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from app.config import DB_PATH
from app.models import RawDocument
from app.storage import init_db, list_documents

NOISY_PAGE_TYPES = {
    "reference_page",
    "registry",
    "results_protocol",
    "section_page",
    "category_page",
    "year_archive",
    "navigation",
    "unknown",
}
ACTION_LEVEL_ORDER = ("requires_attention", "watchlist", "background", "irrelevant")


@dataclass(slots=True)
class SourceDiagnosticsRow:
    source_name: str
    total_documents: int
    requires_attention_count: int
    watchlist_count: int
    background_count: int
    irrelevant_count: int
    missing_published_at_count: int
    missing_summary_count: int
    missing_raw_text_count: int
    reference_page_count: int
    registry_count: int
    measure_card_count: int
    noisy_count: int
    noisy_ratio: float


@dataclass(slots=True)
class DiagnosticsSnapshot:
    generated_at: datetime
    days: int | None
    total_documents: int
    source_count: int
    action_level_totals: dict[str, int]
    rows: list[SourceDiagnosticsRow]
    missing_published_at_total: int
    filtered_by: str
    warning: str | None


def build_diagnostics_snapshot(
    documents: Iterable[RawDocument],
    *,
    days: int | None = None,
) -> DiagnosticsSnapshot:
    document_list = list(documents)
    grouped: dict[str, list[RawDocument]] = {}
    for document in document_list:
        grouped.setdefault(document.source_name, []).append(document)

    rows: list[SourceDiagnosticsRow] = []
    action_level_totals = {level: 0 for level in ACTION_LEVEL_ORDER}
    for document in document_list:
        level = document.action_level or "irrelevant"
        if level in action_level_totals:
            action_level_totals[level] += 1
    missing_published_at_total = sum(
        1 for document in document_list if document.published_at is None
    )

    for source_name, source_documents in sorted(grouped.items()):
        requires_attention_count = sum(
            1 for document in source_documents if document.action_level == "requires_attention"
        )
        watchlist_count = sum(
            1 for document in source_documents if document.action_level == "watchlist"
        )
        background_count = sum(
            1 for document in source_documents if document.action_level == "background"
        )
        irrelevant_count = sum(
            1 for document in source_documents if document.action_level == "irrelevant"
        )
        missing_published_at_count = sum(
            1 for document in source_documents if document.published_at is None
        )
        missing_summary_count = sum(
            1 for document in source_documents if not (document.summary or "").strip()
        )
        missing_raw_text_count = sum(
            1 for document in source_documents if not (document.raw_text or "").strip()
        )
        reference_page_count = sum(
            1 for document in source_documents if document.page_type == "reference_page"
        )
        registry_count = sum(
            1
            for document in source_documents
            if document.page_type in {"registry", "results_protocol"}
        )
        measure_card_count = sum(
            1 for document in source_documents if document.page_type == "measure_card"
        )
        noisy_count = sum(
            1
            for document in source_documents
            if document.action_level == "irrelevant"
            or document.page_type in NOISY_PAGE_TYPES
        )
        total_documents = len(source_documents)
        rows.append(
            SourceDiagnosticsRow(
                source_name=source_name,
                total_documents=total_documents,
                requires_attention_count=requires_attention_count,
                watchlist_count=watchlist_count,
                background_count=background_count,
                irrelevant_count=irrelevant_count,
                missing_published_at_count=missing_published_at_count,
                missing_summary_count=missing_summary_count,
                missing_raw_text_count=missing_raw_text_count,
                reference_page_count=reference_page_count,
                registry_count=registry_count,
                measure_card_count=measure_card_count,
                noisy_count=noisy_count,
                noisy_ratio=(noisy_count / total_documents) if total_documents else 0.0,
            )
        )

    return DiagnosticsSnapshot(
        generated_at=datetime.now(),
        days=days,
        total_documents=len(document_list),
        source_count=len(rows),
        action_level_totals=action_level_totals,
        rows=rows,
        missing_published_at_total=missing_published_at_total,
        filtered_by="collected_at",
        warning=(
            "Warning: many documents have no published_at; --days uses collected_at as fallback."
            if days is not None and missing_published_at_total > 0
            else None
        ),
    )


def format_diagnostics(snapshot: DiagnosticsSnapshot) -> str:
    period_text = (
        f"last {snapshot.days} days"
        if snapshot.days is not None
        else "all time"
    )
    lines = [
        "AHSTEP Source Diagnostics",
        f"Generated at: {snapshot.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Period: {period_text}",
        f"Date filter: {snapshot.filtered_by}",
        f"Total documents: {snapshot.total_documents}",
        f"Sources: {snapshot.source_count}",
    ]
    if snapshot.warning:
        lines.append(snapshot.warning)
    lines.extend(
        [
            f"Missing published_at: {snapshot.missing_published_at_total}",
            "",
            "Action levels:",
        ]
    )
    for level in ACTION_LEVEL_ORDER:
        lines.append(f"- {level}: {snapshot.action_level_totals.get(level, 0)}")

    if not snapshot.rows:
        lines.extend(["", "No documents found in the selected period."])
        return "\n".join(lines)

    lines.extend(["", "Per source:"])
    for row in snapshot.rows:
        lines.extend(
            [
                f"- {row.source_name}",
                "  "
                f"total={row.total_documents}; "
                f"RA={row.requires_attention_count}; "
                f"WL={row.watchlist_count}; "
                f"BG={row.background_count}; "
                f"IRR={row.irrelevant_count}",
                "  "
                f"missing published_at={row.missing_published_at_count}; "
                f"missing summary={row.missing_summary_count}; "
                f"missing raw_text={row.missing_raw_text_count}",
                "  "
                f"reference_page={row.reference_page_count}; "
                f"registry/results={row.registry_count}; "
                f"measure_card={row.measure_card_count}",
            ]
        )

    noisy_rows = sorted(
        snapshot.rows,
        key=lambda row: (row.noisy_ratio, row.noisy_count, row.total_documents),
        reverse=True,
    )[:5]
    lines.extend(["", "Low-signal sources:"])
    for row in noisy_rows:
        lines.append(
            "- "
            f"{row.source_name}: low_signal={row.noisy_count}/{row.total_documents} "
            f"({row.noisy_ratio:.0%})"
        )

    return "\n".join(lines)


def run_diagnostics(
    *,
    days: int | None = None,
    db_path: Path | str | None = None,
) -> str:
    resolved_db_path = Path(db_path) if db_path is not None else DB_PATH
    if not resolved_db_path.exists():
        init_db(resolved_db_path)
    documents = list_documents(db_path=resolved_db_path, days=days)
    snapshot = build_diagnostics_snapshot(documents, days=days)
    return format_diagnostics(snapshot)
