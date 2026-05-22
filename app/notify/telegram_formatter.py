from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from urllib.parse import urlsplit

from app.llm.enrichment import get_display_enrichment
from app.models import RawDocument
from app.operational_health import OperationalNotice, format_operational_notices_telegram
from app.storage import list_document_enrichments
from app.text_utils import safe_truncate_text
from app.user_facing import (
    build_executive_action,
    build_executive_reason,
    disambiguate_visible_titles,
    select_executive_summary,
    user_facing_title,
)
from app.visibility import (
    classify_display_section,
    deduplicate_user_facing_documents,
    should_show_document,
)

HOURLY_REQUIRES_ATTENTION_LIMIT = 10
DAILY_SECTION_ORDER = (
    "requires_attention",
    "measures_and_selections",
    "regional_npa",
    "strategy_signals",
    "news_signals",
)
DAILY_SECTION_TITLES = {
    "requires_attention": "🚨 Требует внимания",
    "measures_and_selections": "📢 Меры и отборы",
    "regional_npa": "⚖️ Региональные НПА",
    "strategy_signals": "🏛 Стратегические сигналы",
    "news_signals": "📰 Новостные предвестники",
}
DAILY_SECTION_LIMITS = {
    "requires_attention": 5,
    "measures_and_selections": 5,
    "regional_npa": 3,
    "strategy_signals": 3,
    "news_signals": 5,
}
SUMMARY_MAX_CHARS = 110
DETAIL_MAX_CHARS = 110


def build_digest_message(
    documents: Sequence[RawDocument],
    *,
    report_path: str | None = None,
    operational_notices: Sequence[OperationalNotice] | None = None,
    force_daily: bool = False,
) -> str:
    if not documents:
        return "Новых документов для уведомления не найдено."

    requires_attention, watchlist = _partition_digest_documents(documents)
    if not requires_attention and not watchlist:
        return "Новых документов для уведомления не найдено."

    if not force_daily and _looks_like_hourly_alert(requires_attention, watchlist):
        enrichment_by_url = list_document_enrichments([document.url for document in requires_attention])
        return _build_hourly_alert(requires_attention, enrichment_by_url=enrichment_by_url)
    enrichment_by_url = list_document_enrichments(
        [document.url for document in [*requires_attention, *watchlist]],
    )
    return _build_daily_digest(
        requires_attention,
        watchlist,
        report_path=report_path,
        operational_notices=list(operational_notices or []),
        enrichment_by_url=enrichment_by_url,
    )


def _partition_digest_documents(
    documents: Sequence[RawDocument],
) -> tuple[list[RawDocument], list[RawDocument]]:
    requires_attention: list[RawDocument] = []
    watchlist: list[RawDocument] = []
    for document in deduplicate_user_facing_documents(documents):
        if not should_show_document(document, surface="telegram_digest", relevant_only=False):
            continue
        if classify_display_section(document) == "requires_attention":
            requires_attention.append(document)
            continue
        watchlist.append(document)
    return requires_attention, watchlist


def _looks_like_hourly_alert(
    requires_attention: Sequence[RawDocument],
    watchlist: Sequence[RawDocument],
) -> bool:
    return bool(requires_attention) and not watchlist and all(
        not document.notified for document in requires_attention
    )


def _build_hourly_alert(
    documents: Sequence[RawDocument],
    *,
    enrichment_by_url: dict[str, dict[str, object]],
) -> str:
    visible = list(documents[:HOURLY_REQUIRES_ATTENTION_LIMIT])
    title_by_id = disambiguate_visible_titles(visible, max_chars=100)
    lines = [f"🚨 Новые документы, требующие внимания: {len(documents)}"]
    for document in visible:
        lines.extend(
            _format_digest_item(
                document,
                include_summary=True,
                enrichment=get_display_enrichment(enrichment_by_url.get(document.url)),
                title_override=title_by_id.get(document.id),
            )
        )
    hidden_count = len(documents) - len(visible)
    if hidden_count > 0:
        lines.append(f"... и еще {hidden_count}.")
    return "\n".join(lines)


