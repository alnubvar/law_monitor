from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.models import CollectedItem
from app.sources.base import BaseSource

DOCUMENT_EXTENSIONS = {
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "docx",
    ".htm": "html",
    ".html": "html",
    ".xml": "xml",
    ".rss": "xml",
}
GLOBAL_DENY_PATTERNS = (
    "sitemap",
    "about",
    "search",
    "archive",
    "persons",
    "person/",
    "photos",
    "photo/",
    "social",
    "rss",
    "comments",
    "catalogue",
    "catalog",
    "page=",
    "rugovclassifier",
    "structure",
    "ministries",
    "agencies",
)
SHORT_TITLE_SKIP_VALUES = {
    "показать еще",
    "показать ещё",
    "еще",
    "ещё",
    "more",
    "далее",
}
NUMERIC_TITLE_REGEX = re.compile(r"^\d{1,3}$")


class GenericHTMLSource(BaseSource):
    def fetch_items(self) -> list[CollectedItem]:
        response = self.get(self.config.url)
        soup = BeautifulSoup(response.text, "html.parser")
        return self._extract_items_from_soup(soup, response.url)

    def _extract_items_from_soup(
        self, soup: BeautifulSoup, base_url: str
    ) -> list[CollectedItem]:
        items: list[CollectedItem] = []
        seen_urls: set[str] = set()
        max_items = self.config.max_items

        for link in soup.find_all("a", href=True):
            if not isinstance(link, Tag):
                continue
            normalized_url = self._normalize_url(link.get("href", ""), base_url)
            if not normalized_url or normalized_url in seen_urls:
                continue

            title = self._extract_title(link, normalized_url)
            if not self._should_include_title(title):
                continue
            if not self._should_include_url(normalized_url, base_url, title):
                continue
            document_type = self._detect_document_type(normalized_url)
            items.append(
                CollectedItem(
                    source_name=self.config.name,
                    source_url=self.config.url,
                    level=self.config.level,
                    region=self.config.region,
                    title=title,
                    url=normalized_url,
                    document_type=document_type,
                )
            )
            seen_urls.add(normalized_url)
            if max_items is not None and len(items) >= max_items:
                break

        self.logger.info(
            "Fetched %s items from %s", len(items), self.config.name
        )
        return items

    def _normalize_url(self, href: str, base_url: str) -> str | None:
        href = (href or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            return None
        absolute_url = urljoin(base_url, href)
        parsed = urlparse(absolute_url)
        if parsed.scheme not in {"http", "https"}:
            return None
        normalized = parsed._replace(fragment="").geturl()
        return normalized

    def _extract_title(self, link: Tag, normalized_url: str) -> str:
        title = " ".join(link.stripped_strings)
        if title:
            return title[:500]
        title_attr = link.get("title")
        if title_attr:
            return str(title_attr).strip()[:500]
        path = PurePosixPath(urlparse(normalized_url).path)
        if path.name:
            return path.name
        return normalized_url

    def _detect_document_type(self, url: str) -> str:
        path = urlparse(url).path.lower()
        extension = PurePosixPath(path).suffix.lower()
        if extension in DOCUMENT_EXTENSIONS:
            return DOCUMENT_EXTENSIONS[extension]
        if not extension:
            return "html"
        return "unknown"

    def _should_include_url(self, url: str, base_url: str, title: str) -> bool:
        base_host = urlparse(base_url).netloc.lower()
        target = urlparse(url)
        target_host = target.netloc.lower()
        document_type = self._detect_document_type(url)
        if not (target_host == base_host or document_type in {"pdf", "doc", "docx"}):
            return False

        combined = f"{url} {title}".lower()
        if url.rstrip("/") == base_url.rstrip("/"):
            return False
        if any(pattern in combined for pattern in GLOBAL_DENY_PATTERNS):
            return False
        if self.config.allow_patterns:
            patterns = [pattern.lower() for pattern in self.config.allow_patterns]
            if not any(pattern in combined for pattern in patterns):
                return False
        if self.config.deny_patterns:
            patterns = [pattern.lower() for pattern in self.config.deny_patterns]
            if any(pattern in combined for pattern in patterns):
                return False
        return True

    def _should_include_title(self, title: str) -> bool:
        normalized = title.strip().lower()
        if not normalized:
            return False
        if normalized in SHORT_TITLE_SKIP_VALUES:
            return False
        if len(normalized) < 4 and NUMERIC_TITLE_REGEX.fullmatch(normalized):
            return False
        if len(normalized) < 4 and normalized in {"rss", "pdf", "doc"}:
            return False
        return True
