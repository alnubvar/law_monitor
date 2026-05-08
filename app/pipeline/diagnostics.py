from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Iterable

from app.config import DB_PATH, OCR_ENABLED, get_source_role, load_sources
from app.extractors.ocr_extractor import get_ocr_runtime_status
from app.models import RawDocument
from app.storage import (
    get_sqlite_runtime_settings,
    init_db,
    list_documents,
    list_latest_source_audit,
    list_recent_document_extraction_audit,
    list_unresolved_scan_candidate_audit,
    summarize_ocr_queue,
)

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


def _build_operational_warnings(
    *,
    snapshot: DiagnosticsSnapshot,
    db_path: Path | str,
) -> list[str]:
    """Return a list of operational warning strings based on current system state.

    Called in run_diagnostics() — reads DB but does NOT write anything.
    """
    warnings: list[str] = []

    if snapshot.total_documents > 0:
        missing_ratio = snapshot.missing_published_at_total / snapshot.total_documents
        if missing_ratio > 0.5:
            warnings.append(
                f"WARN: published_at missing for {snapshot.missing_published_at_total}/"
                f"{snapshot.total_documents} documents ({missing_ratio:.0%}). "
                "Run `python main.py backfill-dates` to improve date coverage."
            )

    audit_rows = list_latest_source_audit(db_path=db_path)
    for row in audit_rows:
        error_msg = str(row.get("error_message") or "")
        warning_text = _source_access_warning_text(error_msg)
        if warning_text:
            source_name = str(row.get("source_name") or "")
            warnings.append(f"WARN: {source_name}: {warning_text}")

    unresolved_rows = list_unresolved_scan_candidate_audit(db_path=db_path, limit=5000)
    unresolved_count = len(
        {
            str(row.get("document_url") or "").strip()
            for row in unresolved_rows
            if row.get("document_url")
        }
    )
    if unresolved_count > 5:
        warnings.append(
            f"WARN: OCR backlog: {unresolved_count} unresolved scan candidates. "
            "Run `python main.py ocr-backfill` + `python main.py ocr-run`."
        )

    return warnings


_SYSTEM_PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


