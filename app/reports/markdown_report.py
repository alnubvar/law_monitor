from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from collections import Counter
import re
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app import config
from app.config import get_source_role
from app.llm.enrichment import get_display_enrichment
from app.models import DigestItem, RawDocument, SourceErrorRecord
from app.operational_health import (
    OperationalNotice,
    format_operational_notices_markdown,
)
from app.periods import PeriodSpec, build_rolling_period, format_period_label
from app.rules.deadline_truth import (
    classify_deadline,
    format_iso_date,
    is_deadline_expired,
    is_deadline_today,
    parse_deadline_date,
    today_utc,
)
from app.storage import list_document_enrichments
from app.user_facing import (
    build_executive_action,
    build_executive_reason,
    disambiguate_visible_titles,
    has_trade_regulation_signal,
    is_meaningful_executive_highlight,
    select_executive_summary,
    user_facing_action_level,
    user_facing_title,
)
from app.rules.gr_topic_ontology import detect_gr_topic
from app.visibility import (
    classify_display_section as visibility_display_section,
    deduplicate_user_facing_documents,
    should_show_document,
    visibility_bucket,
)

SHORT_SUMMARY_MAX_CHARS = 140
REPORT_TITLE_MAX_CHARS = 90
REPORT_REACTION_TITLE_MAX_CHARS = 70
# UI/query parameters that are part of the source-side viewer UX and have no
# operational meaning. Stripped from rendered "- Источник:" links so the GR
# user sees a stable canonical URL.
_UI_QUERY_PARAMS_TO_STRIP = frozenset(
    {
        "showbackbutton",
        "competitiontype",
        "tab",
        "backurl",
        "from",
        "ref",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
    }
)
MEASURES_SECTION_DISPLAY_MAX = 6
# Executive cap on the urgent block. Overflow is NOT hidden — it falls through
# to the source-role-appropriate operational section.
REQUIRES_ATTENTION_DISPLAY_MAX = 5
# Operational priority tiers used to rank items inside the urgent block and to
# decide which items overflow when the block is over capacity. Tiers are
# spread far apart so within-tier nudges (recency, doc id) never cross tier
# boundaries.
_PRIORITY_TODAY_DEADLINE = 400
_PRIORITY_TRADE_REGULATION = 380
_PRIORITY_ACCEPTED_REGULATORY_ACT = 360
_PRIORITY_NEAR_DEADLINE = 340
_PRIORITY_SUPPORT_CHANGE_SIGNAL = 300
_PRIORITY_OPEN_WITHOUT_DEADLINE = 260
_PRIORITY_REGULATION_DISCUSSION = 200
_PRIORITY_STRATEGIC_BACKGROUND = 180
_PRIORITY_OPERATIONAL_DEFAULT = 100
_GR_TOPIC_PRIORITY_BOOSTS = {
    "postanovlenie_1528": 18,
    "concessional_credit": 14,
    "regional_subsidy_distribution": 14,
    "grain_compensation": 12,
    "dairy": 10,
    "elite_seed": 10,
    "processing_modernization": 10,
    "export_support": 10,
    "direct_indirect_subsidies": 8,
}
_ISO_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?")
_ISO_FRAGMENT_RE = re.compile(r"\.\d{1,9}Z\b")
_DEADLINE_GARBAGE_LABEL_RE = re.compile(
    r"\s+(?:Проблема|Описание|Решение|Цель|Цели|Процедура|Статус|Текст)\s*:",
    re.IGNORECASE,
)
_DISCUSSION_DEADLINE_RE = re.compile(
    r"(конец\s+обсуждения)\s*:\s*(\d{1,2}[.]\d{1,2}[.]\d{4})",
    re.IGNORECASE,
)
_SUPPORT_OPERATIONAL_CHANGE_RE = re.compile(
    r"измен|обнов|новые\s+услов|новый\s+поряд|внесени[ея]\s+измен|утвержден|утверждён",
    re.IGNORECASE,
)
_GENERIC_SUPPORT_REFERENCE_TITLE_RE = re.compile(
    r"\b(справочник|брошюр\w*|памятк\w*|инструкц\w*|методич\w*)\b",
    re.IGNORECASE,
)
_KRASNODAR_SUPPORT_ORDER_QUOTED_CORE_RE = re.compile(
    r'["«](?P<core>[^"»]*поряд[^"»]*субсид[^"»]*)["»]',
    re.IGNORECASE,
)
_KRASNODAR_SUPPORT_ORDER_TRAILING_BOILERPLATE_RE = re.compile(
    r"\b(?:в\s+соответствии\s+с(?:о)?|на\s+основании|приказываю|постановляю|настоящ(?:им|ее)|стать(?:ей|и))\b",
    re.IGNORECASE,
)
_BUREAUCRATIC_TITLE_PREFIXES = (
    "об утверждении",
    "о внесении изменений",
    "о признании утратившим",
    "о внесении дополнений",
    "об изменении",
    "о введении",
)
BACKGROUND_DEFAULT_LIMIT = 5
MARKET_BACKGROUND_LIMIT = 5
REPORT_BUCKET_ORDER = (
    "requires_attention",
    "support_reference",
    "target_watchlist",
    "industry_background",
    "non_target_background",
    "market_background",
)
REPORT_BUCKET_TITLES = {
    "requires_attention": "## Требует внимания GR",
    "support_reference": "## Меры поддержки / справочно",
    "target_watchlist": "## На наблюдении по целевым регионам",
    "industry_background": "## Отраслевой фон РФ",
    "non_target_background": "## Фон вне целевой географии",
    "market_background": "## Глобальный / рыночный фон",
}
EMPTY_BUCKET_MESSAGES = {
    "requires_attention": "Документов, требующих внимания GR, за выбранный период не найдено.",
    "support_reference": "Подходящих мер поддержки и справочных материалов за выбранный период не найдено.",
    "target_watchlist": "Документов по целевым регионам на наблюдении за выбранный период не найдено.",
    "industry_background": "Федерального отраслевого фона для показа не найдено.",
    "non_target_background": "Фона вне целевой географии для показа не найдено.",
    "market_background": "Глобальный и рыночный фон за выбранный период не добавлен в отчет.",
}
DISPLAY_SECTION_ORDER = (
    "requires_attention",
    "measures_and_selections",
    "regional_npa",
    "strategy_signals",
    "news_signals",
)
DISPLAY_SECTION_TITLES = {
    "requires_attention": "## 🚨 Требует внимания",
    "measures_and_selections": "## 📢 Меры и отборы",
    "regional_npa": "## ⚖️ Региональные изменения",
    "strategy_signals": "## 🏛 Стратегические сигналы",
    "news_signals": "## 📰 Отраслевые сигналы",
}
DISPLAY_EMPTY_MESSAGES = {
    "requires_attention": "Новых пунктов, требующих внимания, не найдено.",
    "measures_and_selections": "Новых мер и отборов не найдено.",
    "regional_npa": "Новых региональных изменений не найдено.",
    "strategy_signals": "Новых стратегических сигналов не найдено.",
    "news_signals": "Новых отраслевых сигналов не найдено.",
}
BAD_TITLE_VALUES = {"просмотр", "скачать", "документ", "pdf"}
TITLE_SIMILARITY_THRESHOLD = 0.92


