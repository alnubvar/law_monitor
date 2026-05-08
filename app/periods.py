from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from app.models import RawDocument

PeriodKind = Literal["rolling", "today", "yesterday"]


@dataclass(frozen=True)
class PeriodSpec:
    kind: PeriodKind
    days: int
    target_date: date | None = None


def build_rolling_period(days: int) -> PeriodSpec:
    safe_days = max(1, min(int(days), 365))
    return PeriodSpec(kind="rolling", days=safe_days, target_date=None)


def build_today_period(*, now: datetime | None = None) -> PeriodSpec:
    return PeriodSpec(kind="today", days=1, target_date=_today_utc(now=now))


def build_yesterday_period(*, now: datetime | None = None) -> PeriodSpec:
    today = _today_utc(now=now)
    return PeriodSpec(kind="yesterday", days=2, target_date=today - timedelta(days=1))


def parse_period_spec(token: str | None, *, default_days: int, now: datetime | None = None) -> PeriodSpec:
    normalized = str(token or "").strip().lower()
    if normalized == "today":
        return build_today_period(now=now)
    if normalized == "yesterday":
        return build_yesterday_period(now=now)
    if normalized.isdigit():
        return build_rolling_period(int(normalized))
    return build_rolling_period(default_days)


def format_period_label(period: PeriodSpec) -> str:
    if period.kind == "today" and period.target_date is not None:
        return f"сегодня, {period.target_date.strftime('%d.%m.%Y')}"
    if period.kind == "yesterday" and period.target_date is not None:
        return f"вчера, {period.target_date.strftime('%d.%m.%Y')}"
    if period.days == 1:
        return f"сегодня, {_today_utc().strftime('%d.%m.%Y')}"
    if period.days == 3:
        return "последние 3 дня"
    return f"последние {period.days} дней"


def filter_documents_for_period(
    documents: list[RawDocument],
    period: PeriodSpec,
) -> list[RawDocument]:
    if period.kind == "rolling" or period.target_date is None:
        return list(documents)
    return [
        document
        for document in documents
        if document_event_date(document) == period.target_date
    ]


def document_event_date(document: RawDocument) -> date:
    event_dt = document.published_at or document.collected_at
    if event_dt.tzinfo is None:
        event_dt = event_dt.replace(tzinfo=timezone.utc)
    return event_dt.astimezone(timezone.utc).date()


def _today_utc(*, now: datetime | None = None) -> date:
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference.astimezone(timezone.utc).date()
