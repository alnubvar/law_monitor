from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

import requests

from app.models import CollectedItem, ExtractionResult, RawDocument, SourceConfig
from app.pipeline.collect import run_collect_with_options
from app.pipeline.deduplicate import compute_content_hash
from app.pipeline.donland_backfill import run_donland_backfill
from app.storage import (
    get_document_by_url,
    get_runtime_event,
    init_db,
    list_documents,
    list_recent_document_extraction_audit,
    save_document,
)


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


class DonlandBackfillTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path(f"data/test_artifacts/{name}")
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _source_config(self, name: str, url: str) -> SourceConfig:
        return SourceConfig(
            name=name,
            url=url,
            level="regional",
            region="rostov",
            source_role="regional_npa",
            parser="donland",
            description="test",
            verify_ssl=False,
        )

    def _document(
        self,
        *,
        source_name: str,
        source_url: str,
        title: str,
        url: str,
        raw_text: str,
    ) -> RawDocument:
        return RawDocument(
            source_name=source_name,
            source_url=source_url,
            level="regional",
            region="rostov",
            title=title,
            url=url,
            published_at=datetime.now(timezone.utc),
            collected_at=datetime.now(timezone.utc),
            content_hash=compute_content_hash(raw_text, fallback=f"{title}\n{url}"),
            raw_text=raw_text,
            document_type="html",
            status="analyzed",
        )

    def test_backfill_updates_existing_donland_doc_in_place_and_saves_audit(self) -> None:
        db_path = self._db_path("donland_backfill_updates_in_place.db")
        init_db(db_path)
        source_config = self._source_config(
            "Право Ростовской области",
            "https://pravo.donland.ru/doc/list/level/1/",
        )
        document = self._document(
            source_name=source_config.name,
            source_url=source_config.url,
            title="Постановление о субсидиях",
            url="https://pravo.donland.ru/doc/view/id/test-doc/",
            raw_text="Старый HTML текст с шумом",
        )
        save_document(document, db_path)
        page_one = """
        <html><body>
          <article>
            <h1>Постановление о субсидиях</h1>
            <a href="/files/uploads/pravo/pdf/51574/text/main.txt">txt</a>
          </article>
        </body></html>
        """

        def fake_get(url: str, **kwargs):
            if url.endswith("/page/2/"):
                return _FakeResponse(url, status_code=404)
            if url.endswith(".txt"):
                return _FakeResponse(url, text="Текст из основного TXT", content_type="text/plain; charset=utf-8")
            return _FakeResponse(url, text=page_one)

        with patch("app.pipeline.donland_backfill.load_sources", return_value=[source_config]):
            with patch("app.extractors.donland_detail_extractor.requests.get", side_effect=fake_get):
                result = run_donland_backfill(source_name=source_config.name, db_path=db_path)

        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.unchanged, 0)
        documents = list_documents(db_path=db_path)
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].raw_text, "Текст из основного TXT")
        audits = list_recent_document_extraction_audit(db_path=db_path, days=7)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["document_url"], document.url)
        runtime_event = get_runtime_event("donland_backfill", db_path=db_path)
        self.assertIsNotNone(runtime_event)

    def test_backfill_skips_update_when_hash_is_unchanged(self) -> None:
        db_path = self._db_path("donland_backfill_hash_unchanged.db")
        init_db(db_path)
        source_config = self._source_config(
            "Право Ростовской области",
            "https://pravo.donland.ru/doc/list/level/1/",
        )
        raw_text = "Уже обогащенный текст"
        document = self._document(
            source_name=source_config.name,
            source_url=source_config.url,
            title="Постановление о субсидиях",
            url="https://pravo.donland.ru/doc/view/id/same-doc/",
            raw_text=raw_text,
        )
        save_document(document, db_path)

        with patch("app.pipeline.donland_backfill.load_sources", return_value=[source_config]):
            with patch(
                "app.pipeline.donland_backfill.extract_document",
                return_value=ExtractionResult(
                    raw_text=raw_text,
                    document_type="html",
                    extracted_text_length=len(raw_text),
                ),
            ) as extract_document_mock:
                with patch("app.pipeline.donland_backfill.update_document_text_by_url") as update_mock:
                    result = run_donland_backfill(source_name=source_config.name, db_path=db_path)

        extract_document_mock.assert_called_once()
        update_mock.assert_not_called()
        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.unchanged, 1)
        refreshed = get_document_by_url(document.url, db_path=db_path)
        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertEqual(refreshed.raw_text, raw_text)

    def test_backfill_empty_extraction_does_not_overwrite_existing_text(self) -> None:
        db_path = self._db_path("donland_backfill_empty_text_guard.db")
        init_db(db_path)
        source_config = self._source_config(
            "Право Ростовской области",
            "https://pravo.donland.ru/doc/list/level/1/",
        )
        existing_text = "Ранее сохраненный текст постановления"
        document = self._document(
            source_name=source_config.name,
            source_url=source_config.url,
            title="Постановление о субсидиях",
            url="https://pravo.donland.ru/doc/view/id/empty-guard-doc/",
            raw_text=existing_text,
        )
        save_document(document, db_path)

        with patch("app.pipeline.donland_backfill.load_sources", return_value=[source_config]):
            with patch(
                "app.pipeline.donland_backfill.extract_document",
                return_value=ExtractionResult(
                    raw_text="   ",
                    document_type="html",
                    extracted_text_length=0,
                ),
            ):
                result = run_donland_backfill(source_name=source_config.name, db_path=db_path)

        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.skipped, 1)
        refreshed = get_document_by_url(document.url, db_path=db_path)
        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertEqual(refreshed.raw_text, existing_text)
        audits = list_recent_document_extraction_audit(db_path=db_path, days=7)
        self.assertEqual(len(audits), 1)
        self.assertFalse(audits[0]["has_text"])

    def test_backfill_dramatically_shorter_text_without_title_does_not_overwrite(self) -> None:
        db_path = self._db_path("donland_backfill_low_quality_guard.db")
        init_db(db_path)
        source_config = self._source_config(
            "Право Ростовской области",
            "https://pravo.donland.ru/doc/list/level/1/",
        )
        title = (
            "Областной закон Ростовской области от 23.12.2025 № 394-ЗС "
            "«Об областном бюджете на 2026 год и на плановый период 2027 и 2028 годов»"
        )
        existing_text = (
            "Областной закон Ростовской области полный текст документа. " * 50
        ).strip()
        document = self._document(
            source_name=source_config.name,
            source_url=source_config.url,
            title=title,
            url="https://pravo.donland.ru/doc/view/id/low-quality-doc/",
            raw_text=existing_text,
        )
        save_document(document, db_path)
        short_text = (
            "Программа государственных внутренних заимствований Ростовской области на 2026 год."
        )

        with patch("app.pipeline.donland_backfill.load_sources", return_value=[source_config]):
            with patch(
                "app.pipeline.donland_backfill.extract_document",
                return_value=ExtractionResult(
                    raw_text=short_text,
                    document_type="html",
                    extracted_text_length=len(short_text),
                ),
            ):
                result = run_donland_backfill(source_name=source_config.name, db_path=db_path)

        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.skipped, 1)
        refreshed = get_document_by_url(document.url, db_path=db_path)
        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertEqual(refreshed.raw_text, existing_text)
        audits = list_recent_document_extraction_audit(db_path=db_path, days=7)
        self.assertEqual(len(audits), 1)
        self.assertTrue(audits[0]["has_text"])

    def test_backfill_shorter_substantive_text_with_title_may_update(self) -> None:
        db_path = self._db_path("donland_backfill_shorter_with_title_ok.db")
        init_db(db_path)
        source_config = self._source_config(
            "Право Ростовской области",
            "https://pravo.donland.ru/doc/list/level/1/",
        )
        title = (
            "Постановление Министерства образования Ростовской области "
            "от 08.05.2026 № 8"
        )
        existing_text = (
            "Постановление Министерства образования Ростовской области подробный старый текст. " * 40
        ).strip()
        document = self._document(
            source_name=source_config.name,
            source_url=source_config.url,
            title=title,
            url="https://pravo.donland.ru/doc/view/id/shorter-title-doc/",
            raw_text=existing_text,
        )
        save_document(document, db_path)
        shorter_text = (
            "Постановление Министерства образования Ростовской области\n"
            "О внесении изменения в порядок приема.\n"
            "Основной текст новой редакции."
        )

        with patch("app.pipeline.donland_backfill.load_sources", return_value=[source_config]):
            with patch(
                "app.pipeline.donland_backfill.extract_document",
                return_value=ExtractionResult(
                    raw_text=shorter_text,
                    document_type="html",
                    extracted_text_length=len(shorter_text),
                ),
            ):
                result = run_donland_backfill(source_name=source_config.name, db_path=db_path)

        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.skipped, 0)
        refreshed = get_document_by_url(document.url, db_path=db_path)
        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertEqual(refreshed.raw_text, shorter_text)

    def test_backfill_is_limited_to_requested_donland_source(self) -> None:
        db_path = self._db_path("donland_backfill_source_restricted.db")
        init_db(db_path)
        level1 = self._source_config(
            "Право Ростовской области",
            "https://pravo.donland.ru/doc/list/level/1/",
        )
        level2 = self._source_config(
            "Проекты правовых актов Ростовской области",
            "https://pravo.donland.ru/doc/list/level/2/",
        )
        doc_level1 = self._document(
            source_name=level1.name,
            source_url=level1.url,
            title="Уровень 1 документ",
            url="https://pravo.donland.ru/doc/view/id/level1-doc/",
            raw_text="старый текст 1",
        )
        doc_level2 = self._document(
            source_name=level2.name,
            source_url=level2.url,
            title="Уровень 2 документ",
            url="https://pravo.donland.ru/doc/view/id/level2-doc/",
            raw_text="старый текст 2",
        )
        save_document(doc_level1, db_path)
        save_document(doc_level2, db_path)

        def fake_extract(item: CollectedItem, source_config: SourceConfig) -> ExtractionResult:
            return ExtractionResult(
                raw_text=f"обновлено: {item.source_name}",
                document_type="html",
                extracted_text_length=len(item.source_name) + 10,
            )

        with patch("app.pipeline.donland_backfill.load_sources", return_value=[level1, level2]):
            with patch("app.pipeline.donland_backfill.extract_document", side_effect=fake_extract):
                result = run_donland_backfill(source_name=level1.name, db_path=db_path)

        self.assertEqual(result.scanned, 1)
        updated_level1 = get_document_by_url(doc_level1.url, db_path=db_path)
        updated_level2 = get_document_by_url(doc_level2.url, db_path=db_path)
        self.assertIsNotNone(updated_level1)
        self.assertIsNotNone(updated_level2)
        assert updated_level1 is not None
        assert updated_level2 is not None
        self.assertEqual(updated_level1.raw_text, f"обновлено: {level1.name}")
        self.assertEqual(updated_level2.raw_text, "старый текст 2")
        audits = list_recent_document_extraction_audit(db_path=db_path, days=7)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["source_name"], level1.name)

    def test_backfill_exception_path_still_audits_but_does_not_update(self) -> None:
        db_path = self._db_path("donland_backfill_exception_no_update.db")
        init_db(db_path)
        source_config = self._source_config(
            "Право Ростовской области",
            "https://pravo.donland.ru/doc/list/level/1/",
        )
        existing_text = "Стабильный сохраненный текст"
        document = self._document(
            source_name=source_config.name,
            source_url=source_config.url,
            title="Постановление о субсидиях",
            url="https://pravo.donland.ru/doc/view/id/error-doc/",
            raw_text=existing_text,
        )
        save_document(document, db_path)

        with patch("app.pipeline.donland_backfill.load_sources", return_value=[source_config]):
            with patch(
                "app.pipeline.donland_backfill.extract_document",
                side_effect=requests.RequestException("403 blocked"),
            ):
                result = run_donland_backfill(source_name=source_config.name, db_path=db_path)

        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.errors, 1)
        refreshed = get_document_by_url(document.url, db_path=db_path)
        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertEqual(refreshed.raw_text, existing_text)
        audits = list_recent_document_extraction_audit(db_path=db_path, days=7)
        self.assertEqual(len(audits), 1)
        self.assertFalse(audits[0]["has_text"])
        self.assertIn("403 blocked", audits[0]["extraction_error"])

    def test_collect_audit_existing_remains_non_mutating_for_donland_existing_doc(self) -> None:
        db_path = self._db_path("donland_collect_audit_existing_non_mutating.db")
        init_db(db_path)
        source_config = self._source_config(
            "Право Ростовской области",
            "https://pravo.donland.ru/doc/list/level/1/",
        )
        existing = self._document(
            source_name=source_config.name,
            source_url=source_config.url,
            title="Постановление о субсидиях",
            url="https://pravo.donland.ru/doc/view/id/existing-doc/",
            raw_text="исходный текст",
        )
        save_document(existing, db_path)
        item = CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title=existing.title,
            url=existing.url,
            document_type="html",
        )

        class FakeSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {"links_found_count": 1, "html_links_count": 1}

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch(
                    "app.pipeline.collect.extract_document",
                    return_value=ExtractionResult(
                        raw_text="новый текст, но только для audit",
                        document_type="html",
                        extracted_text_length=31,
                    ),
                ):
                    saved_count = run_collect_with_options(
                        source_name=source_config.name,
                        limit=1,
                        audit_existing=True,
                        db_path=str(db_path),
                    )

        self.assertEqual(saved_count, 0)
        refreshed = get_document_by_url(existing.url, db_path=db_path)
        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertEqual(refreshed.raw_text, "исходный текст")
        audits = list_recent_document_extraction_audit(db_path=db_path, days=7)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["document_url"], existing.url)


if __name__ == "__main__":
    unittest.main()