@dataclass(slots=True)
class ReportView:
    shown_buckets: dict[str, list[RawDocument]]
    total_bucket_counts: dict[str, int]
    hidden_service_count: int
    hidden_market_count: int
    hidden_due_to_max_items_count: int
    hidden_background_overflow_count: int

    @property
    def total_visible(self) -> int:
        return sum(len(documents) for documents in self.shown_buckets.values())

    def flatten(self) -> list[RawDocument]:
        visible: list[RawDocument] = []
        for bucket in REPORT_BUCKET_ORDER:
            visible.extend(self.shown_buckets.get(bucket, []))
        return visible


def _to_digest_item(document: RawDocument, *, title: str | None = None) -> DigestItem:
    return DigestItem(
        title=title or _report_title(document),
        region=document.region,
        source_name=document.source_name,
        url=document.url,
        importance=document.importance,
        action_level=user_facing_action_level(document),
        page_type=document.page_type,
        summary=document.summary,
        topic=document.topic,
        impact=document.impact,
        relevance_reason=document.relevance_reason,
        published_at=document.published_at,
        support_status=document.support_status,
        is_active=document.is_active,
        is_continuous=document.is_continuous,
        application_status=document.application_status,
        npa_number=document.npa_number,
        deadline_text=document.deadline_text,
        terms_text=document.terms_text,
        business_signal=document.business_signal,
        risk_notes=document.risk_notes,
    )


def generate_markdown_report(
    documents: Iterable[RawDocument],
    report_date: str,
    *,
    period_days: int | None = None,
    period_label: str | None = None,
    generated_at: datetime | None = None,
    report_title: str | None = None,
    intro_note: str | None = None,
    operational_notices: Iterable[OperationalNotice] | None = None,
    relevant_only: bool = True,
    max_items: int | None = None,
    source_errors: Iterable[SourceErrorRecord] | None = None,
    action_levels: list[str] | None = None,
    include_background: bool = False,
    include_section_pages: bool = False,
    include_registries: bool = False,
    include_market_background: bool = False,
    include_full_background: bool = False,
    db_path: Path | str | None = None,
    period_context_lines: Iterable[str] | None = None,
) -> str:
    document_list = list(documents)
    report_view = build_report_view(
        document_list,
        relevant_only=relevant_only,
        max_items=max_items,
        action_levels=action_levels,
        include_background=include_background,
        include_section_pages=include_section_pages,
        include_registries=include_registries,
        include_market_background=include_market_background,
        include_full_background=include_full_background,
    )
    display_sections = _build_display_sections(report_view.flatten())
    rendered_documents = _flatten_display_sections(display_sections)
    enrichment_by_url = list_document_enrichments(
        [document.url for document in rendered_documents],
        db_path=db_path or config.DB_PATH,
    )
    notices = list(operational_notices or [])

    generated_at_value = generated_at or datetime.now()
    lines: list[str] = [report_title or f"# GR-дайджест за {report_date}", ""]
    if intro_note:
        lines.extend([intro_note, ""])
    lines.extend(
        _format_header_summary(
            document_list,
            report_view=report_view,
            display_sections=display_sections,
            report_date=report_date,
            period_days=period_days,
            period_label=period_label,
            generated_at=generated_at_value,
            period_context_lines=list(period_context_lines or []),
        )
    )
    lines.extend(format_operational_notices_markdown(notices))
    title_by_id = disambiguate_visible_titles(
        rendered_documents, max_chars=REPORT_TITLE_MAX_CHARS
    )
    for section in DISPLAY_SECTION_ORDER:
        documents_for_bucket = display_sections.get(section, [])
        lines.append(DISPLAY_SECTION_TITLES[section])
        if documents_for_bucket:
            for document in documents_for_bucket:
                digest_item = _to_digest_item(
                    document, title=title_by_id.get(document.id)
                )
                lines.extend(
                    _format_human_item(
                        digest_item,
                        require_action=section == "requires_attention",
                        enrichment=get_display_enrichment(
                            enrichment_by_url.get(document.url)
                        ),
                    )
                )
        else:
            lines.append(DISPLAY_EMPTY_MESSAGES[section])
            lines.append("")

    lines.extend(_format_human_outro(display_sections))
    return "\n".join(lines).strip() + "\n"


