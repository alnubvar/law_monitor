from __future__ import annotations

from urllib.parse import urlparse

from app.sources.generic_html_source import GenericHTMLSource


class GovernmentSource(GenericHTMLSource):
    def _should_include_url(self, url: str, base_url: str, title: str) -> bool:
        if not super()._should_include_url(url, base_url, title):
            return False
        path = urlparse(url).path.lower()
        document_type = self._detect_document_type(url)
        return (
            "/docs/" in path
            or "/news/" in path
            or document_type in {"pdf", "doc", "docx", "html"}
        )
