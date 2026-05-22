from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from app.config import get_source_role
from app.models import RawDocument
from app.rules.ahstep_domain_rules import (
    has_ahstep_domain_relevance,
    should_apply_ahstep_domain_gate,
)
from app.rules.ahstep_applicability import evaluate_support_measure_applicability
from app.rules.news_background_guard import guard_news_signal_action_level
from app.user_facing import is_weak_ocr_placeholder_document

VISIBLE_WATCHLIST_PAGE_TYPES = {
    "news_background",
    "measure_card",
    "new_rule",
    "selection_announcement",
    "deadline_update",
}
TELEGRAM_WATCHLIST_BUCKETS = {
    "support_reference",
    "target_watchlist",
    "industry_background",
}
TARGET_REGION_MARKERS: dict[str, tuple[str, ...]] = {
    "rostov": ("ростов", "донланд", "ростовской области"),
    "krasnodar": ("краснодар", "кубани", "кубан", "краснодарского края"),
    "stavropol": ("ставрополь", "ставропольского края"),
    "moscow_oblast": ("московская область", "московской области", "подмосковье"),
}
GLOBAL_MARKERS = (
    "мексик",
    "кндр",
    "казахстан",
    "австрали",
    "южной коре",
    "нигер",
    "брикс",
    "оаэ",
    "китай",
    "мировой рынок",
    "зарубеж",
    "экспортный рынок",
)
NON_TARGET_RF_MARKERS = (
    "томск",
    "томской области",
    "ульянов",
    "башкир",
    "иркут",
    "омск",
    "липецк",
    "пензен",
    "саратов",
    "краснояр",
    "псков",
    "волгоград",
    "алтай",
    "татарстан",
    "чуваш",
    "марий эл",
    "мордов",
)
SUPPORT_PAGE_TYPES = {
    "measure_card",
    "new_rule",
    "selection_announcement",
    "deadline_update",
    "reference_page",
}
REFERENCE_TITLE_WORD_RE = re.compile(
    r"\b(анкета|форма|формы|памятка|инструкция|инструкции|образец)\b",
    re.IGNORECASE,
)
GOVERNMENT_DUPLICATE_RE = re.compile(
    r"^https?://government\.ru/(?:news|docs)/(?P<doc_id>\d+)/?$",
    re.IGNORECASE,
)
MACRO_STRATEGY_TITLE_NOISE_RE = re.compile(
    r"ситуаци\w*\s+в\s+экономик|макроэконом|экономик\w*\s+показател|совещан\w*"
    r"|финансирован\w*|бюджетн\w*\s+кредит|реконструкц|пассажирск\w*\s+железнодорож",
    re.IGNORECASE,
)

VisibilitySurface = Literal["report", "telegram_digest", "telegram_list"]


def effective_user_action_level(document: RawDocument) -> str | None:
    guarded_action_level = guard_news_signal_action_level(
        document.action_level,
        source_role=get_source_role(document.source_name),
        reason=document.relevance_reason,
        impact=document.impact,
        signal=document.business_signal,
        title=document.title,
        summary=document.summary,
        raw_text=document.raw_text,
        page_type=document.page_type,
    )
    if _is_ahstep_domain_excluded(document, action_level=guarded_action_level):
        return "background"
    applicability = _support_measure_applicability(document)
    if (
        guarded_action_level in {"requires_attention", "watchlist"}
        and not applicability.is_applicable
    ):
        return applicability.recommended_action_level or "background"
    if (
        guarded_action_level == "requires_attention"
        and is_weak_ocr_placeholder_document(document)
    ):
        return "watchlist"
    return guarded_action_level


def deduplicate_user_facing_documents(
    documents: Sequence[RawDocument],
) -> list[RawDocument]:
    selected_by_key: dict[str, RawDocument] = {}
    ordered_keys: list[str] = []
    for document in documents:
        dedup_key = _user_facing_dedup_key(document)
        if dedup_key not in selected_by_key:
            selected_by_key[dedup_key] = document
            ordered_keys.append(dedup_key)
            continue
        selected_by_key[dedup_key] = _prefer_user_facing_document(
            selected_by_key[dedup_key],
            document,
        )
    return [selected_by_key[key] for key in ordered_keys]


def classify_display_section(document: RawDocument) -> str:
    if effective_user_action_level(document) == "requires_attention":
        return "requires_attention"
    source_role = get_source_role(document.source_name)
    if source_role in {"active_support_measures", "support_documents"}:
        return "measures_and_selections"
    if source_role == "regional_npa":
        return "regional_npa"
    if source_role == "strategy":
        return "strategy_signals"
    return "news_signals"


