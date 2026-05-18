from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from app.config import get_source_role
from app.llm.enrichment import is_generic_enrichment_text
from app.models import DigestItem, RawDocument
from app.rules.deadline_truth import is_deadline_expired

OCR_FALLBACK_KRASNODAR_TITLE = "НПА Краснодарского края: документ после OCR"
OCR_FALLBACK_GENERIC_TITLE = "Документ после OCR: требуется ручная проверка"

PresentationDocument = RawDocument | DigestItem
TITLE_MAX_CHARS = 120
EXECUTIVE_REASON_MAX_CHARS = 90
EXECUTIVE_ACTION_MAX_CHARS = 85
EXECUTIVE_SUMMARY_MAX_CHARS = 120
GENERIC_EXECUTIVE_SUMMARY_MARKERS = (
    "документ содержит изменения в порядке предоставления поддержки",
    "требуется проверка условий и сроков",
    "документ содержит изменения в порядке предоставления",
)
PARSER_RESIDUE_SUMMARY_MARKERS = (
    "статус:",
    "процедура:",
    "начало обсуждения:",
    "конец обсуждения:",
    "id:",
    "код:",
)

_NPA_IN_TITLE_RE = re.compile(r"[№#]\s*(\d[\d/.\-]*\d|\d{1,6})")
_DATE_IN_TITLE_RE = re.compile(r"\b(\d{1,2})\.(\d{2})(?:\.\d{2,4})?\b")
_IBLOCK_URL_RE = re.compile(r"/iblock/([a-zA-Z0-9]{2,8})/")

SUBSIDY_RE = re.compile(
    r"субсид|грант|финансир|кредит|лизинг|возмещ|льготн", re.IGNORECASE
)
SELECTION_RE = re.compile(r"отбор|заяв|конкурс|прием", re.IGNORECASE)
SUPPORT_RE = re.compile(r"поддержк|мер(а|ы)", re.IGNORECASE)
EXPORT_RESTRICTION_RE = re.compile(
    r"экспорт|импорт|квот|пошлин|пошлина|тариф|ограничен|запрет|тамож|вывоз",
    re.IGNORECASE,
)
TRADE_REGULATION_RE = re.compile(
    r"импорт|квот|пошлин|пошлина|тариф|тамож|вывоз"
    r"|правил\w*\s+экспорт|экспортн\w*\s+правил",
    re.IGNORECASE,
)
# Sub-markers used to pick precise wording inside the trade intent. They do
# NOT participate in intent dispatch — they only refine the rendered reason
# and action once the trade intent has been chosen.
_TRADE_DUTY_RE = re.compile(r"пошлин|тариф", re.IGNORECASE)
_TRADE_QUOTA_RE = re.compile(r"квот", re.IGNORECASE)
_TRADE_RESTRICTION_RE = re.compile(r"запрет|ограничен", re.IGNORECASE)
_TRADE_LOGISTICS_RE = re.compile(
    r"логистик|терминал|перевозк|поставк", re.IGNORECASE
)
_TRADE_EXPORT_CONTEXT_RE = re.compile(
    r"экспорт|вывоз|тамож|импорт|ввоз", re.IGNORECASE
)
OCR_FALLBACK_TITLE_RE = re.compile(r"\bдокумент после ocr\b", re.IGNORECASE)
OCR_MEANINGFUL_MARKERS = (
    "субсид",
    "поддержк",
    "поряд",
    "утвержд",
    "измен",
    "вступ",
    "льгот",
    "кредит",
    "отбор",
    "заяв",
    "срок",
    "апк",
)
OCR_WEAK_TEXT_RE = re.compile(
    r"распознанн\w*\s+текст\s+отсутств|требует\s+ручн\w*\s+провер|документ\s+после\s+ocr"
    r"|requires\s+ocr\s+extraction|ocr\s+placeholder",
    re.IGNORECASE,
)
INTENT_SELECTION_OPEN = "selection_open"
INTENT_SELECTION_EXPIRED = "selection_expired"
INTENT_SELECTION_CHANGE = "selection_change"
INTENT_MARKET_OBSERVATION = "market_observation"
INTENT_STRATEGY = "strategy_general"
INTENT_REGIONAL_SUBSIDY = "regional_subsidy_rule"
INTENT_REGIONAL_RULE = "regional_rule"
INTENT_CREDIT_SUPPORT = "credit_support_change"
INTENT_SUPPORT_CHANGE = "support_change"
INTENT_TRADE_REGULATION = "trade_regulation"
INTENT_SUPPORT_MEASURE = "support_measure"
INTENT_SUPPORT_ATTENTION = "support_attention"
INTENT_OFFICIAL_LEGISLATION_WATCHLIST = "official_legislation_watchlist"
INTENT_OCR_PLACEHOLDER = "ocr_placeholder"
INTENT_REGULATION_DISCUSSION = "regulation_discussion"
_DISCUSSION_DATE_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})|\b(\d{1,2})[.](\d{1,2})[.](\d{4})\b"
)


def user_facing_action_level(document: RawDocument) -> str | None:
    from app.visibility import effective_user_action_level

    return effective_user_action_level(document)


