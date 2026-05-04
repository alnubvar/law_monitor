from __future__ import annotations

import re
from urllib.parse import urlparse

from app.sources.generic_html_source import GenericHTMLSource

REAL_PAGE_PATH_RE = re.compile(r"^/(?:news|docs)/\d+/?$")
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


class GovernmentSource(GenericHTMLSource):
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
