from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.rules import matches_keyword_groups
from app.rules.page_type_rules import REQUIRES_ATTENTION_SIGNALS

STRATEGY_SIGNAL_GROUPS = (
    "support_measures",
    "subsidy_terms",
    "credit_terms",
    "regulatory_change",
    "public_discussion",
)
SUPPORT_DOCUMENT_SIGNAL_GROUPS = (
    "support_measures",
    "subsidy_terms",
    "credit_terms",
    "regulatory_change",
    "public_discussion",
)
REGIONAL_NPA_SIGNAL_GROUPS = (
    "regulatory_change",
    "public_discussion",
    "subsidy_terms",
    "support_measures",
)


def has_strategy_signal(
    title: str,
    lead_text: str,
    *,
    matched_keywords: Sequence[str],
    keyword_groups: Mapping[str, Sequence[str]],
) -> bool:
    combined = f"{title} {lead_text}"
    return (
        matches_keyword_groups(combined, STRATEGY_SIGNAL_GROUPS, keyword_groups)
        or any(keyword.lower() in combined for keyword in matched_keywords)
        or "апк" in combined
    )


def has_support_document_signal(
    title: str,
    lead_text: str,
    *,
    matched_keywords: Sequence[str],
    keyword_groups: Mapping[str, Sequence[str]],
) -> bool:
    combined = f"{title} {lead_text}"
    return (
        matches_keyword_groups(combined, SUPPORT_DOCUMENT_SIGNAL_GROUPS, keyword_groups)
        or any(keyword.lower() in combined for keyword in matched_keywords)
        or any(signal in combined for signal in REQUIRES_ATTENTION_SIGNALS)
    )


def has_regional_npa_signal(
    title: str,
    lead_text: str,
    *,
    matched_keywords: Sequence[str],
    keyword_groups: Mapping[str, Sequence[str]],
) -> bool:
    combined = f"{title} {lead_text}"
    return (
        matches_keyword_groups(combined, REGIONAL_NPA_SIGNAL_GROUPS, keyword_groups)
        or any(keyword.lower() in combined for keyword in matched_keywords)
        or "субсид" in combined
        or "апк" in combined
    )
