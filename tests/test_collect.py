from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

import requests

from app.models import CollectedItem, ExtractionResult, RawDocument, SourceConfig
from app.pipeline.collect import run_collect_with_options
from app.storage import (
    init_db,
    list_ocr_queue,
    list_documents,
    list_latest_source_audit,
    list_recent_document_extraction_audit,
    save_document,
    upsert_ocr_queue_item,
)


class CollectAuditTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path(f"data/test_artifacts/{name}")
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def test_extraction_audit_for_existing_document_without_duplicate_insert(self) -> None:
        db_path = self._db_path("collect_audit_existing.db")
        init_db(db_path)
        existing = RawDocument(
            source_name="Нормативные акты Краснодарского края",
            source_url="https://admkrai.krasnodar.ru/content/1291/",
            level="regional",
            region="krasnodar",
            title="Приказ о субсидии",
            url="https://example.com/subsidy.pdf",
            published_at=datetime.now(timezone.utc),
            collected_at=datetime.now(timezone.utc),
            content_hash="existing-hash",
            raw_text="Ранее сохраненный текст",
            document_type="pdf",
            status="collected",
        )
        save_document(existing, db_path)

        source_config = SourceConfig(
            name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/content/1291/",
            level="regional",
            region="krasnodar",
            source_role="regional_npa",
            parser="krasnodar",
            description="test",
        )
        item = CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="Приказ о субсидии",
            url="https://example.com/subsidy.pdf",
            document_type="pdf",
        )

        class FakeSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {
                    "links_found_count": 1,
                    "links_filtered_count": 0,
                    "pdf_links_count": 1,
                }

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch(
                    "app.pipeline.collect.extract_document",
                    return_value=ExtractionResult(
                        raw_text="Обновленный текст",
                        document_type="pdf",
                        needs_ocr=False,
                        page_count=1,
                        extracted_text_length=15,
                    ),
                ):
                    saved_count = run_collect_with_options(
                        source_name=source_config.name,
                        limit=1,
                        audit_existing=True,
                        db_path=str(db_path),
                    )

        self.assertEqual(saved_count, 0)
        self.assertEqual(len(list_documents(db_path=db_path)), 1)
        audits = list_recent_document_extraction_audit(db_path=db_path, days=7)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["document_url"], "https://example.com/subsidy.pdf")

    def test_source_access_error_creates_audit_warning(self) -> None:
        db_path = self._db_path("collect_source_access_warning.db")
        init_db(db_path)
        source_config = SourceConfig(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            level="regional",
            region="rostov",
            source_role="support_documents",
            parser="donland",
            description="test",
        )

        class FakeSource:
            def fetch_items(self) -> list[CollectedItem]:
                response = requests.Response()
                response.status_code = 403
                error = requests.exceptions.HTTPError("403 Client Error")
                error.response = response
                raise error

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                saved_count = run_collect_with_options(
                    source_name=source_config.name,
                    audit_existing=False,
                    db_path=str(db_path),
                )

        self.assertEqual(saved_count, 0)
        audit_rows = list_latest_source_audit(db_path=db_path)
        self.assertEqual(len(audit_rows), 1)
        self.assertIn("source access blocked (HTTP 403)", audit_rows[0].get("error_message") or "")

    def test_scan_candidate_creates_ocr_queue_item(self) -> None:
        db_path = self._db_path("collect_scan_candidate_ocr_queue.db")
        init_db(db_path)
        source_config = SourceConfig(
            name="Право Ростовской области",
            url="https://pravo.donland.ru/",
            level="regional",
            region="rostov",
            source_role="regional_npa",
            parser="regional_law",
            description="test",
        )
        item = CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="Скан постановления",
            url="https://example.com/scan-a.pdf",
            document_type="pdf",
        )

        class FakeSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {"links_found_count": 1, "pdf_links_count": 1}

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch(
                    "app.pipeline.collect.extract_document",
                    return_value=ExtractionResult(
                        raw_text="",
                        document_type="pdf",
                        needs_ocr=True,
                        page_count=4,
                        extracted_text_length=0,
                    ),
                ):
                    run_collect_with_options(
                        source_name=source_config.name,
                        limit=1,
                        audit_existing=False,
                        db_path=str(db_path),
                    )

        queue_rows = list_ocr_queue(db_path=db_path, statuses=["pending"], limit=10)
        self.assertEqual(len(queue_rows), 1)
        self.assertEqual(queue_rows[0]["document_url"], "https://example.com/scan-a.pdf")

    def test_duplicate_scan_candidate_does_not_create_duplicate_ocr_queue_rows(self) -> None:
        db_path = self._db_path("collect_scan_candidate_ocr_queue_dedup.db")
        init_db(db_path)
        source_config = SourceConfig(
            name="Право Ростовской области",
            url="https://pravo.donland.ru/",
            level="regional",
            region="rostov",
            source_role="regional_npa",
            parser="regional_law",
            description="test",
        )
        item = CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="Скан постановления",
            url="https://example.com/scan-b.pdf",
            document_type="pdf",
        )

        class FakeSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {"links_found_count": 1, "pdf_links_count": 1}

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch(
                    "app.pipeline.collect.extract_document",
                    return_value=ExtractionResult(
                        raw_text="",
                        document_type="pdf",
                        needs_ocr=True,
                        page_count=5,
                        extracted_text_length=0,
                    ),
                ):
                    run_collect_with_options(
                        source_name=source_config.name,
                        limit=1,
                        audit_existing=False,
                        db_path=str(db_path),
                    )
                    run_collect_with_options(
                        source_name=source_config.name,
                        limit=1,
                        audit_existing=True,
                        db_path=str(db_path),
                    )

        queue_rows = list_ocr_queue(db_path=db_path, limit=10)
        self.assertEqual(len(queue_rows), 1)
        self.assertEqual(queue_rows[0]["document_url"], "https://example.com/scan-b.pdf")

    def test_ocr_priority_is_high_for_watchlist_or_krasnodar_npa(self) -> None:
        db_path = self._db_path("collect_scan_candidate_ocr_priority.db")
        init_db(db_path)
        watchlist_document = RawDocument(
            source_name="Право Ростовской области",
            source_url="https://pravo.donland.ru/",
            level="regional",
            region="rostov",
            title="Действующий приказ",
            url="https://example.com/watchlist-scan.pdf",
            published_at=datetime.now(timezone.utc),
            collected_at=datetime.now(timezone.utc),
            content_hash="watchlist-scan-hash",
            raw_text="text",
            document_type="pdf",
            action_level="watchlist",
            status="analyzed",
        )
        save_document(watchlist_document, db_path)
        source_config = SourceConfig(
            name="Право Ростовской области",
            url="https://pravo.donland.ru/",
            level="regional",
            region="rostov",
            source_role="regional_npa",
            parser="regional_law",
            description="test",
        )
        watchlist_item = CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="Действующий приказ",
            url="https://example.com/watchlist-scan.pdf",
            document_type="pdf",
        )

        class FakeExistingSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {"links_found_count": 1, "pdf_links_count": 1}

            def fetch_items(self) -> list[CollectedItem]:
                return [watchlist_item]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeExistingSource()):
                with patch(
                    "app.pipeline.collect.extract_document",
                    return_value=ExtractionResult(
                        raw_text="",
                        document_type="pdf",
                        needs_ocr=True,
                        page_count=4,
                        extracted_text_length=0,
                    ),
                ):
                    run_collect_with_options(
                        source_name=source_config.name,
                        limit=1,
                        audit_existing=True,
                        db_path=str(db_path),
                    )

        krasnodar_config = SourceConfig(
            name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/content/1291/",
            level="regional",
            region="krasnodar",
            source_role="regional_npa",
            parser="krasnodar",
            description="test",
        )
        krasnodar_item = CollectedItem(
            source_name=krasnodar_config.name,
            source_url=krasnodar_config.url,
            level=krasnodar_config.level,
            region=krasnodar_config.region,
            title="Краевой скан НПА",
            url="https://example.com/krasnodar-scan.pdf",
            document_type="pdf",
        )

        class FakeKrasnodarSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {"links_found_count": 1, "pdf_links_count": 1}

            def fetch_items(self) -> list[CollectedItem]:
                return [krasnodar_item]

        with patch("app.pipeline.collect.load_sources", return_value=[krasnodar_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeKrasnodarSource()):
                with patch(
                    "app.pipeline.collect.extract_document",
                    return_value=ExtractionResult(
                        raw_text="",
                        document_type="pdf",
                        needs_ocr=True,
                        page_count=3,
                        extracted_text_length=0,
                    ),
                ):
                    run_collect_with_options(
                        source_name=krasnodar_config.name,
                        limit=1,
                        audit_existing=False,
                        db_path=str(db_path),
                    )

        queue_rows = list_ocr_queue(db_path=db_path, limit=10)
        by_url = {str(row["document_url"]): row for row in queue_rows}
        self.assertEqual(by_url["https://example.com/watchlist-scan.pdf"]["priority"], "high")
        self.assertEqual(by_url["https://example.com/krasnodar-scan.pdf"]["priority"], "high")

    def test_ocr_unavailable_scan_candidate_is_recorded_in_audit(self) -> None:
        db_path = self._db_path("collect_ocr_unavailable_audit.db")
        init_db(db_path)
        source_config = SourceConfig(
            name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/content/1291/",
            level="regional",
            region="krasnodar",
            source_role="regional_npa",
            parser="krasnodar",
            description="test",
        )
        item = CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="Скан НПА",
            url="https://example.com/unavailable-scan.pdf",
            document_type="pdf",
        )

        class FakeSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {"links_found_count": 1, "pdf_links_count": 1}

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch(
                    "app.pipeline.collect.extract_document",
                    return_value=ExtractionResult(
                        raw_text="",
                        document_type="pdf",
                        needs_ocr=True,
                        page_count=4,
                        extracted_text_length=0,
                        ocr_status="unavailable",
                        ocr_error="OCR runtime unavailable",
                    ),
                ):
                    run_collect_with_options(
                        source_name=source_config.name,
                        limit=1,
                        audit_existing=False,
                        db_path=str(db_path),
                    )

        audits = list_recent_document_extraction_audit(db_path=db_path, days=7)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["ocr_status"], "unavailable")
        self.assertEqual(audits[0]["ocr_text_length"], 0)
        queue_rows = list_ocr_queue(db_path=db_path, statuses=["pending"], limit=10)
        self.assertEqual(len(queue_rows), 1)

    def test_ocr_success_fills_raw_text(self) -> None:
        db_path = self._db_path("collect_ocr_success_text.db")
        init_db(db_path)
        source_config = SourceConfig(
            name="Право Ростовской области",
            url="https://pravo.donland.ru/",
            level="regional",
            region="rostov",
            source_role="regional_npa",
            parser="regional_law",
            description="test",
        )
        item = CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="OCR success документ",
            url="https://example.com/ocr-success.pdf",
            document_type="pdf",
        )

        class FakeSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {"links_found_count": 1, "pdf_links_count": 1}

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch(
                    "app.pipeline.collect.extract_document",
                    return_value=ExtractionResult(
                        raw_text="OCR extracted legal text",
                        document_type="pdf",
                        needs_ocr=False,
                        page_count=3,
                        extracted_text_length=24,
                        ocr_status="success",
                        ocr_text_length=24,
                        ocr_pages_processed=3,
                    ),
                ):
                    run_collect_with_options(
                        source_name=source_config.name,
                        limit=1,
                        audit_existing=False,
                        db_path=str(db_path),
                    )

        documents = list_documents(db_path=db_path)
        self.assertEqual(len(documents), 1)
        self.assertIn("OCR extracted legal text", documents[0].raw_text)
        audits = list_recent_document_extraction_audit(db_path=db_path, days=7)
        self.assertEqual(audits[0]["ocr_status"], "success")

    def test_ocr_success_marks_existing_queue_item_done(self) -> None:
        db_path = self._db_path("collect_ocr_success_done.db")
        init_db(db_path)
        source_config = SourceConfig(
            name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/content/1291/",
            level="regional",
            region="krasnodar",
            source_role="regional_npa",
            parser="krasnodar",
            description="test",
        )
        existing = RawDocument(
            source_name=source_config.name,
            source_url=source_config.url,
            level="regional",
            region="krasnodar",
            title="Скан НПА",
            url="https://example.com/queue-done.pdf",
            published_at=datetime.now(timezone.utc),
            collected_at=datetime.now(timezone.utc),
            content_hash="existing-ocr-done",
            raw_text="",
            document_type="pdf",
            status="collected",
        )
        save_document(existing, db_path)
        upsert_ocr_queue_item(
            document_url="https://example.com/queue-done.pdf",
            source_name=source_config.name,
            title="Скан НПА",
            priority="high",
            reason="scan_candidate_pdf",
            db_path=db_path,
        )
        item = CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="Скан НПА",
            url="https://example.com/queue-done.pdf",
            document_type="pdf",
        )

        class FakeSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {"links_found_count": 1, "pdf_links_count": 1}

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch(
                    "app.pipeline.collect.extract_document",
                    return_value=ExtractionResult(
                        raw_text="OCR ok",
                        document_type="pdf",
                        needs_ocr=False,
                        page_count=2,
                        extracted_text_length=6,
                        ocr_status="success",
                        ocr_text_length=6,
                        ocr_pages_processed=2,
                    ),
                ):
                    run_collect_with_options(
                        source_name=source_config.name,
                        limit=1,
                        audit_existing=True,
                        db_path=str(db_path),
                    )

        queue_rows = list_ocr_queue(db_path=db_path, statuses=["done"], limit=10)
        self.assertEqual(len(queue_rows), 1)
        self.assertEqual(queue_rows[0]["document_url"], "https://example.com/queue-done.pdf")

    def test_ocr_disabled_path_does_not_break_pdf_collection(self) -> None:
        db_path = self._db_path("collect_ocr_disabled_no_break.db")
        init_db(db_path)
        source_config = SourceConfig(
            name="Правительство РФ - документы",
            url="http://government.ru/docs/",
            level="federal",
            region="federal",
            source_role="strategy",
            parser="government",
            description="test",
        )
        item = CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="PDF с текстовым слоем",
            url="https://example.com/pdf-text-layer.pdf",
            document_type="pdf",
        )

        class FakeSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {"links_found_count": 1, "pdf_links_count": 1}

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch(
                    "app.pipeline.collect.extract_document",
                    return_value=ExtractionResult(
                        raw_text="T" * 1200,
                        document_type="pdf",
                        needs_ocr=False,
                        page_count=4,
                        extracted_text_length=1200,
                        ocr_status="disabled",
                    ),
                ):
                    saved_count = run_collect_with_options(
                        source_name=source_config.name,
                        limit=1,
                        audit_existing=False,
                        db_path=str(db_path),
                    )

        self.assertEqual(saved_count, 1)
        documents = list_documents(db_path=db_path)
        self.assertEqual(len(documents), 1)

    def test_hash_duplicate_does_not_create_ocr_queue_entry(self) -> None:
        """Second URL with identical content should not be added to OCR queue."""
        db_path = self._db_path("collect_hash_dup_no_ocr_queue.db")
        init_db(db_path)
        source_config = SourceConfig(
            name="Право Ростовской области",
            url="https://pravo.donland.ru/",
            level="regional",
            region="rostov",
            source_role="regional_npa",
            parser="regional_law",
            description="test",
        )
        shared_text = "Постановление Правительства Ростовской области от 01.01.2024"
        items = [
            CollectedItem(
                source_name=source_config.name,
                source_url=source_config.url,
                level=source_config.level,
                region=source_config.region,
                title="Документ А",
                url="https://example.com/doc-a.pdf",
                document_type="pdf",
            ),
            CollectedItem(
                source_name=source_config.name,
                source_url=source_config.url,
                level=source_config.level,
                region=source_config.region,
                title="Документ Б (дубль)",
                url="https://example.com/doc-b.pdf",
                document_type="pdf",
            ),
        ]

        class FakeSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {"links_found_count": 2, "pdf_links_count": 2}

            def fetch_items(self) -> list[CollectedItem]:
                return items

        identical_result = ExtractionResult(
            raw_text=shared_text,
            document_type="pdf",
            needs_ocr=False,
            page_count=1,
            extracted_text_length=len(shared_text),
        )
        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch(
                    "app.pipeline.collect.extract_document",
                    return_value=identical_result,
                ):
                    run_collect_with_options(
                        source_name=source_config.name,
                        limit=10,
                        audit_existing=False,
                        db_path=str(db_path),
                    )

        documents = list_documents(db_path=db_path)
        queue_rows = list_ocr_queue(db_path=db_path, limit=10)
        self.assertEqual(len(documents), 1, "Only first URL should be saved")
        self.assertEqual(len(queue_rows), 0, "Hash-duplicate must not enter OCR queue")


    def test_prefilled_raw_text_skips_extract_document(self) -> None:
        """When CollectedItem.raw_text is set, extract_document must not be called."""
        db_path = self._db_path("collect_prefilled_raw_text.db")
        init_db(db_path)
        source_config = SourceConfig(
            name="Regulation.gov.ru",
            url="https://regulation.gov.ru/",
            level="federal",
            region="federal",
            source_role="strategy",
            parser="regulation_gov",
            description="test",
        )
        items = [
            CollectedItem(
                source_name=source_config.name,
                source_url=source_config.url,
                level=source_config.level,
                region=source_config.region,
                title=f"Проект НПА {i}",
                url=f"https://regulation.gov.ru/projects/{i}",
                document_type="html",
                raw_text=f"Проект НПА: Проект НПА {i}\nID: {i}\nМинистерство: Минсельхоз России",
            )
            for i in range(1, 4)
        ]

        class FakeSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {"links_found_count": 3, "html_links_count": 3}

            def fetch_items(self) -> list[CollectedItem]:
                return items

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch("app.pipeline.collect.extract_document") as mock_extract:
                    saved_count = run_collect_with_options(
                        source_name=source_config.name,
                        limit=10,
                        audit_existing=False,
                        db_path=str(db_path),
                    )

        mock_extract.assert_not_called()
        self.assertEqual(saved_count, 3)
        documents = list_documents(db_path=db_path)
        self.assertEqual(len(documents), 3)
        urls = {d.url for d in documents}
        self.assertIn("https://regulation.gov.ru/projects/1", urls)
        self.assertIn("https://regulation.gov.ru/projects/3", urls)


