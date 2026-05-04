from __future__ import annotations

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from app.extractors.date_extractor import normalize_date_to_iso
from app.models import SourceConfig
from app.sources.krasnodar_source import KrasnodarSource

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "html"


class KrasnodarSourceTest(unittest.TestCase):
    def _source(self, *, name: str, url: str, source_role: str) -> KrasnodarSource:
        return KrasnodarSource(
            SourceConfig(
                name=name,
                url=url,
                level="regional",
                region="krasnodar",
                source_role=source_role,  # type: ignore[arg-type]
                parser="krasnodar",
                description="fixture source",
                allow_patterns=["content", "document", "subsid", "finans", ".pdf", ".doc", ".docx"],
            )
        )

    def _items(self, source: KrasnodarSource, fixture_name: str):
        html = (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        return source._extract_items_from_soup(soup, source.config.url)

    def test_admkrai_listing_keeps_pdf_and_drops_reference_pages(self) -> None:
        source = self._source(
            name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/content/1291/",
            source_role="regional_npa",
        )

        items = self._items(source, "admkrai_content_1291_listing.html")

        self.assertEqual([item.url for item in items], ["https://admkrai.krasnodar.ru/upload/iblock/261/krasnodar-order.pdf"])

    def test_admkrai_public_consultation_listing_is_not_collected_as_document(self) -> None:
        source = self._source(
            name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/content/1291/",
            source_role="regional_npa",
        )

        items = self._items(source, "admkrai_public_consultations_listing.html")

        self.assertEqual(items, [])

    def test_admkrai_pdf_link_uses_safe_publication_date_only(self) -> None:
        source = self._source(
            name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/content/1291/",
            source_role="regional_npa",
        )

        items = self._items(source, "admkrai_pdf_link_with_date.html")

        self.assertEqual(len(items), 2)
        self.assertEqual(normalize_date_to_iso(items[0].published_at), "2026-05-02")
        self.assertIsNone(items[1].published_at)

    def test_msh_krasnodar_listing_pages_are_filtered(self) -> None:
        source = self._source(
            name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1",
            source_role="support_documents",
        )

        items = self._items(source, "msh_krasnodar_subsidirovanie_listing.html")

        self.assertEqual(items, [])

    def test_msh_krasnodar_year_listing_keeps_real_pdf(self) -> None:
        source = self._source(
            name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1",
            source_role="support_documents",
        )

        items = self._items(source, "msh_krasnodar_i2024_year_listing.html")

        self.assertEqual([item.url for item in items], ["https://msh.krasnodar.ru/upload/subsidy-order-2024.pdf"])

    def test_msh_krasnodar_real_selection_announcement_is_kept(self) -> None:
        source = self._source(
            name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1",
            source_role="support_documents",
        )

        items = self._items(source, "msh_krasnodar_real_selection_announcement.html")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, "https://msh.krasnodar.ru/documents/subsidy-open-2026")
        self.assertEqual(normalize_date_to_iso(items[0].published_at), "2026-05-03")

    def test_msh_krasnodar_pdf_and_docx_links_are_kept(self) -> None:
        source = self._source(
            name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1",
            source_role="support_documents",
        )

        items = self._items(source, "msh_krasnodar_order_subsidy_pdf_link.html")

        self.assertEqual(
            [item.document_type for item in items],
            ["pdf", "docx"],
        )
        self.assertTrue(all(normalize_date_to_iso(item.published_at) == "2026-05-04" for item in items))


if __name__ == "__main__":
    unittest.main()