def build_report_view(
    documents: Iterable[RawDocument],
    *,
    relevant_only: bool = True,
    max_items: int | None = None,
    action_levels: list[str] | None = None,
    include_background: bool = False,
    include_section_pages: bool = False,
    include_registries: bool = False,
    include_market_background: bool = False,
    include_full_background: bool = False,
) -> ReportView:
    document_list = list(documents)
    best_document_list = select_best_report_documents(document_list)
    filtered_action_levels = list(action_levels or ["requires_attention", "watchlist"])
    if include_background and "background" not in filtered_action_levels:
        filtered_action_levels.append("background")

    report_candidates = best_document_list
    if relevant_only:
        report_candidates = [
            document
            for document in report_candidates
            if user_facing_action_level(document) != "irrelevant"
        ]
    report_candidates = [
        document
        for document in report_candidates
        if user_facing_action_level(document) in filtered_action_levels
    ]

    visible_candidates: list[RawDocument] = []
    hidden_service_count = 0
    for document in report_candidates:
        if should_show_document(
            document,
            surface="report",
            relevant_only=False,
            action_levels=filtered_action_levels,
            include_section_pages=include_section_pages,
            include_registries=include_registries,
            include_market_background=True,
        ):
            visible_candidates.append(document)
            continue
        hidden_service_count += 1

    raw_buckets: dict[str, list[RawDocument]] = {
        bucket: [] for bucket in REPORT_BUCKET_ORDER
    }
    hidden_market_count = 0
    for document in visible_candidates:
        bucket = classify_document_bucket(document)
        if bucket == "market_background" and not include_market_background:
            hidden_market_count += 1
            continue
        raw_buckets[bucket].append(document)

    hidden_background_overflow_count = 0
    if not include_full_background:
        for bucket in ("industry_background", "non_target_background"):
            if len(raw_buckets[bucket]) > BACKGROUND_DEFAULT_LIMIT:
                overflow = len(raw_buckets[bucket]) - BACKGROUND_DEFAULT_LIMIT
                hidden_background_overflow_count += overflow
                raw_buckets[bucket] = raw_buckets[bucket][:BACKGROUND_DEFAULT_LIMIT]
    if (
        include_market_background
        and len(raw_buckets["market_background"]) > MARKET_BACKGROUND_LIMIT
    ):
        overflow = len(raw_buckets["market_background"]) - MARKET_BACKGROUND_LIMIT
        hidden_market_count += overflow
        raw_buckets["market_background"] = raw_buckets["market_background"][
            :MARKET_BACKGROUND_LIMIT
        ]

    shown_buckets: dict[str, list[RawDocument]] = {
        bucket: [] for bucket in REPORT_BUCKET_ORDER
    }
    hidden_due_to_max_items_count = 0
    remaining_items = max_items
    for bucket in REPORT_BUCKET_ORDER:
        bucket_documents = raw_buckets[bucket]
        if remaining_items is None:
            shown_buckets[bucket] = bucket_documents
            continue
        if remaining_items <= 0:
            hidden_due_to_max_items_count += len(bucket_documents)
            continue
        shown_buckets[bucket] = bucket_documents[:remaining_items]
        hidden_due_to_max_items_count += max(
            len(bucket_documents) - len(shown_buckets[bucket]),
            0,
        )
        remaining_items -= len(shown_buckets[bucket])

    total_bucket_counts = {
        bucket: len(raw_buckets[bucket]) for bucket in REPORT_BUCKET_ORDER
    }
    return ReportView(
        shown_buckets=shown_buckets,
        total_bucket_counts=total_bucket_counts,
        hidden_service_count=hidden_service_count,
        hidden_market_count=hidden_market_count,
        hidden_due_to_max_items_count=hidden_due_to_max_items_count,
        hidden_background_overflow_count=hidden_background_overflow_count,
    )


def select_best_report_documents(documents: Iterable[RawDocument]) -> list[RawDocument]:
    document_list = deduplicate_user_facing_documents(list(documents))
    groups: list[list[RawDocument]] = []
    group_indexes: list[int] = []
    url_group_indexes: dict[str, int] = {}
    title_group_keys: list[str] = []

    for index, document in enumerate(document_list):
        url_key = _document_url_key(document)
        if url_key:
            group_index = url_group_indexes.get(url_key)
            if group_index is not None:
                groups[group_index].append(document)
                continue

        title_key = _document_title_key(document)
        group_index = _find_similar_title_group(title_key, title_group_keys)
        if group_index is None:
            group_index = len(groups)
            groups.append([document])
            group_indexes.append(index)
            title_group_keys.append(title_key)
        else:
            groups[group_index].append(document)

        if url_key:
            url_group_indexes[url_key] = group_index

    selected = [
        (group_indexes[index], _select_best_document(group))
        for index, group in enumerate(groups)
    ]
    return [document for _, document in sorted(selected, key=lambda item: item[0])]


def select_visible_report_documents(
    documents: Iterable[RawDocument],
    *,
    relevant_only: bool = True,
    action_levels: list[str] | None = None,
    include_background: bool = False,
    include_section_pages: bool = False,
    include_registries: bool = False,
    include_market_background: bool = False,
    include_full_background: bool = False,
    max_items: int | None = None,
) -> list[RawDocument]:
    report_view = build_report_view(
        documents,
        relevant_only=relevant_only,
        action_levels=action_levels,
        include_background=include_background,
        include_section_pages=include_section_pages,
        include_registries=include_registries,
        include_market_background=include_market_background,
        include_full_background=include_full_background,
        max_items=max_items,
    )
    return report_view.flatten()


