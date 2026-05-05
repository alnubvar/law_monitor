from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from app.config import DB_PATH, get_source_role, load_sources
from app.models import RawDocument
from app.storage import backfill_missing_published_at, init_db, list_documents, list_latest_source_audit

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
    source_role: str
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
    unknown_page_type_count: int
    noisy_count: int
    noisy_ratio: float
    published_at_coverage: str


@dataclass(slots=True)
class RoleDiagnosticsRow:
    source_role: str
    total_documents: int
    requires_attention_count: int
    watchlist_count: int
    background_count: int
    irrelevant_count: int
    missing_published_at_count: int
    unknown_page_type_count: int
    noisy_count: int
    noisy_ratio: float
    published_at_coverage: str


@dataclass(slots=True)
class DiagnosticsSnapshot:
    generated_at: datetime
    days: int | None
    total_documents: int
    source_count: int
    action_level_totals: dict[str, int]
    role_rows: list[RoleDiagnosticsRow]
    rows: list[SourceDiagnosticsRow]
    missing_published_at_total: int
    published_at_present_total: int
    published_at_coverage: str
    filtered_by: str
    warning: str | None
    backfilled_count: int


def build_diagnostics_snapshot(
    documents: Iterable[RawDocument],
    *,
    days: int | None = None,
) -> DiagnosticsSnapshot:
    document_list = list(documents)
    grouped: dict[str, list[RawDocument]] = {}
    for document in document_list:
        grouped.setdefault(document.source_name, []).append(document)
    grouped_by_role: dict[str, list[RawDocument]] = {}
    for document in document_list:
        source_role = get_source_role(document.source_name) or "unknown"
        grouped_by_role.setdefault(source_role, []).append(document)

    rows: list[SourceDiagnosticsRow] = []
    role_rows: list[RoleDiagnosticsRow] = []
    action_level_totals = {level: 0 for level in ACTION_LEVEL_ORDER}
    for document in document_list:
        level = document.action_level or "irrelevant"
        if level in action_level_totals:
            action_level_totals[level] += 1
    missing_published_at_total = sum(
        1 for document in document_list if document.published_at is None
    )
    published_at_present_total = len(document_list) - missing_published_at_total

    for source_role, role_documents in sorted(grouped_by_role.items()):
        total_documents = len(role_documents)
        missing_published_at_count = sum(
            1 for document in role_documents if document.published_at is None
        )
        unknown_page_type_count = sum(
            1 for document in role_documents if (document.page_type or "unknown") == "unknown"
        )
        noisy_count = sum(
            1
            for document in role_documents
            if document.action_level == "irrelevant"
            or document.page_type in NOISY_PAGE_TYPES
        )
        role_rows.append(
            RoleDiagnosticsRow(
                source_role=source_role,
                total_documents=total_documents,
                requires_attention_count=sum(
                    1 for document in role_documents if document.action_level == "requires_attention"
                ),
                watchlist_count=sum(
                    1 for document in role_documents if document.action_level == "watchlist"
                ),
                background_count=sum(
                    1 for document in role_documents if document.action_level == "background"
                ),
                irrelevant_count=sum(
                    1 for document in role_documents if document.action_level == "irrelevant"
                ),
                missing_published_at_count=missing_published_at_count,
                unknown_page_type_count=unknown_page_type_count,
                noisy_count=noisy_count,
                noisy_ratio=(noisy_count / total_documents) if total_documents else 0.0,
                published_at_coverage=f"{total_documents - missing_published_at_count}/{total_documents}",
            )
        )

    for source_name, source_documents in sorted(grouped.items()):
        source_role = get_source_role(source_name) or "unknown"
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
        unknown_page_type_count = sum(
            1 for document in source_documents if (document.page_type or "unknown") == "unknown"
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
                source_role=source_role,
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
                unknown_page_type_count=unknown_page_type_count,
                noisy_count=noisy_count,
                noisy_ratio=(noisy_count / total_documents) if total_documents else 0.0,
                published_at_coverage=f"{total_documents - missing_published_at_count}/{total_documents}",
            )
        )

    return DiagnosticsSnapshot(
        generated_at=datetime.now(),
        days=days,
        total_documents=len(document_list),
        source_count=len(rows),
        action_level_totals=action_level_totals,
        role_rows=role_rows,
        rows=rows,
        missing_published_at_total=missing_published_at_total,
        published_at_present_total=published_at_present_total,
        published_at_coverage=f"{published_at_present_total}/{len(document_list)}",
        filtered_by="published_at with collected_at fallback",
        warning=(
            "Warning: many documents have no published_at; --days uses published_at with collected_at fallback."
            if days is not None and missing_published_at_total > 0
            else None
        ),
        backfilled_count=0,
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
        f"Published_at coverage: {snapshot.published_at_coverage}",
    ]
    if snapshot.backfilled_count > 0:
        lines.append(f"Published_at backfilled this run: {snapshot.backfilled_count}")
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

    lines.extend(["", "By source_role:"])
    for row in snapshot.role_rows:
        lines.extend(
            [
                f"- {row.source_role}",
                "  "
                f"total={row.total_documents}; "
                f"RA={row.requires_attention_count}; "
                f"WL={row.watchlist_count}; "
                f"BG={row.background_count}; "
                f"IRR={row.irrelevant_count}",
                "  "
                f"coverage={row.published_at_coverage}; "
                f"missing published_at={row.missing_published_at_count}; "
                f"low_signal={row.noisy_count}/{row.total_documents} ({row.noisy_ratio:.0%}); "
                f"unknown_page_type={row.unknown_page_type_count}",
            ]
        )

    lines.extend(["", "Per source:"])
    for row in snapshot.rows:
        lines.extend(
            [
                f"- {row.source_name} [{row.source_role}]",
                "  "
                f"total={row.total_documents}; "
                f"RA={row.requires_attention_count}; "
                f"WL={row.watchlist_count}; "
                f"BG={row.background_count}; "
                f"IRR={row.irrelevant_count}",
                "  "
                f"missing published_at={row.missing_published_at_count}; "
                f"coverage={row.published_at_coverage}; "
                f"missing summary={row.missing_summary_count}; "
                f"missing raw_text={row.missing_raw_text_count}",
                "  "
                f"reference_page={row.reference_page_count}; "
                f"registry/results={row.registry_count}; "
                f"measure_card={row.measure_card_count}; "
                f"unknown_page_type={row.unknown_page_type_count}",
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

    lines.extend(["", "Parser quality hints:"])
    high_missing = [
        row for row in snapshot.rows
        if row.total_documents > 0 and row.missing_published_at_count / row.total_documents >= 0.6
    ][:3]
    high_low_signal = [
        row for row in noisy_rows
        if row.total_documents > 0 and row.noisy_ratio >= 0.7
    ][:3]
    high_unknown = sorted(
        [row for row in snapshot.rows if row.unknown_page_type_count > 0],
        key=lambda row: (row.unknown_page_type_count, row.total_documents),
        reverse=True,
    )[:3]
    if high_missing:
        lines.append(
            "- high missing published_at: "
            + ", ".join(
                f"{row.source_name} ({row.missing_published_at_count}/{row.total_documents})"
                for row in high_missing
            )
        )
    if high_low_signal:
        lines.append(
            "- high low_signal: "
            + ", ".join(
                f"{row.source_name} ({row.noisy_ratio:.0%})"
                for row in high_low_signal
            )
        )
    if high_unknown:
        lines.append(
            "- many unknown page_type: "
            + ", ".join(
                f"{row.source_name} ({row.unknown_page_type_count})"
                for row in high_unknown
            )
        )
    if not any((high_missing, high_low_signal, high_unknown)):
        lines.append("- no major parser quality issues detected in the selected period.")

    return "\n".join(lines)


def run_diagnostics(
    *,
    days: int | None = None,
    db_path: Path | str | None = None,
) -> str:
    resolved_db_path = Path(db_path) if db_path is not None else DB_PATH
    if not resolved_db_path.exists():
        init_db(resolved_db_path)
    backfilled_count = backfill_missing_published_at(resolved_db_path)
    documents = list_documents(db_path=resolved_db_path, days=days)
    snapshot = build_diagnostics_snapshot(documents, days=days)
    snapshot.backfilled_count = backfilled_count
    diagnostics_text = format_diagnostics(snapshot)
    audit_text = format_source_coverage_audit(db_path=resolved_db_path)
    return f"{diagnostics_text}\n\n{audit_text}".strip()


def format_source_coverage_audit(*, db_path: Path | str) -> str:
    rows = list_latest_source_audit(db_path=db_path)
    by_name = {row["source_name"]: row for row in rows}
    lines = ["Source coverage audit:"]
    for source in load_sources():
        row = by_name.get(source.name)
        if row is None:
            lines.append(
                f"- {source.name} | enabled={source.enabled} | url={source.url} | "
                "last_attempt_at=n/a | last_success_at=n/a | last_error_at=n/a | "
                "fetched_count=0 | saved_count=0 | existing_count=0 | duplicates_count=0 | item_errors=0"
            )
            continue
        lines.append(
            f"- {source.name} | enabled={source.enabled} | url={source.url} | "
            f"last_attempt_at={_fmt_dt(row.get('attempted_at'))} | "
            f"last_success_at={_fmt_dt(row.get('success_at'))} | "
            f"last_error_at={_fmt_dt(row.get('error_at'))} | "
            f"last_error_message={str(row.get('error_message') or 'n/a')[:120]} | "
            f"fetched_count={row.get('fetched_count', 0)} | "
            f"saved_count={row.get('saved_count', 0)} | "
            f"existing_count={row.get('existing_count', 0)} | "
            f"duplicates_count={row.get('duplicates_count', 0)} | "
            f"item_errors={row.get('item_errors_count', 0)}"
        )
    return "\n".join(lines)


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.strftime("%Y-%m-%d %H:%M:%S")
