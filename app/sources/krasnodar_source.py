from __future__ import annotations

import re
from datetime import datetime, time, timezone
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from app.extractors.date_extractor import parse_russian_date
from app.models import CollectedItem
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
    "грант",
    "агротуризм",
    "крестьянск",
    "фермер",
    "картоф",
    "овощ",
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

    def fetch_items(self) -> list[CollectedItem]:
        response = self.get(self.config.url)
        soup = BeautifulSoup(response.text, "html.parser")
        items = self._extract_items_from_soup(soup, response.url)
        if self._is_msh_krasnodar_source():
            items = self._harvest_msh_listing_attachments(items)
        return items

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

    def _is_msh_krasnodar_source(self) -> bool:
        source_key = f"{self.config.name} {self.config.url}".lower()
        return "msh.krasnodar.ru" in source_key

    def _harvest_msh_listing_attachments(self, items: list[CollectedItem]) -> list[CollectedItem]:
        max_items = self.config.max_items
        harvested_items = list(items)
        seen_urls = {item.url for item in harvested_items}
        attachment_counts = {"pdf": 0, "docx": 0, "doc": 0}

        for item in items:
            if max_items is not None and len(harvested_items) >= max_items:
                break
            if not _is_msh_attachment_listing_item(item):
                continue
            try:
                response = self.get(item.url)
            except Exception as exc:
                self.logger.warning(
                    "Could not harvest attachments from %s: %s",
                    item.url,
                    exc,
                )
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.find_all("a", href=True):
                if max_items is not None and len(harvested_items) >= max_items:
                    break
                if not isinstance(link, Tag):
                    continue
                normalized_url = self._normalize_url(link.get("href", ""), response.url)
                if not normalized_url or normalized_url in seen_urls:
                    continue
                document_type = self._detect_document_type(normalized_url)
                if document_type not in {"pdf", "doc", "docx"}:
                    continue
                title = _extract_msh_attachment_title(self, link, normalized_url)
                if not self._should_include_title(title):
                    continue
                if not self._should_include_url(normalized_url, response.url, title):
                    continue
                if not _has_actionable_title(f"{title} {normalized_url.lower()}"):
                    continue
                harvested_items.append(
                    CollectedItem(
                        source_name=self.config.name,
                        source_url=self.config.url,
                        level=self.config.level,
                        region=self.config.region,
                        title=title,
                        url=normalized_url,
                        published_at=self._extract_published_at(link, normalized_url),
                        document_type=document_type,
                    )
                )
                seen_urls.add(normalized_url)
                attachment_counts[document_type] += 1

        if attachment_counts:
            stats = dict(getattr(self, "last_fetch_stats", {}) or {})
            stats["items_collected_count"] = len(harvested_items)
            stats["harvested_attachment_count"] = sum(attachment_counts.values())
            stats["pdf_links_count"] = int(stats.get("pdf_links_count", 0)) + attachment_counts["pdf"]
            stats["docx_links_count"] = int(stats.get("docx_links_count", 0)) + attachment_counts["docx"]
            stats["doc_links_count"] = int(stats.get("doc_links_count", 0)) + attachment_counts["doc"]
            self.last_fetch_stats = stats
        return harvested_items


def _has_actionable_title(text: str) -> bool:
    return any(marker in text for marker in ACTIONABLE_TITLE_MARKERS)


def _is_msh_attachment_listing_item(item: CollectedItem) -> bool:
    if item.document_type != "html":
        return False
    parsed = urlparse(item.url.lower())
    if parsed.netloc != "msh.krasnodar.ru":
        return False
    if not parsed.path.startswith("/documents/"):
        return False
    title_text = item.title.lower().strip()
    return (
        title_text.startswith("приказы ")
        or LISTING_TITLE_RE.search(title_text) is not None
        or "subsidirovanie-i-finansirovanie" in parsed.path
    )


def _extract_msh_attachment_title(source: KrasnodarSource, link: Tag, normalized_url: str) -> str:
    link_title = source._extract_title(link, normalized_url)
    if not _is_generic_download_title(link_title):
        return link_title
    for ancestor_name in ("tr", "li", "article", "div", "section"):
        ancestor = link.find_parent(ancestor_name)
        if not isinstance(ancestor, Tag):
            continue
        text = " ".join(ancestor.stripped_strings)
        title = _clean_attachment_title(text)
        if title and not _is_generic_download_title(title):
            return title[:500]
    return link_title


def _is_generic_download_title(title: str) -> bool:
    normalized = " ".join((title or "").lower().split()).strip(" .,:;")
    if not normalized:
        return True
    return normalized in {
        "скачать",
        "скачать документ",
        "скачать файл",
        "pdf",
        "doc",
        "docx",
        "файл",
    } or normalized.startswith(("pdf,", "doc,", "docx,"))


def _clean_attachment_title(text: str) -> str:
    normalized = " ".join((text or "").split())
    normalized = re.sub(
        r"\b(?:pdf|docx?|rtf|zip)\s*,?\s*\d+(?:[.,]\d+)?\s*(?:кб|kb|мб|mb)\b",
        " ",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\bскачать(?:\s+документ|\s+файл)?\b", " ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .,:;")
    return normalized


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