def _select_best_document(documents: list[RawDocument]) -> RawDocument:
    return max(
        documents,
        key=lambda document: (
            _document_action_level_score(document),
            _document_text_quality_score(document),
            _document_fact_score(document),
            len(document.summary or ""),
            int(_has_informative_title(document.title)),
            _direct_file_score(document),
            _krasnodar_source_priority(document),
            _published_timestamp(document),
            document.id or 0,
        ),
    )


def _document_action_level_score(document: RawDocument) -> int:
    return {
        "requires_attention": 4,
        "watchlist": 3,
        "background": 2,
        "irrelevant": 1,
    }.get(str(document.action_level or "").strip(), 0)


def _direct_file_score(document: RawDocument) -> int:
    url = (document.url or "").lower()
    return int(url.endswith(".pdf") or url.endswith(".doc") or url.endswith(".docx"))


def _krasnodar_source_priority(document: RawDocument) -> int:
    url = (document.url or "").lower()
    if "admkrai.krasnodar.ru" in url:
        return 2
    if "npa.krasnodar.ru" in url:
        return 1
    return 0


def _document_url_key(document: RawDocument) -> str | None:
    if not document.url:
        return None
    parsed = urlsplit(document.url.strip())
    if not parsed.scheme or not parsed.netloc:
        return document.url.strip().lower().rstrip("/")
    path = parsed.path.rstrip("/")
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.query,
            "",
        )
    )


def _document_title_key(document: RawDocument) -> str:
    title = document.title or ""
    if _is_krasnodar_support_order_document(document):
        title = _normalize_krasnodar_support_order_title(title)
    return _normalize_title_key(title)


