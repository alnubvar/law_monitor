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
                allow_patterns=["content", "document", "subsid", "finans", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip"],
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
        # Fixture has a /page2 link; page2 is empty so traversal stops after it.
        page2_url = "https://msh.krasnodar.ru/documents/prikazy-minselkhoza-krasnodarskogo-kraya/page2"
        page2_html = "<html><body></body></html>"
        calls: list[str] = []

        def fake_get(url: str):
            calls.append(url)
            if url == source.config.url:
                return self._response(root_html, source.config.url)
            if url == listing_url:
                return self._response(listing_html, listing_url)
            if url == page2_url:
                return self._response(page2_html, page2_url)
            raise AssertionError(f"Unexpected recursive fetch: {url}")

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        self.assertEqual(calls, [source.config.url, listing_url, page2_url])
        self.assertEqual(
            [item.document_type for item in items],
            ["pdf", "pdf", "pdf", "html"],
        )
        self.assertEqual(
            [item.url for item in items[:3]],
            [
                "https://npa.krasnodar.ru/rest/files/1233707",
                "https://npa.krasnodar.ru/rest/files/1233677",
                "https://npa.krasnodar.ru/rest/files/1233654",
            ],
        )
        self.assertIn("Порядка предоставления субсидий", items[0].title)
        self.assertIn("гранта «Агротуризм»", items[1].title)
        self.assertIn("крестьянским (фермерским)", items[2].title)
        self.assertEqual(normalize_date_to_iso(items[0].published_at), "2026-04-30")
        self.assertEqual(normalize_date_to_iso(items[1].published_at), "2026-04-29")
        self.assertEqual(normalize_date_to_iso(items[2].published_at), "2026-04-28")
        self.assertEqual(items[3].url, listing_url)
        self.assertIsNone(items[3].published_at)
        self.assertNotIn(page2_url, {item.url for item in items})
        self.assertNotIn(
            "https://msh.krasnodar.ru/documents/prikazy-minselkhoza-krasnodarskogo-kraya/159222",
            {item.url for item in items},
        )
        self.assertNotIn("https://msh.krasnodar.ru/contacts/", {item.url for item in items})
        self.assertEqual(source.last_fetch_stats["seed_pages_found"], 1)
        self.assertEqual(source.last_fetch_stats["seed_pages_visited"], 1)
        self.assertEqual(source.last_fetch_stats["pagination_pages_visited"], 1)
        self.assertEqual(source.last_fetch_stats["harvested_attachment_count"], 3)

    def test_msh_krasnodar_fetch_harvests_pagen_1_attachments(self) -> None:
        """Verify that PAGEN_1 page2+ listing pages are traversed and their attachments collected."""
        source = self._source(
            name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1",
            source_role="support_documents",
        )
        source.config.max_items = 10
        root_html = """
        <html><body>
          <a href="/documents/prikazy-minselkhoza-krasnodarskogo-kraya">Приказы минсельхоза</a>
        </body></html>
        """
        listing_url = "https://msh.krasnodar.ru/documents/prikazy-minselkhoza-krasnodarskogo-kraya"
        # Real Bitrix pagination href includes MENU_CODE_PATH and MUL_MODE alongside PAGEN_1
        page2_url = (
            "https://msh.krasnodar.ru/documents/prikazy-minselkhoza-krasnodarskogo-kraya"
            "?MENU_CODE_PATH=documents%2Fprikazy-minselkhoza-krasnodarskogo-kraya&MUL_MODE=&PAGEN_1=2"
        )

        page1_html = """
        <html><body>
          <div class="document-item">
            <div class="document-item__title-wrap">
              <a class="document-item__title" href="/documents/prikazy/159300">
                №170 от 07.05.2026 "Об утверждении Порядка предоставления субсидий фермерским хозяйствам"
                <div class="document-item-extra-info">Вид документа: Приказ;</div>
              </a>
            </div>
            <span class="document-info-bar__type">pdf</span>
            <a class="document-info-bar__download-link" href="https://npa.krasnodar.ru/rest/files/1234100">
              <span class="document-info-bar__download-text">скачать документ</span>
            </a>
          </div>
          <a href="/documents/prikazy-minselkhoza-krasnodarskogo-kraya?MENU_CODE_PATH=documents%2Fprikazy-minselkhoza-krasnodarskogo-kraya&amp;MUL_MODE=&amp;PAGEN_1=2">Следующая</a>
        </body></html>
        """
        page2_html = """
        <html><body>
          <div class="document-item">
            <div class="document-item__title-wrap">
              <a class="document-item__title" href="/documents/prikazy/159200">
                №160 от 27.04.2026 "О внесении изменений в приказ об утверждении Порядка предоставления субсидий"
                <div class="document-item-extra-info">Вид документа: Приказ;</div>
              </a>
            </div>
            <span class="document-info-bar__type">pdf</span>
            <a class="document-info-bar__download-link" href="https://npa.krasnodar.ru/rest/files/1234050">
              <span class="document-info-bar__download-text">скачать документ</span>
            </a>
          </div>
        </body></html>
        """
        calls: list[str] = []

        def fake_get(url: str):
            calls.append(url)
            if url == source.config.url:
                return self._response(root_html, source.config.url)
            if url == listing_url:
                return self._response(page1_html, listing_url)
            if url == page2_url:
                return self._response(page2_html, page2_url)
            raise AssertionError(f"Unexpected fetch: {url}")

        source.get = fake_get  # type: ignore[method-assign]
        items = source.fetch_items()

        self.assertEqual(calls, [source.config.url, listing_url, page2_url])
        urls = [item.url for item in items]
        self.assertIn("https://npa.krasnodar.ru/rest/files/1234100", urls)
        self.assertIn("https://npa.krasnodar.ru/rest/files/1234050", urls)
        self.assertEqual(urls[-1], listing_url)
        self.assertEqual(len(items), 3)
        self.assertEqual(source.last_fetch_stats["harvested_attachment_count"], 2)
        self.assertEqual(source.last_fetch_stats["pagination_pages_visited"], 1)
        self.assertEqual(normalize_date_to_iso(items[0].published_at), "2026-05-07")
        self.assertEqual(normalize_date_to_iso(items[1].published_at), "2026-04-27")

    def test_msh_krasnodar_fetch_traverses_year_pages_and_skips_archive(self) -> None:
        source = self._source(
            name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1",
            source_role="support_documents",
        )
        source.config.max_items = 10
        root_html = """
        <html><body>
          <a href="/documents/subsidirovanie-i-finansirovanie1/i2026">2026</a>
          <a href="/documents/subsidirovanie-i-finansirovanie1/i2025">2025</a>
          <a href="/documents/subsidirovanie-i-finansirovanie1/i2024">2024</a>
          <a href="/documents/subsidirovanie-i-finansirovanie1/i2023">2023</a>
          <a href="/documents/subsidirovanie-i-finansirovanie1/arkhiv-subs">Архив</a>
        </body></html>
        """
        year_urls = {
            "https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1/i2026": "<html><body></body></html>",
            "https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1/i2025": "<html><body></body></html>",
            "https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1/i2024": """
            <html><body>
              <a href="/upload/subsidy-order-2024.pdf">Приказ о субсидиях</a>
            </body></html>
            """,
            "https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1/i2023": "<html><body></body></html>",
        }
        archive_url = "https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1/arkhiv-subs"
        calls: list[str] = []

        def fake_get(url: str):
            calls.append(url)
            if url == source.config.url:
                return self._response(root_html, source.config.url)
            if url in year_urls:
                return self._response(year_urls[url], url)
            if url == archive_url:
                raise AssertionError("Archive must not be traversed in S1")
            raise AssertionError(f"Unexpected fetch: {url}")

        source.get = fake_get  # type: ignore[method-assign]
        items = source.fetch_items()

        self.assertEqual(
            calls,
            [
                source.config.url,
                "https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1/i2026",
                "https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1/i2025",
                "https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1/i2024",
                "https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1/i2023",
            ],
        )
        self.assertEqual([item.url for item in items], ["https://msh.krasnodar.ru/upload/subsidy-order-2024.pdf"])
        self.assertNotIn(archive_url, {item.url for item in items})
        self.assertEqual(source.last_fetch_stats["seed_pages_found"], 4)
        self.assertEqual(source.last_fetch_stats["seed_pages_visited"], 4)

    def test_msh_krasnodar_fetch_keeps_pdf_doc_docx_xls_xlsx_zip_links(self) -> None:
        source = self._source(
            name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1",
            source_role="support_documents",
        )
        source.config.max_items = 10
        year_url = "https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1/i2024"
        root_html = f'<html><body><a href="{year_url}">2024</a></body></html>'
        year_html = """
        <html><body>
          <a href="/upload/poryadok-subsidii.pdf">Порядок предоставления субсидий PDF</a>
          <a href="/upload/forma-zayavki.doc">Форма заявки DOC</a>
          <a href="/upload/paket-dokumentov.docx">Пакет документов DOCX</a>
          <a href="/upload/reestr-uchastnikov.xls">Реестр участников XLS</a>
          <a href="/upload/shablon-rascheta.xlsx">Шаблон расчета XLSX</a>
          <a href="/upload/komplekt-form.zip">Комплект форм ZIP</a>
        </body></html>
        """

        def fake_get(url: str):
            if url == source.config.url:
                return self._response(root_html, source.config.url)
            if url == year_url:
                return self._response(year_html, year_url)
            raise AssertionError(f"Unexpected fetch: {url}")

        source.get = fake_get  # type: ignore[method-assign]
        items = source.fetch_items()

        self.assertEqual(
            [item.document_type for item in items],
            ["pdf", "doc", "docx", "xls", "xlsx", "zip"],
        )
        self.assertEqual(source.last_fetch_stats["xls_links_count"], 1)
        self.assertEqual(source.last_fetch_stats["xlsx_links_count"], 1)
        self.assertEqual(source.last_fetch_stats["zip_links_count"], 1)

    def test_msh_krasnodar_fetch_suppresses_duplicate_attachments_across_seed_pages(self) -> None:
        source = self._source(
            name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1",
            source_role="support_documents",
        )
        source.config.max_items = 10
        year_url = "https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1/i2024"
        listing_url = "https://msh.krasnodar.ru/documents/prikazy-minselkhoza-krasnodarskogo-kraya"
        attachment_url = "https://npa.krasnodar.ru/rest/files/1233654"
        root_html = f"""
        <html><body>
          <a href="{year_url}">2024</a>
          <a href="{listing_url}">Приказы минсельхоза</a>
        </body></html>
        """
        year_html = f"""
        <html><body>
          <a href="{attachment_url}">Порядок предоставления грантов</a>
        </body></html>
        """
        listing_html = f"""
        <html><body>
          <div class="document-item">
            <a class="document-item__title" href="/documents/prikazy/159168">
              № 161 от 28.04.2026 "Об утверждении Порядка предоставления грантов крестьянским (фермерским) хозяйствам"
            </a>
            <span class="document-info-bar__type">pdf</span>
            <a class="document-info-bar__download-link" href="{attachment_url}">
              <span class="document-info-bar__download-text">скачать документ</span>
            </a>
          </div>
        </body></html>
        """

        def fake_get(url: str):
            if url == source.config.url:
                return self._response(root_html, source.config.url)
            if url == year_url:
                return self._response(year_html, year_url)
            if url == listing_url:
                return self._response(listing_html, listing_url)
            raise AssertionError(f"Unexpected fetch: {url}")

        source.get = fake_get  # type: ignore[method-assign]
        items = source.fetch_items()

        self.assertEqual([item.url for item in items].count(attachment_url), 1)
        self.assertEqual(items[-1].url, listing_url)

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