def user_facing_title(
    document: PresentationDocument,
    *,
    max_chars: int | None = None,
) -> str:
    return compress_visible_title(document, max_chars=max_chars)


def compress_visible_title(
    document: PresentationDocument,
    *,
    max_chars: int | None = None,
) -> str:
    title = _base_visible_title(document)
    title = _normalize_text(title)
    if not title:
        title = "Документ требует проверки"
    compressed = _compress_bureaucratic_title(document, title)
    return (
        _clip_text(compressed, max_chars or TITLE_MAX_CHARS)
        if max_chars is not None
        else compressed
    )


def disambiguate_visible_titles(
    documents: Sequence[RawDocument],
    *,
    max_chars: int = TITLE_MAX_CHARS,
) -> dict[int, str]:
    """Return {doc.id: display_title} with disambiguation suffixes for collision groups.

    Documents with the same compressed title get a short distinguishing suffix so
    they remain separable in the rendered report or Telegram digest.  Documents
    whose compressed title is already unique are returned unchanged (no suffix).
    Docs without an id are not included.
    """
    id_title_doc: list[tuple[int, str, RawDocument]] = []
    for doc in documents:
        if doc.id is None:
            continue
        id_title_doc.append((doc.id, compress_visible_title(doc), doc))

    by_title: dict[str, list[tuple[int, RawDocument]]] = {}
    for doc_id, title, doc in id_title_doc:
        by_title.setdefault(title, []).append((doc_id, doc))

    result: dict[int, str] = {}
    for title, group_items in by_title.items():
        if len(group_items) == 1:
            result[group_items[0][0]] = _clip_text(title, max_chars)
        else:
            group_docs = [doc for _, doc in group_items]
            suffixes = _choose_group_suffixes(group_docs)
            for (doc_id, _), suffix in zip(group_items, suffixes):
                combined = f"{title} ({suffix})" if suffix else title
                result[doc_id] = _clip_text(combined, max_chars)
    return result


def _choose_group_suffixes(group: Sequence[RawDocument]) -> list[str]:
    for extractor in (
        _suffix_npa_field,
        _suffix_title_npa,
        _suffix_title_date,
        _suffix_title_keyword,
    ):
        suffixes = [extractor(doc) for doc in group]
        if all(suffixes) and len(set(suffixes)) == len(suffixes):
            return suffixes
    # No safe disambiguation — return empty suffixes so titles stay clean.
    # Duplicate titles are still distinguishable by URL further down the item;
    # showing parser-looking "(1)" / "(2)" tokens is worse for executive UX.
    return [""] * len(group)


def _suffix_npa_field(doc: RawDocument) -> str:
    npa = _normalize_text(_get_value(doc, "npa_number"))
    return f"№{npa}" if npa else ""


def _suffix_title_npa(doc: RawDocument) -> str:
    title = _normalize_text(_get_value(doc, "title"))
    if not title or _is_technical_ocr_placeholder(title):
        return ""
    m = _NPA_IN_TITLE_RE.search(title)
    return f"№{m.group(1)}" if m else ""


def _suffix_title_date(doc: RawDocument) -> str:
    title = _normalize_text(_get_value(doc, "title"))
    if not title or _is_technical_ocr_placeholder(title):
        return ""
    m = _DATE_IN_TITLE_RE.search(title)
    return f"от {m.group(1)}.{m.group(2)}" if m else ""


# Topical stem → executive-friendly noun phrase used as disambiguation suffix.
# Bare Russian wordforms ("займам", "полугодие", "агросмене") leaked into report
# titles before this whitelist was introduced — they looked like parser tokens.
# Order matters: more specific stems first.
_TOPICAL_DISAMBIGUATION_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("агросмен", "АгроСмена"),
    ("льготн", "льготные займы"),
    ("займ", "льготные займы"),
    ("грант", "гранты"),
    ("мелиорац", "мелиорация"),
    ("молоч", "молочное животноводство"),
    ("животновод", "животноводство"),
    ("растениевод", "растениеводство"),
    ("элит", "элитное семеноводство"),
    ("семен", "семеноводство"),
    ("семян", "семеноводство"),
    ("овощ", "овощеводство"),
    ("зернов", "зерновые"),
    ("пшениц", "зерновые"),
    ("картофел", "картофель"),
    ("экспорт", "экспорт"),
    ("импорт", "импорт"),
    ("пошлин", "пошлины"),
    ("квот", "квоты"),
    ("агротуризм", "агротуризм"),
    ("возмещ", "возмещение затрат"),
    ("кредит", "льготные кредиты"),
)


def _suffix_title_keyword(doc: RawDocument) -> str:
    """Topical suffix derived from the original title.

    Returns an executive-friendly noun phrase (e.g. "льготные займы") matched
    against ``_TOPICAL_DISAMBIGUATION_SUFFIXES``. Falls back to empty string
    when no topical stem is found — earlier callers used a bare Russian
    wordform here, which read like a parser leak ("(займам)", "(агросмене)").
    """
    original = _normalize_text(_get_value(doc, "title"))
    if not original or _is_technical_ocr_placeholder(original):
        return ""
    compressed = compress_visible_title(doc)
    compressed_lower = compressed.lower()
    original_lower = original.lower()
    if not compressed or original_lower == compressed_lower:
        return ""
    for stem, label in _TOPICAL_DISAMBIGUATION_SUFFIXES:
        if stem in original_lower and stem not in compressed_lower:
            return label
    return ""