def _normalize_title_key(title: str | None) -> str:
    normalized = re.sub(
        r"[^0-9a-zа-яё]+", " ", (title or "").lower(), flags=re.IGNORECASE
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _is_krasnodar_support_order_document(document: RawDocument) -> bool:
    text = " ".join(
        part
        for part in (
            (document.title or "").lower(),
            (document.summary or "").lower(),
            (document.url or "").lower(),
            (document.source_name or "").lower(),
        )
        if part
    )
    if (
        document.region != "krasnodar"
        and "краснодар" not in text
        and "krasnodar" not in text
    ):
        return False
    return bool(re.search(r"порядк\w*\s+предоставлен\w*\s+субсид", text))


def _normalize_krasnodar_support_order_title(title: str) -> str:
    normalized = title.replace("«", '"').replace("»", '"').strip()
    quoted_core_matches = _KRASNODAR_SUPPORT_ORDER_QUOTED_CORE_RE.findall(normalized)
    if quoted_core_matches:
        normalized = quoted_core_matches[-1]
    normalized = _KRASNODAR_SUPPORT_ORDER_TRAILING_BOILERPLATE_RE.split(
        normalized,
        maxsplit=1,
    )[0]
    normalized = re.sub(r"^№\s*[\d-]+\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(
        r"\bот\s*\d{1,2}[./]\d{1,2}[./]\d{2,4}\b",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\b(?:об утверждении|о внесении изменений|о внесении изменения|об изменении|о внесении изменений)\b",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r'\s*"\s*', " ", normalized)
    return normalized.strip(" \"'.,;:-")


def _find_similar_title_group(
    title_key: str, title_group_keys: list[str]
) -> int | None:
    if len(title_key) < 24:
        return None
    for index, existing_key in enumerate(title_group_keys):
        if len(existing_key) < 24:
            continue
        if title_key == existing_key:
            return index
        if _numeric_tokens(title_key) != _numeric_tokens(existing_key):
            continue
        if (
            SequenceMatcher(None, title_key, existing_key).ratio()
            >= TITLE_SIMILARITY_THRESHOLD
        ):
            return index
    return None


def _document_text_quality_score(document: RawDocument) -> int:
    text_length = len((document.raw_text or "").strip())
    if text_length >= 5000:
        return 3
    if text_length >= 1000:
        return 2
    if text_length >= 100:
        return 1
    return 0


def _numeric_tokens(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\d+", text))


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


def _has_informative_title(title: str | None) -> bool:
    title_key = _normalize_title_key(title)
    return len(title_key) >= 12 and title_key not in BAD_TITLE_VALUES


def _published_timestamp(document: RawDocument) -> float:
    if document.published_at is None:
        return 0.0
    return document.published_at.timestamp()


def _build_display_sections(
    documents: Iterable[RawDocument],
) -> dict[str, list[RawDocument]]:
    sections: dict[str, list[RawDocument]] = {
        section: [] for section in DISPLAY_SECTION_ORDER
    }
    for document in documents:
        if _is_evergreen_support_reference(document):
            continue
        section = visibility_display_section(document)
        if section in sections:
            sections[section].append(document)
    sections = _collapse_cross_section_duplicates(sections)
    sections = _apply_requires_attention_cap(sections)
    measures = sections.get("measures_and_selections", [])
    measures.sort(
        key=_display_priority_key,
        reverse=True,
    )
    if len(measures) > MEASURES_SECTION_DISPLAY_MAX:
        sections["measures_and_selections"] = measures[:MEASURES_SECTION_DISPLAY_MAX]
    else:
        sections["measures_and_selections"] = measures
    return sections


def _apply_requires_attention_cap(
    sections: dict[str, list[RawDocument]],
) -> dict[str, list[RawDocument]]:
    """Cap the urgent block and route overflow to source-role-natural sections.

    Always sorts the urgent block by operational priority so the rendered
    order is deterministic, regardless of whether overflow occurred. Overflow
    items are appended to the section that matches their source role and the
    section is re-sorted so the higher-priority overflow appears before
    evergreen entries.
    """
    urgent = list(sections.get("requires_attention", []))
    urgent.sort(key=_requires_attention_sort_key, reverse=True)
    if len(urgent) <= REQUIRES_ATTENTION_DISPLAY_MAX:
        sections["requires_attention"] = urgent
        return sections

    sections["requires_attention"] = urgent[:REQUIRES_ATTENTION_DISPLAY_MAX]
    overflow = urgent[REQUIRES_ATTENTION_DISPLAY_MAX:]
    touched: set[str] = set()
    for document in overflow:
        fallback = _fallback_display_section(document)
        sections[fallback].append(document)
        touched.add(fallback)
    for section in touched:
        # measures_and_selections is re-sorted by the existing measures path
        # immediately after this helper returns, so we don't sort it twice.
        if section == "measures_and_selections":
            continue
        sections[section].sort(key=_display_priority_key, reverse=True)
    return sections


def _fallback_display_section(document: RawDocument) -> str:
    """Section an overflowed urgent item lands in.

    Mirrors ``visibility.classify_display_section`` minus the
    requires_attention short-circuit, so an item routed here ends up in the
    section that matches its source role.
    """
    source_role = get_source_role(document.source_name)
    if source_role in {"active_support_measures", "support_documents"}:
        return "measures_and_selections"
    if source_role == "regional_npa":
        return "regional_npa"
    if source_role == "strategy":
        return "strategy_signals"
    return "news_signals"


def _requires_attention_sort_key(
    document: RawDocument,
) -> tuple[int, float, int]:
    """Deterministic sort key for the urgent block.

    Sorts by operational priority (desc), then recency (desc), then by
    document id (asc, encoded as negative so the tuple-compare with
    ``reverse=True`` flips it to ascending).
    """
    return (
        _operational_priority_score(document),
        _published_timestamp(document) or _published_timestamp_from_collected(document),
        -(document.id or 0),
    )


def _operational_priority_score(document: RawDocument) -> int:
    """Assign an executive priority tier to a urgent-eligible item.

    Trade and support-change signals are checked against the document's own
    text (title + summary) rather than against the full enriched context.
    The generated ``business_signal`` and ``impact`` strings can mention
    "экспорту/логистике" or "изменений" as generic GR commentary, which
    would otherwise drag unrelated documents (e.g. educational event news)
    into the trade tier.
    """
    document_text = " ".join(
        part
        for part in (
            (document.title or "").lower(),
            (document.summary or "").lower(),
        )
        if part
    )
    source_role = get_source_role(document.source_name)
    page_type = document.page_type or ""
    topic_boost = _gr_topic_priority_boost(document)

    # Today's deadline on a still-open application is the most perishable
    # operational signal and must lead the urgent block.
    if (
        document.application_status == "open"
        and document.deadline_text
        and is_deadline_today(document.deadline_text)
    ):
        return _PRIORITY_TODAY_DEADLINE + topic_boost

    # Trade / export / duty / quota / restriction signals — immediate market
    # impact, ranked second so they outrank slower-burn regulatory acts.
    if has_trade_regulation_signal(document_text):
        return _PRIORITY_TRADE_REGULATION + topic_boost

    # Accepted regulatory / support acts (regional NPA, GISP support docs)
    # that carry a new rule or measure card.
    if (
        source_role == "regional_npa"
        and page_type in {"new_rule", "deadline_update"}
    ):
        return _PRIORITY_ACCEPTED_REGULATORY_ACT + topic_boost
    if (
        source_role in {"support_documents", "active_support_measures"}
        and page_type in {"new_rule", "measure_card", "deadline_update"}
    ):
        return _PRIORITY_ACCEPTED_REGULATORY_ACT + topic_boost

    # Near-deadline (within a week) open applications — still time to react.
    if (
        document.application_status == "open"
        and document.deadline_text
        and classify_deadline(document.deadline_text) == "near"
    ):
        return _PRIORITY_NEAR_DEADLINE + topic_boost

    # Documents whose visible signal is a support-change without an accepted
    # act status. Use the document's own text so generic rule wording in
    # business_signal/impact does not pollute the tier.
    if _SUPPORT_OPERATIONAL_CHANGE_RE.search(document_text):
        return _PRIORITY_SUPPORT_CHANGE_SIGNAL + topic_boost

    # Open applications without a known deadline — useful but unbounded.
    if document.application_status == "open":
        return _PRIORITY_OPEN_WITHOUT_DEADLINE + topic_boost

    # Regulation drafts / public discussions — strategic, not operational.
    if source_role == "strategy":
        return _PRIORITY_REGULATION_DISCUSSION + topic_boost

    # News-strategic background that escalated through guards but does not
    # fit any of the above tiers (e.g. soft announcements).
    if source_role in {"strategy", "news_signals"}:
        return _PRIORITY_STRATEGIC_BACKGROUND + topic_boost

    return _PRIORITY_OPERATIONAL_DEFAULT + topic_boost


def _flatten_display_sections(
    display_sections: dict[str, list[RawDocument]],
) -> list[RawDocument]:
    documents: list[RawDocument] = []
    for section in DISPLAY_SECTION_ORDER:
        documents.extend(display_sections.get(section, []))
    return documents


def _is_evergreen_support_reference(document: RawDocument) -> bool:
    if user_facing_action_level(document) == "requires_attention":
        return False
    if visibility_display_section(document) != "measures_and_selections":
        return False
    if document.deadline_text:
        return False
    if document.application_status == "open":
        return False
    if _has_support_change_signal(document):
        return False
    if _is_generic_support_reference_document(document):
        return True
    return document.published_at is None


def _is_generic_support_reference_document(document: RawDocument) -> bool:
    source_role = get_source_role(document.source_name)
    if source_role not in {"support_documents", "active_support_measures"}:
        return False
    if document.page_type == "reference_page":
        return True
    if not _GENERIC_SUPPORT_REFERENCE_TITLE_RE.search(document.title or ""):
        return False
    return bool(_direct_file_score(document))


def _collapse_cross_section_duplicates(
    sections: dict[str, list[RawDocument]],
) -> dict[str, list[RawDocument]]:
    collapsed: dict[str, list[RawDocument]] = {
        section: [] for section in DISPLAY_SECTION_ORDER
    }
    seen_keys: set[str] = set()
    for section in DISPLAY_SECTION_ORDER:
        for document in sections.get(section, []):
            dedup_key = _cross_section_duplicate_key(document)
            if dedup_key and dedup_key in seen_keys:
                continue
            if dedup_key:
                seen_keys.add(dedup_key)
            collapsed[section].append(document)
    return collapsed


def _cross_section_duplicate_key(document: RawDocument) -> str | None:
    if not _is_krasnodar_support_order_document(document):
        return None
    normalized_title = _normalize_title_key(
        _normalize_krasnodar_support_order_title(document.title or "")
    )
    if len(normalized_title) < 24:
        return None
    return f"krasnodar-support::{normalized_title}"


def _has_support_change_signal(document: RawDocument) -> bool:
    text = " ".join(
        part
        for part in (
            document.title,
            document.summary,
            document.business_signal,
            document.impact,
        )
        if part
    )
    return bool(_SUPPORT_OPERATIONAL_CHANGE_RE.search(text))


def _display_priority_key(document: RawDocument) -> tuple[int, float]:
    return (
        _display_priority_score(document),
        _published_timestamp(document) or _published_timestamp_from_collected(document),
    )


def _display_priority_score(document: RawDocument) -> int:
    score = 0
    if user_facing_action_level(document) == "requires_attention":
        score += 100
    if document.deadline_text:
        score += 40
    if document.application_status == "open":
        score += 35
    if document.published_at is not None:
        score += 25
    if visibility_bucket(document) == "target_watchlist":
        score += 20
    if _has_support_change_signal(document):
        score += 15
    if document.is_active:
        score += 2
    if _has_regulation_subsidy_watchlist_priority(document):
        score += 30
    score += _gr_topic_priority_boost(document)
    return score


def _gr_topic_priority_boost(document: RawDocument) -> int:
    ontology_match = detect_gr_topic(
        document.topic,
        document.title,
        document.summary,
        document.business_signal,
        document.impact,
    )
    if ontology_match is None:
        return 0
    return _GR_TOPIC_PRIORITY_BOOSTS.get(ontology_match.family, 0)


def _has_regulation_subsidy_watchlist_priority(document: RawDocument) -> bool:
    if get_source_role(document.source_name) != "strategy":
        return False
    if not document.url or "regulation.gov.ru" not in document.url.lower():
        return False
    title = (document.title or "").lower()
    return "порядок предоставления субсид" in title


def _published_timestamp_from_collected(document: RawDocument) -> float:
    if document.collected_at is None:
        return 0.0
    return document.collected_at.timestamp()


def classify_display_section(document: RawDocument) -> str:
    return visibility_display_section(document)


def classify_document_bucket(document: RawDocument) -> str:
    return visibility_bucket(document)


def _clean_iso_timestamps(text: str) -> str:
    def _replace(m: re.Match[str]) -> str:
        try:
            dt = datetime.strptime(m.group(0)[:19], "%Y-%m-%dT%H:%M:%S")
            return dt.strftime("%d.%m.%Y")
        except ValueError:
            return m.group(0)

    cleaned = _ISO_DATETIME_RE.sub(_replace, text)
    cleaned = _ISO_FRAGMENT_RE.sub("", cleaned)
    return cleaned


def _word_boundary_clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    clipped = text[: max_chars - 3]
    last_space = clipped.rfind(" ")
    if last_space > max_chars // 2:
        clipped = clipped[:last_space]
    return f"{clipped.rstrip(' ,.;:-')}..."


def _shorten_summary(text: str | None) -> str:
    if not text:
        return "Краткое пояснение пока не добавлено."
    normalized = re.sub(r"\s+", " ", _clean_iso_timestamps(text)).strip()
    if len(normalized) <= SHORT_SUMMARY_MAX_CHARS:
        return normalized
    return _word_boundary_clip(normalized, SHORT_SUMMARY_MAX_CHARS)


def _format_deadline_hint(text: str | None) -> str | None:
    if not text:
        return None
    normalized = re.sub(r"\s+", " ", _clean_iso_timestamps(text)).strip(" ;,-")
    if not normalized:
        return None
    normalized = _DEADLINE_GARBAGE_LABEL_RE.split(normalized, maxsplit=1)[0].strip(
        " ;,-"
    )
    if not normalized:
        return None

    parsed_date = parse_deadline_date(normalized)
    discussion_match = _DISCUSSION_DEADLINE_RE.search(normalized)
    if parsed_date is None:
        # No parsable date: keep the existing prose so we never hallucinate a
        # status from an unparsable snippet.
        if discussion_match is not None:
            label = discussion_match.group(1).capitalize()
            return f"{label}: {discussion_match.group(2)}"
        return _shorten_summary(normalized)

    formatted = format_iso_date(parsed_date)
    days_left = (parsed_date - today_utc()).days
    if days_left < 0:
        return f"Срок истёк: {formatted}"
    if days_left == 0:
        return f"Срок: сегодня ({formatted})"
    if discussion_match is not None:
        return f"Конец обсуждения: {formatted}"
    return f"Срок: до {formatted}"


def _format_human_item(
    item: DigestItem,
    *,
    require_action: bool,
    enrichment: dict[str, str] | None = None,
) -> list[str]:
    clean_summary = _clean_iso_timestamps(item.summary) if item.summary else None
    summary_text = select_executive_summary(
        item,
        enrichment_text=enrichment.get("executive_summary") if enrichment else None,
        fallback_text=clean_summary,
        max_chars=SHORT_SUMMARY_MAX_CHARS,
    )
    lines = [
        f"### {item.title}",
        f"- Почему важно: {_build_human_importance_text(item, enrichment=enrichment)}",
    ]
    if summary_text:
        lines.append(f"- Кратко: {_clean_iso_timestamps(summary_text)}")
    deadline_text = _format_deadline_hint(
        enrichment.get("deadline_hint") if enrichment else None
    )
    if deadline_text:
        # The helper returns a complete executive-friendly label
        # ("Срок: до …", "Срок истёк: …", "Конец обсуждения: …") so the
        # caller emits it as-is and does not prepend a redundant prefix.
        lines.append(f"- {deadline_text}")
    action_text = _build_human_action_text(item, enrichment=enrichment)
    if require_action or action_text:
        lines.append(
            f"- Что проверить: {action_text or 'Оценить влияние и определить следующий шаг.'}"
        )
    lines.extend(
        [
            f"- Источник: {_clean_display_url(item.url)}",
            "",
        ]
    )
    return lines


def _clean_display_url(url: str | None) -> str:
    """Strip UI-only query params from the URL rendered in the report.

    Operational params that influence the document identity (NPA ids, doc
    numbers, regional anchor ids) are preserved — only purely-UI params
    listed in ``_UI_QUERY_PARAMS_TO_STRIP`` are dropped. Returns the original
    URL unchanged when it has no scheme/netloc (unparsable) or no query.
    """
    if not url:
        return ""
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc or not parsed.query:
        return url
    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _UI_QUERY_PARAMS_TO_STRIP
    ]
    new_query = urlencode(kept, doseq=True)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment)
    )


