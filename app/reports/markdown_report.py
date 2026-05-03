from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Iterable

from app.config import get_source_role
from app.models import DigestItem, RawDocument, SourceErrorRecord

VISIBLE_WATCHLIST_PAGE_TYPES = {
    "news_background",
    "measure_card",
    "new_rule",
    "selection_announcement",
    "deadline_update",
}
TARGET_REGION_MARKERS: dict[str, tuple[str, ...]] = {
    "rostov": ("ростов", "донланд", "ростовской области"),
    "krasnodar": ("краснодар", "кубани", "кубан", "краснодарского края"),
    "stavropol": ("ставрополь", "ставропольского края"),
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
SHORT_SUMMARY_MAX_CHARS = 180
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
    "active_support_measures",
    "support_documents",
    "regional_npa",
    "strategy_signals",
    "news_signals",
    "background_reference",
)
DISPLAY_SECTION_TITLES = {
    "requires_attention": "## Требует внимания GR",
    "active_support_measures": "## Объявленные меры / отборы",
    "support_documents": "## Документы по мерам поддержки",
    "regional_npa": "## Региональные НПА",
    "strategy_signals": "## Стратегические федеральные сигналы",
    "news_signals": "## Новостные предвестники изменений",
    "background_reference": "## Фон / справочно",
}
DISPLAY_EMPTY_MESSAGES = {
    "requires_attention": "Документов, требующих внимания GR, за выбранный период не найдено.",
    "active_support_measures": "Подходящих объявленных мер поддержки и отборов не найдено.",
    "support_documents": "Подходящих документов по мерам поддержки не найдено.",
    "regional_npa": "Подходящих региональных НПА для показа не найдено.",
    "strategy_signals": "Стратегических федеральных сигналов для показа не найдено.",
    "news_signals": "Новостных предвестников изменений для показа не найдено.",
    "background_reference": "Фоновых и справочных материалов для показа не найдено.",
}


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


