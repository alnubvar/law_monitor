from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

import requests

from app.extractors.donland_detail_extractor import maybe_extract_donland_detail
from app.models import CollectedItem, SourceConfig
from app.pipeline.collect import run_collect_with_options
from app.storage import init_db, list_documents, list_recent_document_extraction_audit


class _FakeResponse:
    def __init__(
        self,
        url: str,
        *,
        text: str = "",
        status_code: int = 200,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.encoding = "utf-8"
        self.headers = {"Content-Type": content_type}
        self.content = text.encode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.HTTPError(f"{self.status_code} error for {self.url}")
            error.response = self
            raise error


class DonlandDetailExtractorTest(unittest.TestCase):
    def _item(self, url: str = "https://pravo.donland.ru/doc/view/id/test-doc/") -> CollectedItem:
        return CollectedItem(
            source_name="Право Ростовской области",
            source_url="https://pravo.donland.ru/doc/list/level/1/",
            level="regional",
            region="rostov",
            title="Постановление о субсидиях",
            url=url,
            document_type="html",
        )

    def test_merges_bounded_detail_pages_for_same_card(self) -> None:
        item = self._item()
        page_one = """
        <html><body>
          <div>Портал</div>
          <article>
            <h1>Постановление о субсидиях</h1>
            <p>Преамбула документа.</p>
            <div>Страница 1 из 3</div>
            <div>Выбор страниц документа</div>
          </article>
          <footer>Свидетельство о регистрации СМИ</footer>
        </body></html>
        """
        page_two = """
        <html><body>
          <article>
            <h1>Постановление о субсидиях</h1>
            <p>Основной текст второй страницы.</p>
          </article>
        </body></html>
        """
        page_three = """
        <html><body>
          <article>
            <h1>Постановление о субсидиях</h1>
            <p>Заключительные положения третьей страницы.</p>
          </article>
        </body></html>
        """
        requested_urls: list[str] = []

        def fake_get(url: str, **kwargs):
            requested_urls.append(url)
            if url.endswith("/page/2/"):
                return _FakeResponse(url, text=page_two)
            if url.endswith("/page/3/"):
                return _FakeResponse(url, text=page_three)
            if url.endswith("/page/4/"):
                raise AssertionError("detail page limit should stop at page 3")
            return _FakeResponse(url, text=page_one)

        with patch("app.extractors.donland_detail_extractor.requests.get", side_effect=fake_get):
            result = maybe_extract_donland_detail(item, headers={"User-Agent": "test"}, timeout=5, verify_ssl=True)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.document_type, "html")
        self.assertIn("Преамбула документа.", result.raw_text)
        self.assertIn("Основной текст второй страницы.", result.raw_text)
        self.assertIn("Заключительные положения третьей страницы.", result.raw_text)
        self.assertNotIn("Свидетельство о регистрации СМИ", result.raw_text)
        self.assertNotIn("Выбор страниц документа", result.raw_text)
        self.assertEqual(
            requested_urls,
            [
                "https://pravo.donland.ru/doc/view/id/test-doc/",
                "https://pravo.donland.ru/doc/view/id/test-doc/page/2/",
                "https://pravo.donland.ru/doc/view/id/test-doc/page/3/",
            ],
        )

    def test_prefers_txt_attachment_and_ignores_sig(self) -> None:
        item = self._item()
        page_one = """
        <html><body>
          <article>
            <h1>Постановление о субсидиях</h1>
            <a href="/files/uploads/pravo/pdf/51574/6124202605150006.sig">sig</a>
            <a href="/files/uploads/pravo/pdf/51574/6124202605150006.pdf">pdf</a>
            <a href="/files/uploads/pravo/pdf/51574/doc/main.docx">docx</a>
            <a href="/files/uploads/pravo/pdf/51574/text/main.txt">txt</a>
          </article>
        </body></html>
        """

        def fake_get(url: str, **kwargs):
            if url.endswith("/page/2/"):
                return _FakeResponse(url, status_code=404)
            if url.endswith(".txt"):
                return _FakeResponse(url, text="Текст из TXT вложения", content_type="text/plain; charset=utf-8")
            return _FakeResponse(url, text=page_one)

        with patch("app.extractors.donland_detail_extractor.requests.get", side_effect=fake_get):
            with patch("app.extractors.donland_detail_extractor.extract_text_from_docx") as mock_docx:
                with patch("app.extractors.donland_detail_extractor.extract_text_from_pdf") as mock_pdf:
                    result = maybe_extract_donland_detail(item, headers={"User-Agent": "test"}, timeout=5, verify_ssl=True)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.document_type, "html")
        self.assertEqual(result.raw_text, "Текст из TXT вложения")
        mock_docx.assert_not_called()
        mock_pdf.assert_not_called()

    def test_attachment_fetch_failure_keeps_html_fallback(self) -> None:
        item = self._item()
        page_one = """
        <html><body>
          <article>
            <h1>Постановление о субсидиях</h1>
            <p>HTML fallback text.</p>
            <a href="/files/uploads/pravo/pdf/51574/doc/main.docx">docx</a>
          </article>
        </body></html>
        """
        page_two = """
        <html><body>
          <article>
            <h1>Постановление о субсидиях</h1>
            <p>HTML continuation text.</p>
          </article>
        </body></html>
        """

        def fake_get(url: str, **kwargs):
            if url.endswith("/page/2/"):
                return _FakeResponse(url, text=page_two)
            if url.endswith("/page/3/"):
                return _FakeResponse(url, status_code=404)
            return _FakeResponse(url, text=page_one)

        with patch("app.extractors.donland_detail_extractor.requests.get", side_effect=fake_get):
            with patch(
                "app.extractors.donland_detail_extractor.extract_text_from_docx",
                side_effect=requests.RequestException("docx blocked"),
            ):
                result = maybe_extract_donland_detail(item, headers={"User-Agent": "test"}, timeout=5, verify_ssl=True)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.document_type, "html")
        self.assertIn("HTML fallback text.", result.raw_text)
        self.assertIn("HTML continuation text.", result.raw_text)
        self.assertNotIn("docx blocked", result.raw_text)

    def test_attachment_candidate_limit_caps_visible_links(self) -> None:
        item = self._item()
        attachment_links = "\n".join(
            f'<a href="/files/uploads/pravo/pdf/51574/text/file{i}.txt">txt{i}</a>'
            for i in range(1, 7)
        )
        page_one = f"""
        <html><body>
          <article>
            <h1>Постановление о субсидиях</h1>
            <p>HTML fallback text.</p>
            {attachment_links}
          </article>
        </body></html>
        """
        fetched_attachment_urls: list[str] = []

        def fake_get(url: str, **kwargs):
            if url.endswith("/page/2/"):
                return _FakeResponse(url, status_code=404)
            if url.endswith(".txt"):
                fetched_attachment_urls.append(url)
                if url.endswith("file6.txt"):
                    return _FakeResponse(url, text="This should never be fetched", content_type="text/plain; charset=utf-8")
                raise requests.RequestException(f"failed {url}")
            return _FakeResponse(url, text=page_one)

        with patch("app.extractors.donland_detail_extractor.requests.get", side_effect=fake_get):
            result = maybe_extract_donland_detail(item, headers={"User-Agent": "test"}, timeout=5, verify_ssl=True)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.document_type, "html")
        self.assertIn("HTML fallback text.", result.raw_text)
        self.assertEqual(len(fetched_attachment_urls), 5)
        self.assertFalse(any(url.endswith("file6.txt") for url in fetched_attachment_urls))


class DonlandCollectIntegrationTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path(f"data/test_artifacts/{name}")
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def test_collect_enriches_single_doc_view_item_without_creating_attachment_rows(self) -> None:
        db_path = self._db_path("collect_donland_doc_view_enrichment.db")
        init_db(db_path)
        source_config = SourceConfig(
            name="Право Ростовской области",
            url="https://pravo.donland.ru/doc/list/level/1/",
            level="regional",
            region="rostov",
            source_role="regional_npa",
            parser="donland",
            description="test",
            verify_ssl=False,
        )
        item = CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="Постановление о субсидиях",
            url="https://pravo.donland.ru/doc/view/id/test-doc/",
            document_type="html",
        )
        page_one = """
        <html><body>
          <article>
            <h1>Постановление о субсидиях</h1>
            <a href="/files/uploads/pravo/pdf/51574/text/main.txt">txt</a>
          </article>
        </body></html>
        """

        class FakeSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {"links_found_count": 1, "html_links_count": 1}

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        def fake_get(url: str, **kwargs):
            if url.endswith("/page/2/"):
                return _FakeResponse(url, status_code=404)
            if url.endswith(".txt"):
                return _FakeResponse(url, text="Текст из основного TXT", content_type="text/plain; charset=utf-8")
            return _FakeResponse(url, text=page_one)

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch("app.extractors.donland_detail_extractor.requests.get", side_effect=fake_get):
                    saved_count = run_collect_with_options(
                        source_name=source_config.name,
                        limit=1,
                        audit_existing=False,
                        db_path=str(db_path),
                    )

        self.assertEqual(saved_count, 1)
        documents = list_documents(db_path=db_path)
        self.assertEqual(len(documents), 1)
        document = documents[0]
        self.assertEqual(document.url, item.url)
        self.assertEqual(document.document_type, "html")
        self.assertEqual(document.raw_text, "Текст из основного TXT")

        audits = list_recent_document_extraction_audit(db_path=db_path, days=7)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["document_url"], item.url)


if __name__ == "__main__":
    unittest.main()