def _suffix_url(doc: RawDocument) -> str:
    """URL-derived suffix — intentionally narrow.

    The previous numeric-stem fallbacks emitted parser-looking tokens like
    "#1233850" or "документ aae" in visible titles. Those are dropped here.
    Only the iblock token is kept as a last-resort, and only as a short
    "документ XXX" form for the very narrow case where no topical suffix
    could be derived.
    """
    url = _normalize_text(_get_value(doc, "url"))
    if not url:
        return ""
    return ""


def build_executive_reason(
    document: PresentationDocument,
    *,
    enrichment_text: str | None = None,
    fallback_text: str | None = None,
    section: str | None = None,
    max_chars: int = EXECUTIVE_REASON_MAX_CHARS,
) -> str:
    deterministic = _deterministic_reason(document, section=section)
    if deterministic:
        return _clip_text(deterministic, max_chars)
    if enrichment_text and not is_generic_enrichment_text(enrichment_text):
        compressed = _compress_freeform_reason(enrichment_text, document=document)
        if compressed:
            return _clip_text(compressed, max_chars)
    if fallback_text:
        return _clip_text(
            _compress_freeform_reason(fallback_text, document=document), max_chars
        )
    return ""


def build_executive_action(
    document: PresentationDocument,
    *,
    enrichment_text: str | None = None,
    section: str | None = None,
    max_chars: int = EXECUTIVE_ACTION_MAX_CHARS,
) -> str:
    deterministic = _deterministic_action(document, section=section)
    if deterministic:
        return _clip_text(deterministic, max_chars)
    if enrichment_text and not is_generic_enrichment_text(enrichment_text):
        compressed = _compress_freeform_action(enrichment_text, document=document)
        if compressed:
            return _clip_text(compressed, max_chars)
    return ""


def select_executive_summary(
    document: PresentationDocument,
    *,
    enrichment_text: str | None = None,
    fallback_text: str | None = None,
    max_chars: int = EXECUTIVE_SUMMARY_MAX_CHARS,
) -> str:
    deterministic = _deterministic_summary(document)
    if deterministic and (
        _is_generic_executive_summary(enrichment_text)
        or _is_generic_executive_summary(fallback_text)
        or _looks_like_parser_residue_summary(fallback_text)
    ):
        return _clip_text(deterministic, max_chars)
    if enrichment_text and is_useful_executive_summary(enrichment_text):
        normalized_enrichment = _normalize_text(enrichment_text)
        if deterministic and _is_generic_executive_summary(normalized_enrichment):
            return _clip_text(deterministic, max_chars)
        return _clip_text(normalized_enrichment, max_chars)
    if fallback_text and not _is_generic_executive_summary(fallback_text):
        return _clip_text(_normalize_text(fallback_text), max_chars)
    if deterministic:
        return _clip_text(deterministic, max_chars)
    if fallback_text:
        return _clip_text(_normalize_text(fallback_text), max_chars)
    return ""


def is_meaningful_executive_highlight(document: PresentationDocument) -> bool:
    if is_weak_ocr_placeholder_document(document):
        return False
    title = user_facing_title(document).lower()
    if (
        "документ после ocr" in title
        or "требуется ручная проверка" in title
        or title == "документ требует проверки"
    ):
        return False
    summary = select_executive_summary(
        document,
        fallback_text=_get_value(document, "summary"),
    ).lower()
    if summary and (
        "текст после ocr недостаточен" in summary
        or _is_generic_executive_summary(summary)
        or "краткое пояснение пока не добавлено" in summary
    ):
        return False
    return True


def is_useful_executive_summary(text: str | None) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    lowered = normalized.lower()
    generic_markers = (
        "документ оставлен на наблюдении",
        "сигнал может повлиять",
        "оценить срочность сигнала",
        "требует наблюдения со стороны gr",
    )
    return not any(marker in lowered for marker in generic_markers)


def _is_generic_executive_summary(text: str | None) -> bool:
    normalized = _normalize_text(text).lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in GENERIC_EXECUTIVE_SUMMARY_MARKERS)


def _looks_like_parser_residue_summary(text: str | None) -> bool:
    normalized = _normalize_text(text).lower()
    if not normalized:
        return False
    return (
        sum(1 for marker in PARSER_RESIDUE_SUMMARY_MARKERS if marker in normalized) >= 2
    )


