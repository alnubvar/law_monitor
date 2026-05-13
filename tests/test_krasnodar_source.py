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

    def _response(self, text: str, url: str):
        class Response:
            def __init__(self, response_text: str, response_url: str) -> None:
                self.text = response_text
                self.url = response_url

        return Response(text, url)

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

    def test_msh_krasnodar_fetch_harvests_direct_attachments_from_accepted_listing(self) -> None:
        source = self._source(
            name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1",
            source_role="support_documents",
        )
        source.config.max_items = 10
        root_html = """
        <html><body>
          <a href="/documents/prikazy-minselkhoza-krasnodarskogo-kraya">Приказы минсельхоза Краснодарского края</a>
          <a href="/contacts/">Контакты</a>
        </body></html>
        """
        listing_url = "https://msh.krasnodar.ru/documents/prikazy-minselkhoza-krasnodarskogo-kraya"
        listing_html = (FIXTURES_DIR / "msh_krasnodar_prikazy_listing_with_attachments.html").read_text(
            encoding="utf-8"
        )
        calls: list[str] = []

        def fake_get(url: str):
            calls.append(url)
            if url == source.config.url:
                return self._response(root_html, source.config.url)
            if url == listing_url:
                return self._response(listing_html, listing_url)
            raise AssertionError(f"Unexpected recursive fetch: {url}")

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        self.assertEqual(calls, [source.config.url, listing_url])
        self.assertEqual(
            [item.document_type for item in items],
            ["html", "pdf", "pdf", "pdf"],
        )
        self.assertEqual(items[0].url, listing_url)
        self.assertIsNone(items[0].published_at)
        self.assertEqual(
            [item.url for item in items[1:]],
            [
                "https://npa.krasnodar.ru/rest/files/1233707",
                "https://npa.krasnodar.ru/rest/files/1233677",
                "https://npa.krasnodar.ru/rest/files/1233654",
            ],
        )
        self.assertIn("Порядка предоставления субсидий", items[1].title)
        self.assertIn("гранта «Агротуризм»", items[2].title)
        self.assertIn("крестьянским (фермерским)", items[3].title)
        self.assertEqual(normalize_date_to_iso(items[1].published_at), "2026-04-30")
        self.assertEqual(normalize_date_to_iso(items[2].published_at), "2026-04-29")
        self.assertEqual(normalize_date_to_iso(items[3].published_at), "2026-04-28")
        self.assertNotIn(
            "https://msh.krasnodar.ru/documents/prikazy-minselkhoza-krasnodarskogo-kraya/page2",
            {item.url for item in items},
        )
        self.assertNotIn(
            "https://msh.krasnodar.ru/documents/prikazy-minselkhoza-krasnodarskogo-kraya/159222",
            {item.url for item in items},
        )
        self.assertNotIn("https://msh.krasnodar.ru/contacts/", {item.url for item in items})
        self.assertEqual(source.last_fetch_stats["harvested_attachment_count"], 3)

    def test_msh_krasnodar_document_item_ignores_referenced_internal_date(self) -> None:
        source = self._source(
            name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1",
            source_role="support_documents",
        )
        source.config.max_items = 10
        root_html = """
        <html><body>
          <a href="/documents/prikazy-minselkhoza-krasnodarskogo-kraya">Приказы минсельхоза Краснодарского края</a>
        </body></html>
        """
        listing_url = "https://msh.krasnodar.ru/documents/prikazy-minselkhoza-krasnodarskogo-kraya"
        listing_html = """
        <html><body>
          <div class="document-item">
            <a class="document-item__title" href="/documents/prikazy-minselkhoza-krasnodarskogo-kraya/1">
              О внесении изменений в приказ министерства сельского хозяйства Краснодарского края
              от 01 апреля 2026 г. № 114 «Об утверждении Порядка предоставления субсидий»
            </a>
            <span class="document-info-bar__type">pdf</span>
            <a class="document-info-bar__download-link" href="https://npa.krasnodar.ru/rest/files/1233999">
              <span class="document-info-bar__download-text">скачать документ</span>
            </a>
          </div>
        </body></html>
        """

        def fake_get(url: str):
            if url == source.config.url:
                return self._response(root_html, source.config.url)
            if url == listing_url:
                return self._response(listing_html, listing_url)
            raise AssertionError(f"Unexpected recursive fetch: {url}")

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        self.assertIsNone(items[0].published_at)
        npa_items = [item for item in items if item.url == "https://npa.krasnodar.ru/rest/files/1233999"]
        self.assertEqual(len(npa_items), 1)
        self.assertIsNone(npa_items[0].published_at)


if __name__ == "__main__":
    unittest.main()
