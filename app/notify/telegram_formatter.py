from __future__ import annotations

import re
from collections.abc import Sequence

from app.models import RawDocument
from app.reports.markdown_report import classify_display_section, classify_document_bucket

HOURLY_REQUIRES_ATTENTION_LIMIT = 10
TELEGRAM_WATCHLIST_BUCKETS = {
    "support_reference",
    "target_watchlist",
    "industry_background",
}
DAILY_SECTION_ORDER = (
    "requires_attention",
    "active_support_measures",
    "support_documents",
    "regional_npa",
    "strategy_signals",
    "news_signals",
    "background_reference",
)
DAILY_SECTION_TITLES = {
    "requires_attention": "🚨 Требует внимания",
    "active_support_measures": "📢 Объявленные меры / отборы",
    "support_documents": "📚 Документы по мерам поддержки",
    "regional_npa": "⚖️ Региональные НПА",
    "strategy_signals": "🏛 Стратегические сигналы",
    "news_signals": "📰 Новостные предвестники",
    "background_reference": "📎 Фон / справочно",
}
DAILY_SECTION_LIMITS = {
    "requires_attention": 5,
    "active_support_measures": 5,
    "support_documents": 3,
    "regional_npa": 3,
    "strategy_signals": 3,
    "news_signals": 5,
    "background_reference": 3,
}
SUMMARY_MAX_CHARS = 140
DETAIL_MAX_CHARS = 180


def build_digest_message(
    documents: Sequence[RawDocument],
    *,
    report_path: str | None = None,
) -> str:
    if not documents:
        return "Новых документов для уведомления не найдено."

    requires_attention, watchlist = _partition_digest_documents(documents)
    if not requires_attention and not watchlist:
        return "Новых документов для уведомления не найдено."

    if _looks_like_hourly_alert(requires_attention, watchlist):
        return _build_hourly_alert(requires_attention)
    return _build_daily_digest(requires_attention, watchlist, report_path=report_path)


def _partition_digest_documents(
    documents: Sequence[RawDocument],
) -> tuple[list[RawDocument], list[RawDocument]]:
    requires_attention: list[RawDocument] = []
    watchlist: list[RawDocument] = []
    for document in documents:
        if document.support_status == "inactive":
            continue
        if document.application_status == "closed":
            continue
        if document.page_type == "reference_page":
            continue
        bucket = classify_document_bucket(document)
        if document.action_level == "requires_attention" and bucket == "requires_attention":
            requires_attention.append(document)
            continue
        if bucket in TELEGRAM_WATCHLIST_BUCKETS:
            watchlist.append(document)
    return requires_attention, watchlist


def _looks_like_hourly_alert(
    requires_attention: Sequence[RawDocument],
    watchlist: Sequence[RawDocument],
) -> bool:
    return bool(requires_attention) and not watchlist and all(
        not document.notified for document in requires_attention
    )


def _build_hourly_alert(documents: Sequence[RawDocument]) -> str:
    visible = list(documents[:HOURLY_REQUIRES_ATTENTION_LIMIT])
    lines = [f"🚨 Новые документы, требующие внимания: {len(documents)}"]
    for document in visible:
        lines.extend(_format_digest_item(document, include_summary=True))
    hidden_count = len(documents) - len(visible)
    if hidden_count > 0:
        lines.append(f"... и еще {hidden_count}.")
    return "\n".join(lines)


def _build_daily_digest(
    requires_attention: Sequence[RawDocument],
    watchlist: Sequence[RawDocument],
    *,
    report_path: str | None,
) -> str:
    sections = {section: [] for section in DAILY_SECTION_ORDER}
    for document in requires_attention:
        sections["requires_attention"].append(document)
    for document in watchlist:
        section = classify_display_section(document)
        if section in sections:
            sections[section].append(document)

    lines = [
        "🧾 Ежедневная GR-сводка",
        _build_daily_summary_line(sections),
    ]
    if report_path:
        lines.append(f"Полный report: {report_path}")

    for section in DAILY_SECTION_ORDER:
        section_documents = sections[section]
        if not section_documents:
            continue
        lines.append("")
        lines.append(f"{DAILY_SECTION_TITLES[section]} ({len(section_documents)})")
        visible = section_documents[: DAILY_SECTION_LIMITS[section]]
        include_summary = section == "requires_attention"
        for document in visible:
            lines.extend(_format_digest_item(document, include_summary=include_summary))
        hidden_count = len(section_documents) - len(visible)
        if hidden_count > 0:
            lines.append(f"... и еще {hidden_count}.")

    return "\n".join(lines)


def _build_daily_summary_line(sections: dict[str, list[RawDocument]]) -> str:
    summary_parts: list[str] = []
    label_map = {
        "requires_attention": "urgent",
        "active_support_measures": "меры",
        "regional_npa": "НПА",
        "strategy_signals": "стратегия",
        "news_signals": "новости",
    }
    for section in (
        "requires_attention",
        "active_support_measures",
        "regional_npa",
        "strategy_signals",
        "news_signals",
    ):
        count = len(sections.get(section, []))
        if count:
            summary_parts.append(f"{label_map[section]}: {count}")
    return " | ".join(summary_parts) if summary_parts else "Новых visible-документов не найдено."


def _format_digest_item(
    document: RawDocument,
    *,
    include_summary: bool,
) -> list[str]:
    lines = [f"- {document.title}"]
    details: list[str] = []
    if document.support_status and document.support_status != "unknown":
        details.append(f"статус: {document.support_status}")
    if document.application_status and document.application_status != "unknown":
        details.append(f"режим: {document.application_status}")
    if details:
        lines.append(f"  {'; '.join(details)}")
    if document.deadline_text and not _is_inactive_or_closed(document):
        lines.append(f"  Срок: {_truncate_text(document.deadline_text, DETAIL_MAX_CHARS)}")
    if document.business_signal:
        lines.append(
            f"  Сигнал: {_truncate_text(document.business_signal, DETAIL_MAX_CHARS)}"
        )
    if include_summary and document.summary:
        lines.append(f"  Кратко: {_truncate_text(document.summary, SUMMARY_MAX_CHARS)}")
    lines.append(f"  {document.url}")
    return lines


def _is_inactive_or_closed(document: RawDocument) -> bool:
    return (
        document.support_status == "inactive"
        or document.application_status == "closed"
    )


def _truncate_text(text: str, max_chars: int) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3].rstrip(' ,.;:-')}..."