def _build_human_importance_text(
    item: DigestItem,
    *,
    enrichment: dict[str, str] | None = None,
) -> str:
    if enrichment and enrichment.get("_document_card") and enrichment.get("business_impact"):
        return _shorten_summary(enrichment["business_impact"])
    return _shorten_summary(
        build_executive_reason(
            item,
            enrichment_text=enrichment.get("business_impact") if enrichment else None,
            fallback_text=item.business_signal or item.impact or item.summary,
            max_chars=SHORT_SUMMARY_MAX_CHARS,
        )
        or "Сигнал требует короткой оценки."
    )


def _build_human_action_text(
    item: DigestItem,
    *,
    enrichment: dict[str, str] | None = None,
) -> str:
    if enrichment and enrichment.get("_document_card") and enrichment.get("recommended_action"):
        return _shorten_summary(enrichment["recommended_action"])
    action_text = build_executive_action(
        item,
        enrichment_text=enrichment.get("recommended_action") if enrichment else None,
        max_chars=SHORT_SUMMARY_MAX_CHARS,
    )
    if action_text:
        return _shorten_summary(action_text)
    if (
        item.application_status == "open"
        and item.deadline_text
        and not is_deadline_expired(item.deadline_text)
    ):
        return f"Проверить сроки и подачу: {_shorten_summary(item.deadline_text)}"
    if item.deadline_text and is_deadline_expired(item.deadline_text):
        return "Срок истёк, документ — справочно."
    return "Оставить на наблюдении."