def has_meaningful_extracted_ocr_text(document: PresentationDocument) -> bool:
    raw_text = _normalize_text(_get_value(document, "raw_text"))
    if not raw_text:
        return False
    lowered = raw_text.lower()
    if (
        _is_technical_ocr_placeholder(raw_text)
        or OCR_FALLBACK_TITLE_RE.search(raw_text)
        or OCR_WEAK_TEXT_RE.search(raw_text)
    ):
        return False
    informative_words = re.findall(r"[a-zа-яё]{4,}", lowered, flags=re.IGNORECASE)
    unique_words = set(informative_words)
    marker_hits = sum(1 for marker in OCR_MEANINGFUL_MARKERS if marker in lowered)
    if len(raw_text) >= 250 and len(unique_words) >= 14 and marker_hits >= 2:
        return True
    if len(raw_text) >= 120 and len(unique_words) >= 10 and marker_hits >= 3:
        return True
    return False


def is_weak_ocr_placeholder_document(document: PresentationDocument) -> bool:
    title = _normalize_text(_get_value(document, "title"))
    visible_title = _normalize_text(_base_visible_title(document))
    has_placeholder_title = (
        _is_technical_ocr_placeholder(title)
        or bool(OCR_FALLBACK_TITLE_RE.search(title))
        or bool(OCR_FALLBACK_TITLE_RE.search(visible_title))
    )
    if not has_placeholder_title:
        return False
    return not has_meaningful_extracted_ocr_text(document)


def _base_visible_title(document: PresentationDocument) -> str:
    title = _normalize_text(_get_value(document, "title"))
    if _is_technical_ocr_placeholder(title):
        return _ocr_fallback_title(document)
    return title


def _compress_bureaucratic_title(document: PresentationDocument, title: str) -> str:
    lowered = title.lower()
    combined = _combined_text(document, title=title)
    if _looks_like_selection_announcement(document, combined):
        if _application_is_closed(document):
            return "Прием заявок завершён"
        topic = _selection_topic_hint(combined)
        if topic:
            return f"Открыт прием заявок {topic}"
        return "Открыт прием заявок"
    if "о реализации мероприятий" in lowered:
        return (
            "Запущены новые меры поддержки"
            if _is_support_context(combined)
            else "Запущены новые меры"
        )
    if "об утверждении порядка" in lowered or "утверждении порядка" in lowered:
        return _approval_headline(document, combined)
    if "о внесении изменений" in lowered or "внесении изменений" in lowered:
        return _change_headline(document, combined)
    if "об утверждении" in lowered and _is_support_context(combined):
        return _approval_headline(document, combined)
    return title


def _subsidy_topic_hint(combined: str) -> str:
    """Return a topic clause that slots cleanly after "Субсидии" / "на ..." paths.

    All return values use either ``на …`` or ``для …`` so the caller can always
    connect to a region with ``в {region}`` without producing double-в grammar
    artifacts (the previous "в АПК — Краснодарском крае" form).
    """
    lowered = combined.lower()
    if "мелиора" in lowered:
        return "на мелиорацию"
    if "семен" in lowered or "семян" in lowered:
        return "на элитное семеноводство"
    if "молоч" in lowered or "животновод" in lowered:
        return "на молочное животноводство"
    if "экспорт" in lowered:
        return "на экспорт"
    if "овощ" in lowered:
        return "на овощеводство"
    if "зерн" in lowered or "пшениц" in lowered:
        return "на зерновые"
    if "агротуризм" in lowered:
        return "на агротуризм"
    if "апк" in lowered or "агропромышлен" in lowered:
        return "для АПК"
    return ""


def _selection_topic_hint(combined: str) -> str:
    """Topic suffix that turns a bare "Открыт прием заявок" into a specific one.

    Returned values are noun-phrase clauses that read naturally after the verb
    "Открыт прием заявок ...". Empty string → no suffix.
    """
    lowered = combined.lower()
    if "агросмен" in lowered:
        return "на АгроСмену"
    if "льготн" in lowered and ("займ" in lowered or "кредит" in lowered):
        return "на льготные займы и кредиты"
    if "грант" in lowered:
        return "на гранты"
    if "возмещ" in lowered and "затрат" in lowered:
        return "на возмещение затрат"
    if "возмещ" in lowered:
        return "на возмещение"
    if "мелиора" in lowered:
        return "на мелиорацию"
    if "молоч" in lowered or "животновод" in lowered:
        return "по молочному животноводству"
    if "семен" in lowered:
        return "на семеноводство"
    if "субсид" in lowered:
        return "на субсидии"
    return ""


def _approval_headline(document: PresentationDocument, combined: str) -> str:
    if _looks_like_export_restriction(combined):
        return "Утверждены экспортные правила"
    if _looks_like_selection(combined):
        return "Утверждены правила отбора"
    if _looks_like_subsidy(combined):
        region = _region_label(document)
        topic = _subsidy_topic_hint(combined)
        if topic and region:
            return f"Субсидии {topic} в {region}"
        if topic:
            return f"Субсидии {topic}"
        if region:
            return f"Субсидии в {region}"
        return "Утверждены условия субсидирования"
    if _is_support_context(combined):
        return "Утверждены правила поддержки"
    return "Утверждены новые правила"


