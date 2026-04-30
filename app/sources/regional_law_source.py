from __future__ import annotations

from urllib.parse import urlparse

from app.sources.generic_html_source import GenericHTMLSource

LAW_PATH_HINTS = (
    "document",
    "docs",
    "pravo",
    "law",
    "zakon",
    "prikaz",
    "postanov",
    "rasporyaz",
    "content",
)


class RegionalLawSource(GenericHTMLSource):
    def _should_include_url(self, url: str, base_url: str, title: str) -> bool:
        if not super()._should_include_url(url, base_url, title):
            return False
        path = urlparse(url).path.lower()
        document_type = self._detect_document_type(url)
        return document_type in {"pdf", "doc", "docx", "html"} or any(
            hint in path for hint in LAW_PATH_HINTS
        )
