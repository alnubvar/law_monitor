from __future__ import annotations

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from app.extractors.date_extractor import normalize_date_to_iso
from app.models import SourceConfig
from app.sources.government_source import GovernmentSource

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "html"


class GovernmentSourceTest(unittest.TestCase):
    def _source(self, *, name: str, url: str) -> GovernmentSource:
        return GovernmentSource(
            SourceConfig(
                name=name,
                url=url,
                level="federal",
                region="federal",
                source_role="strategy",
                parser="government",
                description="fixture source",
                allow_patterns=["/news/", "/docs/", ".pdf", ".doc", ".docx"],
            )
        )

    def _items(self, source: GovernmentSource, fixture_name: str):
        html = (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        return source._extract_items_from_soup(soup, source.config.url)

    def test_news_source_filters_rss_and_classifier_links(self) -> None:
        source = self._source(
            name="Правительство РФ - новости",
            url="http://government.ru/news/",
        )

        items = self._items(source, "government_rss_classifier_listing_page.html")

        self.assertEqual([item.url for item in items], ["http://government.ru/news/58642/"])

    def test_docs_source_filters_archive_search_and_date_query_links(self) -> None:
        source = self._source(
            name="Правительство РФ - документы",
            url="http://government.ru/docs/",
        )

        items = self._items(source, "government_archive_search_page.html")

        self.assertEqual([item.url for item in items], ["http://government.ru/docs/58316/"])

    def test_real_news_link_keeps_safe_publication_date(self) -> None:
        source = self._source(
            name="Правительство РФ - новости",
            url="http://government.ru/news/",
        )

        html = """
        <html><body>
          <article>
            <span>1 мая 2026 12:00</span>
            <a href="/news/58642/">Решения, принятые на заседании Правительства</a>
          </article>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        items = source._extract_items_from_soup(soup, source.config.url)

        self.assertEqual(len(items), 1)
        self.assertEqual(normalize_date_to_iso(items[0].published_at), "2026-05-01")


if __name__ == "__main__":
    unittest.main()
