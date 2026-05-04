from __future__ import annotations

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from app.extractors.date_extractor import normalize_date_to_iso
from app.models import SourceConfig
from app.sources.stavropol_source import StavropolSource

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "html"


class StavropolSourceTest(unittest.TestCase):
    def _source(
        self,
        *,
        name: str,
        url: str,
        source_role: str,
        region: str,
    ) -> StavropolSource:
        return StavropolSource(
            SourceConfig(
                name=name,
                url=url,
                level="regional",
                region=region,  # type: ignore[arg-type]
                source_role=source_role,  # type: ignore[arg-type]
                parser="stavropol",
                description="fixture source",
                allow_patterns=[
                    "gospod",
                    "subsid",
                    "grant",
                    "document",
                    "law",
                    "pravo",
                    ".pdf",
                    ".doc",
                    ".docx",
                ],
            )
        )

    def _items(self, source: StavropolSource, fixture_name: str):
        html = (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        return source._extract_items_from_soup(soup, source.config.url)

    def test_mshsk_listing_reference_pages_are_filtered(self) -> None:
        source = self._source(
            name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/gospodderzhka/",
            source_role="support_documents",
            region="stavropol",
        )

        items = self._items(source, "mshsk_listing_reference_page.html")

        self.assertEqual(items, [])

    def test_mshsk_real_selection_announcement_is_kept(self) -> None:
        source = self._source(
            name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/gospodderzhka/",
            source_role="support_documents",
            region="stavropol",
        )

        items = self._items(source, "mshsk_real_selection_announcement.html")

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].url, "https://mshsk.ru/gospodderzhka/selection-berry-2026.php")
        self.assertEqual(items[1].document_type, "pdf")
        self.assertEqual(normalize_date_to_iso(items[0].published_at), "2026-01-30")
        self.assertEqual(normalize_date_to_iso(items[1].published_at), "2026-01-24")

    def test_mshsk_publication_date_is_not_confused_with_deadline(self) -> None:
        source = self._source(
            name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/gospodderzhka/",
            source_role="support_documents",
            region="stavropol",
        )

        items = self._items(source, "mshsk_real_selection_announcement.html")

        self.assertEqual(normalize_date_to_iso(items[0].published_at), "2026-01-30")

    def test_pravo_stavregion_listing_reference_pages_are_filtered(self) -> None:
        source = self._source(
            name="Право Ставропольского края",
            url="https://pravo.stavregion.ru/",
            source_role="regional_npa",
            region="stavropol",
        )

        items = self._items(source, "pravo_stavregion_listing_reference_page.html")

        self.assertEqual(items, [])

    def test_pravo_stavregion_real_npa_html_and_docx_are_kept(self) -> None:
        source = self._source(
            name="Право Ставропольского края",
            url="https://pravo.stavregion.ru/",
            source_role="regional_npa",
            region="stavropol",
        )

        items = self._items(source, "pravo_stavregion_real_npa_links.html")

        self.assertEqual([item.document_type for item in items], ["html", "docx"])
        self.assertEqual(
            [item.url for item in items],
            [
                "https://pravo.stavregion.ru/document/98765",
                "https://pravo.stavregion.ru/files/prikaz-subsidii.docx",
            ],
        )
        self.assertEqual(normalize_date_to_iso(items[0].published_at), "2026-04-29")
        self.assertEqual(normalize_date_to_iso(items[1].published_at), "2026-04-30")

    def test_pravo_stavregion_publication_date_is_not_confused_with_order_date(self) -> None:
        source = self._source(
            name="Право Ставропольского края",
            url="https://pravo.stavregion.ru/",
            source_role="regional_npa",
            region="stavropol",
        )

        items = self._items(source, "pravo_stavregion_real_npa_links.html")

        self.assertEqual(normalize_date_to_iso(items[0].published_at), "2026-04-29")


if __name__ == "__main__":
    unittest.main()