def _to_digest_item(document: RawDocument) -> DigestItem:
    return DigestItem(
        title=document.title,
        region=document.region,
        source_name=document.source_name,
        url=document.url,
        importance=document.importance,
        action_level=document.action_level,
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
    generated_at: datetime | None = None,
    report_title: str | None = None,
    intro_note: str | None = None,
    relevant_only: bool = True,
    max_items: int | None = None,
    source_errors: Iterable[SourceErrorRecord] | None = None,
    action_levels: list[str] | None = None,
    include_background: bool = False,
    include_section_pages: bool = False,
    include_registries: bool = False,
    include_market_background: bool = False,
    include_full_background: bool = False,
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
    error_records = list(source_errors or [])

    generated_at_value = generated_at or datetime.now()
    lines: list[str] = [report_title or f"# GR-мониторинг за {report_date}", ""]
    if intro_note:
        lines.extend([intro_note, ""])
    lines.extend(
        _format_header_summary(
            document_list,
            report_view=report_view,
            report_date=report_date,
            period_days=period_days,
            generated_at=generated_at_value,
        )
    )
    for section in DISPLAY_SECTION_ORDER:
        lines.append(DISPLAY_SECTION_TITLES[section])
        documents_for_bucket = display_sections.get(section, [])
        if documents_for_bucket:
            for document in documents_for_bucket:
                digest_item = _to_digest_item(document)
                if section == "requires_attention":
                    lines.extend(_format_detailed_item(digest_item))
                else:
                    lines.extend(
                        _format_compact_item(
                            digest_item,
                            include_business_facts=section in {"active_support_measures", "support_documents", "regional_npa"},
                        )
                    )
        else:
            lines.append(DISPLAY_EMPTY_MESSAGES[section])
            lines.append("")

    lines.extend(
        _format_stats(
            document_list,
            error_records,
            report_view=report_view,
            display_sections=display_sections,
            include_market_background=include_market_background,
        )
    )
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
    filtered_action_levels = list(action_levels or ["requires_attention", "watchlist"])
    if include_background and "background" not in filtered_action_levels:
        filtered_action_levels.append("background")

    report_candidates = document_list
    if relevant_only:
        report_candidates = [
            document
            for document in report_candidates
            if document.action_level != "irrelevant"
        ]
    report_candidates = [
        document
        for document in report_candidates
        if document.action_level in filtered_action_levels
    ]

    visible_candidates: list[RawDocument] = []
    hidden_service_count = 0
    for document in report_candidates:
        if document.action_level == "requires_attention":
            visible_candidates.append(document)
            continue
        if _should_show_watchlist_document(
            document,
            include_section_pages=include_section_pages,
            include_registries=include_registries,
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


def _build_display_sections(documents: Iterable[RawDocument]) -> dict[str, list[RawDocument]]:
    sections = {section: [] for section in DISPLAY_SECTION_ORDER}
    for document in documents:
        sections[classify_display_section(document)].append(document)
    return sections


def classify_display_section(document: RawDocument) -> str:
    if document.action_level == "requires_attention":
        return "requires_attention"
    source_role = get_source_role(document.source_name)
    if source_role == "active_support_measures":
        return "active_support_measures"
    if source_role == "support_documents":
        return "support_documents"
    if source_role == "regional_npa":
        return "regional_npa"
    if source_role == "strategy":
        return "strategy_signals"
    if source_role == "news_signals":
        return "news_signals"
    return "background_reference"


def classify_document_bucket(document: RawDocument) -> str:
    geo_scope = _detect_geo_scope(document)
    if document.action_level == "requires_attention":
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
        return "support_reference" if geo_scope in {"target_region", "federal_rf"} else "non_target_background"

    if geo_scope == "global_market":
        return "market_background"
    if geo_scope == "target_region":
        return "target_watchlist"
    if geo_scope == "non_target_rf":
        return "non_target_background"
    return "industry_background"


def _should_show_watchlist_document(
    document: RawDocument,
    *,
    include_section_pages: bool,
    include_registries: bool,
) -> bool:
    page_type = document.page_type or "unknown"
    if include_section_pages:
        return page_type not in {"navigation", "unknown"}
    if page_type in {"registry", "results_protocol"}:
        return include_registries
    if page_type == "reference_page":
        return _is_support_reference_document(document)
    return page_type in VISIBLE_WATCHLIST_PAGE_TYPES


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

    if document.region in {"rostov", "krasnodar", "stavropol"}:
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


def _shorten_summary(text: str | None) -> str:
    if not text:
        return "Нет summary"
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= SHORT_SUMMARY_MAX_CHARS:
        return normalized
    return f"{normalized[: SHORT_SUMMARY_MAX_CHARS - 3].rstrip(' ,.;:-')}..."


def _format_detailed_item(item: DigestItem) -> list[str]:
    lines = [
        f"### {item.title}",
        f"- Регион: {item.region}",
        f"- Источник: {item.source_name}",
        f"- Ссылка: {item.url}",
        f"- Action level: {item.action_level or 'n/a'}",
        f"- Тип страницы: {item.page_type or 'unknown'}",
        f"- Важность: {item.importance or 'n/a'}",
        f"- Кратко: {_shorten_summary(item.summary)}",
    ]
    lines.extend(_format_business_fact_lines(item))
    lines.extend(
        [
            f"- Почему важно / влияние: {item.impact or 'Не определено'}",
            f"- Причина релевантности: {item.relevance_reason or 'Не определена'}",
            "",
        ]
    )
    return lines


def _format_compact_item(
    item: DigestItem,
    *,
    include_business_facts: bool = False,
) -> list[str]:
    lines = [
        f"- {item.title}",
        f"  Источник: {item.source_name} | Регион: {item.region} | Action level: {item.action_level or 'n/a'} | Тип страницы: {item.page_type or 'unknown'} | Ссылка: {item.url}",
        f"  Кратко: {_shorten_summary(item.summary)}",
    ]
    if include_business_facts:
        for fact_line in _format_business_fact_lines(item, compact=True):
            lines.append(fact_line)
    lines.append("")
    return lines


def _format_business_fact_lines(item: DigestItem, *, compact: bool = False) -> list[str]:
    prefix = "  " if compact else "- "
    lines: list[str] = []
    is_inactive_or_closed = (
        item.support_status == "inactive"
        or item.application_status == "closed"
    )
    if item.support_status and item.support_status != "unknown":
        lines.append(f"{prefix}Статус меры: {item.support_status}")
    if item.application_status and item.application_status != "unknown":
        lines.append(f"{prefix}Режим: {item.application_status}")
    if item.npa_number:
        lines.append(f"{prefix}НПА: {item.npa_number}")
    if item.deadline_text and not is_inactive_or_closed:
        lines.append(f"{prefix}Дедлайн/срок подачи: {_shorten_summary(item.deadline_text)}")
    if item.terms_text:
        lines.append(f"{prefix}Условия/срок действия: {_shorten_summary(item.terms_text)}")
    if item.business_signal:
        lines.append(f"{prefix}Сигнал: {item.business_signal}")
    if item.risk_notes:
        lines.append(f"{prefix}Примечание: {item.risk_notes}")
    return lines


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
        1 for document in documents if document.action_level == "requires_attention"
    )
    watchlist_count = sum(
        1 for document in documents if document.action_level == "watchlist"
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
    generated_at: datetime,
) -> list[str]:
    requires_attention_count = sum(
        1 for document in documents if document.action_level == "requires_attention"
    )
    watchlist_count = sum(
        1 for document in documents if document.action_level == "watchlist"
    )
    hidden_background_irrelevant_count = sum(
        1
        for document in documents
        if document.action_level in {"background", "irrelevant"}
    )
    published_with_date_count = sum(
        1 for document in documents if document.published_at is not None
    )
    period_text = (
        f"Последние {period_days} дн."
        if period_days is not None
        else f"Дата отчета: {report_date}"
    )
    reaction_text = _build_reaction_summary(report_view)
    return [
        "## Сводка",
        f"- Дата генерации: {generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Период: {period_text}",
        f"- Total documents: {len(documents)}",
        f"- Published date coverage: {published_with_date_count}/{len(documents)}",
        f"- Visible documents: {report_view.total_visible}",
        f"- Requires attention count: {requires_attention_count}",
        f"- Watchlist count: {watchlist_count}",
        f"- Support/reference count: {report_view.total_bucket_counts['support_reference']}",
        f"- Скрыто как background/irrelevant: {hidden_background_irrelevant_count}",
        f"- Что требует реакции сегодня: {reaction_text}",
        "",
    ]


def _build_reaction_summary(report_view: ReportView) -> str:
    requires_attention_documents = report_view.shown_buckets.get("requires_attention", [])
    if not requires_attention_documents:
        return "Срочных GR-сигналов не найдено."
    titles = [document.title for document in requires_attention_documents[:3]]
    if len(requires_attention_documents) > 3:
        extra_count = len(requires_attention_documents) - 3
        return f"{'; '.join(titles)}; и еще {extra_count}."
    return "; ".join(titles)


def save_markdown_report(markdown: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