def _format_stats(
    documents: list[RawDocument],
    source_errors: list[SourceErrorRecord],
    *,
    report_view: ReportView,
    display_sections: dict[str, list[RawDocument]],
    include_market_background: bool,
) -> list[str]:
    relevant_count = sum(1 for document in documents if document.is_relevant)
    high_count = sum(1 for document in documents if document.importance == "high")
    medium_count = sum(1 for document in documents if document.importance == "medium")
    low_count = sum(1 for document in documents if document.importance == "low")
    requires_attention_count = sum(
        1
        for document in documents
        if user_facing_action_level(document) == "requires_attention"
    )
    watchlist_count = sum(
        1 for document in documents if user_facing_action_level(document) == "watchlist"
    )
    background_count = sum(
        1 for document in documents if document.action_level == "background"
    )
    irrelevant_count = sum(
        1 for document in documents if document.action_level == "irrelevant"
    )
    page_type_counts: dict[str, int] = {}
    for document in documents:
        key = document.page_type or "unknown"
        page_type_counts[key] = page_type_counts.get(key, 0) + 1

    unique_errors: dict[str, str] = {}
    for record in source_errors:
        unique_errors.setdefault(record.source_name, record.error)

    lines = [
        "## Статистика",
        f"- Всего собрано: {len(documents)}",
        f"- Релевантных: {relevant_count}",
        f"- Requires attention: {requires_attention_count}",
        f"- Watchlist: {watchlist_count}",
        f"- Background: {background_count}",
        f"- Irrelevant: {irrelevant_count}",
        f"- High: {high_count}",
        f"- Medium: {medium_count}",
        f"- Low: {low_count}",
        f"- Visible documents: {report_view.total_visible}",
        f"- Показано требует внимания GR: {len(display_sections['requires_attention'])}",
        f"- Показано объявленных мер / отборов: {len(display_sections['active_support_measures'])}",
        f"- Показано документов по мерам поддержки: {len(display_sections['support_documents'])}",
        f"- Показано региональных НПА: {len(display_sections['regional_npa'])}",
        f"- Показано стратегических федеральных сигналов: {len(display_sections['strategy_signals'])}",
        f"- Показано новостных предвестников: {len(display_sections['news_signals'])}",
        f"- Показано фона / справочно: {len(display_sections['background_reference'])}",
    ]
    if include_market_background:
        lines.append(
            f"- Показано глобального / рыночного фона: {len(report_view.shown_buckets['market_background'])}"
        )
    lines.extend(
        [
            f"- Скрыто служебных/registry/results страниц: {report_view.hidden_service_count}",
            f"- Скрыто глобального / рыночного фона: {report_view.hidden_market_count}",
            f"- Скрыто из-за лимита background: {report_view.hidden_background_overflow_count}",
            f"- Скрыто из-за max_items: {report_view.hidden_due_to_max_items_count}",
        ]
    )
    if page_type_counts:
        lines.append("- Page types:")
        for page_type, count in sorted(page_type_counts.items()):
            lines.append(f"  - {page_type}: {count}")
    if unique_errors:
        lines.append("- Источники с ошибками:")
        for source_name, error in unique_errors.items():
            lines.append(f"  - {source_name}: {error}")
    else:
        lines.append("- Источники с ошибками: нет")
    lines.append("")
    return lines