def visibility_bucket(document: RawDocument) -> str:
    geo_scope = _detect_geo_scope(document)
    if effective_user_action_level(document) == "requires_attention":
        if geo_scope in {"target_region", "federal_rf"}:
            return "requires_attention"
        if _is_support_reference_document(document):
            return "support_reference"
        if geo_scope == "global_market":
            return "market_background"
        return "non_target_background"

    if _is_support_reference_document(document):
        if geo_scope == "global_market":
            return "market_background"
        if geo_scope in {"target_region", "federal_rf"}:
            return "support_reference"
        return "non_target_background"

    if geo_scope == "global_market":
        return "market_background"
    if geo_scope == "target_region":
        return "target_watchlist"
    if geo_scope == "non_target_rf":
        return "non_target_background"
    return "industry_background"


def should_show_document(
    document: RawDocument,
    *,
    surface: VisibilitySurface = "report",
    relevant_only: bool = True,
    action_levels: list[str] | None = None,
    include_section_pages: bool = False,
    include_registries: bool = False,
    include_market_background: bool = False,
) -> bool:
    display_action_level = effective_user_action_level(document)
    if relevant_only and display_action_level == "irrelevant":
        return False
    if action_levels is not None and display_action_level not in action_levels:
        return False
    if _is_ahstep_domain_excluded(
        document,
        action_level=display_action_level,
    ) or _is_ahstep_domain_excluded(document, action_level=document.action_level):
        return False
    if get_source_role(
        document.source_name
    ) == "strategy" and not _is_executive_strategy_relevant(document):
        return False

    if surface == "report":
        if display_action_level == "requires_attention":
            return visibility_bucket(document) == "requires_attention"
        if not _should_show_watchlist_document(
            document,
            include_section_pages=include_section_pages,
            include_registries=include_registries,
        ):
            return False
        if (
            visibility_bucket(document) == "market_background"
            and not include_market_background
        ):
            return False
        return True

    if surface == "telegram_digest":
        if document.support_status == "inactive":
            return False
        if document.application_status == "closed":
            return False
        if document.page_type == "reference_page":
            return False
        if display_action_level == "requires_attention":
            return visibility_bucket(document) == "requires_attention"
        return (
            display_action_level == "watchlist"
            and visibility_bucket(document) in TELEGRAM_WATCHLIST_BUCKETS
        )

    if display_action_level == "requires_attention":
        return visibility_bucket(document) == "requires_attention"
    if display_action_level != "watchlist":
        return False
    if not _should_show_watchlist_document(
        document,
        include_section_pages=include_section_pages,
        include_registries=include_registries,
    ):
        return False
    return (
        visibility_bucket(document) != "market_background" or include_market_background
    )


def _is_executive_strategy_relevant(document: RawDocument) -> bool:
    title = document.title or ""
    summary = document.summary or ""
    raw_text = document.raw_text[:1200] if document.raw_text else ""
    title_summary_text = " ".join(part for part in (title, summary) if part)
    if not has_ahstep_domain_relevance(title, summary, raw_text):
        return False
    if MACRO_STRATEGY_TITLE_NOISE_RE.search(
        title_summary_text
    ) and not has_ahstep_domain_relevance(title_summary_text):
        return False
    return True


def _is_ahstep_domain_excluded(
    document: RawDocument,
    *,
    action_level: str | None,
) -> bool:
    if action_level not in {"requires_attention", "watchlist"}:
        return False
    if is_weak_ocr_placeholder_document(document):
        return False
    source_role = get_source_role(document.source_name)
    if not should_apply_ahstep_domain_gate(source_role):
        return False
    return not has_ahstep_domain_relevance(
        document.title,
        document.summary,
        (document.raw_text or "")[:5000],
        document.terms_text,
    )


def _should_show_watchlist_document(
    document: RawDocument,
    *,
    include_section_pages: bool,
    include_registries: bool,
) -> bool:
    page_type = document.page_type or "unknown"
    if _looks_like_regulation_gov_subsidy_watchlist(document):
        return True
    if include_section_pages:
        return page_type not in {"navigation", "unknown"}
    if page_type in {"registry", "results_protocol"}:
        return include_registries
    if page_type == "reference_page":
        return _is_support_reference_document(document)
    return page_type in VISIBLE_WATCHLIST_PAGE_TYPES


def _looks_like_regulation_gov_subsidy_watchlist(document: RawDocument) -> bool:
    if document.action_level != "watchlist":
        return False
    if not document.url or "regulation.gov.ru" not in document.url.lower():
        return False
    title_text = (document.title or "").lower()
    return bool(re.search(r"порядк\w*.*субсид", title_text))


