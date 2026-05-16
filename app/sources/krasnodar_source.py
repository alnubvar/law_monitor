from __future__ import annotations

import re
from datetime import datetime, time, timezone
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.extractors.date_extractor import parse_russian_date
from app.models import CollectedItem
from app.sources.generic_html_source import GLOBAL_DENY_PATTERNS, GenericHTMLSource

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
MSH_ARCHIVE_PATH_RE = re.compile(r"/documents/subsidirovanie-i-finansirovanie1/(?:arkhiv-subs|i20\d{2}/archive)/?$", re.IGNORECASE)
DATE_CONTEXT_RE = re.compile(
    r"(?:дата публикации|опубликовано|опубликован|размещено|размещен|размещён)"
    r"[:\s]+(.{0,80})",
    re.IGNORECASE,
)
MSH_LEADING_ORDER_DATE_RE = re.compile(
    r"^\s*№\s*\d+\s+от\s+(\d{1,2}[.]\d{1,2}[.]\d{4})\b",
    re.IGNORECASE,
)
NPA_KRASNODAR_FILE_RE = re.compile(r"^/rest/files/\d+/?$", re.IGNORECASE)
_MSH_PAGINATION_PATH_RE = re.compile(r"/page\d+/?$", re.IGNORECASE)
_MSH_MAX_EXTRA_PAGES = 4  # follow at most pages 2-5 per listing
MSH_ALLOWED_YEAR_PATHS = {
    "/documents/subsidirovanie-i-finansirovanie1/i2026",
    "/documents/subsidirovanie-i-finansirovanie1/i2025",
    "/documents/subsidirovanie-i-finansirovanie1/i2024",
    "/documents/subsidirovanie-i-finansirovanie1/i2023",
}
MSH_ALLOWED_ATTACHMENT_TYPES = {"pdf", "doc", "docx", "xls", "xlsx", "zip"}