def _format_header_summary(
    documents: list[RawDocument],
    *,
    report_view: ReportView,
    display_sections: dict[str, list[RawDocument]],
    report_date: str,
    period_days: int | None,
    period_label: str | None,
    generated_at: datetime,
    period_context_lines: list[str],
) -> list[str]:
    rendered_documents = _flatten_display_sections(display_sections)
    requires_attention_count = len(display_sections.get("requires_attention", []))
    watchlist_count = sum(
        len(display_sections.get(section, []))
        for section in DISPLAY_SECTION_ORDER
        if section != "requires_attention"
    )
    period_spec: PeriodSpec | None = (
        build_rolling_period(period_days) if period_days is not None else None
    )
    period_text = period_label or (
        format_period_label(period_spec)
        if period_spec is not None
        else f"дата отчета, {datetime.strptime(report_date, '%Y-%m-%d').strftime('%d.%m.%Y')}"
    )
    reaction_text = _build_reaction_summary(report_view)
    source_heat_line = _build_source_heat_line(rendered_documents)
    lines = [
        "## Сводка",
        f"- Подготовлено: {generated_at.strftime('%Y-%m-%d %H:%M')}",
        f"- Период: {period_text}",
        f"- Проанализировано: {len(documents)}",
        f"- Включено в сводку: {len(rendered_documents)}",
        f"- Требует реакции: {requires_attention_count}",
        f"- На наблюдении: {watchlist_count}",
        f"- Главный акцент: {reaction_text}",
    ]
    if source_heat_line:
        lines.append(f"- По источникам: {source_heat_line}")
    lines.extend([f"- {line}" for line in period_context_lines])
    lines.append("")
    return lines


def _build_source_heat_line(
    documents: list[RawDocument], *, max_sources: int = 4
) -> str:
    counts: Counter[str] = Counter()
    for document in documents:
        label = _source_heat_label(document)
        if label:
            counts[label] += 1
    if not counts:
        return ""
    return "; ".join(
        f"{name}: {count}" for name, count in counts.most_common(max_sources)
    )


def _source_heat_label(document: RawDocument) -> str:
    parsed = urlsplit((document.url or "").strip())
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host:
        return host
    return (document.source_name or "").strip()


def _format_human_outro(display_sections: dict[str, list[RawDocument]]) -> list[str]:
    total_requires_attention = len(display_sections.get("requires_attention", []))
    total_other = sum(
        len(display_sections.get(section, []))
        for section in (
            "measures_and_selections",
            "regional_npa",
            "strategy_signals",
            "news_signals",
        )
    )
    if total_requires_attention:
        result = "Есть приоритетные вопросы для GR-реакции в ближайшее время."
    elif total_other:
        result = "Критичных изменений не выявлено, продолжаем плановое наблюдение."
    else:
        result = "Новых значимых сигналов за период не обнаружено."
    return ["## Итог", result, ""]


def _build_reaction_summary(report_view: ReportView) -> str:
    requires_attention_documents = report_view.shown_buckets.get(
        "requires_attention", []
    )
    if not requires_attention_documents:
        return "Срочных поводов для GR-реакции не выявлено."
    meaningful_documents = [
        document
        for document in requires_attention_documents
        if is_meaningful_executive_highlight(document)
    ]
    if not meaningful_documents:
        return "Срочных поводов для GR-реакции не выявлено."
    titles: list[str] = []
    seen_titles: set[str] = set()
    for document in meaningful_documents:
        title = _reaction_title(document)
        if title in seen_titles:
            continue
        seen_titles.add(title)
        titles.append(title)
        if len(titles) >= 3:
            break
    extra_count = max(
        len(seen_titles_from_documents(meaningful_documents)) - len(titles), 0
    )
    if extra_count > 0:
        return f"{'; '.join(titles)}; и еще {extra_count}."
    return "; ".join(titles)


def seen_titles_from_documents(documents: list[RawDocument]) -> set[str]:
    return {_reaction_title(document) for document in documents}


def _report_title(
    document: RawDocument, *, max_chars: int = REPORT_TITLE_MAX_CHARS
) -> str:
    title = user_facing_title(document)
    normalized = re.sub(r"\s+", " ", title).strip()
    if len(normalized) <= max_chars:
        return normalized
    return _word_boundary_clip(normalized, max_chars)


def _reaction_title(document: RawDocument) -> str:
    title = _report_title(document, max_chars=REPORT_REACTION_TITLE_MAX_CHARS)
    title_lower = title.lower()
    if document.business_signal and any(
        title_lower.startswith(prefix) for prefix in _BUREAUCRATIC_TITLE_PREFIXES
    ):
        return _word_boundary_clip(
            document.business_signal, REPORT_REACTION_TITLE_MAX_CHARS
        )
    return title


def save_markdown_report(markdown: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
