from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from app.models import SourceConfig
from app.sources.generic_html_source import GenericHTMLSource


class LinkFilteringSmokeTest(unittest.TestCase):
    def test_generic_parser_filters_noise_and_respects_max_items(self) -> None:
        config = SourceConfig(
            name="Test",
            url="https://example.com/docs/",
            level="federal",
            region="federal",
            parser="generic_html",
            description="test source",
            max_items=2,
            allow_patterns=["/docs/", ".pdf"],
            deny_patterns=["archive"],
        )
        source = GenericHTMLSource(config)
        html = """
        <html><body>
          <a href="/docs/123">Поддержка АПК</a>
          <a href="/about">About</a>
          <a href="/docs/archive">Архив</a>
          <a href="/docs/report.pdf">Отчет по субсидиям</a>
          <a href="/docs/1">1</a>
          <a href="/docs/more">Показать еще</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")

        items = source._extract_items_from_soup(soup, config.url)

        self.assertEqual(len(items), 2)
        urls = [item.url for item in items]
        self.assertIn("https://example.com/docs/123", urls)
        self.assertIn("https://example.com/docs/report.pdf", urls)


if __name__ == "__main__":
    unittest.main()
