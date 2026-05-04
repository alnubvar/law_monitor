from __future__ import annotations

import re
from datetime import datetime, time, timezone
from urllib.parse import urlparse

from bs4 import Tag

from app.extractors.date_extractor import parse_russian_date
from app.sources.generic_html_source import GenericHTMLSource

SAFE_PUBLICATION_MARKERS = (
    "дата публикации",
    "опубликовано",
    "опубликован",
    "размещено",
    "размещен",
    "размещён",
)
ACTIONABLE_TITLE_MARKERS = (
    "объявлен отбор",
    "объявление о проведении отбора",
    "прием заявок",
    "приём заявок",
    "срок подачи",
    "заявки принимаются",
    "субсид",
    "постановление",
    "приказ",
    "распоряжение",
    "о внесении изменений",
)
LISTING_TITLE_RE = re.compile(
    r"^(?:20\d{2}|i20\d{2}|субсидирование и финансирование(?:\s+20\d{2})?|"
    r"субсидии|господдержка|документы|приказы|архив)",
    re.IGNORECASE,
)
MSH_YEAR_PATH_RE = re.compile(r"/i20\d{2}/?$", re.IGNORECASE)
DATE_CONTEXT_RE = re.compile(
    r"(?:дата публикации|опубликовано|опубликован|размещено|размещен|размещён)"
    r"[:\s]+(.{0,80})",
    re.IGNORECASE,
)


class KrasnodarSource(GenericHTMLSource):
    """Source-specific link filtering for Краснодар regional portals."""

    def _should_include_url(self, url: str, base_url: str, title: str) -> bool:
        if not super()._should_include_url(url, base_url, title):
            return False

        source_key = f"{self.config.name} {self.config.url}".lower()
        if "admkrai.krasnodar.ru" in source_key:
            return self._should_include_admkrai_url(url, title)
        if "msh.krasnodar.ru" in source_key:
            return self._should_include_msh_url(url, title)
        return True

    def _should_include_admkrai_url(self, url: str, title: str) -> bool:
        document_type = self._detect_document_type(url)
        if document_type in {"pdf", "doc", "docx"}:
            return True

        lower_url = url.lower()
        title_text = title.lower().strip()
        if _is_admkrai_listing_path(lower_url):
            return False
        if any(marker in title_text for marker in ACTIONABLE_TITLE_MARKERS):
            return True
        return "document" in lower_url

    def _should_include_msh_url(self, url: str, title: str) -> bool:
        document_type = self._detect_document_type(url)
        if document_type in {"pdf", "doc", "docx"}:
            return True

        lower_url = url.lower()
        title_text = title.lower().strip()
        if MSH_YEAR_PATH_RE.search(urlparse(lower_url).path):
            return False
        if LISTING_TITLE_RE.search(title_text) and not _has_actionable_title(title_text):
            return False
        if any(fragment in lower_url for fragment in ("/archive", "/i202", "/i203")):
            return False
        return _has_actionable_title(f"{title_text} {lower_url}")

    def _extract_published_at(self, link: Tag, normalized_url: str):
        extracted = _extract_safe_publication_date(link)
        if extracted is None:
            return super()._extract_published_at(link, normalized_url)
        return datetime.combine(extracted, time.min, tzinfo=timezone.utc)


def _has_actionable_title(text: str) -> bool:
    return any(marker in text for marker in ACTIONABLE_TITLE_MARKERS)


def _is_admkrai_listing_path(lower_url: str) -> bool:
    path = urlparse(lower_url).path.rstrip("/")
    return path.startswith("/content/") and not path.startswith("/content/1291/")


def _extract_safe_publication_date(link: Tag):
    time_tag = link.find_previous("time") or link.find("time")
    if isinstance(time_tag, Tag):
        datetime_attr = str(time_tag.get("datetime", "")).strip()
        parsed = parse_russian_date(datetime_attr or " ".join(time_tag.stripped_strings))
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
        if len(text) > 600:
            break
    return None