def _build_daily_digest(
    requires_attention: Sequence[RawDocument],
    watchlist: Sequence[RawDocument],
    *,
    report_path: str | None,
    operational_notices: Sequence[OperationalNotice],
    enrichment_by_url: dict[str, dict[str, object]],
) -> str:
    sections = {section: [] for section in DAILY_SECTION_ORDER}
    for document in requires_attention:
        sections["requires_attention"].append(document)
    for document in watchlist:
        section = classify_display_section(document)
        if section in sections:
            sections[section].append(document)

    all_visible = [
        doc
        for sec in DAILY_SECTION_ORDER
        for doc in sections[sec][: DAILY_SECTION_LIMITS[sec]]
    ]
    title_by_id = disambiguate_visible_titles(all_visible, max_chars=100)

    lines = [
        "🧾 Ежедневная GR-сводка",
        _build_daily_summary_line(sections),
    ]
    if report_path:
        lines.append("Полная версия отчета — во вложении.")
    if operational_notices:
        lines.append("")
        lines.extend(format_operational_notices_telegram(operational_notices))

    for section in DAILY_SECTION_ORDER:
        section_documents = sections[section]
        if not section_documents:
            continue
        lines.append("")
        lines.append(f"{DAILY_SECTION_TITLES[section]} ({len(section_documents)})")
        visible = section_documents[: DAILY_SECTION_LIMITS[section]]
        include_summary = section == "requires_attention"
        for document in visible:
            lines.extend(
                _format_digest_item(
                    document,
                    include_summary=include_summary,
                    enrichment=get_display_enrichment(enrichment_by_url.get(document.url)),
                    title_override=title_by_id.get(document.id),
                )
            )
        hidden_count = len(section_documents) - len(visible)
        if hidden_count > 0:
            lines.append(f"... и еще {hidden_count}.")

    return "\n".join(lines)


def _build_daily_summary_line(sections: dict[str, list[RawDocument]]) -> str:
    summary_parts: list[str] = []
    label_map = {
        "requires_attention": "Требует реакции",
        "measures_and_selections": "меры",
        "regional_npa": "НПА",
        "strategy_signals": "стратегия",
        "news_signals": "новости",
    }
    for section in (
        "requires_attention",
        "measures_and_selections",
        "regional_npa",
        "strategy_signals",
        "news_signals",
    ):
        count = len(sections.get(section, []))
        if count:
            summary_parts.append(f"{label_map[section]}: {count}")
    source_heat = _build_source_heat_line(
        [
            document
            for section in DAILY_SECTION_ORDER
            for document in sections.get(section, [])[: DAILY_SECTION_LIMITS[section]]
        ],
        max_sources=3,
    )
    if source_heat:
        summary_parts.append(f"источники: {source_heat}")
    return " | ".join(summary_parts) if summary_parts else "Новых документов не найдено."


def _build_source_heat_line(documents: list[RawDocument], *, max_sources: int) -> str:
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


def _format_digest_item(
    document: RawDocument,
    *,
    include_summary: bool,
    enrichment: dict[str, str] | None = None,
    title_override: str | None = None,
) -> list[str]:
    lines = [f"- {title_override or user_facing_title(document, max_chars=100)}"]
    details: list[str] = []
    if document.support_status and document.support_status != "unknown":
        status_label = "активна" if document.support_status == "active" else document.support_status
        details.append(f"статус: {status_label}")
    if document.application_status and document.application_status != "unknown":
        app_status = (
            "регулярная мера"
            if document.application_status == "regular"
            else document.application_status
        )
        details.append(f"режим: {app_status}")
    if details:
        lines.append(f"  {'; '.join(details)}")
    deadline_text = (
        enrichment.get("deadline_hint")
        if enrichment and enrichment.get("deadline_hint")
        else document.deadline_text
    )
    if deadline_text and not _is_inactive_or_closed(document):
        lines.append(f"  {_format_deadline_line(deadline_text)}")
    signal_text = (
        build_executive_reason(
            document,
            enrichment_text=enrichment.get("business_impact") if enrichment else None,
            fallback_text=document.business_signal or document.impact or document.summary,
        )
    )
    if signal_text:
        lines.append(f"  Сигнал: {_truncate_text(signal_text, DETAIL_MAX_CHARS)}")
    hint = _build_digest_action_hint(document, enrichment=enrichment)
    if hint:
        lines.append(f"  Что проверить: {hint}")
    summary_text = select_executive_summary(
        document,
        enrichment_text=enrichment.get("executive_summary") if enrichment else None,
        fallback_text=document.summary,
        max_chars=SUMMARY_MAX_CHARS,
    )
    if include_summary and summary_text:
        lines.append(f"  Кратко: {summary_text}")
    lines.append(f"  {document.url}")
    return lines


def _is_inactive_or_closed(document: RawDocument) -> bool:
    return (
        document.support_status == "inactive"
        or document.application_status == "closed"
    )


def _truncate_text(text: str, max_chars: int) -> str:
    return safe_truncate_text(text, max_chars)


def _build_digest_action_hint(
    document: RawDocument,
    *,
    enrichment: dict[str, str] | None = None,
) -> str:
    action_text = build_executive_action(
        document,
        enrichment_text=enrichment.get("recommended_action") if enrichment else None,
        section=classify_display_section(document),
    )
    if action_text:
        return _truncate_text(action_text, DETAIL_MAX_CHARS)
    if document.application_status == "open" and document.deadline_text:
        return _truncate_text(document.deadline_text, DETAIL_MAX_CHARS)
    return ""


def _format_deadline_line(text: str) -> str:
    normalized = _truncate_text(text, DETAIL_MAX_CHARS)
    lowered = normalized.lower()
    if lowered.startswith("срок:") or lowered.startswith("срок истёк:") or lowered.startswith("конец обсуждения:"):
        return normalized
    return f"Срок: {normalized}"
