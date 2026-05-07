from __future__ import annotations

import re
from collections.abc import Sequence

from app.config import get_source_role
from app.models import RawDocument
from app.operational_health import OperationalNotice, format_operational_notices_telegram
from app.user_facing import user_facing_title
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
SUMMARY_MAX_CHARS = 140
DETAIL_MAX_CHARS = 180


def build_digest_message(
    documents: Sequence[RawDocument],
    *,
    report_path: str | None = None,
    operational_notices: Sequence[OperationalNotice] | None = None,
) -> str:
    if not documents:
        return "Новых документов для уведомления не найдено."

    requires_attention, watchlist = _partition_digest_documents(documents)
    if not requires_attention and not watchlist:
        return "Новых документов для уведомления не найдено."

    if _looks_like_hourly_alert(requires_attention, watchlist):
        return _build_hourly_alert(requires_attention)
    return _build_daily_digest(
        requires_attention,
        watchlist,
        report_path=report_path,
        operational_notices=list(operational_notices or []),
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
    operational_notices: Sequence[OperationalNotice],
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
            lines.extend(_format_digest_item(document, include_summary=include_summary))
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
    return " | ".join(summary_parts) if summary_parts else "Новых visible-документов не найдено."


def _format_digest_item(
    document: RawDocument,
    *,
    include_summary: bool,
) -> list[str]:
    lines = [f"- {user_facing_title(document)}"]
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
    if document.deadline_text and not _is_inactive_or_closed(document):
        lines.append(f"  Срок: {_truncate_text(document.deadline_text, DETAIL_MAX_CHARS)}")
    if document.business_signal:
        lines.append(
            f"  Сигнал: {_truncate_text(document.business_signal, DETAIL_MAX_CHARS)}"
        )
    hint = _build_digest_action_hint(document)
    if hint:
        lines.append(f"  Что проверить: {hint}")
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


def _build_digest_action_hint(document: RawDocument) -> str:
    if document.application_status == "open" and document.deadline_text:
        return _truncate_text(document.deadline_text, DETAIL_MAX_CHARS)
    if document.application_status == "open":
        return "Проверить условия участия и окно подачи."
    if get_source_role(document.source_name) == "regional_npa" and document.page_type == "new_rule":
        return "Проверить изменения порядка субсидирования, сроки вступления в силу и затронутые регионы/организации."
    section = classify_display_section(document)
    if section == "requires_attention":
        return "Проверить применимость меры, сроки и ответственного."
    if section == "measures_and_selections":
        return "Проверить условия участия и окно подачи."
    if section == "strategy_signals":
        return "Оценить влияние на меры господдержки и регулирование."
    if section == "news_signals":
        return "Оставить как отраслевой фон, без срочной реакции."
    return ""