def _change_headline(document: PresentationDocument, combined: str) -> str:
    if _looks_like_subsidy(combined):
        region = _region_label(document)
        topic = _subsidy_topic_hint(combined)
        if topic and region:
            return f"Изменены субсидии {topic} в {region}"
        if topic:
            return f"Изменены субсидии {topic}"
        if region:
            return f"Изменены субсидии в {region}"
        return "Изменены условия субсидирования"
    if _is_support_context(combined):
        if "льгот" in combined and "кредит" in combined:
            return "Обновлены условия льготного кредитования"
        return "Обновлены правила поддержки"
    if _looks_like_export_restriction(combined):
        return "Подготовлены экспортные ограничения"
    if _looks_like_selection(combined):
        return "Обновлены правила отбора"
    if _source_role(document) == "regional_npa":
        return "Обновлены правила поддержки"
    return "Обновлены правила"


def _deterministic_reason(
    document: PresentationDocument,
    *,
    section: str | None,
) -> str:
    intent = _detect_deterministic_intent(document, section=section)
    return _reason_for_intent(intent, document=document)


def _deterministic_action(
    document: PresentationDocument,
    *,
    section: str | None,
) -> str:
    intent = _detect_deterministic_intent(document, section=section)
    return _action_for_intent(intent, document=document)


def _deterministic_summary(document: PresentationDocument) -> str:
    if is_weak_ocr_placeholder_document(document):
        return (
            "Текст после OCR недостаточен для уверенного выделения условий документа."
        )

    title_summary = _summary_from_title(document)
    if title_summary:
        return title_summary

    intent = _detect_deterministic_intent(document)
    if intent == INTENT_REGULATION_DISCUSSION:
        return "Проект НПА вынесен на публичное обсуждение."
    if intent == INTENT_REGIONAL_SUBSIDY:
        region_label = _region_label(document)
        if region_label:
            return f"Изменён порядок предоставления субсидий в {region_label}."
        return "Изменён порядок предоставления субсидий."
    if intent == INTENT_CREDIT_SUPPORT:
        return "Обновляются условия льготного кредитования АПК."
    if intent == INTENT_SELECTION_OPEN:
        if _looks_like_subsidy(_combined_text(document)):
            return "Открыт приём заявок на субсидии для АПК."
        return "Открыт приём заявок по профильной мере поддержки."
    if intent == INTENT_SELECTION_EXPIRED:
        return "Приём заявок по данной мере поддержки завершён."
    if intent == INTENT_SUPPORT_CHANGE:
        return "Изменены условия предоставления меры поддержки."
    return ""


def _compress_freeform_reason(text: str, *, document: PresentationDocument) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    combined = f"{normalized} {_combined_text(document)}"
    intent = _detect_deterministic_intent(document, combined_text=combined)
    deterministic = _reason_for_intent(intent, document=document)
    if deterministic:
        return deterministic
    return normalized


def _compress_freeform_action(text: str, *, document: PresentationDocument) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    combined = f"{normalized} {_combined_text(document)}"
    intent = _detect_deterministic_intent(document, combined_text=combined)
    deterministic = _action_for_intent(intent, document=document)
    if deterministic:
        return deterministic
    return normalized


def _detect_deterministic_intent(
    document: PresentationDocument,
    *,
    section: str | None = None,
    combined_text: str | None = None,
) -> str:
    combined = combined_text or _combined_text(document)
    source_role = _source_role(document)
    page_type = _page_type(document)
    action_level = _action_level(document)
    application_status = _get_value(document, "application_status").lower()

    if is_weak_ocr_placeholder_document(document):
        return INTENT_OCR_PLACEHOLDER
    application_is_closed = _application_is_closed(document)
    if _looks_like_selection_announcement(document, combined):
        if application_is_closed:
            return INTENT_SELECTION_EXPIRED
        return INTENT_SELECTION_OPEN
    if _looks_like_regulation_public_discussion(document, combined):
        return INTENT_REGULATION_DISCUSSION
    if application_status == "open" and not application_is_closed:
        return INTENT_SELECTION_OPEN
    if application_is_closed and _looks_like_selection(combined):
        return INTENT_SELECTION_EXPIRED
    if _looks_like_mcx_official_support_watchlist(document, combined):
        return INTENT_SUPPORT_CHANGE
    if _looks_like_mcx_official_legislative_watchlist(document, combined):
        return INTENT_OFFICIAL_LEGISLATION_WATCHLIST
    if source_role == "news_signals" and (
        action_level == "watchlist" or section == "news_signals"
    ):
        return INTENT_MARKET_OBSERVATION
    if section == "strategy_signals" or source_role == "strategy":
        return INTENT_STRATEGY
    if source_role == "regional_npa" and page_type == "new_rule":
        if _looks_like_credit_support_context(combined) and _contains_change_signal(
            combined
        ):
            return INTENT_CREDIT_SUPPORT
        if _looks_like_subsidy(combined):
            return INTENT_REGIONAL_SUBSIDY
        return INTENT_REGIONAL_RULE
    if (
        source_role == "support_documents"
        and _region_label(document)
        and _looks_like_subsidy(combined)
    ):
        return INTENT_REGIONAL_SUBSIDY
    if _looks_like_credit_support_context(combined) and _contains_change_signal(
        combined
    ):
        return INTENT_CREDIT_SUPPORT
    # Trade/export signals must win over generic "support change" wording —
    # otherwise a doc whose business_signal or impact text mentions
    # "господдержки/мер поддержки/изменений" would render as a support change
    # even when its core subject is duties, quotas, or export restrictions.
    if _looks_like_trade_regulation_context(combined):
        return INTENT_TRADE_REGULATION
    if _looks_like_selection(combined) and _contains_change_signal(combined):
        return INTENT_SELECTION_CHANGE
    if _is_support_context(combined) and _contains_change_signal(combined):
        return INTENT_SUPPORT_CHANGE
    if _is_support_measure_context(document, combined):
        return INTENT_SUPPORT_MEASURE
    if _is_support_context(combined) and action_level == "requires_attention":
        return INTENT_SUPPORT_ATTENTION
    return ""


