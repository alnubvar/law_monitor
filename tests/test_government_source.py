from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_fetch_news_follows_safe_listing_pagination(self) -> None:
        source = self._source(
            name="Правительство РФ - новости",
            url="http://government.ru/news/",
        )
        page_one = """
        <html><body>
          <main>
            <article><a href="/news/58642/">Решения по АПК</a></article>
            <a href="/news/?page=2">Следующая</a>
            <a href="/archive/">Архив</a>
          </main>
        </body></html>
        """
        page_two = """
        <html><body>
          <main>
            <article><a href="/news/58643/">Новая повестка</a></article>
            <a href="/news/?page=3">Следующая</a>
          </main>
        </body></html>
        """
        page_three = """
        <html><body>
          <main>
            <a href="/news/?page=4">Следующая</a>
            <a href="/news/58643/">Новая повестка</a>
          </main>
        </body></html>
        """

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str):
            if url == "http://government.ru/news/":
                return Response(page_one, url)
            if url == "http://government.ru/news/?page=2":
                return Response(page_two, url)
            if url == "http://government.ru/news/?page=3":
                return Response(page_three, url)
            raise AssertionError(f"Unexpected URL: {url}")

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        self.assertEqual(
            [item.url for item in items],
            ["http://government.ru/news/58642/", "http://government.ru/news/58643/"],
        )

    def test_fetch_docs_respects_max_items_during_pagination(self) -> None:
        source = self._source(
            name="Правительство РФ - документы",
            url="http://government.ru/docs/",
        )
        source.config.max_items = 2
        page_one = """
        <html><body>
          <main>
            <a href="/docs/58316/">Постановление №362</a>
            <a href="/docs/?page=2">Следующая</a>
          </main>
        </body></html>
        """
        page_two = """
        <html><body>
          <main>
            <a href="/docs/58317/">Постановление №363</a>
            <a href="/docs/?page=3">Следующая</a>
          </main>
        </body></html>
        """

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str):
            if url == "http://government.ru/docs/":
                return Response(page_one, url)
            if url == "http://government.ru/docs/?page=2":
                return Response(page_two, url)
            raise AssertionError(f"Unexpected URL: {url}")

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        self.assertEqual(
            [item.url for item in items],
            ["http://government.ru/docs/58316/", "http://government.ru/docs/58317/"],
        )

    def test_fetch_stops_when_next_page_has_no_new_valid_urls(self) -> None:
        source = self._source(
            name="Правительство РФ - новости",
            url="http://government.ru/news/",
        )
        page_one = """
        <html><body>
          <main>
            <a href="/news/58642/">Решения по АПК</a>
            <a href="/news/?page=2">Следующая</a>
          </main>
        </body></html>
        """
        page_two = """
        <html><body>
          <main>
            <a href="/news/58642/">Решения по АПК</a>
            <a href="/archive/">Архив</a>
            <a href="/search/">Поиск</a>
          </main>
        </body></html>
        """

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        with patch.object(source, "get", side_effect=[Response(page_one, "http://government.ru/news/"), Response(page_two, "http://government.ru/news/?page=2")]) as mock_get:
            items = source.fetch_items()

        self.assertEqual([item.url for item in items], ["http://government.ru/news/58642/"])
        self.assertEqual(mock_get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
