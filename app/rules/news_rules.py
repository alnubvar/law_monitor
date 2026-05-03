from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from app.rules import matches_keyword_groups

NEWS_SIGNAL_GROUPS = (
    "support_measures",
    "subsidy_terms",
    "credit_terms",
    "export_support",
    "grain_support",
    "regulatory_change",
)
NEWS_SIGNAL_MARKERS = (
    "пошлин",
    "экспорт",
    "господдерж",
    "субсид",
    "льготн",
    "кредит",
    "поручен",
    "финансирован",
    "программа",
)


def has_news_signal(
    title: str,
    lead_text: str,
    *,
    matched_keywords: Sequence[str],
    keyword_groups: Mapping[str, Sequence[str]],
) -> bool:
    combined = f"{title} {lead_text}"
    if re.search(r"без сигналов\s+(господдерж|субсид|регулир|экспорт)", combined):
        return False
    return (
        matches_keyword_groups(combined, NEWS_SIGNAL_GROUPS, keyword_groups)
        or any(marker in combined for marker in NEWS_SIGNAL_MARKERS)
        or any(keyword.lower() in combined for keyword in matched_keywords)
    )