def format_network_environment_note() -> str:
    lines = ["Network environment:"]

    telegram_proxy_configured = bool(os.environ.get("TELEGRAM_PROXY_URL", "").strip())
    lines.append(f"- Telegram proxy configured: {'yes' if telegram_proxy_configured else 'no'}")

    detected_proxies = {
        var: os.environ[var]
        for var in _SYSTEM_PROXY_ENV_VARS
        if var in os.environ and os.environ[var].strip()
    }
    if detected_proxies:
        lines.append("- System HTTP/HTTPS proxy env vars detected: yes")
        lines.append("- WARN: system proxy env vars detected: " + ", ".join(sorted(detected_proxies.keys())))
    else:
        lines.append("- System HTTP/HTTPS proxy env vars detected: no")

    lines.append(
        "- Source requests do NOT use Telegram proxy. Telegram proxy applies only to Telegram API calls."
    )
    lines.append(
        "- Local VPN/IP may differ from production Russian VPS, so source 403/network behavior can differ."
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
    diagnostics_text = format_diagnostics(snapshot)
    sqlite_text = format_sqlite_runtime_diagnostics(db_path=resolved_db_path)
    audit_text = format_source_coverage_audit(db_path=resolved_db_path)
    extraction_text = format_document_extraction_quality_audit(
        db_path=resolved_db_path,
        days=days or 7,
    )
    ocr_runtime_text = format_ocr_runtime_diagnostics(
        db_path=resolved_db_path,
        days=days or 7,
    )
    ocr_triage_text = format_ocr_triage_queue_diagnostics(
        db_path=resolved_db_path,
    )
    audit_gaps_text = format_pdf_docx_audit_gaps(
        db_path=resolved_db_path,
        days=days or 7,
    )
    depth_text = format_source_depth_audit(
        db_path=resolved_db_path,
        days=days or 7,
    )
    filtered_links_text = format_filtered_links_review(
        db_path=resolved_db_path,
        days=days or 7,
    )
    operational_warnings = _build_operational_warnings(
        snapshot=snapshot,
        db_path=resolved_db_path,
    )
    network_note = format_network_environment_note()
    parts: list[str] = []
    if operational_warnings:
        parts.append("Operational warnings:\n" + "\n".join(f"- {w}" for w in operational_warnings))
    parts.extend(
        [
            network_note,
            sqlite_text,
            diagnostics_text,
            audit_text,
            extraction_text,
            ocr_runtime_text,
            ocr_triage_text,
            audit_gaps_text,
            depth_text,
            filtered_links_text,
        ]
    )
    return "\n\n".join(p for p in parts if p.strip()).strip()


def format_sqlite_runtime_diagnostics(*, db_path: Path | str) -> str:
    settings = get_sqlite_runtime_settings(db_path=db_path)
    return "\n".join(
        [
            "SQLite runtime:",
            f"- journal_mode: {settings['journal_mode']}",
            f"- busy_timeout_ms: {settings['busy_timeout']}",
            f"- synchronous: {settings['synchronous']}",
            f"- foreign_keys: {'ON' if settings['foreign_keys'] else 'OFF'}",
        ]
    )


def format_source_coverage_audit(*, db_path: Path | str) -> str:
    rows = list_latest_source_audit(db_path=db_path)
    by_name = {row["source_name"]: row for row in rows}
    lines = ["Source coverage audit:"]
    for source in load_sources():
        row = by_name.get(source.name)
        if row is None:
            lines.append(
                f"- [NO DATA] {source.name} | enabled={source.enabled} | url={source.url} | "
                "last_attempt_at=n/a | last_success_at=n/a | last_success_age=never | "
                "last_error_at=n/a | fetched_count=0 | saved_count=0 | existing_count=0 | "
                "duplicates_count=0 | item_errors=0"
            )
            continue
        status_tag = _source_status_tag(row)
        status_prefix = f"{status_tag} " if status_tag else ""
        warning = _source_access_warning_text(str(row.get("error_message") or ""))
        warning_suffix = f" | warning={warning}" if warning else ""
        lines.append(
            f"- {status_prefix}{source.name} | enabled={source.enabled} | url={source.url} | "
            f"last_attempt_at={_fmt_dt(row.get('attempted_at'))} | "
            f"last_success_at={_fmt_dt(row.get('success_at'))} | "
            f"last_success_age={_format_age(row.get('success_at'))} | "
            f"last_error_at={_fmt_dt(row.get('error_at'))} | "
            f"last_error_message={str(row.get('error_message') or 'n/a')[:120]} | "
            f"fetched_count={row.get('fetched_count', 0)} | "
            f"saved_count={row.get('saved_count', 0)} | "
            f"existing_count={row.get('existing_count', 0)} | "
            f"duplicates_count={row.get('duplicates_count', 0)} | "
            f"item_errors={row.get('item_errors_count', 0)} | "
            f"links_found={row.get('links_found_count', 0)} | "
            f"links_filtered={row.get('links_filtered_count', 0)}"
            f"{warning_suffix}"
        )
    return "\n".join(lines)


def format_document_extraction_quality_audit(*, db_path: Path | str, days: int = 7) -> str:
    rows = list_recent_document_extraction_audit(db_path=db_path, days=days)
    lines = [f"Document extraction quality (last {days} days):"]
    if not rows:
        lines.append("- no extraction records in the selected period.")
        return "\n".join(lines)

    pdf_rows = [row for row in rows if (row.get("file_type") or row.get("extracted_type")) == "pdf"]
    pdf_with_text = sum(1 for row in pdf_rows if row.get("has_text"))
    pdf_fully_extracted = sum(
        1
        for row in pdf_rows
        if row.get("has_text")
        and not row.get("scan_candidate")
        and not row.get("needs_ocr")
        and not row.get("extraction_error")
    )
    pdf_scan_candidates = sum(1 for row in pdf_rows if row.get("scan_candidate"))
    docx_rows = [row for row in rows if (row.get("file_type") or row.get("extracted_type")) == "docx"]
    docx_with_text = sum(1 for row in docx_rows if row.get("has_text"))
    html_rows = [
        row for row in rows
        if (row.get("file_type") or row.get("extracted_type")) in {"html", "xml"}
    ]
    html_no_text = sum(1 for row in html_rows if not row.get("has_text"))
    missing_text_by_source: dict[str, int] = {}
    for row in rows:
        if row.get("has_text"):
            continue
        source_name = str(row.get("source_name") or "unknown")
        missing_text_by_source[source_name] = missing_text_by_source.get(source_name, 0) + 1
    top_missing = sorted(
        missing_text_by_source.items(),
        key=lambda pair: pair[1],
        reverse=True,
    )[:5]
    unresolved_rows_all = list_unresolved_scan_candidate_audit(db_path=db_path, limit=5000)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    unresolved_rows: list[dict[str, object]] = []
    seen_unresolved_urls: set[str] = set()
    for row in unresolved_rows_all:
        collected_at = row.get("collected_at")
        if isinstance(collected_at, datetime) and collected_at < cutoff:
            continue
        document_url = str(row.get("document_url") or "").strip()
        if not document_url or document_url in seen_unresolved_urls:
            continue
        seen_unresolved_urls.add(document_url)
        unresolved_rows.append(row)
    ocr_resolved_by_runtime = sum(
        1
        for row in rows
        if row.get("scan_candidate")
        and row.get("has_text")
        and str(row.get("ocr_status") or "") == "success"
    )
    queue_summary = summarize_ocr_queue(db_path=db_path)
    recent_documents = list_documents(db_path=db_path, days=days)
    visible_urls = {
        document.url
        for document in recent_documents
        if document.action_level in {"requires_attention", "watchlist"}
    }
    visible_ocr_rows = [
        row for row in unresolved_rows if str(row.get("document_url") or "") in visible_urls
    ]
    lines.extend(
        [
            f"- PDF: total={len(pdf_rows)}; with_text={pdf_with_text}; scan_candidates={pdf_scan_candidates}",
            f"- PDF fully extracted (text-layer ok): {pdf_fully_extracted}/{len(pdf_rows)}",
            f"- DOCX: total={len(docx_rows)}; with_text={docx_with_text}",
            f"- HTML/XML without text: {html_no_text}",
            f"- OCR resolved by runtime: {ocr_resolved_by_runtime}",
            f"- OCR unresolved: {len(unresolved_rows)}",
            f"- OCR queue pending: {queue_summary['pending']}",
            f"- OCR queue done: {queue_summary['done']}",
        ]
    )
    if pdf_scan_candidates > 0:
        lines.append("- Warning: Есть PDF без текстового слоя; требуется OCR для полного анализа.")
    if visible_ocr_rows:
        lines.append(
            f"- Priority warning: среди видимых документов есть PDF/вложения, требующие OCR ({len(visible_ocr_rows)})."
        )
    if top_missing:
        lines.append("- Top sources by missing raw_text:")
        for source_name, count in top_missing:
            lines.append(f"  - {source_name}: {count}")
    else:
        lines.append("- Top sources by missing raw_text: none")
    if unresolved_rows:
        lines.append("- Documents requiring OCR:")
        for row in unresolved_rows[:10]:
            lines.append(
                "  - "
                f"{row.get('source_name')} | {row.get('document_url')} | "
                f"file_type={row.get('file_type')} | text_length={row.get('raw_text_length', 0)}"
            )
    else:
        lines.append("- Documents requiring OCR: none")
    return "\n".join(lines)


def format_ocr_runtime_diagnostics(*, db_path: Path | str, days: int = 7) -> str:
    rows = list_recent_document_extraction_audit(db_path=db_path, days=days)
    runtime = get_ocr_runtime_status()
    enabled_text = "enabled" if OCR_ENABLED else "disabled"
    available_text = "available" if runtime.get("available") else "unavailable"
    success_count = sum(1 for row in rows if row.get("ocr_status") == "success")
    failed_count = sum(1 for row in rows if row.get("ocr_status") == "failed")
    unavailable_count = sum(1 for row in rows if row.get("ocr_status") == "unavailable")
    resolved_by_runtime_count = sum(
        1
        for row in rows
        if row.get("scan_candidate")
        and row.get("has_text")
        and row.get("ocr_status") == "success"
    )
    unresolved_rows = list_unresolved_scan_candidate_audit(db_path=db_path, limit=5000)
    unresolved_count = len({str(row.get("document_url") or "").strip() for row in unresolved_rows if row.get("document_url")})
    queue_summary = summarize_ocr_queue(db_path=db_path)
    ocr_text_total = sum(int(row.get("ocr_text_length") or 0) for row in rows)
    lines = [
        f"OCR runtime (last {days} days):",
        f"- runtime: {enabled_text}",
        f"- availability: {available_text}",
        f"- language: {runtime.get('language') or '-'}",
        f"- max_pages: {runtime.get('max_pages')}",
        f"- tessdata_path: {runtime.get('tessdata_path') or '-'}",
        f"- OCR success count: {success_count}",
        f"- OCR failed count: {failed_count}",
        f"- OCR unavailable count: {unavailable_count}",
        f"- OCR resolved by runtime: {resolved_by_runtime_count}",
        f"- OCR unresolved: {unresolved_count}",
        f"- OCR queue pending: {queue_summary['pending']}",
        f"- OCR queue done: {queue_summary['done']}",
        f"- OCR text extracted total: {ocr_text_total}",
    ]
    available_languages = runtime.get("available_languages") or []
    if available_languages:
        lines.append("- available languages: " + ", ".join(str(value) for value in available_languages))
    if runtime.get("reason"):
        lines.append(f"- note: {runtime.get('reason')}")
    return "\n".join(lines)


def format_ocr_triage_queue_diagnostics(*, db_path: Path | str) -> str:
    summary = summarize_ocr_queue(db_path=db_path)
    unresolved = list_unresolved_scan_candidate_audit(db_path=db_path, limit=5000)
    lines = [
        "OCR triage queue:",
        f"- pending: {summary['pending']}",
        f"- in_review: {summary['in_review']}",
        f"- done: {summary['done']}",
        f"- skipped: {summary['skipped']}",
        f"- high priority pending: {summary['high_priority_pending']}",
    ]
    if unresolved and summary["pending"] == 0 and summary["in_review"] == 0:
        lines.append(
            "- Warning: OCR queue is empty but unresolved scan candidates exist. "
            "Run: python main.py ocr-backfill"
        )
    return "\n".join(lines)


def format_source_depth_audit(*, db_path: Path | str, days: int = 7) -> str:
    rows = list_latest_source_audit(db_path=db_path)
    by_name = {row["source_name"]: row for row in rows}
    lines = [f"Source depth audit (last snapshot, diagnostics window {days} days):"]
    for source in load_sources():
        row = by_name.get(source.name)
        if row is None:
            lines.append(
                f"- {source.name}: links_found=0; links_filtered=0; fetched=0; saved=0; "
                "docs(pdf/docx/html/xml)=0/0/0/0; note=no audit snapshot"
            )
            continue
        pdf_count = int(row.get("pdf_links_count", 0))
        docx_count = int(row.get("docx_links_count", 0))
        html_count = int(row.get("html_links_count", 0))
        xml_count = int(row.get("xml_links_count", 0))
        unknown_count = int(row.get("unknown_links_count", 0))
        listing_only_note = ""
        if int(row.get("saved_count", 0)) == 0 and (pdf_count + docx_count + html_count + xml_count) > 0:
            listing_only_note = "; note=no new saves in this run (existing-heavy or listing-heavy)"
        links_found = int(row.get("links_found_count", 0))
        links_filtered = int(row.get("links_filtered_count", 0))
        filtered_ratio = (links_filtered / links_found) if links_found > 0 else 0.0
        filtered_warning = ""
        if links_found > 0 and filtered_ratio >= 0.9 and int(row.get("fetched_count", 0)) <= 3:
            filtered_warning = "; warning=high filtered ratio"
        lines.append(
            f"- {source.name}: links_found={links_found}; "
            f"links_filtered={links_filtered}; "
            f"fetched={int(row.get('fetched_count', 0))}; saved={int(row.get('saved_count', 0))}; "
            f"docs(pdf/docx/html/xml)={pdf_count}/{docx_count}/{html_count}/{xml_count}; "
            f"unknown={unknown_count}{listing_only_note}{filtered_warning}"
        )
    return "\n".join(lines)


def format_pdf_docx_audit_gaps(*, db_path: Path | str, days: int = 7) -> str:
    extraction_rows = list_recent_document_extraction_audit(db_path=db_path, days=days)
    extraction_rows_all = list_recent_document_extraction_audit(db_path=db_path, days=None)
    source_rows = list_latest_source_audit(db_path=db_path)
    documents = list_documents(db_path=db_path, days=days)
    lines = [f"PDF/DOCX audit gaps (last {days} days):"]

    extracted_by_source_pdf: dict[str, int] = {}
    for row in extraction_rows:
        file_type = str(row.get("file_type") or row.get("extracted_type") or "")
        if file_type != "pdf":
            continue
        source_name = str(row.get("source_name") or "unknown")
        extracted_by_source_pdf[source_name] = extracted_by_source_pdf.get(source_name, 0) + 1

    pdf_link_gaps: list[str] = []
    for source_row in source_rows:
        source_name = str(source_row.get("source_name") or "unknown")
        pdf_links = int(source_row.get("pdf_links_count", 0))
        if pdf_links <= 0:
            continue
        extracted_count = extracted_by_source_pdf.get(source_name, 0)
        if extracted_count < pdf_links:
            pdf_link_gaps.append(
                f"{source_name}: PDF links found but not extracted ({pdf_links - extracted_count} gap, found={pdf_links}, extracted={extracted_count})"
            )

    audit_urls_all = {
        str(row.get("document_url"))
        for row in extraction_rows_all
        if row.get("document_url")
    }
    pdf_existing_without_audit = [
        document
        for document in documents
        if (document.document_type or "").lower() == "pdf" and document.url not in audit_urls_all
    ]
    docx_existing_without_audit = [
        document
        for document in documents
        if (document.document_type or "").lower() == "docx" and document.url not in audit_urls_all
    ]

    if pdf_link_gaps:
        lines.append("- PDF links found but not extracted:")
        for entry in pdf_link_gaps[:10]:
            lines.append(f"  - {entry}")
    else:
        lines.append("- PDF links found but not extracted: none")

    if pdf_existing_without_audit:
        lines.append(f"- PDF existing without extraction audit: {len(pdf_existing_without_audit)}")
    else:
        lines.append("- PDF existing without extraction audit: 0")
    if docx_existing_without_audit:
        lines.append(f"- DOCX existing without extraction audit: {len(docx_existing_without_audit)}")
    else:
        lines.append("- DOCX existing without extraction audit: 0")
    return "\n".join(lines)


def format_filtered_links_review(*, db_path: Path | str, days: int = 7) -> str:
    rows = list_latest_source_audit(db_path=db_path)
    by_name = {row["source_name"]: row for row in rows}
    lines = [f"Filtered links review (last snapshot, diagnostics window {days} days):"]
    for source in load_sources():
        row = by_name.get(source.name)
        if row is None:
            lines.append(f"- {source.name}: no filtered-links snapshot")
            continue
        samples_raw = str(row.get("filtered_samples") or "").strip()
        samples_payload: dict[str, list[str]] = {}
        if samples_raw:
            try:
                parsed = json.loads(samples_raw)
                if isinstance(parsed, dict):
                    samples_payload = {
                        str(key): [str(item) for item in value[:2]]
                        for key, value in parsed.items()
                        if isinstance(value, list)
                    }
            except json.JSONDecodeError:
                samples_payload = {}
        lines.append(
            f"- {source.name}: "
            f"navigation={int(row.get('navigation_filtered_count', 0))}; "
            f"archive={int(row.get('archive_filtered_count', 0))}; "
            f"external={int(row.get('external_filtered_count', 0))}; "
            f"duplicate={int(row.get('duplicate_filtered_count', 0))}; "
            f"unsupported={int(row.get('unsupported_filtered_count', 0))}; "
            f"pdf_kept={int(row.get('pdf_links_count', 0))}; "
            f"pdf_filtered={int(row.get('pdf_filtered_count', 0))}; "
            f"docx_kept={int(row.get('docx_links_count', 0))}; "
            f"docx_filtered={int(row.get('docx_filtered_count', 0))}"
        )
        if samples_payload:
            sample_parts = []
            for key in ("navigation", "archive", "external", "duplicate", "unsupported", "pdf_filtered", "docx_filtered"):
                values = samples_payload.get(key) or []
                if not values:
                    continue
                sample_parts.append(f"{key}: {', '.join(values)}")
            if sample_parts:
                lines.append(f"  samples: {' | '.join(sample_parts)}")
    return "\n".join(lines)


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _format_age(dt: datetime | None) -> str:
    if dt is None:
        return "never"
    now = datetime.now(timezone.utc)
    delta = now - dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else now - dt
    days = delta.days
    hours = delta.seconds // 3600
    if days == 0:
        return "today" if hours < 1 else f"{hours}h ago"
    return f"{days}d ago"


def _source_status_tag(row: dict) -> str:
    success_at: datetime | None = row.get("success_at")
    error_at: datetime | None = row.get("error_at")
    error_message = str(row.get("error_message") or "")
    warning = _source_access_warning_text(error_message)

    if success_at is None:
        if error_at is not None:
            if warning in ("source access blocked", "source rate-limited"):
                return "[BLOCKED]"
            return "[NETWORK ERROR]"
        return "[DEGRADED]"

    now = datetime.now(timezone.utc)
    success_dt = success_at.replace(tzinfo=timezone.utc) if success_at.tzinfo is None else success_at
    if error_at is not None and (now - success_dt).days > 3:
        return "[STALE]"
    return ""


def _source_access_warning_text(error_message: str) -> str | None:
    normalized = (error_message or "").lower()
    if not normalized:
        return None
    if "source access blocked" in normalized:
        return "source access blocked"
    if "403" in normalized and ("client error" in normalized or "forbidden" in normalized):
        return "source access blocked"
    if "429" in normalized and ("client error" in normalized or "too many requests" in normalized):
        return "source rate-limited"
    if "source temporary server error" in normalized:
        return "source temporary server error"
    if "500" in normalized and ("server error" in normalized or "internal server error" in normalized):
        return "source temporary server error"
    if "source timeout" in normalized:
        return "source timeout"
    if "timeout" in normalized:
        return "source timeout"
    if "source proxy/network error" in normalized or "source connection error" in normalized:
        return "source network issue"
    if "connectionpool" in normalized or "max retries exceeded" in normalized:
        return "source network issue"
    return None
