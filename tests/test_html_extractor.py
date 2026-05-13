from __future__ import annotations

import unittest
from unittest.mock import patch

from app.extractors.html_extractor import extract_text_from_html


class FakeResponse:
    def __init__(self, body: bytes, content_type: str, url: str = "https://example.test/page.php") -> None:
        self._body = body
        self.headers = {"Content-Type": content_type}
        self.url = url
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 8192, decode_unicode: bool = False):
        del chunk_size, decode_unicode
        yield self._body


class HtmlExtractorTest(unittest.TestCase):
    def test_php_html_response_extracts_text(self) -> None:
        response = FakeResponse(
            (
                "<html><body><main>"
                "<h1>Объявление об отборе на субсидии</h1>"
                "<p>Прием заявок открыт для сельхозтоваропроизводителей.</p>"
                "</main></body></html>"
            ).encode("utf-8"),
            "text/html; charset=utf-8",
        )

        with patch("app.extractors.html_extractor.requests.get", return_value=response):
            result = extract_text_from_html("https://mshsk.ru/gospodderzhka/selection-berry-2026.php")

        self.assertEqual(result.document_type, "html")
        self.assertGreater(len(result.raw_text), 0)
        self.assertIn("Объявление об отборе", result.raw_text)

    def test_php_binary_response_is_not_treated_as_html(self) -> None:
        response = FakeResponse(b"%PDF-1.7 binary payload", "application/octet-stream")

        with patch("app.extractors.html_extractor.requests.get", return_value=response):
            result = extract_text_from_html("https://mshsk.ru/gospodderzhka/download.php?id=1")

        self.assertEqual(result.document_type, "unknown")
        self.assertEqual(result.raw_text, "")
        self.assertIn("Unsupported content type", result.error or "")


if __name__ == "__main__":
    unittest.main()
