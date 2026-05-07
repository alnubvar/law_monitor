from __future__ import annotations

from app.config import get_source_role
from app.models import RawDocument
from app.rules.news_background_guard import guard_news_signal_action_level

OCR_FALLBACK_KRASNODAR_TITLE = "НПА Краснодарского края: документ после OCR"
OCR_FALLBACK_GENERIC_TITLE = "Документ после OCR: требуется ручная проверка"


def user_facing_action_level(document: RawDocument) -> str | None:
    return guard_news_signal_action_level(
        document.action_level,
        source_role=get_source_role(document.source_name),
        reason=document.relevance_reason,
        impact=document.impact,
        signal=document.business_signal,
    )


def user_facing_title(document: RawDocument, *, max_chars: int | None = None) -> str:
    title = (document.title or "").strip()
    if _is_technical_ocr_placeholder(title):
        title = _ocr_fallback_title(document)
    if max_chars is not None and len(title) > max_chars:
        return title[:max_chars].rstrip()
    return title


def _is_technical_ocr_placeholder(title: str) -> bool:
    normalized = title.lower()
    return normalized.startswith("document '") and "requires ocr extraction" in normalized


def _ocr_fallback_title(document: RawDocument) -> str:
    haystack = " ".join(
        part.lower()
        for part in (
            document.source_name,
            document.source_url,
            document.url,
            document.region,
        )
        if part
    )
    if get_source_role(document.source_name) == "regional_npa" and (
        document.region == "krasnodar" or "краснодар" in haystack or "krasnodar" in haystack
    ):
        return OCR_FALLBACK_KRASNODAR_TITLE
    return OCR_FALLBACK_GENERIC_TITLE
