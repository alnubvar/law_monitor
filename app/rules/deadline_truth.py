"""Single source of truth for deadline parsing and lifecycle classification.

This module centralises the date-parsing and "is this deadline still alive?"
question that previously lived in three places:

- ``app/llm/facts_extractor.py`` (application_status detection)
- ``app/rules/business_signal_rules.py`` (escalation/near-deadline gates)
- ``app/reports/markdown_report.py`` (user-facing "Срок" rendering)

All callers must consult these helpers so classification and rendering agree on
whether an application window is still operational.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Literal

from app.extractors.date_extractor import parse_russian_date

DeadlineStatus = Literal["expired", "today", "near", "future", "unknown"]

NEAR_DEADLINE_DEFAULT_DAYS = 7

DATE_TOKEN_RE = re.compile(
    r"(?<!\d)("
    r"\d{1,2}[./]\d{1,2}[./]\d{4}"
    r"|"
    r"\d{4}-\d{2}-\d{2}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:января|январь|февраля|февраль|марта|март|апреля|апрель|мая|май|июня|июнь|"
    r"июля|июль|августа|август|сентября|сентябрь|октября|октябрь|ноября|ноябрь|"
    r"декабря|декабрь)"
    r"\s+\d{4}(?:\s+г(?:ода|\.))?"
    r")(?!\d)",
    re.IGNORECASE,
)
DEADLINE_DATE_RE = re.compile(
    r"\b(?:до|по)\s+(?P<date>"
    r"\d{1,2}[./]\d{1,2}[./]\d{4}"
    r"|"
    r"\d{4}-\d{2}-\d{2}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:января|январь|февраля|февраль|марта|март|апреля|апрель|мая|май|июня|июнь|"
    r"июля|июль|августа|август|сентября|сентябрь|октября|октябрь|ноября|ноябрь|"
    r"декабря|декабрь)"
    r"\s+\d{4}(?:\s+г(?:ода|\.))?"
    r")",
    re.IGNORECASE,
)
DEADLINE_RANGE_RE = re.compile(
    r"\bс\s+(?P<start>"
    r"\d{1,2}[./]\d{1,2}[./]\d{4}"
    r"|"
    r"\d{4}-\d{2}-\d{2}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:января|январь|февраля|февраль|марта|март|апреля|апрель|мая|май|июня|июнь|"
    r"июля|июль|августа|август|сентября|сентябрь|октября|октябрь|ноября|ноябрь|"
    r"декабря|декабрь)"
    r"\s+\d{4}(?:\s+г(?:ода|\.))?"
    r")\s+по\s+(?P<end>"
    r"\d{1,2}[./]\d{1,2}[./]\d{4}"
    r"|"
    r"\d{4}-\d{2}-\d{2}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:января|январь|февраля|февраль|марта|март|апреля|апрель|мая|май|июня|июнь|"
    r"июля|июль|августа|август|сентября|сентябрь|октября|октябрь|ноября|ноябрь|"
    r"декабря|декабрь)"
    r"\s+\d{4}(?:\s+г(?:ода|\.))?"
    r")",
    re.IGNORECASE,
)
DISCUSSION_DEADLINE_RE = re.compile(
    r"(?:конец|окончание|завершение)\s+(?:публичного\s+)?обсуждени[яй]\s*[:\-]?\s*(?P<date>"
    r"\d{1,2}[./]\d{1,2}[./]\d{4}"
    r"|"
    r"\d{4}-\d{2}-\d{2}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:января|январь|февраля|февраль|марта|март|апреля|апрель|мая|май|июня|июнь|"
    r"июля|июль|августа|август|сентября|сентябрь|октября|октябрь|ноября|ноябрь|"
    r"декабря|декабрь)"
    r"\s+\d{4}(?:\s+г(?:ода|\.))?"
    r")",
    re.IGNORECASE,
)


def today_utc() -> date:
    """Return the current UTC date.

    Centralised so tests can monkey-patch a single location to inject a
    deterministic "today" for the whole pipeline.
    """
    return datetime.now(timezone.utc).date()


def parse_deadline_date(text: str | None) -> date | None:
    """Parse a deadline date from an already-extracted deadline snippet.

    Resolution order mirrors ``facts_extractor._extract_deadline_date`` so the
    behaviour is identical: prefer the end of a ``с X по Y`` range, then a
    ``до/по X`` date, then a ``конец обсуждения X`` date, finally any date
    token in the string. When several candidates are present the latest match
    wins so that ranges and "до 30.05" beat a leading "приказ от 01.04".
    """
    if not text:
        return None

    candidate_date: date | None = None
    for match in DEADLINE_RANGE_RE.finditer(text):
        parsed = parse_russian_date(match.group("end"))
        if parsed is not None:
            candidate_date = parsed
    for match in DEADLINE_DATE_RE.finditer(text):
        parsed = parse_russian_date(match.group("date"))
        if parsed is not None:
            candidate_date = parsed
    for match in DISCUSSION_DEADLINE_RE.finditer(text):
        parsed = parse_russian_date(match.group("date"))
        if parsed is not None:
            candidate_date = parsed
    if candidate_date is not None:
        return candidate_date
    for match in DATE_TOKEN_RE.finditer(text):
        parsed = parse_russian_date(match.group(1))
        if parsed is not None:
            candidate_date = parsed
    return candidate_date


def classify_deadline(
    text: str | None,
    *,
    today: date | None = None,
    near_days: int = NEAR_DEADLINE_DEFAULT_DAYS,
) -> DeadlineStatus:
    """Classify a deadline snippet against ``today``.

    - ``unknown`` — no date could be parsed
    - ``expired`` — parsed date strictly before today
    - ``today`` — parsed date equals today
    - ``near`` — parsed date within ``near_days`` after today
    - ``future`` — parsed date strictly later than ``today + near_days``
    """
    parsed = parse_deadline_date(text)
    if parsed is None:
        return "unknown"
    current = today if today is not None else today_utc()
    delta = (parsed - current).days
    if delta < 0:
        return "expired"
    if delta == 0:
        return "today"
    if delta <= near_days:
        return "near"
    return "future"


def is_deadline_expired(text: str | None, *, today: date | None = None) -> bool:
    """Return ``True`` only when a parsable deadline is strictly in the past."""
    return classify_deadline(text, today=today) == "expired"


def is_deadline_today(text: str | None, *, today: date | None = None) -> bool:
    """Return ``True`` only when a parsable deadline equals today."""
    return classify_deadline(text, today=today) == "today"


def is_deadline_alive(text: str | None, *, today: date | None = None) -> bool:
    """Return ``True`` when a parsable deadline is today or later.

    Returns ``False`` for expired deadlines. Returns ``False`` for unparsable
    text so callers cannot accidentally treat "unknown" as alive — they must
    decide what to do with ``unknown`` explicitly.
    """
    status = classify_deadline(text, today=today)
    return status in {"today", "near", "future"}


def format_iso_date(value: date) -> str:
    """Render a date as ``DD.MM.YYYY`` for executive-friendly Russian wording."""
    return value.strftime("%d.%m.%Y")