def _is_support_reference_document(document: RawDocument) -> bool:
    source_key = f"{document.source_name} {document.source_url}".lower()
    title_text = document.title.lower()
    has_reference_title = bool(REFERENCE_TITLE_WORD_RE.search(title_text))
    is_anti_corruption_reference = "корруп" in title_text and (
        "форм" in title_text
        or "деклар" in title_text
        or "конфликт интерес" in title_text
    )
    if document.page_type == "reference_page":
        if is_anti_corruption_reference:
            return False
        return has_reference_title or (
            "гисп" in source_key
            or document.level == "support_measures"
            or "господдерж" in source_key
            or "меры поддержки" in source_key
        )
    if has_reference_title:
        return not is_anti_corruption_reference
    if document.page_type in SUPPORT_PAGE_TYPES and (
        "гисп" in source_key
        or document.level == "support_measures"
        or "господдерж" in source_key
        or "меры поддержки" in source_key
    ):
        return True
    return False


def _detect_geo_scope(document: RawDocument) -> str:
    applicability = _support_measure_applicability(document)
    if not applicability.is_applicable:
        return "non_target_rf"
    target_text = " ".join(
        part
        for part in (
            document.title.lower(),
            document.source_name.lower(),
            document.url.lower(),
        )
        if part
    )
    broad_text = " ".join(
        part
        for part in (
            target_text,
            (document.summary or "").lower(),
            (document.relevance_reason or "").lower(),
        )
        if part
    )

    if document.region in {"rostov", "krasnodar", "stavropol", "moscow_oblast"}:
        return "target_region"
    for markers in TARGET_REGION_MARKERS.values():
        if any(marker in target_text for marker in markers):
            return "target_region"
    if any(marker in broad_text for marker in GLOBAL_MARKERS):
        return "global_market"
    if any(marker in broad_text for marker in NON_TARGET_RF_MARKERS):
        return "non_target_rf"
    if document.region == "federal":
        return "federal_rf"
    return "non_target_rf"


def _support_measure_applicability(document: RawDocument):
    return evaluate_support_measure_applicability(
        title=document.title,
        raw_text=document.raw_text or "",
        source_name=document.source_name,
        url=document.url,
        level=document.level,
        region=document.region,
        source_role=get_source_role(document.source_name),
        page_type=document.page_type,
    )


def _user_facing_dedup_key(document: RawDocument) -> str:
    government_key = _government_canonical_key(document.url)
    if government_key:
        return government_key
    normalized_url = _normalized_url_key(document.url)
    if normalized_url:
        return normalized_url
    return f"title::{(document.title or '').strip().lower()}::{document.id or 0}"


def _government_canonical_key(url: str | None) -> str | None:
    normalized_url = (url or "").strip()
    if not normalized_url:
        return None
    match = GOVERNMENT_DUPLICATE_RE.match(normalized_url)
    if not match:
        return None
    document_id = str(match.group("doc_id") or "").strip()
    if not document_id:
        return None
    return f"government.ru:{document_id}"


def _normalized_url_key(url: str | None) -> str | None:
    normalized_url = (url or "").strip()
    if not normalized_url:
        return None
    parsed = urlsplit(normalized_url)
    if not parsed.scheme or not parsed.netloc:
        return normalized_url.lower().rstrip("/")
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            parsed.query,
            "",
        )
    )


def _prefer_user_facing_document(left: RawDocument, right: RawDocument) -> RawDocument:
    left_score = _user_facing_document_score(left)
    right_score = _user_facing_document_score(right)
    if right_score > left_score:
        return right
    return left


def _user_facing_document_score(
    document: RawDocument,
) -> tuple[int, int, int, int, int]:
    return (
        _government_docs_preference(document),
        _document_text_quality_score(document),
        _document_fact_score(document),
        len((document.summary or "").strip()),
        int(document.id or 0),
    )


def _government_docs_preference(document: RawDocument) -> int:
    normalized_url = (document.url or "").strip().lower()
    if "/docs/" in normalized_url:
        return 1
    return 0


def _document_text_quality_score(document: RawDocument) -> int:
    text_length = len((document.raw_text or "").strip())
    if text_length >= 5000:
        return 3
    if text_length >= 1000:
        return 2
    if text_length >= 100:
        return 1
    return 0


def _document_fact_score(document: RawDocument) -> int:
    score = 0
    if document.deadline_text:
        score += 3
    if document.application_status and document.application_status != "unknown":
        score += 2
    if document.support_status and document.support_status != "unknown":
        score += 1
    if document.npa_number:
        score += 1
    if document.terms_text:
        score += 1
    if document.business_signal:
        score += 1
    return score