def _summary_from_title(document: PresentationDocument) -> str:
    title = _normalize_text(_get_value(document, "title"))
    if not title or _is_technical_ocr_placeholder(title):
        return ""
    lowered = title.lower()
    region_label = _region_label(document)

    if "льготн" in lowered and "кредит" in lowered:
        if "минсельхоз" in lowered and any(
            marker in lowered for marker in ("предлож", "обнов", "новые условия")
        ):
            return "Минсельхоз предложил обновить условия льготного кредитования АПК."
        return "Обновляются условия льготного кредитования АПК."
    if "субсид" in lowered and any(
        marker in lowered
        for marker in ("внесении изменений", "о внесении изменений", "изменени")
    ):
        if region_label:
            return f"Изменён порядок предоставления субсидий в {region_label}."
        return "Изменён порядок предоставления субсидий."
    if "субсид" in lowered and any(
        marker in lowered for marker in ("утвержден", "утверждён", "утверждены")
    ):
        if region_label:
            return f"Утверждены условия субсидирования в {region_label}."
        return "Утверждены условия субсидирования."
    if any(
        marker in lowered
        for marker in (
            "прием заявок",
            "приём заявок",
            "отбор заявок",
            "конкурсный отбор",
            "объявлен отбор",
        )
    ):
        if _application_is_closed(document):
            return "Приём заявок по данной мере поддержки завершён."
        if "субсид" in lowered:
            return "Открыт приём заявок на субсидии для АПК."
        return "Открыт приём заявок по профильной мере поддержки."
    if _source_role(document) == "news_signals" and title:
        normalized = _normalize_title_sentence(title)
        if normalized:
            return normalized
    return ""


def _reason_for_intent(
    intent: str,
    *,
    document: PresentationDocument | None = None,
) -> str:
    if intent == INTENT_SELECTION_OPEN:
        return "Открыт прием заявок"
    if intent == INTENT_SELECTION_EXPIRED:
        return "Прием заявок завершён"
    if intent == INTENT_REGULATION_DISCUSSION:
        return "Проект НПА на публичном обсуждении"
    if intent == INTENT_MARKET_OBSERVATION:
        return "Рынок оставлен на наблюдении"
    if intent == INTENT_STRATEGY:
        return "Стратегический федеральный сигнал по господдержке или порядку регулирования."
    if intent == INTENT_REGIONAL_SUBSIDY:
        return "Изменены условия субсидирования"
    if intent == INTENT_REGIONAL_RULE:
        return "Обновлены правила поддержки"
    if intent == INTENT_CREDIT_SUPPORT:
        return "Обновлены условия льготного кредитования"
    if intent == INTENT_SUPPORT_CHANGE:
        return "Изменены условия поддержки"
    if intent == INTENT_OFFICIAL_LEGISLATION_WATCHLIST:
        return "Законодательный сигнал по регулированию АПК"
    if intent == INTENT_TRADE_REGULATION:
        return _trade_reason_for_document(document)
    if intent == INTENT_SELECTION_CHANGE:
        return "Обновлены правила отбора"
    if intent == INTENT_OCR_PLACEHOLDER:
        return "Документ после OCR требует ручной проверки"
    return ""


def _trade_reason_for_document(document: PresentationDocument | None) -> str:
    """Pick precise trade wording (duty/quota/restriction/logistics/general)."""
    combined = _combined_text(document) if document is not None else ""
    if _TRADE_DUTY_RE.search(combined):
        return "Изменение экспортных пошлин"
    if _TRADE_QUOTA_RE.search(combined):
        return "Изменение экспортных квот"
    if (
        _TRADE_RESTRICTION_RE.search(combined)
        and _TRADE_EXPORT_CONTEXT_RE.search(combined)
    ):
        return "Ограничения экспорта"
    if _TRADE_LOGISTICS_RE.search(combined) and _TRADE_EXPORT_CONTEXT_RE.search(
        combined
    ):
        return "Изменение условий логистики экспорта"
    return "Изменение экспортных условий"


