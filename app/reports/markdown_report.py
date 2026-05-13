from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from collections import Counter
import re
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

from app import config
from app.llm.enrichment import get_display_enrichment
from app.models import DigestItem, RawDocument, SourceErrorRecord
from app.operational_health import OperationalNotice, format_operational_notices_markdown
from app.periods import PeriodSpec, build_rolling_period, format_period_label
from app.storage import list_document_enrichments
from app.user_facing import (
    build_executive_action,
    build_executive_reason,
    disambiguate_visible_titles,
    is_meaningful_executive_highlight,
    select_executive_summary,
    user_facing_action_level,
    user_facing_title,
)
from app.visibility import (
    classify_display_section as visibility_display_section,
    deduplicate_user_facing_documents,
    should_show_document,
    visibility_bucket,
)
SHORT_SUMMARY_MAX_CHARS = 140
REPORT_TITLE_MAX_CHARS = 90
REPORT_REACTION_TITLE_MAX_CHARS = 70
MEASURES_SECTION_DISPLAY_MAX = 6
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
    enrichment_by_url = list_document_enrichments(
        [document.url for document in report_view.flatten()],
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
            report_date=report_date,
            period_days=period_days,
            period_label=period_label,
            generated_at=generated_at_value,
            period_context_lines=list(period_context_lines or []),
        )
    )
    lines.extend(format_operational_notices_markdown(notices))
    title_by_id = disambiguate_visible_titles(
        report_view.flatten(), max_chars=REPORT_TITLE_MAX_CHARS
    )
    for section in DISPLAY_SECTION_ORDER:
        lines.append(DISPLAY_SECTION_TITLES[section])
        documents_for_bucket = display_sections.get(section, [])
        if documents_for_bucket:
            for document in documents_for_bucket:
                digest_item = _to_digest_item(document, title=title_by_id.get(document.id))
                lines.extend(
                    _format_human_item(
                        digest_item,
                        require_action=section == "requires_attention",
                        enrichment=get_display_enrichment(enrichment_by_url.get(document.url)),
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
    if include_market_background and len(raw_buckets["market_background"]) > MARKET_BACKGROUND_LIMIT:
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
            _document_text_quality_score(document),
            _document_fact_score(document),
            len(document.summary or ""),
            int(_has_informative_title(document.title)),
            _published_timestamp(document),
            document.id or 0,
        ),
    )


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
    return _normalize_title_key(document.title)