class KrasnodarSource(GenericHTMLSource):
    """Source-specific link filtering for Краснодар regional portals."""

    def fetch_items(self) -> list[CollectedItem]:
        response = self.get(self.config.url)
        soup = BeautifulSoup(response.text, "html.parser")
        items = self._extract_items_from_soup(soup, response.url)
        if self._is_msh_krasnodar_source():
            items = self._harvest_msh_listing_attachments(items, soup, response.url)
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
        if document_type in MSH_ALLOWED_ATTACHMENT_TYPES:
            return True

        lower_url = url.lower()
        title_text = title.lower().strip()
        if MSH_YEAR_PATH_RE.search(urlparse(lower_url).path):
            return False
        if LISTING_TITLE_RE.search(title_text) and not _has_actionable_title(title_text):
            return False
        if MSH_ARCHIVE_PATH_RE.search(urlparse(lower_url).path):
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

    def _harvest_msh_listing_attachments(
        self,
        items: list[CollectedItem],
        root_soup: BeautifulSoup,
        root_url: str,
    ) -> list[CollectedItem]:
        max_items = self.config.max_items
        direct_items = [item for item in items if not _is_msh_attachment_listing_item(item)]
        listing_items = [item for item in items if _is_msh_attachment_listing_item(item)]
        final_items = list(direct_items)
        seen_urls = {item.url for item in final_items}
        attachment_counts = {"pdf": 0, "docx": 0, "doc": 0, "xls": 0, "xlsx": 0, "zip": 0}
        seed_urls = _collect_msh_seed_urls(root_soup, root_url, listing_items)
        visited_pages: set[str] = set()
        pagination_pages_visited = 0
        seed_pages_visited = 0

        for seed_url in seed_urls:
            if max_items is not None and len(final_items) >= max_items:
                break
            page_url = seed_url
            current_page = 1
            page_visits_for_seed = 0

            while True:
                if max_items is not None and len(final_items) >= max_items:
                    break
                try:
                    response = self.get(page_url)
                except Exception as exc:
                    self.logger.warning(
                        "Could not harvest attachments from %s: %s",
                        page_url,
                        exc,
                    )
                    break
                normalized_page_url = _normalize_msh_page_url(response.url)
                if normalized_page_url in visited_pages:
                    break
                visited_pages.add(normalized_page_url)
                page_visits_for_seed += 1
                if current_page > 1:
                    pagination_pages_visited += 1
                soup = BeautifulSoup(response.text, "html.parser")

                for document_item in soup.select(".document-item"):
                    if max_items is not None and len(final_items) >= max_items:
                        break
                    if not isinstance(document_item, Tag):
                        continue
                    collected = _extract_msh_document_item_attachment(self, document_item, response.url)
                    if collected is None or collected.url in seen_urls:
                        continue
                    final_items.append(collected)
                    seen_urls.add(collected.url)
                    attachment_counts[collected.document_type] += 1

                for link in soup.find_all("a", href=True):
                    if max_items is not None and len(final_items) >= max_items:
                        break
                    if not isinstance(link, Tag):
                        continue
                    normalized_url = self._normalize_url(link.get("href", ""), response.url)
                    if not normalized_url or normalized_url in seen_urls:
                        continue
                    document_type = _detect_msh_attachment_document_type(self, normalized_url, link)
                    if document_type not in MSH_ALLOWED_ATTACHMENT_TYPES:
                        continue
                    title = _extract_msh_attachment_title(self, link, normalized_url)
                    if not self._should_include_title(title):
                        continue
                    if _is_generic_download_title(title):
                        continue
                    if not _is_safe_msh_attachment_url(self, normalized_url, response.url, title):
                        continue
                    final_items.append(
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

                if page_visits_for_seed >= _MSH_MAX_EXTRA_PAGES + 1:
                    break
                if max_items is not None and len(final_items) >= max_items:
                    break
                next_url = _find_msh_next_page_url(soup, response.url, current_page + 1)
                if next_url is None:
                    break
                page_url = next_url
                current_page += 1

            if page_visits_for_seed > 0:
                seed_pages_visited += 1

        for item in listing_items:
            if max_items is not None and len(final_items) >= max_items:
                break
            if item.url in seen_urls:
                continue
            final_items.append(item)
            seen_urls.add(item.url)

        if attachment_counts or seed_urls:
            stats = dict(getattr(self, "last_fetch_stats", {}) or {})
            stats["items_collected_count"] = len(final_items)
            stats["seed_pages_found"] = len(seed_urls)
            stats["seed_pages_visited"] = seed_pages_visited
            stats["pagination_pages_visited"] = pagination_pages_visited
            stats["harvested_attachment_count"] = sum(attachment_counts.values())
            stats["pdf_links_count"] = int(stats.get("pdf_links_count", 0)) + attachment_counts["pdf"]
            stats["docx_links_count"] = int(stats.get("docx_links_count", 0)) + attachment_counts["docx"]
            stats["doc_links_count"] = int(stats.get("doc_links_count", 0)) + attachment_counts["doc"]
            stats["xls_links_count"] = int(stats.get("xls_links_count", 0)) + attachment_counts["xls"]
            stats["xlsx_links_count"] = int(stats.get("xlsx_links_count", 0)) + attachment_counts["xlsx"]
            stats["zip_links_count"] = int(stats.get("zip_links_count", 0)) + attachment_counts["zip"]
            self.last_fetch_stats = stats
        return final_items


def _collect_msh_seed_urls(
    soup: BeautifulSoup,
    base_url: str,
    listing_items: list[CollectedItem],
) -> list[str]:
    seed_urls: list[str] = []
    seen_urls: set[str] = set()

    for item in listing_items:
        if item.url not in seen_urls:
            seed_urls.append(item.url)
            seen_urls.add(item.url)

    for link in soup.find_all("a", href=True):
        if not isinstance(link, Tag):
            continue
        normalized_url = urljoin(base_url, (link.get("href") or "").strip())
        normalized = _normalize_msh_page_url(normalized_url)
        if not normalized or normalized in seen_urls:
            continue
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != "msh.krasnodar.ru":
            continue
        normalized_path = parsed.path.rstrip("/").lower()
        if normalized_path in MSH_ALLOWED_YEAR_PATHS:
            seed_urls.append(normalized)
            seen_urls.add(normalized)
            continue
        if normalized_path == "/documents/prikazy-minselkhoza-krasnodarskogo-kraya":
            seed_urls.append(normalized)
            seen_urls.add(normalized)
    return seed_urls


def _normalize_msh_page_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    query_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key == "PAGEN_1":
            query_pairs.append((key, value))
    normalized_query = urlencode(query_pairs)
    normalized_path = parsed.path.rstrip("/") or "/"
    return parsed._replace(path=normalized_path, query=normalized_query, fragment="").geturl()


def _find_msh_next_page_url(soup: BeautifulSoup, page_url: str, next_page: int) -> str | None:
    """Return the next listing page URL if a /pageN or ?PAGEN_1=N link matching next_page is in soup."""
    parsed_base = urlparse(page_url)
    base_path = _MSH_PAGINATION_PATH_RE.sub("", parsed_base.path).rstrip("/")
    expected_path = f"{base_path}/page{next_page}".lower()
    for link in soup.find_all("a", href=True):
        if not isinstance(link, Tag):
            continue
        href = (link.get("href") or "").strip()
        if not href:
            continue
        normalized = urljoin(page_url, href)
        parsed = urlparse(normalized)
        if parsed.netloc.lower() != parsed_base.netloc.lower():
            continue
        if parsed.path.rstrip("/").lower() == expected_path:
            return _normalize_msh_page_url(normalized)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if parsed.path.rstrip("/").lower() != base_path.lower():
            continue
        if query.get("PAGEN_1") == str(next_page):
            return normalized
    return None


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


def _extract_msh_document_item_attachment(
    source: KrasnodarSource,
    document_item: Tag,
    base_url: str,
) -> CollectedItem | None:
    title_tag = document_item.select_one(".document-item__title")
    download_link = document_item.select_one("a.document-info-bar__download-link")
    if not isinstance(title_tag, Tag) or not isinstance(download_link, Tag):
        return None

    normalized_url = source._normalize_url(download_link.get("href", ""), base_url)
    if not normalized_url:
        return None

    document_type = _extract_msh_document_item_type(source, document_item, normalized_url, download_link)
    if document_type not in MSH_ALLOWED_ATTACHMENT_TYPES:
        return None

    title = _extract_msh_document_item_title(title_tag)
    if not source._should_include_title(title):
        return None
    if not _is_safe_msh_attachment_url(source, normalized_url, base_url, title):
        return None

    return CollectedItem(
        source_name=source.config.name,
        source_url=source.config.url,
        level=source.config.level,
        region=source.config.region,
        title=title,
        url=normalized_url,
        published_at=_extract_msh_document_item_published_at(title, normalized_url),
        document_type=document_type,
    )


def _extract_msh_document_item_type(
    source: KrasnodarSource,
    document_item: Tag,
    normalized_url: str,
    download_link: Tag,
) -> str:
    type_tag = document_item.select_one(".document-info-bar__type")
    type_text = " ".join(type_tag.stripped_strings).lower() if isinstance(type_tag, Tag) else ""
    if type_text in MSH_ALLOWED_ATTACHMENT_TYPES:
        return type_text
    return _detect_msh_attachment_document_type(source, normalized_url, download_link)


def _extract_msh_document_item_title(title_tag: Tag) -> str:
    for extra in title_tag.select(".document-item-extra-info"):
        extra.extract()
    return " ".join(title_tag.stripped_strings).strip()[:500]


def _extract_msh_document_item_published_at(title: str, normalized_url: str) -> datetime | None:
    parsed_url = urlparse(normalized_url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.netloc.lower() != "npa.krasnodar.ru"
        or NPA_KRASNODAR_FILE_RE.fullmatch(parsed_url.path) is None
    ):
        return None
    match = MSH_LEADING_ORDER_DATE_RE.search(title)
    if match is None:
        return None
    parsed_date = parse_russian_date(match.group(1))
    if parsed_date is None:
        return None
    return datetime.combine(parsed_date, time.min, tzinfo=timezone.utc)


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


def _detect_msh_attachment_document_type(source: KrasnodarSource, normalized_url: str, link: Tag) -> str:
    document_type = source._detect_document_type(normalized_url)
    if document_type in MSH_ALLOWED_ATTACHMENT_TYPES:
        return document_type

    evidence = _msh_attachment_evidence_text(link, normalized_url)
    if ".pdf" in evidence or re.search(r"(?<![a-zа-я])pdf(?![a-zа-я])", evidence):
        return "pdf"
    if ".docx" in evidence or re.search(r"(?<![a-zа-я])docx(?![a-zа-я])", evidence):
        return "docx"
    if ".doc" in evidence or re.search(r"(?<![a-zа-я])doc(?![a-zа-я])", evidence):
        return "doc"
    if ".xlsx" in evidence or re.search(r"(?<![a-zа-я])xlsx(?![a-zа-я])", evidence):
        return "xlsx"
    if ".xls" in evidence or re.search(r"(?<![a-zа-я])xls(?![a-zа-я])", evidence):
        return "xls"
    if ".zip" in evidence or re.search(r"(?<![a-zа-я])zip(?![a-zа-я])", evidence):
        return "zip"
    return document_type


def _msh_attachment_evidence_text(link: Tag, normalized_url: str) -> str:
    parts = [normalized_url]
    parts.extend(link.stripped_strings)
    for ancestor_name in ("tr", "li", "article", "div", "section"):
        ancestor = link.find_parent(ancestor_name)
        if not isinstance(ancestor, Tag):
            continue
        ancestor_text = " ".join(ancestor.stripped_strings)
        if len(ancestor_text) <= 800:
            parts.append(ancestor_text)
        break
    return " ".join(parts).lower()


def _is_safe_msh_attachment_url(source: KrasnodarSource, url: str, base_url: str, title: str) -> bool:
    parsed = urlparse(url)
    base = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        return False
    target_host = parsed.netloc.lower()
    is_same_host = target_host == base.netloc.lower()
    is_approved_npa_file = (
        target_host == "npa.krasnodar.ru"
        and parsed.scheme == "https"
        and NPA_KRASNODAR_FILE_RE.fullmatch(parsed.path) is not None
    )
    if not (is_same_host or is_approved_npa_file):
        return False
    if url.rstrip("/") == base_url.rstrip("/"):
        return False

    combined = f"{url} {title}".lower()
    if any(pattern in combined for pattern in GLOBAL_DENY_PATTERNS):
        return False
    if source.config.deny_patterns:
        patterns = [pattern.lower() for pattern in source.config.deny_patterns]
        if any(pattern in combined for pattern in patterns):
            return False
    return True


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
        "xls",
        "xlsx",
        "zip",
        "файл",
    } or normalized.startswith(("pdf,", "doc,", "docx,", "xls,", "xlsx,", "zip,"))


def _clean_attachment_title(text: str) -> str:
    normalized = " ".join((text or "").split())
    normalized = re.sub(
        r"\b(?:pdf|docx?|xlsx?|rtf|zip)\s*,?\s*\d+(?:[.,]\d+)?\s*(?:кб|kb|мб|mb)\b",
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