def _trade_action_for_document(document: PresentationDocument | None) -> str:
    combined = _combined_text(document) if document is not None else ""
    if (
        _TRADE_RESTRICTION_RE.search(combined)
        and _TRADE_EXPORT_CONTEXT_RE.search(combined)
    ):
        return "Проверить влияние ограничений на экспортные контракты и логистику."
    if _TRADE_LOGISTICS_RE.search(combined) and _TRADE_EXPORT_CONTEXT_RE.search(
        combined
    ):
        return "Проверить влияние на логистику и условия поставок."
    return "Проверить влияние на экспортные контракты и логистику."


def _action_for_intent(
    intent: str,
    *,
    document: PresentationDocument,
) -> str:
    if intent == INTENT_SELECTION_OPEN:
        deadline_text = _get_value(document, "deadline_text")
        if deadline_text:
            return "Проверить сроки подачи документов и готовность заявки."
        return "Проверить применимость меры и порядок подачи заявки."
    if intent == INTENT_SELECTION_EXPIRED:
        return "Срок истёк, документ — справочно."
    if intent == INTENT_REGULATION_DISCUSSION:
        deadline = _discussion_deadline_label(document)
        if deadline:
            return f"Проверить влияние проекта и подготовить позицию до {deadline}."
        return "Проверить влияние проекта и подготовить позицию."
    if intent == INTENT_MARKET_OBSERVATION:
        return "Оставить как отраслевой фон."
    if intent == INTENT_STRATEGY:
        return "Оценить влияние на регулирование АПК."
    if intent == INTENT_REGIONAL_SUBSIDY:
        return "Проверить изменения условий субсидирования и критерии отбора."
    if intent == INTENT_REGIONAL_RULE:
        return "Проверить вступление изменений в силу и применимость к холдингу."
    if intent == INTENT_CREDIT_SUPPORT:
        return "Проверить условия кредитования и применимость для АПК."
    if intent == INTENT_SUPPORT_CHANGE:
        if _source_role(document) == "news_signals":
            return "Проверить влияние на условия поддержки и регламент применения."
        return "Проверить условия поддержки и регламент применения."
    if intent == INTENT_OFFICIAL_LEGISLATION_WATCHLIST:
        return "Проверить, какие законопроекты одобрены, и оценить влияние на регулирование АПК."
    if intent == INTENT_TRADE_REGULATION:
        return _trade_action_for_document(document)
    if intent == INTENT_SELECTION_CHANGE:
        return "Проверить условия и сроки отбора."
    if intent == INTENT_SUPPORT_MEASURE:
        return "Проверить применимость меры, сроки и ответственного."
    if intent == INTENT_SUPPORT_ATTENTION:
        return "Проверить условия поддержки."
    if intent == INTENT_OCR_PLACEHOLDER:
        return "Дождаться повторной проверки OCR или сверить текст вручную."
    return ""


def _combined_text(document: PresentationDocument, *, title: str | None = None) -> str:
    return " ".join(
        _normalize_text(part).lower()
        for part in (
            title or _get_value(document, "title"),
            _get_value(document, "summary"),
            _get_value(document, "impact"),
            _get_value(document, "business_signal"),
            _get_value(document, "deadline_text"),
            _get_value(document, "terms_text"),
        )
        if _normalize_text(part)
    )


def _is_official_mcx_news(document: PresentationDocument) -> bool:
    source_name = _get_value(document, "source_name")
    url = _get_value(document, "url").lower()
    return (
        source_name == "Минсельхоз России - новости"
        or "mcx.gov.ru/press-service/news/" in url
    )


def _looks_like_mcx_official_support_watchlist(
    document: PresentationDocument,
    combined: str,
) -> bool:
    if not _is_official_mcx_news(document):
        return False
    if _action_level(document) != "watchlist":
        return False
    support_markers = ("господдерж", "меры поддержки", "субсид", "льготн", "кредит")
    change_markers = ("расшир", "измен", "обнов", "утверд", "одобрил", "одобрено")
    return any(marker in combined for marker in support_markers) and any(
        marker in combined for marker in change_markers
    )


def _looks_like_mcx_official_legislative_watchlist(
    document: PresentationDocument,
    combined: str,
) -> bool:
    if not _is_official_mcx_news(document):
        return False
    if _action_level(document) != "watchlist":
        return False
    legislative_markers = ("законопроект", "совет федерации", "госдум", "федеральн")
    return "апк" in combined and any(
        marker in combined for marker in legislative_markers
    )


def _looks_like_selection_announcement(
    document: PresentationDocument, combined: str
) -> bool:
    if _page_type(document) == "selection_announcement":
        return True
    title = _normalize_text(_get_value(document, "title")).lower()
    return _looks_like_selection(title) and (
        "прием заяв" in title or "отбор" in title or "конкурс" in title
    )


def _looks_like_selection(text: str) -> bool:
    return bool(SELECTION_RE.search(text))


def _looks_like_subsidy(text: str) -> bool:
    return bool(SUBSIDY_RE.search(text))


def _is_support_context(text: str) -> bool:
    return bool(SUPPORT_RE.search(text) or SUBSIDY_RE.search(text))


