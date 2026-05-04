from __future__ import annotations

import re
from datetime import datetime, time, timezone
from urllib.parse import urlparse

from bs4 import NavigableString, Tag

from app.extractors.date_extractor import parse_russian_date
from app.sources.generic_html_source import GenericHTMLSource

SAFE_PUBLICATION_MARKERS = (
    "дата публикации",
    "дата опубликования",
    "опубликовано",
    "опубликован",
    "размещено",
    "размещен",
    "размещён",
)
UNSAFE_DATE_MARKERS = (
    "приказ от",
    "постановление от",
    "распоряжение от",
    "закон от",
    "прием заяв",
    "приём заяв",
    "заявки принима",
    "срок подачи",
    "до ",
)
MSHSK_ACTIONABLE_TITLE_MARKERS = (
    "объявление о проведении отбора",
    "объявление об отборе",
    "объявление о приеме заявочной документации",
    "прием заявочной документации",
    "прием заявок",
    "приём заявок",
    "срок подачи",
    "заявки принимаются",
    "грант",
    "субсид",
    "господдерж",
    "мера поддержки",
    "агростартап",
    "отбор",
    "конкурс",
    "возмещение части затрат",
    "решение о порядке предоставления субсидии",
    "постановление",
    "приказ",
    "распоряжение",
    "о внесении изменений",
)
MSHSK_REFERENCE_TITLES = {
    "гранты",
    "субсидии",
    "объявления",
    "результаты",
    "электронный бюджет",
    "грантовая поддержка \"агростартап\"",
    "решения о порядке предоставления субсидии",
    "справочник по мерам государственной поддержки",
}
MSHSK_REFERENCE_PATHS = {
    "/gospodderzhka",
    "/subsidii",
}
MSHSK_ARCHIVE_PATH_RE = re.compile(r"/gospodderzhka/obyavleniya-20\d{2}\.php$", re.IGNORECASE)
MSHSK_YEAR_TITLE_RE = re.compile(r"^(?:20\d{2}|перечень мер господдержки 20\d{2} год)$", re.IGNORECASE)
PRAVO_STAV_DOCUMENT_TITLE_MARKERS = (
    "закон",
    "постановление",
    "приказ",
    "распоряжение",
    "решение",
    "указ",
)
PRAVO_STAV_REFERENCE_TITLES = {
    "архив документов",
    "документы",
    "контакты",
    "новости",
    "о портале",
    "поиск документов",
    "правовые акты",
    "проекты документов",
    "расширенный поиск",
}
PRAVO_STAV_REFERENCE_PATH_HINTS = (
    "/search",
    "/archive",
    "/news",
    "/rss",
    "/about",
    "/contacts",
    "/document/list",
    "/documents/list",
)
PRAVO_STAV_DOCUMENT_PATH_HINTS = (
    "/document/",
    "/documents/",
    "/doc/",
    "/law/",
)
DATE_CONTEXT_RE = re.compile(
    r"(?:дата публикации|дата опубликования|опубликовано|опубликован|размещено|размещен|размещён)"
    r"[:\s,]+(.{0,120})",
    re.IGNORECASE,
)


class StavropolSource(GenericHTMLSource):
    """Source-specific filtering for Ставропольские regional portals."""

    def _should_include_url(self, url: str, base_url: str, title: str) -> bool:
        if not super()._should_include_url(url, base_url, title):
            return False

        source_key = f"{self.config.name} {self.config.url}".lower()
        if "mshsk.ru" in source_key:
            return self._should_include_mshsk_url(url, title)
        if "pravo.stavregion.ru" in source_key:
            return self._should_include_pravo_url(url, title)
        return True

    def _should_include_mshsk_url(self, url: str, title: str) -> bool:
        document_type = self._detect_document_type(url)
        if document_type in {"pdf", "doc", "docx"}:
            return True

        lower_url = url.lower()
        path = urlparse(lower_url).path.rstrip("/") or "/"
        title_text = _normalize_title(title)
        if MSHSK_YEAR_TITLE_RE.fullmatch(title_text):
            return False
        if path in MSHSK_REFERENCE_PATHS:
            return False
        if MSHSK_ARCHIVE_PATH_RE.search(path):
            return False
        if title_text in MSHSK_REFERENCE_TITLES:
            return False
        return _has_mshsk_actionable_title(f"{title_text} {lower_url}")

    def _should_include_pravo_url(self, url: str, title: str) -> bool:
        lower_url = url.lower()
        path = urlparse(lower_url).path.rstrip("/") or "/"
        title_text = _normalize_title(title)
        document_type = self._detect_document_type(url)

        if title_text in PRAVO_STAV_REFERENCE_TITLES:
            return False
        if any(fragment in path for fragment in PRAVO_STAV_REFERENCE_PATH_HINTS):
            return False
        if document_type in {"pdf", "doc", "docx"}:
            return _has_pravo_document_title(title_text)
        if any(fragment in path for fragment in PRAVO_STAV_DOCUMENT_PATH_HINTS):
            return _has_pravo_document_title(title_text)
        return False

    def _extract_published_at(self, link: Tag, normalized_url: str):
        extracted = _extract_safe_publication_date(link)
        if extracted is None:
            return super()._extract_published_at(link, normalized_url)
        return datetime.combine(extracted, time.min, tzinfo=timezone.utc)


def _normalize_title(title: str) -> str:
    return " ".join((title or "").lower().split())


def _has_mshsk_actionable_title(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in MSHSK_ACTIONABLE_TITLE_MARKERS)


def _has_pravo_document_title(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in PRAVO_STAV_DOCUMENT_TITLE_MARKERS)


def _extract_safe_publication_date(link: Tag):
    time_tag = link.find_previous("time") or link.find("time")
    if isinstance(time_tag, Tag):
        datetime_attr = str(time_tag.get("datetime", "")).strip()
        parsed = parse_russian_date(datetime_attr or " ".join(time_tag.stripped_strings))
        if parsed is not None:
            return parsed

    for candidate in _nearby_date_candidates(link):
        parsed = _parse_safe_date_candidate(candidate)
        if parsed is not None:
            return parsed

    for ancestor in link.parents:
        if not isinstance(ancestor, Tag):
            continue
        if ancestor.name not in {"article", "li", "tr", "div", "section"}:
            continue
        text = " ".join(ancestor.stripped_strings)
        lowered = text.lower()
        if not any(marker in lowered for marker in SAFE_PUBLICATION_MARKERS):
            continue
        context_match = DATE_CONTEXT_RE.search(text)
        parsed = parse_russian_date(context_match.group(1) if context_match else text)
        if parsed is not None:
            return parsed
        if len(text) > 800:
            break
    return None


def _nearby_date_candidates(link: Tag) -> list[str]:
    candidates: list[str] = []
    for sibling in list(link.previous_siblings)[:3]:
        text = _extract_candidate_text(sibling)
        if text:
            candidates.append(text)
    parent = link.parent
    if isinstance(parent, Tag):
        for sibling in list(parent.previous_siblings)[:3]:
            text = _extract_candidate_text(sibling)
            if text:
                candidates.append(text)
    return candidates


def _extract_candidate_text(node: object) -> str:
    if isinstance(node, Tag):
        return " ".join(node.stripped_strings)
    if isinstance(node, NavigableString):
        return str(node).strip()
    return ""


def _parse_safe_date_candidate(text: str):
    normalized = " ".join((text or "").split())
    if not normalized or len(normalized) > 80:
        return None
    lowered = normalized.lower()
    if any(marker in lowered for marker in UNSAFE_DATE_MARKERS):
        return None
    return parse_russian_date(normalized)
