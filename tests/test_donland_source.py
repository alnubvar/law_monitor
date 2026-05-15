from __future__ import annotations

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from app.extractors.date_extractor import normalize_date_to_iso
from app.models import SourceConfig
from app.sources.donland_source import DonlandSource

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "html"


class DonlandSourceTest(unittest.TestCase):
    def _source(
        self,
        *,
        name: str,
        url: str,
        source_role: str,
        region: str,
        max_items: int | None = None,
        deny_patterns: list[str] | None = None,
    ) -> DonlandSource:
        return DonlandSource(
            SourceConfig(
                name=name,
                url=url,
                level="regional",
                region=region,  # type: ignore[arg-type]
                source_role=source_role,  # type: ignore[arg-type]
                parser="donland",
                description="fixture source",
                allow_patterns=[
                    "/activity/",
                    "/documents/",
                    "/presscenter/events/",
                    "doc",
                    "support",
                    "subsid",
                    ".pdf",
                    ".doc",
                    ".docx",
                ],
                deny_patterns=deny_patterns or [],
                max_items=max_items,
            )
        )

    def _items(self, source: DonlandSource, fixture_name: str):
        html = (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        return source._extract_items_from_soup(soup, source.config.url)

    def test_mcx_donland_listing_and_reference_pages_are_filtered(self) -> None:
        source = self._source(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            source_role="support_documents",
            region="rostov",
        )

        items = self._items(source, "mcx_donland_listing_reference_page.html")

        self.assertEqual(items, [])

    def test_mcx_donland_real_selection_announcement_is_kept(self) -> None:
        source = self._source(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            source_role="support_documents",
            region="rostov",
            deny_patterns=["/request/", "/presscenter/video", "/documents/all/"],
        )

        items = self._items(source, "mcx_donland_real_selection_announcement.html")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, "https://mcx.donland.ru/presscenter/events/72822/")
        self.assertEqual(normalize_date_to_iso(items[0].published_at), "2026-04-28")

    def test_mcx_donland_root_support_page_traverses_curated_categories(self) -> None:
        source = self._source(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            source_role="support_documents",
            region="rostov",
            deny_patterns=["/request/", "/presscenter/video", "/documents/all/"],
        )
        root_html = (FIXTURES_DIR / "mcx_donland_support_root_with_categories.html").read_text(encoding="utf-8")
        nested_html = (FIXTURES_DIR / "mcx_donland_support_category_nested_page.html").read_text(encoding="utf-8")
        fetched_urls: list[str] = []

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str):
            fetched_urls.append(url)
            if url == source.config.url:
                return Response(root_html, source.config.url)
            if url in {
                "https://mcx.donland.ru/activity/37370/",
                "https://mcx.donland.ru/activity/37371/",
            }:
                return Response(nested_html, url)
            raise AssertionError(f"Unexpected fetch: {url}")

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        self.assertEqual(
            [item.url for item in items],
            [
                "https://mcx.donland.ru/presscenter/events/72822/",
                "https://mcx.donland.ru/files/poryadok-subsidii.docx",
            ],
        )
        self.assertEqual(fetched_urls.count("https://mcx.donland.ru/activity/37370/"), 1)
        self.assertEqual(fetched_urls.count("https://mcx.donland.ru/activity/37371/"), 1)
        self.assertEqual(source.last_fetch_stats["traversed_page_count"], 2)

    def test_mcx_donland_traversal_deduplicates_and_obeys_max_items(self) -> None:
        source = self._source(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            source_role="support_documents",
            region="rostov",
            max_items=2,
            deny_patterns=["/request/", "/presscenter/video", "/documents/all/"],
        )
        root_html = """
        <html><body>
          <a href="/presscenter/events/72822/">Объявление о проведении отбора на субсидию</a>
          <a href="/activity/37370/">Животноводство</a>
          <a href="/activity/37371/">Растениеводство</a>
        </body></html>
        """
        nested_html = """
        <html><body>
          <span>Дата публикации: 02.05.2026</span>
          <a href="/presscenter/events/72822/">Объявление о проведении отбора на субсидию</a>
          <a href="/files/poryadok-subsidii.docx">Порядок предоставления субсидии</a>
        </body></html>
        """
        fetched_urls: list[str] = []

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str):
            fetched_urls.append(url)
            if url == source.config.url:
                return Response(root_html, source.config.url)
            if url == "https://mcx.donland.ru/activity/37370/":
                return Response(nested_html, url)
            raise AssertionError(f"Unexpected fetch: {url}")

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        self.assertEqual(len(items), 2)
        self.assertEqual(
            [item.url for item in items],
            [
                "https://mcx.donland.ru/presscenter/events/72822/",
                "https://mcx.donland.ru/files/poryadok-subsidii.docx",
            ],
        )
        self.assertEqual(fetched_urls, [source.config.url, "https://mcx.donland.ru/activity/37370/"])

    def test_pravo_donland_listing_search_and_reference_pages_are_filtered(self) -> None:
        source = self._source(
            name="Право Ростовской области",
            url="https://pravo.donland.ru/",
            source_role="regional_npa",
            region="rostov",
        )

        items = self._items(source, "pravo_donland_listing_reference_page.html")

        self.assertEqual(items, [])

    def test_pravo_donland_real_npa_html_and_docx_are_kept(self) -> None:
        source = self._source(
            name="Право Ростовской области",
            url="https://pravo.donland.ru/",
            source_role="regional_npa",
            region="rostov",
        )

        items = self._items(source, "pravo_donland_real_npa_links.html")

        self.assertEqual([item.document_type for item in items], ["html", "docx"])
        self.assertEqual(
            [item.url for item in items],
            [
                "https://pravo.donland.ru/doc/view/id/Постановление_42_29042026_60001/",
                "https://pravo.donland.ru/files/postanovlenie-subsidii-apk.docx",
            ],
        )
        self.assertEqual(normalize_date_to_iso(items[0].published_at), "2026-04-29")
        self.assertEqual(normalize_date_to_iso(items[1].published_at), "2026-04-30")

    def test_pravo_donland_publication_date_is_not_confused_with_order_date(self) -> None:
        source = self._source(
            name="Право Ростовской области",
            url="https://pravo.donland.ru/",
            source_role="regional_npa",
            region="rostov",
        )

        items = self._items(source, "pravo_donland_real_npa_links.html")

        self.assertEqual(normalize_date_to_iso(items[0].published_at), "2026-04-29")

    def test_pravo_donland_pdf_is_kept_even_with_generic_download_title(self) -> None:
        source = self._source(
            name="Право Ростовской области",
            url="https://pravo.donland.ru/",
            source_role="regional_npa",
            region="rostov",
        )
        soup = BeautifulSoup(
            '<a href="/files/prikaz-apk-2026.pdf">Скачать файл</a>',
            "html.parser",
        )
        items = source._extract_items_from_soup(soup, source.config.url)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].document_type, "pdf")
        self.assertEqual(items[0].url, "https://pravo.donland.ru/files/prikaz-apk-2026.pdf")

    def test_mcx_donland_publication_date_is_not_confused_with_deadline(self) -> None:
        source = self._source(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            source_role="support_documents",
            region="rostov",
        )

        items = self._items(source, "mcx_donland_real_selection_announcement.html")

        self.assertEqual(normalize_date_to_iso(items[0].published_at), "2026-04-28")

    def test_mcx_donland_rejects_malformed_embedded_foreign_host_url(self) -> None:
        source = self._source(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            source_role="support_documents",
            region="rostov",
            deny_patterns=["/request/", "/presscenter/video", "/documents/all/"],
        )
        soup = BeautifulSoup(
            """
            <a href="/activity/37368/edit/publication.pravo.gov.ru/document/6100202401170016">
              Обновлены правила поддержки
            </a>
            """,
            "html.parser",
        )

        items = source._extract_items_from_soup(soup, source.config.url)

        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