def _is_support_measure_context(document: PresentationDocument, text: str) -> bool:
    return (
        _source_role(document) in {"active_support_measures", "support_documents"}
        or _page_type(document) in {"measure_card", "selection_announcement"}
        or (
            _is_support_context(text)
            and _get_value(document, "application_status").lower()
            in {"open", "regular"}
        )
    )


def _looks_like_export_restriction(text: str) -> bool:
    return bool(EXPORT_RESTRICTION_RE.search(text))


def has_trade_regulation_signal(text: str) -> bool:
    """Public predicate: does ``text`` carry trade/export/duty/quota/restriction wording.

    Thin wrapper around the internal trade-regulation detector so the
    report-layer prioritization can consult the same definition the intent
    taxonomy uses, without depending on a private helper.
    """
    return _looks_like_trade_regulation_context(text)


def _looks_like_trade_regulation_context(text: str) -> bool:
    if TRADE_REGULATION_RE.search(text):
        return True
    # Restriction wording (ограничение / запрет) on its own is ambiguous —
    # "ограничение приема заявок" is a support context, not a trade one. Treat
    # it as trade only when paired with explicit export/import wording.
    if _TRADE_RESTRICTION_RE.search(text) and _TRADE_EXPORT_CONTEXT_RE.search(text):
        return True
    # Logistics wording (логистика / терминал / поставки / перевозки) on its
    # own is ambiguous too — only treat it as a trade signal when explicit
    # export/import context is also present.
    if _TRADE_LOGISTICS_RE.search(text) and _TRADE_EXPORT_CONTEXT_RE.search(text):
        return True
    return False


def _looks_like_regulation_public_discussion(
    document: PresentationDocument,
    combined: str,
) -> bool:
    if _source_role(document) != "strategy":
        return False
    return "обсужден" in combined or "публичн" in combined


def _discussion_deadline_label(document: PresentationDocument) -> str:
    text = " ".join(
        part
        for part in (
            _get_value(document, "deadline_text"),
            _get_value(document, "summary"),
        )
        if part
    )
    match = _DISCUSSION_DATE_RE.search(text)
    if not match:
        return ""
    if match.group(1):
        return f"{match.group(3)}.{match.group(2)}.{match.group(1)}"
    day = int(match.group(4))
    month = int(match.group(5))
    year = int(match.group(6))
    return f"{day:02d}.{month:02d}.{year:04d}"


def _looks_like_credit_support_context(text: str) -> bool:
    return bool(
        re.search(r"льготн\w*\s+кредит|кредитован|заем|займ", text, re.IGNORECASE)
    )


def _contains_change_signal(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "измен",
            "обнов",
            "новые условия",
            "новый порядок",
            "новые правила",
        )
    )


def _is_technical_ocr_placeholder(title: str) -> bool:
    normalized = title.lower()
    return (
        normalized.startswith("document '") and "requires ocr extraction" in normalized
    )


def _ocr_fallback_title(document: PresentationDocument) -> str:
    haystack = " ".join(
        _normalize_text(part).lower()
        for part in (
            _get_value(document, "source_name"),
            _get_value(document, "source_url"),
            _get_value(document, "url"),
            _get_value(document, "region"),
        )
        if _normalize_text(part)
    )
    if _source_role(document) == "regional_npa" and (
        _get_value(document, "region") == "krasnodar"
        or "краснодар" in haystack
        or "krasnodar" in haystack
    ):
        return OCR_FALLBACK_KRASNODAR_TITLE
    return OCR_FALLBACK_GENERIC_TITLE


def _normalize_title_sentence(title: str) -> str:
    normalized = _normalize_text(re.sub(r"\([^)]*\)$", "", title))
    if not normalized:
        return ""
    if normalized.endswith((".", "!", "?")):
        return normalized
    return f"{normalized}."


def _region_label(document: PresentationDocument) -> str:
    region = _get_value(document, "region").lower()
    if region == "krasnodar":
        return "Краснодарском крае"
    if region == "stavropol":
        return "Ставропольском крае"
    if region == "rostov":
        return "Ростовской области"
    return ""


def _source_role(document: PresentationDocument) -> str:
    return get_source_role(_get_value(document, "source_name")) or ""


def _page_type(document: PresentationDocument) -> str:
    return _get_value(document, "page_type").lower()


def _action_level(document: PresentationDocument) -> str:
    return _get_value(document, "action_level").lower()


def _application_is_closed(document: PresentationDocument) -> bool:
    """True when the application window is no longer alive.

    Checks both the stored ``application_status`` (analysis-time decision) and
    the parsed deadline at render time. The render-time recheck protects
    against stored ``open`` rows whose deadline silently elapsed between
    analyze and report runs.
    """
    if _get_value(document, "application_status").lower() == "closed":
        return True
    deadline_text = _get_value(document, "deadline_text")
    return bool(deadline_text) and is_deadline_expired(deadline_text)


def _get_value(document: PresentationDocument, field_name: str) -> str:
    value = getattr(document, field_name, "") or ""
    return str(value).strip()


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clip_text(text: str, max_chars: int) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3].rstrip(' ,.;:-')}..."
