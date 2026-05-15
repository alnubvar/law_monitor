from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Sequence

from app.config import DB_PATH, get_source_role, load_sources
from app.storage import (
    list_latest_source_audit,
    list_recent_source_errors,
    list_source_audit_records,
    summarize_ocr_queue,
)

OperationalSeverity = Literal["info", "warning"]

SOURCE_ERROR_LOOKBACK_HOURS = 24
STALE_NEWS_DAYS = 3
STALE_SUPPORT_DAYS = 7
COLLECT_SUCCESS_STALE_AFTER = timedelta(hours=24)
OCR_BACKLOG_INFO_THRESHOLD = 5
OCR_BACKLOG_WARNING_THRESHOLD = 20


@dataclass(slots=True, frozen=True)
class OperationalNotice:
    severity: OperationalSeverity
    message: str


@dataclass(slots=True, frozen=True)
class StaleSource:
    source_name: str
    stale_days: int


@dataclass(slots=True, frozen=True)
class SourceHealthSummary:
    latest_attempt_at: datetime | None
    latest_success_at: datetime | None
    failed_sources: tuple[str, ...]
    stale_sources: tuple[StaleSource, ...]
    source_last_success_at: dict[str, datetime]


def build_source_health_summary(
    *,
    db_path: Path | str = DB_PATH,
    now: datetime | None = None,
) -> SourceHealthSummary:
    current_time = _normalize_dt(now) or datetime.now(timezone.utc)
    enabled_sources = {source.name: source for source in load_sources() if source.enabled}
    latest_rows = {
        str(row.get("source_name") or ""): row
        for row in list_latest_source_audit(db_path=db_path)
        if str(row.get("source_name") or "") in enabled_sources
    }
    audit_records = [
        row
        for row in list_source_audit_records(db_path=db_path)
        if str(row.get("source_name") or "") in enabled_sources
    ]

    source_last_success_at: dict[str, datetime] = {}
    for row in audit_records:
        source_name = str(row.get("source_name") or "")
        success_at = _normalize_dt(row.get("success_at"))
        if not source_name or success_at is None:
            continue
        previous = source_last_success_at.get(source_name)
        if previous is None or success_at > previous:
            source_last_success_at[source_name] = success_at

    latest_attempt_at = max(
        (dt for dt in (_normalize_dt(row.get("attempted_at")) for row in latest_rows.values()) if dt),
        default=None,
    )
    latest_success_at = max(source_last_success_at.values(), default=None)

    failed_sources: list[str] = []
    stale_sources: list[StaleSource] = []
    for source_name, row in sorted(latest_rows.items()):
        source = enabled_sources[source_name]
        error_at = _normalize_dt(row.get("error_at"))
        latest_row_success = _normalize_dt(row.get("success_at"))
        last_success = source_last_success_at.get(source_name)
        latest_attempt = _normalize_dt(row.get("attempted_at"))
        has_latest_failure = bool(row.get("error_message")) and error_at is not None
        if has_latest_failure and latest_row_success is None and (
            last_success is None or error_at >= last_success
        ):
            failed_sources.append(source_name)

        threshold_days = _stale_threshold_days(source.name)
        if threshold_days is not None:
            reference_at = last_success or latest_attempt
            if reference_at is None:
                continue
            stale_days = (
                current_time.astimezone(timezone.utc).date()
                - reference_at.astimezone(timezone.utc).date()
            ).days
            if stale_days >= threshold_days:
                stale_sources.append(StaleSource(source_name=source_name, stale_days=stale_days))

    return SourceHealthSummary(
        latest_attempt_at=latest_attempt_at,
        latest_success_at=latest_success_at,
        failed_sources=tuple(failed_sources),
        stale_sources=tuple(stale_sources),
        source_last_success_at=source_last_success_at,
    )


def collect_operational_notices(
    *,
    db_path: Path | str = DB_PATH,
    now: datetime | None = None,
) -> list[OperationalNotice]:
    current_time = _normalize_dt(now) or datetime.now(timezone.utc)
    source_health = build_source_health_summary(db_path=db_path, now=current_time)
    audit_by_source = {
        str(row.get("source_name") or ""): row
        for row in list_latest_source_audit(db_path=db_path)
    }

    notices: list[OperationalNotice] = []
    notices.extend(_collect_collect_freshness_notices(source_health=source_health, now=current_time))
    notices.extend(
        _collect_source_error_notices(
            db_path=db_path,
            audit_by_source=audit_by_source,
            source_health=source_health,
            now=current_time,
        )
    )
    notices.extend(
        _collect_stale_source_notices(
            source_health=source_health,
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
    source_health: SourceHealthSummary,
    now: datetime,
) -> list[OperationalNotice]:
    cutoff = now.timestamp() - SOURCE_ERROR_LOOKBACK_HOURS * 3600
    errored_sources: dict[str, bool] = {}

    for record in list_recent_source_errors(db_path=db_path, days=1):
        collected_at = _normalize_dt(record.collected_at)
        if collected_at is None or collected_at.timestamp() < cutoff:
            continue
        latest_row = audit_by_source.get(record.source_name)
        if latest_row is not None and not _is_unavailable_source_row(latest_row):
            continue
        errored_sources.setdefault(record.source_name, True)

    for source_name in source_health.failed_sources:
        errored_sources.setdefault(source_name, True)

    for source_name, row in audit_by_source.items():
        error_at = _normalize_dt(row.get("error_at"))
        if error_at is None or error_at.timestamp() < cutoff:
            continue
        if row.get("error_message") and _is_unavailable_source_row(row):
            errored_sources.setdefault(source_name, True)

    return [
        OperationalNotice(
            severity="warning",
            message=f"Источник временно недоступен: {source_name}. Данные по нему могут быть неполными.",
        )
        for source_name in sorted(errored_sources)
    ]


def _collect_collect_freshness_notices(
    *,
    source_health: SourceHealthSummary,
    now: datetime,
) -> list[OperationalNotice]:
    latest_success_at = source_health.latest_success_at
    if source_health.latest_attempt_at is None:
        return []
    if latest_success_at is None:
        return [
            OperationalNotice(
                severity="warning",
                message="Данные могут быть не полностью свежими: успешный сбор источников пока не подтвержден.",
            )
        ]
    delta = now.astimezone(timezone.utc) - latest_success_at.astimezone(timezone.utc)
    if delta <= COLLECT_SUCCESS_STALE_AFTER:
        return []
    return [
        OperationalNotice(
            severity="warning",
            message=(
                "Данные могут быть не полностью свежими: последний успешный сбор источников "
                f"{_format_user_dt(latest_success_at)}."
            ),
        )
    ]


def _collect_stale_source_notices(
    *,
    source_health: SourceHealthSummary,
) -> list[OperationalNotice]:
    notices: list[OperationalNotice] = []
    for source in source_health.stale_sources:
        if source.source_name in source_health.source_last_success_at:
            message = (
                f"{source.source_name}: последнее успешное обновление было "
                f"{source.stale_days} дней назад. Свежие публикации могут появиться с задержкой."
            )
        else:
            message = (
                f"{source.source_name}: успешный сбор не подтвержден "
                f"{source.stale_days} дней. Свежие публикации могут быть пропущены."
            )
        notices.append(OperationalNotice(severity="warning", message=message))
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


def _format_user_dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def _normalize_dt(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_unavailable_source_row(row: dict[str, object]) -> bool:
    return bool(row.get("error_message")) and _normalize_dt(row.get("success_at")) is None
