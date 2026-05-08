from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from app.config import get_source_role
from app.llm.enrichment import is_generic_enrichment_text
from app.models import DigestItem, RawDocument
from app.visibility import effective_user_action_level

OCR_FALLBACK_KRASNODAR_TITLE = "НПА Краснодарского края: документ после OCR"
OCR_FALLBACK_GENERIC_TITLE = "Документ после OCR: требуется ручная проверка"

PresentationDocument = RawDocument | DigestItem
TITLE_MAX_CHARS = 120
EXECUTIVE_REASON_MAX_CHARS = 90
EXECUTIVE_ACTION_MAX_CHARS = 85
EXECUTIVE_SUMMARY_MAX_CHARS = 120

_NPA_IN_TITLE_RE = re.compile(r"[№#]\s*(\d[\d/.\-]*\d|\d{1,6})")
_DATE_IN_TITLE_RE = re.compile(r"\b(\d{1,2})\.(\d{2})(?:\.\d{2,4})?\b")
_IBLOCK_URL_RE = re.compile(r"/iblock/([a-zA-Z0-9]{2,8})/")

SUBSIDY_RE = re.compile(r"субсид|грант|финансир|кредит|лизинг|возмещ|льготн", re.IGNORECASE)
SELECTION_RE = re.compile(r"отбор|заяв|конкурс|прием", re.IGNORECASE)
SUPPORT_RE = re.compile(r"поддержк|мер(а|ы)", re.IGNORECASE)
EXPORT_RESTRICTION_RE = re.compile(
    r"экспорт|импорт|квот|пошлин|пошлина|тариф|ограничен|запрет|тамож|вывоз",
    re.IGNORECASE,
)


def user_facing_action_level(document: RawDocument) -> str | None:
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
    return _clip_text(compressed, max_chars or TITLE_MAX_CHARS) if max_chars is not None else compressed


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
        _suffix_url,
    ):
        suffixes = [extractor(doc) for doc in group]
        if all(suffixes) and len(set(suffixes)) == len(suffixes):
            return suffixes
    return [str(i + 1) for i in range(len(group))]


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


def _suffix_title_keyword(doc: RawDocument) -> str:
    original = _normalize_text(_get_value(doc, "title"))
    if not original or _is_technical_ocr_placeholder(original):
        return ""
    compressed = compress_visible_title(doc)
    if not compressed or original.lower() == compressed.lower():
        return ""
    compressed_words = set(re.sub(r"[^\w]", " ", compressed.lower()).split())
    candidates = [
        w for w in re.sub(r"[^\w]", " ", original.lower()).split()
        if len(w) >= 5 and w not in compressed_words
    ]
    for word in reversed(candidates):
        if len(word) <= 20:
            return word[:20]
    return ""


def _suffix_url(doc: RawDocument) -> str:
    url = _normalize_text(_get_value(doc, "url"))
    if not url:
        return ""
    m = _IBLOCK_URL_RE.search(url)
    if m:
        return f"документ {m.group(1)}"
    try:
        path = urlsplit(url).path.rstrip("/")
        parts = [p for p in path.split("/") if p]
        if not parts:
            return ""
        stem = parts[-1].split(".")[0]
        if stem.isdigit() and 1 <= len(stem) <= 8:
            return f"#{stem}"
        if 3 <= len(stem) <= 12:
            return f"документ {stem[:8]}"
    except Exception:
        pass
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
        return _clip_text(_compress_freeform_reason(fallback_text, document=document), max_chars)
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
    if enrichment_text and is_useful_executive_summary(enrichment_text):
        return _clip_text(_normalize_text(enrichment_text), max_chars)
    if fallback_text:
        return _clip_text(_normalize_text(fallback_text), max_chars)
    return ""


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


def _base_visible_title(document: PresentationDocument) -> str:
    title = _normalize_text(_get_value(document, "title"))
    if _is_technical_ocr_placeholder(title):
        return _ocr_fallback_title(document)
    return title


def _compress_bureaucratic_title(document: PresentationDocument, title: str) -> str:
    lowered = title.lower()
    combined = _combined_text(document, title=title)
    if _looks_like_selection_announcement(document, combined):
        return "Открыт прием заявок"
    if "о реализации мероприятий" in lowered:
        return "Запущены новые меры поддержки" if _is_support_context(combined) else "Запущены новые меры"
    if "об утверждении порядка" in lowered or "утверждении порядка" in lowered:
        return _approval_headline(document, combined)
    if "о внесении изменений" in lowered or "внесении изменений" in lowered:
        return _change_headline(document, combined)
    if "об утверждении" in lowered and _is_support_context(combined):
        return _approval_headline(document, combined)
    return title


def _approval_headline(document: PresentationDocument, combined: str) -> str:
    if _looks_like_export_restriction(combined):
        return "Утверждены экспортные правила"
    if _looks_like_selection(combined):
        return "Утверждены правила отбора"
    if _looks_like_subsidy(combined):
        return "Утверждены условия субсидирования"
    if _is_support_context(combined):
        return "Утверждены правила поддержки"
    return "Утверждены новые правила"


def _change_headline(document: PresentationDocument, combined: str) -> str:
    if _looks_like_subsidy(combined):
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
    combined = _combined_text(document)
    source_role = _source_role(document)
    if _looks_like_selection_announcement(document, combined):
        return "Открыт прием заявок"
    if _source_role(document) == "news_signals" and (_action_level(document) == "watchlist" or section == "news_signals"):
        return "Рынок оставлен на наблюдении"
    if source_role == "regional_npa" and _page_type(document) == "new_rule":
        if _looks_like_subsidy(combined):
            return "Изменены условия субсидирования"
        return "Обновлены правила поддержки"
    if _looks_like_subsidy(combined) and _contains_change_signal(combined):
        if "льгот" in combined and "кредит" in combined:
            return "Обновлены условия льготного кредитования"
        return "Изменены условия поддержки"
    if _looks_like_export_restriction(combined):
        return "Подготовлены экспортные ограничения"
    if _looks_like_selection(combined) and _contains_change_signal(combined):
        return "Обновлены правила отбора"
    if _is_support_context(combined) and _contains_change_signal(combined):
        return "Изменены условия поддержки"
    return ""