def _normalize_title_key(title: str | None) -> str:
    normalized = re.sub(r"[^0-9a-zа-яё]+", " ", (title or "").lower(), flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _find_similar_title_group(title_key: str, title_group_keys: list[str]) -> int | None:
    if len(title_key) < 24:
        return None
    for index, existing_key in enumerate(title_group_keys):
        if len(existing_key) < 24:
            continue
        if title_key == existing_key:
            return index
        if _numeric_tokens(title_key) != _numeric_tokens(existing_key):
            continue
        if SequenceMatcher(None, title_key, existing_key).ratio() >= TITLE_SIMILARITY_THRESHOLD:
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


def _build_display_sections(documents: Iterable[RawDocument]) -> dict[str, list[RawDocument]]:
    sections: dict[str, list[RawDocument]] = {section: [] for section in DISPLAY_SECTION_ORDER}
    for document in documents:
        section = visibility_display_section(document)
        if section in sections:
            sections[section].append(document)
    measures = sections.get("measures_and_selections", [])
    if len(measures) > MEASURES_SECTION_DISPLAY_MAX:
        measures.sort(
            key=lambda d: (
                d.application_status == "open",
                bool(d.deadline_text),
                bool(d.business_signal),
                d.is_active or False,
            ),
            reverse=True,
        )
        sections["measures_and_selections"] = measures[:MEASURES_SECTION_DISPLAY_MAX]
    return sections


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
    discussion_match = _DISCUSSION_DEADLINE_RE.search(normalized)
    if discussion_match:
        label = discussion_match.group(1).capitalize()
        return f"{label}: {discussion_match.group(2)}"
    normalized = _DEADLINE_GARBAGE_LABEL_RE.split(normalized, maxsplit=1)[0].strip(" ;,-")
    if not normalized:
        return None
    discussion_match = _DISCUSSION_DEADLINE_RE.search(normalized)
    if discussion_match:
        label = discussion_match.group(1).capitalize()
        return f"{label}: {discussion_match.group(2)}"
    return _shorten_summary(normalized)


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
        lines.append(f"- Срок: {deadline_text}")
    action_text = _build_human_action_text(item, enrichment=enrichment)
    if require_action or action_text:
        lines.append(f"- Что проверить: {action_text or 'Оценить влияние и определить следующий шаг.'}")
    lines.extend(
        [
            f"- Источник: {item.url}",
            "",
        ]
    )
    return lines


def _build_human_importance_text(
    item: DigestItem,
    *,
    enrichment: dict[str, str] | None = None,
) -> str:
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
    action_text = build_executive_action(
        item,
        enrichment_text=enrichment.get("recommended_action") if enrichment else None,
        max_chars=SHORT_SUMMARY_MAX_CHARS,
    )
    if action_text:
        return _shorten_summary(action_text)
    if item.application_status == "open" and item.deadline_text:
        return f"Проверить сроки и подачу: {_shorten_summary(item.deadline_text)}"
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
        1 for document in documents if user_facing_action_level(document) == "requires_attention"
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
    report_date: str,
    period_days: int | None,
    period_label: str | None,
    generated_at: datetime,
    period_context_lines: list[str],
) -> list[str]:
    requires_attention_count = sum(
        1 for document in documents if user_facing_action_level(document) == "requires_attention"
    )
    watchlist_count = sum(
        1 for document in documents if user_facing_action_level(document) == "watchlist"
    )
    period_spec: PeriodSpec | None = build_rolling_period(period_days) if period_days is not None else None
    period_text = period_label or (
        format_period_label(period_spec)
        if period_spec is not None
        else f"дата отчета, {datetime.strptime(report_date, '%Y-%m-%d').strftime('%d.%m.%Y')}"
    )
    reaction_text = _build_reaction_summary(report_view)
    source_heat_line = _build_source_heat_line(report_view.flatten())
    lines = [
        "## Сводка",
        f"- Подготовлено: {generated_at.strftime('%Y-%m-%d %H:%M')}",
        f"- Период: {period_text}",
        f"- Проанализировано: {len(documents)}",
        f"- Включено в сводку: {report_view.total_visible}",
        f"- Требует реакции: {requires_attention_count}",
        f"- На наблюдении: {watchlist_count}",
        f"- Главный акцент: {reaction_text}",
    ]
    if source_heat_line:
        lines.append(f"- По источникам: {source_heat_line}")
    lines.extend([f"- {line}" for line in period_context_lines])
    lines.append("")
    return lines


def _build_source_heat_line(documents: list[RawDocument], *, max_sources: int = 4) -> str:
    counts: Counter[str] = Counter()
    for document in documents:
        label = _source_heat_label(document)
        if label:
            counts[label] += 1
    if not counts:
        return ""
    return "; ".join(f"{name}: {count}" for name, count in counts.most_common(max_sources))


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
        for section in ("measures_and_selections", "regional_npa", "strategy_signals", "news_signals")
    )
    if total_requires_attention:
        result = "Есть приоритетные вопросы для GR-реакции в ближайшее время."
    elif total_other:
        result = "Критичных изменений не выявлено, продолжаем плановое наблюдение."
    else:
        result = "Новых значимых сигналов за период не обнаружено."
    return ["## Итог", result, ""]


def _build_reaction_summary(report_view: ReportView) -> str:
    requires_attention_documents = report_view.shown_buckets.get("requires_attention", [])
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
    extra_count = max(len(seen_titles_from_documents(meaningful_documents)) - len(titles), 0)
    if extra_count > 0:
        return f"{'; '.join(titles)}; и еще {extra_count}."
    return "; ".join(titles)


def seen_titles_from_documents(documents: list[RawDocument]) -> set[str]:
    return {_reaction_title(document) for document in documents}


def _report_title(document: RawDocument, *, max_chars: int = REPORT_TITLE_MAX_CHARS) -> str:
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
        return _word_boundary_clip(document.business_signal, REPORT_REACTION_TITLE_MAX_CHARS)
    return title


def save_markdown_report(markdown: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
