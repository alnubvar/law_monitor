from __future__ import annotations

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from app.config import load_sources
from app.extractors.date_extractor import normalize_date_to_iso
from app.models import SourceConfig
from app.sources.donland_source import DonlandSource, MCX_CURATED_ACTIVITY_URLS

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
                    ".xls",
                    ".xlsx",
                    ".zip",
                ],
                deny_patterns=deny_patterns or [],
                max_items=max_items,
            )
        )

    def _items(self, source: DonlandSource, fixture_name: str):
        html = (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        return source._extract_items_from_soup(soup, source.config.url)

    def test_mcx_donland_source_config_raises_max_items_to_50(self) -> None:
        source = next(
            config for config in load_sources()
            if config.name == "Минсельхоз Ростовской области - господдержка"
        )
        self.assertEqual(source.max_items, 50)

    def test_mcx_donland_listing_and_reference_pages_are_filtered(self) -> None:
        source = self._source(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            source_role="support_documents",
            region="rostov",
        )

        items = self._items(source, "mcx_donland_listing_reference_page.html")

        self.assertEqual(
            [item.url for item in items],
            ["https://mcx.donland.ru/activity/37370/"],
        )

    def test_mcx_donland_real_selection_announcement_is_kept(self) -> None:
        source = self._source(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            source_role="support_documents",
            region="rostov",
            deny_patterns=["/request/", "/presscenter/video", "/documents/all/"],
        )

        items = self._items(source, "mcx_donland_real_selection_announcement.html")

        by_url = {item.url: item for item in items}
        self.assertIn("https://mcx.donland.ru/presscenter/events/72822/", by_url)
        self.assertEqual(
            normalize_date_to_iso(by_url["https://mcx.donland.ru/presscenter/events/72822/"].published_at),
            "2026-04-28",
        )

    def test_mcx_donland_root_support_page_fetches_curated_seed_pages_first(self) -> None:
        source = self._source(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            source_role="support_documents",
            region="rostov",
            max_items=4,
            deny_patterns=["/request/", "/presscenter/video", "/documents/all/"],
        )
        html_by_url = {
            "https://mcx.donland.ru/activity/35217/": "<html><body><h1>Меры государственной поддержки</h1></body></html>",
            "https://mcx.donland.ru/activity/37368/": "<html><body><h1>Страхование и инвестиции</h1></body></html>",
            "https://mcx.donland.ru/activity/37369/": "<html><body><h1>Пищевая и перерабатывающая промышленность</h1></body></html>",
            "https://mcx.donland.ru/activity/37370/": "<html><body><h1>Животноводство</h1></body></html>",
        }
        fetched_urls: list[str] = []

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str):
            fetched_urls.append(url)
            if url not in html_by_url:
                raise AssertionError(f"Unexpected fetch: {url}")
            return Response(html_by_url[url], url)

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        self.assertEqual(
            [item.url for item in items],
            [
                "https://mcx.donland.ru/activity/35217/",
                "https://mcx.donland.ru/activity/37368/",
                "https://mcx.donland.ru/activity/37369/",
                "https://mcx.donland.ru/activity/37370/",
            ],
        )
        self.assertEqual(fetched_urls, list(MCX_CURATED_ACTIVITY_URLS[:4]))
        self.assertEqual(source.last_fetch_stats["traversed_page_count"], 3)

    def test_mcx_donland_documents_active_does_not_starve_curated_support_pages(self) -> None:
        source = self._source(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            source_role="support_documents",
            region="rostov",
            max_items=3,
            deny_patterns=["/request/", "/presscenter/video", "/documents/all/"],
        )
        html_by_url = {
            "https://mcx.donland.ru/activity/35217/": """
            <html><body>
              <h1>Меры государственной поддержки</h1>
              <a href="/documents/active/521032/">Действующие документы</a>
              <a href="/upload/uf/test/support.pdf">Порядок субсидии PDF</a>
            </body></html>
            """,
            "https://mcx.donland.ru/activity/37368/": "<html><body><h1>Страхование и инвестиции</h1></body></html>",
            "https://mcx.donland.ru/activity/37369/": "<html><body><h1>Пищевая и перерабатывающая промышленность</h1></body></html>",
        }
        fetched_urls: list[str] = []

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str):
            fetched_urls.append(url)
            if url not in html_by_url:
                raise AssertionError(f"Unexpected fetch: {url}")
            return Response(html_by_url[url], url)

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        self.assertEqual(
            [item.url for item in items],
            [
                "https://mcx.donland.ru/activity/35217/",
                "https://mcx.donland.ru/activity/37368/",
                "https://mcx.donland.ru/activity/37369/",
            ],
        )
        self.assertEqual(fetched_urls, list(MCX_CURATED_ACTIVITY_URLS[:3]))

    def test_mcx_donland_collects_known_rastenievodstvo_and_operational_pages(self) -> None:
        source = self._source(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            source_role="support_documents",
            region="rostov",
            max_items=30,
            deny_patterns=["/request/", "/presscenter/video", "/documents/all/"],
        )
        titles_by_url = {
            "https://mcx.donland.ru/activity/35217/": "Меры государственной поддержки",
            "https://mcx.donland.ru/activity/37368/": "Страхование и инвестиции",
            "https://mcx.donland.ru/activity/37369/": "Пищевая и перерабатывающая промышленность",
            "https://mcx.donland.ru/activity/37370/": "Животноводство",
            "https://mcx.donland.ru/activity/37371/": "Рыбохозяйственный комплекс",
            "https://mcx.donland.ru/activity/37372/": "Малые формы хозяйствования",
            "https://mcx.donland.ru/activity/37373/": "Растениеводство",
            "https://mcx.donland.ru/activity/37543/": "Элитное семеноводство и зерновые культуры",
            "https://mcx.donland.ru/activity/37540/": "Субсидии на закладку и уход за многолетними насаждениями",
            "https://mcx.donland.ru/activity/37378/": "Техника и оборудование",
            "https://mcx.donland.ru/activity/37541/": "Мелиорация и Агрохимия",
            "https://mcx.donland.ru/activity/37544/": "Овощи защищенного и открытого грунта, картофель",
            "https://mcx.donland.ru/activity/47419/": "Меры поддержки по зерну и элитному семеноводству",
            "https://mcx.donland.ru/activity/56370/": "Условия по заработной плате",
            "https://mcx.donland.ru/activity/72821/": "Льготный лизинг Росагролизинг",
            "https://mcx.donland.ru/activity/51403/": "Протоколы рассмотрения заявок участников отборов",
            "https://mcx.donland.ru/activity/35332/": "Сроки предоставления госуслуг через МФЦ",
            "https://mcx.donland.ru/activity/35333/": "Результаты отборов и конкурсов на получение субсидий",
            "https://mcx.donland.ru/activity/35337/": "Информация по отказам участникам отбора",
            "https://mcx.donland.ru/activity/35334/": "Реестры получателей субсидий",
            "https://mcx.donland.ru/activity/35336/": "Реестр сельхозтоваропроизводителей",
            "https://mcx.donland.ru/activity/39115/": "Меры поддержки бизнеса в условиях санкций",
        }
        fetched_urls: list[str] = []

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str):
            fetched_urls.append(url)
            if url not in titles_by_url:
                raise AssertionError(f"Unexpected fetch: {url}")
            return Response(f"<html><body><h1>{titles_by_url[url]}</h1></body></html>", url)

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        item_urls = {item.url for item in items}
        self.assertIn("https://mcx.donland.ru/activity/37540/", item_urls)
        self.assertIn("https://mcx.donland.ru/activity/47419/", item_urls)
        self.assertIn("https://mcx.donland.ru/activity/35333/", item_urls)
        self.assertIn("https://mcx.donland.ru/activity/35334/", item_urls)
        self.assertIn("https://mcx.donland.ru/activity/39115/", item_urls)
        self.assertEqual(fetched_urls, list(MCX_CURATED_ACTIVITY_URLS))

    def test_mcx_donland_redirected_curated_ids_still_materialize_requested_activity_urls(self) -> None:
        source = self._source(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            source_role="support_documents",
            region="rostov",
            max_items=22,
            deny_patterns=["/request/", "/presscenter/video", "/documents/all/"],
        )
        titles_by_url = {
            url: f"Страница {index}"
            for index, url in enumerate(MCX_CURATED_ACTIVITY_URLS, start=1)
        }
        titles_by_url["https://mcx.donland.ru/activity/47419/"] = "Меры поддержки по зерну и элитному семеноводству"
        titles_by_url["https://mcx.donland.ru/activity/39115/"] = "Меры поддержки бизнеса в условиях санкций"

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str):
            if url not in titles_by_url:
                raise AssertionError(f"Unexpected fetch: {url}")
            response_url = url
            if url == "https://mcx.donland.ru/activity/47419/":
                response_url = "https://mcx.donland.ru/activity/47419/?from=menu"
            if url == "https://mcx.donland.ru/activity/39115/":
                response_url = "https://mcx.donland.ru/activity/39115/index.php"
            return Response(f"<html><body><h1>{titles_by_url[url]}</h1></body></html>", response_url)

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        item_urls = {item.url for item in items}
        self.assertIn("https://mcx.donland.ru/activity/47419/", item_urls)
        self.assertIn("https://mcx.donland.ru/activity/39115/", item_urls)

    def test_mcx_donland_collects_xlsx_and_zip_attachments_with_remaining_budget(self) -> None:
        source = self._source(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            source_role="support_documents",
            region="rostov",
            max_items=24,
            deny_patterns=["/request/", "/presscenter/video", "/documents/all/"],
        )
        titles_by_url = {
            url: f"Страница {index}"
            for index, url in enumerate(MCX_CURATED_ACTIVITY_URLS, start=1)
        }
        attachments_html = """
        <html><body>
          <h1>Страхование и инвестиции</h1>
          <a href="/upload/uf/test/support-data.xlsx">Таблица поддержки</a>
          <a href="/upload/uf/test/support-archive.zip">Архив материалов</a>
        </body></html>
        """

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str):
            if url == "https://mcx.donland.ru/activity/37368/":
                return Response(attachments_html, url)
            if url not in titles_by_url:
                raise AssertionError(f"Unexpected fetch: {url}")
            return Response(f"<html><body><h1>{titles_by_url[url]}</h1></body></html>", url)

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        by_url = {item.url: item for item in items}
        self.assertEqual(by_url["https://mcx.donland.ru/upload/uf/test/support-data.xlsx"].document_type, "xlsx")
        self.assertEqual(by_url["https://mcx.donland.ru/upload/uf/test/support-archive.zip"].document_type, "zip")

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

    def test_pravo_donland_fetch_items_paginates_listing_pages(self) -> None:
        source = self._source(
            name="Право Ростовской области",
            url="https://pravo.donland.ru/doc/list/level/1/",
            source_role="regional_npa",
            region="rostov",
        )
        p1_html = (FIXTURES_DIR / "pravo_donland_listing_page_p1.html").read_text(encoding="utf-8")
        p2_html = (FIXTURES_DIR / "pravo_donland_listing_page_p2.html").read_text(encoding="utf-8")
        fetched_urls: list[str] = []

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str):
            fetched_urls.append(url)
            if "page/2" in url:
                return Response(p2_html, url)
            return Response(p1_html, url)

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        self.assertEqual(len(items), 4)
        self.assertEqual(fetched_urls[0], "https://pravo.donland.ru/doc/list/level/1/")
        self.assertIn("page/2", fetched_urls[1])
        self.assertEqual(
            items[0].url,
            "https://pravo.donland.ru/doc/view/id/Постановление_42_29042026_60001/",
        )
        self.assertEqual(
            items[3].url,
            "https://pravo.donland.ru/doc/view/id/Постановление_38_20042026_60001/",
        )

    def test_pravo_donland_fetch_items_stops_when_page_yields_no_new_items(self) -> None:
        source = self._source(
            name="Право Ростовской области",
            url="https://pravo.donland.ru/doc/list/level/1/",
            source_role="regional_npa",
            region="rostov",
        )
        p1_html = (FIXTURES_DIR / "pravo_donland_listing_page_p1.html").read_text(encoding="utf-8")
        empty_html = "<html><body><div class='doc-list'></div></body></html>"
        fetched_urls: list[str] = []

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str):
            fetched_urls.append(url)
            if "page/2" in url:
                return Response(empty_html, url)
            return Response(p1_html, url)

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        self.assertEqual(len(items), 3)
        self.assertEqual(len(fetched_urls), 2)

    def test_pravo_donland_level2_fetch_items_paginates_correctly(self) -> None:
        source = self._source(
            name="Проекты правовых актов Ростовской области",
            url="https://pravo.donland.ru/doc/list/level/2/",
            source_role="regional_npa",
            region="rostov",
        )
        p1_html = (FIXTURES_DIR / "pravo_donland_listing_page_p1.html").read_text(encoding="utf-8")
        p2_html = (FIXTURES_DIR / "pravo_donland_listing_page_p2.html").read_text(encoding="utf-8")
        fetched_urls: list[str] = []

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str):
            fetched_urls.append(url)
            if "page/2" in url:
                return Response(p2_html, url)
            return Response(p1_html, url)

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        self.assertEqual(len(items), 4)
        self.assertIn("doc/list/level/2", fetched_urls[0])
        self.assertIn("doc/list/level/2/page/2", fetched_urls[1])

    def test_pravo_donland_fetch_items_respects_max_items(self) -> None:
        source = self._source(
            name="Право Ростовской области",
            url="https://pravo.donland.ru/doc/list/level/1/",
            source_role="regional_npa",
            region="rostov",
            max_items=2,
        )
        p1_html = (FIXTURES_DIR / "pravo_donland_listing_page_p1.html").read_text(encoding="utf-8")
        fetched_urls: list[str] = []

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str):
            fetched_urls.append(url)
            return Response(p1_html, url)

        source.get = fake_get  # type: ignore[method-assign]

        items = source.fetch_items()

        self.assertEqual(len(items), 2)
        self.assertEqual(len(fetched_urls), 1)

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