def _deterministic_action(
    document: PresentationDocument,
    *,
    section: str | None,
) -> str:
    combined = _combined_text(document)
    source_role = _source_role(document)
    application_status = _get_value(document, "application_status").lower()
    if _looks_like_selection_announcement(document, combined):
        return "Проверить сроки подачи и ответственного."
    if application_status == "open":
        return "Проверить сроки подачи и ответственного."
    if source_role == "news_signals" and (_action_level(document) == "watchlist" or section == "news_signals"):
        return "Оставить как отраслевой фон."
    if source_role == "news_signals" and _looks_like_export_restriction(combined):
        return "Проверить влияние пошлины/торгового регулирования на рынок и контрагентов."
    if source_role == "news_signals" and _looks_like_credit_support_context(combined):
        return "Проверить условия кредитования, сроки и применимость для АПК."
    if source_role == "news_signals" and _is_support_context(combined):
        return "Проверить влияние на условия поддержки."
    if source_role == "regional_npa" and _page_type(document) == "new_rule":
        if _looks_like_subsidy(combined):
            return "Проверить изменения порядка субсидирования и сроки вступления."
        return "Проверить новые правила и сроки вступления."
    if _looks_like_export_restriction(combined):
        return "Проверить влияние пошлины/торгового регулирования на рынок и контрагентов."
    if _looks_like_selection(combined):
        return "Проверить условия и сроки отбора."
    if section == "strategy_signals" or source_role == "strategy":
        return "Оценить влияние на регулирование."
    if _is_support_measure_context(document, combined):
        return "Проверить применимость меры, сроки и ответственного."
    if _is_support_context(combined) and _action_level(document) == "requires_attention":
        return "Проверить условия поддержки."
    return ""


def _compress_freeform_reason(text: str, *, document: PresentationDocument) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    combined = f"{normalized} {_combined_text(document)}"
    if _looks_like_selection_announcement(document, combined):
        return "Открыт прием заявок"
    if _looks_like_subsidy(combined) and _contains_change_signal(combined):
        if "льгот" in combined and "кредит" in combined:
            return "Обновлены условия льготного кредитования"
        return "Изменены условия поддержки"
    if _looks_like_export_restriction(combined):
        return "Подготовлены экспортные ограничения"
    if _looks_like_selection(combined) and _contains_change_signal(combined):
        return "Обновлены правила отбора"
    if _source_role(document) == "news_signals" and _action_level(document) != "requires_attention":
        return "Рынок оставлен на наблюдении"
    return normalized


def _compress_freeform_action(text: str, *, document: PresentationDocument) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    combined = f"{normalized} {_combined_text(document)}"
    if _source_role(document) == "news_signals" and _action_level(document) != "requires_attention":
        return "Оставить как отраслевой фон."
    if _source_role(document) == "news_signals" and _looks_like_export_restriction(combined):
        return "Проверить влияние пошлины/торгового регулирования на рынок и контрагентов."
    if _source_role(document) == "news_signals" and _looks_like_credit_support_context(combined):
        return "Проверить условия кредитования, сроки и применимость для АПК."
    if _source_role(document) == "news_signals" and _is_support_context(combined):
        return "Проверить влияние на условия поддержки."
    if _looks_like_selection(combined):
        return "Проверить сроки подачи и ответственного."
    if _source_role(document) == "regional_npa" and _page_type(document) == "new_rule":
        if _looks_like_subsidy(combined):
            return "Проверить изменения порядка субсидирования и сроки вступления."
        return "Проверить новые правила и сроки вступления."
    if _is_support_measure_context(document, combined):
        return "Проверить применимость меры, сроки и ответственного."
    if _is_support_context(combined) and _action_level(document) == "requires_attention":
        return "Проверить условия поддержки."
    return normalized


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


def _looks_like_selection_announcement(document: PresentationDocument, combined: str) -> bool:
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
        or (_is_support_context(text) and _get_value(document, "application_status").lower() in {"open", "regular"})
    )


def _looks_like_export_restriction(text: str) -> bool:
    return bool(EXPORT_RESTRICTION_RE.search(text))


def _looks_like_credit_support_context(text: str) -> bool:
    return bool(re.search(r"льготн\w*\s+кредит|кредитован|заем|займ", text, re.IGNORECASE))


def _contains_change_signal(text: str) -> bool:
    return any(
        marker in text
        for marker in ("измен", "обнов", "новые условия", "новый порядок", "новые правила")
    )


def _is_technical_ocr_placeholder(title: str) -> bool:
    normalized = title.lower()
    return normalized.startswith("document '") and "requires ocr extraction" in normalized


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
        _get_value(document, "region") == "krasnodar" or "краснодар" in haystack or "krasnodar" in haystack
    ):
        return OCR_FALLBACK_KRASNODAR_TITLE
    return OCR_FALLBACK_GENERIC_TITLE


def _source_role(document: PresentationDocument) -> str:
    return get_source_role(_get_value(document, "source_name")) or ""


def _page_type(document: PresentationDocument) -> str:
    return _get_value(document, "page_type").lower()


def _action_level(document: PresentationDocument) -> str:
    return _get_value(document, "action_level").lower()


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
