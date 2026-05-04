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
        )

        items = self._items(source, "mcx_donland_real_selection_announcement.html")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, "https://mcx.donland.ru/presscenter/events/72822/")
        self.assertEqual(normalize_date_to_iso(items[0].published_at), "2026-04-28")

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

    def test_mcx_donland_publication_date_is_not_confused_with_deadline(self) -> None:
        source = self._source(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            source_role="support_documents",
            region="rostov",
        )

        items = self._items(source, "mcx_donland_real_selection_announcement.html")

        self.assertEqual(normalize_date_to_iso(items[0].published_at), "2026-04-28")


if __name__ == "__main__":
    unittest.main()
