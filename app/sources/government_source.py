from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlparse

from bs4 import BeautifulSoup, Tag
from app.sources.generic_html_source import GenericHTMLSource

REAL_PAGE_PATH_RE = re.compile(r"^/(?:news|docs)/\d+/?$")
LISTING_PATH_RE = re.compile(r"^/(?:news|docs)/?$")
SERVICE_PATH_MARKERS = (
    "/rss/",
    "/search",
    "/archive",
    "/rugovclassifier/",
    "/persons/",
    "/sitemap/",
    "/about/",
    "/photos/",
    "/social/",
    "/all/rss/",
)
SERVICE_QUERY_MARKERS = (
    "dt.since",
    "dt.till",
    "search",
    "page=",
)
MAX_LISTING_PAGES = 5


class GovernmentSource(GenericHTMLSource):
    def fetch_items(self):
        items = []
        seen_urls: set[str] = set()
        visited_pages: set[str] = set()
        aggregated_stats: dict[str, int | str] = {}
        next_url = self.config.url

        for _ in range(MAX_LISTING_PAGES):
            remaining = self._remaining_item_budget(items)
            if remaining == 0:
                break
            response = self.get(next_url)
            if response.url in visited_pages:
                break
            visited_pages.add(response.url)
            soup = BeautifulSoup(response.text, "html.parser")
            page_items = self._extract_items_from_soup(
                soup,
                response.url,
                seen_urls=seen_urls,
                max_items_override=remaining,
            )
            self._merge_fetch_stats(aggregated_stats, self.last_fetch_stats)
            items.extend(page_items)
            if not page_items:
                break
            if self._remaining_item_budget(items) == 0:
                break
            next_page_url = self._next_listing_page_url(soup, response.url)
            if next_page_url is None or next_page_url in visited_pages:
                break
            next_url = next_page_url

        if aggregated_stats:
            aggregated_stats["items_collected_count"] = len(items)
            self.last_fetch_stats = aggregated_stats
        return items

    def _should_include_url(self, url: str, base_url: str, title: str) -> bool:
        if not super()._should_include_url(url, base_url, title):
            return False
        parsed = urlparse(url)
        lowered_url = url.lower()
        path = parsed.path.lower()
        query = parsed.query.lower()
        document_type = self._detect_document_type(url)
        if any(marker in lowered_url for marker in SERVICE_PATH_MARKERS):
            return False
        if query and any(marker in query for marker in SERVICE_QUERY_MARKERS):
            return False
        if REAL_PAGE_PATH_RE.fullmatch(path):
            return True
        return document_type in {"pdf", "doc", "docx"} and "/docs/" in path

    def _next_listing_page_url(self, soup: BeautifulSoup, current_url: str) -> str | None:
        current = urlparse(current_url)
        current_path = current.path.rstrip("/") + "/"
        if LISTING_PATH_RE.fullmatch(current_path) is None:
            return None
        current_page = self._page_number_from_url(current_url)
        candidates: list[tuple[int, str]] = []
        for link in soup.find_all("a", href=True):
            if not isinstance(link, Tag):
                continue
            normalized_url = self._normalize_url(link.get("href", ""), current_url)
            if not normalized_url:
                continue
            parsed = urlparse(normalized_url)
            if parsed.netloc.lower() != current.netloc.lower():
                continue
            path = parsed.path.rstrip("/") + "/"
            if path != current_path:
                continue
            page_number = self._page_number_from_url(normalized_url)
            if page_number <= current_page:
                continue
            if not self._is_safe_listing_page_query(parsed.query):
                continue
            candidates.append((page_number, normalized_url))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    @staticmethod
    def _page_number_from_url(url: str) -> int:
        parsed = urlparse(url)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if key != "page":
                continue
            try:
                return max(1, int(value))
            except ValueError:
                return 1
        return 1

    @staticmethod
    def _is_safe_listing_page_query(query: str) -> bool:
        if not query:
            return True
        pairs = parse_qsl(query, keep_blank_values=True)
        if not pairs:
            return False
        page_seen = False
        for key, value in pairs:
            if key == "page":
                if page_seen:
                    return False
                if not value.isdigit():
                    return False
                page_seen = True
                continue
            return False
        return page_seen
