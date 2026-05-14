from __future__ import annotations

import unittest
from urllib.parse import quote

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
            source_role="strategy",
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

    def test_gisp_parser_keeps_measure_links_and_drops_ui_noise(self) -> None:
        config = SourceConfig(
            name="ГИСП - меры поддержки АПК",
            url=f"https://gisp.gov.ru/nmp/main/1?recommended=0&searchstr={quote('АПК')}",
            level="support_measures",
            region="federal",
            source_role="active_support_measures",
            parser="generic_html",
            description="gisp source",
            max_items=20,
            allow_patterns=["nmp", "support", "measure", "apk"],
            deny_patterns=["recommended=", "page="],
        )
        source = GenericHTMLSource(config)
        html = """
        <html><body>
          <a href="/nmp/measure/9564204">Льготное кредитование АПК</a>
          <a href="/nmp/compare/">Сравнить</a>
          <a href="/nmp/sso?BACKURL=https://gisp.gov.ru/nmp/measure/9512857">Войти</a>
          <a href="/nmp/main/2?recommended=0&searchstr=%D0%90%D0%9F%D0%9A">Следующая страница</a>
          <a href="/nmp/main/1?recommended=1">Рекомендованные меры</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")

        items = source._extract_items_from_soup(soup, config.url)

        self.assertEqual([item.url for item in items], ["https://gisp.gov.ru/nmp/measure/9564204"])

    def test_gisp_fetch_items_uses_safe_bounded_pagination(self) -> None:
        config = SourceConfig(
            name="ГИСП - меры поддержки АПК",
            url=f"https://gisp.gov.ru/nmp/main/1?recommended=0&searchstr={quote('АПК')}",
            level="support_measures",
            region="federal",
            source_role="active_support_measures",
            parser="generic_html",
            description="gisp source",
            max_items=3,
            allow_patterns=["nmp", "support", "measure", "apk"],
            deny_patterns=["recommended=", "page="],
        )
        source = GenericHTMLSource(config)
        page_one_url = config.url
        page_two_url = f"https://gisp.gov.ru/nmp/main/2?recommended=0&searchstr={quote('АПК')}"
        calls: list[str] = []

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        page_one_html = """
        <html><body>
          <a href="/nmp/compare/">Сравнить</a>
          <a href="/nmp/sso?BACKURL=https://gisp.gov.ru/nmp/measure/9512857">Войти</a>
          <a href="/nmp/measure/9564204">Льготное кредитование АПК</a>
        </body></html>
        """
        page_two_html = """
        <html><body>
          <a href="/nmp/measure/9512857">Поддержка экспорта</a>
          <a href="/nmp/measure/12446930">Субсидия на кооперацию</a>
          <a href="/nmp/main/3?recommended=0&searchstr=%D0%90%D0%9F%D0%9A">Следующая</a>
        </body></html>
        """

        def fake_get(url: str) -> Response:
            calls.append(url)
            if url == page_one_url:
                return Response(page_one_html, page_one_url)
            if url == page_two_url:
                return Response(page_two_html, page_two_url)
            raise AssertionError(f"Unexpected page fetch: {url}")

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        self.assertEqual(
            [item.url for item in items],
            [
                "https://gisp.gov.ru/nmp/measure/9564204",
                "https://gisp.gov.ru/nmp/measure/9512857",
                "https://gisp.gov.ru/nmp/measure/12446930",
            ],
        )
        self.assertEqual(calls, [page_one_url, page_two_url])

    def test_non_gisp_generic_parser_does_not_paginate(self) -> None:
        config = SourceConfig(
            name="Test",
            url="https://example.com/docs/",
            level="federal",
            region="federal",
            source_role="strategy",
            parser="generic_html",
            description="test source",
            max_items=10,
            allow_patterns=["/docs/"],
        )
        source = GenericHTMLSource(config)
        calls: list[str] = []

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str) -> Response:
            calls.append(url)
            return Response('<html><body><a href="/docs/123">Документ</a></body></html>', url)

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        self.assertEqual([item.url for item in items], ["https://example.com/docs/123"])
        self.assertEqual(calls, ["https://example.com/docs/"])

    def test_non_gisp_page_query_remains_filtered(self) -> None:
        config = SourceConfig(
            name="ZOL-like Test",
            url="https://example.com/news/",
            level="news",
            region="federal",
            source_role="news_signals",
            parser="generic_html",
            description="test source",
            max_items=10,
            allow_patterns=["/n/"],
        )
        source = GenericHTMLSource(config)
        html = """
        <html><body>
          <a href="/n/123">Целевая новость</a>
          <a href="/news/?page=2">Следующая страница</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "html.parser")

        items = source._extract_items_from_soup(soup, config.url)

        self.assertEqual([item.url for item in items], ["https://example.com/n/123"])


if __name__ == "__main__":
    unittest.main()