class BaseSourceUserAgentTest(unittest.TestCase):
    def test_user_agent_field_overrides_request_headers_ua(self) -> None:
        from app.sources.base import BaseSource
        from app.models import SourceConfig

        config = SourceConfig(
            name="Test Source",
            url="https://example.com/",
            level="federal",
            region="federal",
            source_role="strategy",
            parser="generic_html",
            description="test",
            request_headers={"User-Agent": "from-request-headers"},
            user_agent="custom-ua-override",
        )

        class ConcreteSource(BaseSource):
            def fetch_items(self):
                return []

        source = ConcreteSource(config)
        self.assertEqual(source.session.headers.get("User-Agent"), "custom-ua-override")

    def test_user_agent_field_absent_keeps_request_headers_ua(self) -> None:
        from app.sources.base import BaseSource
        from app.models import SourceConfig

        config = SourceConfig(
            name="Test Source",
            url="https://example.com/",
            level="federal",
            region="federal",
            source_role="strategy",
            parser="generic_html",
            description="test",
            request_headers={"User-Agent": "from-request-headers"},
        )

        class ConcreteSource(BaseSource):
            def fetch_items(self):
                return []

        source = ConcreteSource(config)
        self.assertEqual(source.session.headers.get("User-Agent"), "from-request-headers")


if __name__ == "__main__":
    unittest.main()
