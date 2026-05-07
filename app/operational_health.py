from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Sequence

from app.config import DB_PATH, get_source_role, load_sources
from app.storage import (
    list_latest_source_audit,
    list_recent_source_errors,
    summarize_ocr_queue,
)

OperationalSeverity = Literal["info", "warning"]

SOURCE_ERROR_LOOKBACK_HOURS = 24
STALE_NEWS_DAYS = 3
STALE_SUPPORT_DAYS = 7
OCR_BACKLOG_INFO_THRESHOLD = 5
OCR_BACKLOG_WARNING_THRESHOLD = 20


@dataclass(slots=True, frozen=True)
class OperationalNotice:
    severity: OperationalSeverity
    message: str


def collect_operational_notices(
    *,
    db_path: Path | str = DB_PATH,
    now: datetime | None = None,
) -> list[OperationalNotice]:
    current_time = _normalize_dt(now) or datetime.now(timezone.utc)
    audit_by_source = {
        str(row.get("source_name") or ""): row
        for row in list_latest_source_audit(db_path=db_path)
    }

    notices: list[OperationalNotice] = []
    notices.extend(
        _collect_source_error_notices(
            db_path=db_path,
            audit_by_source=audit_by_source,
            now=current_time,
        )
    )
    notices.extend(
        _collect_stale_source_notices(
            audit_by_source=audit_by_source,
            now=current_time,
        )
    )
    notices.extend(_collect_ocr_backlog_notices(db_path=db_path))
    return _deduplicate_notices(notices)


def format_operational_notices_markdown(
    notices: Sequence[OperationalNotice],
) -> list[str]:
    if not notices:
        return []
    lines = ["## ⚠️ На что обратить внимание по системе", ""]
    for notice in notices:
        prefix = "⚠️" if notice.severity == "warning" else "ℹ️"
        lines.append(f"- {prefix} {notice.message}")
    lines.append("")
    return lines


def format_operational_notices_telegram(
    notices: Sequence[OperationalNotice],
) -> list[str]:
    if not notices:
        return []
    lines = ["⚠️ На что обратить внимание по системе"]
    for notice in notices:
        prefix = "⚠️" if notice.severity == "warning" else "ℹ️"
        lines.append(f"- {prefix} {notice.message}")
    return lines


def _collect_source_error_notices(
    *,
    db_path: Path | str,
    audit_by_source: dict[str, dict[str, object]],
    now: datetime,
) -> list[OperationalNotice]:
    cutoff = now.timestamp() - SOURCE_ERROR_LOOKBACK_HOURS * 3600
    errored_sources: dict[str, str] = {}

    for record in list_recent_source_errors(db_path=db_path, days=1):
        collected_at = _normalize_dt(record.collected_at)
        if collected_at is None or collected_at.timestamp() < cutoff:
            continue
        errored_sources.setdefault(record.source_name, "ошибки доступа за последние 24 часа")

    for source_name, row in audit_by_source.items():
        error_at = _normalize_dt(row.get("error_at"))
        if error_at is None or error_at.timestamp() < cutoff:
            continue
        if row.get("error_message"):
            errored_sources.setdefault(source_name, "ошибки доступа за последние 24 часа")

    return [
        OperationalNotice(
            severity="warning",
            message=f"{source_name}: были {message}",
        )
        for source_name, message in sorted(errored_sources.items())
    ]


def _collect_stale_source_notices(
    *,
    audit_by_source: dict[str, dict[str, object]],
    now: datetime,
) -> list[OperationalNotice]:
    notices: list[OperationalNotice] = []
    for source in load_sources():
        if not source.enabled:
            continue
        threshold_days = _stale_threshold_days(source.name)
        if threshold_days is None:
            continue
        audit_row = audit_by_source.get(source.name)
        if not audit_row:
            continue
        latest_success_at = _normalize_dt(audit_row.get("success_at"))
        if latest_success_at is None:
            continue
        stale_days = (
            now.astimezone(timezone.utc).date()
            - latest_success_at.astimezone(timezone.utc).date()
        ).days
        if stale_days < threshold_days:
            continue
        notices.append(
            OperationalNotice(
                severity="warning",
                message=f"{source.name}: нет успешного сбора {stale_days} дней",
            )
        )
    return notices


def _collect_ocr_backlog_notices(
    db_path: Path | str,
) -> list[OperationalNotice]:
    pending_count = int(summarize_ocr_queue(db_path=db_path).get("pending", 0))
    if pending_count >= OCR_BACKLOG_WARNING_THRESHOLD:
        return [
            OperationalNotice(
                severity="warning",
                message=f"OCR queue: в очереди {pending_count} документов на обработку",
            )
        ]
    if pending_count >= OCR_BACKLOG_INFO_THRESHOLD:
        return [
            OperationalNotice(
                severity="info",
                message=f"OCR queue: в очереди {pending_count} документов на обработку",
            )
        ]
    return []

def _stale_threshold_days(source_name: str) -> int | None:
    source_role = get_source_role(source_name)
    if source_role == "news_signals":
        return STALE_NEWS_DAYS
    if source_role in {"regional_npa", "support_documents", "active_support_measures", "strategy"}:
        return STALE_SUPPORT_DAYS
    return None


def _deduplicate_notices(notices: Sequence[OperationalNotice]) -> list[OperationalNotice]:
    seen: set[tuple[str, str]] = set()
    ordered: list[OperationalNotice] = []
    severity_rank = {"warning": 0, "info": 1}
    for notice in sorted(notices, key=lambda item: (severity_rank[item.severity], item.message)):
        key = (notice.severity, notice.message)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(notice)
    return ordered


def _normalize_dt(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
