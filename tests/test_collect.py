from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

import requests

from app.models import CollectedItem, ExtractionResult, RawDocument, SourceConfig
from app.pipeline.collect import run_collect_with_options
from app.pipeline.collect import create_source
from app.sources.generic_html_source import GenericHTMLSource
from app.sources.krasnodar_source import KrasnodarSource
from app.sources.promote_budget_source import PromoteBudgetSource
from app.sources.pravo_stavregion_api_source import PravoStavregionApiSource
from app.pipeline.deduplicate import compute_content_hash
from app.storage import (
    get_document_by_url,
    init_db,
    list_ocr_queue,
    list_documents,
    list_latest_source_audit,
    list_recent_document_extraction_audit,
    save_document,
    upsert_ocr_queue_item,
    update_document_text_by_url,
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

    def test_php_support_page_is_extracted_as_html_document(self) -> None:
        db_path = self._db_path("collect_php_support_html.db")
        init_db(db_path)
        source_config = SourceConfig(
            name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/gospodderzhka/",
            level="regional",
            region="stavropol",
            source_role="support_documents",
            parser="stavropol",
            description="test",
        )
        item = CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="Объявление об отборе на субсидии",
            url="https://mshsk.ru/gospodderzhka/selection-berry-2026.php",
            document_type="html",
        )

        class FakeSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {"links_found_count": 1, "html_links_count": 1}

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        extracted = ExtractionResult(
            raw_text="Объявление об отборе на субсидии. Прием заявок открыт для сельхозтоваропроизводителей.",
            document_type="html",
            extracted_text_length=87,
        )
        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch("app.pipeline.collect.extract_text_from_html", return_value=extracted) as mock_extract:
                    saved_count = run_collect_with_options(
                        source_name=source_config.name,
                        limit=10,
                        audit_existing=False,
                        db_path=str(db_path),
                    )

        self.assertEqual(saved_count, 1)
        mock_extract.assert_called_once_with(
            item.url,
            source_name=source_config.name,
            headers=source_config.request_headers,
            timeout=source_config.request_timeout,
            verify_ssl=source_config.verify_ssl,
        )
        documents = list_documents(db_path=db_path)
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].document_type, "html")
        self.assertGreater(len(documents[0].raw_text), 0)
        self.assertIn("Прием заявок", documents[0].raw_text)

    def test_krasnodar_existing_listing_still_allows_harvested_attachment_save(self) -> None:
        db_path = self._db_path("collect_krasnodar_existing_listing_attachment.db")
        init_db(db_path)
        source_config = SourceConfig(
            name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1",
            level="regional",
            region="krasnodar",
            source_role="support_documents",
            parser="krasnodar",
            description="test",
            max_items=10,
            deny_patterns=["/news", "/department", "/contacts", "/activity", "/serv"],
            allow_patterns=["subsid", "finans", "document", ".pdf", ".doc", ".docx"],
        )
        listing_url = "https://msh.krasnodar.ru/documents/prikazy-minselkhoza-krasnodarskogo-kraya"
        existing_listing = RawDocument(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="Приказы минсельхоза Краснодарского края",
            url=listing_url,
            published_at=datetime.now(timezone.utc),
            collected_at=datetime.now(timezone.utc),
            content_hash="existing-krasnodar-listing",
            raw_text="Существующая строка листинга с приказами минсельхоза Краснодарского края.",
            document_type="html",
            status="collected",
        )
        save_document(existing_listing, db_path)

        source = KrasnodarSource(source_config)
        root_html = """
        <html><body>
          <a href="/documents/prikazy-minselkhoza-krasnodarskogo-kraya">Приказы минсельхоза Краснодарского края</a>
        </body></html>
        """
        listing_html = """
        <html><body>
          <div class="doc-row">
            <span>№ 167 от 07.05.2026 "Об утверждении Порядка предоставления субсидий на картофель и овощи"</span>
            <a href="/documents/prikazy-minselkhoza-krasnodarskogo-kraya/download?id=167">pdf, 82.41 КБ скачать документ</a>
          </div>
        </body></html>
        """

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str):
            if url == source_config.url:
                return Response(root_html, source_config.url)
            if url == listing_url:
                return Response(listing_html, listing_url)
            raise AssertionError(f"Unexpected recursive fetch: {url}")

        source.get = fake_get  # type: ignore[method-assign]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=source):
                with patch(
                    "app.pipeline.collect.extract_document",
                    return_value=ExtractionResult(
                        raw_text="Текст приказа о предоставлении субсидий на картофель и овощи.",
                        document_type="pdf",
                        extracted_text_length=62,
                    ),
                ):
                    saved_count = run_collect_with_options(
                        source_name=source_config.name,
                        limit=10,
                        audit_existing=False,
                        db_path=str(db_path),
                    )

        documents = list_documents(db_path=db_path)
        self.assertEqual(saved_count, 1)
        self.assertEqual(len(documents), 2)
        urls = {document.url for document in documents}
        self.assertIn(listing_url, urls)
        self.assertIn(
            "https://msh.krasnodar.ru/documents/prikazy-minselkhoza-krasnodarskogo-kraya/download?id=167",
            urls,
        )
        self.assertEqual(source.last_fetch_stats["harvested_attachment_count"], 1)

    def test_gisp_collect_saves_only_measure_cards_from_noisy_paginated_listing(self) -> None:
        db_path = self._db_path("collect_gisp_measure_cards_only.db")
        init_db(db_path)
        source_config = SourceConfig(
            name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/main/1?recommended=0&searchstr=%D0%90%D0%9F%D0%9A",
            level="support_measures",
            region="federal",
            source_role="active_support_measures",
            parser="generic_html",
            description="test",
            max_items=3,
            deny_patterns=["recommended=", "page="],
            allow_patterns=["nmp", "support", "measure", "apk"],
        )
        source = GenericHTMLSource(source_config)
        page_one_url = source_config.url
        page_two_url = "https://gisp.gov.ru/nmp/main/2?recommended=0&searchstr=%D0%90%D0%9F%D0%9A"
        page_one_html = """
        <html><body>
          <a href="/nmp/compare/">Сравнить</a>
          <a href="/nmp/sso?BACKURL=https://gisp.gov.ru/nmp/measure/9512857">Войти</a>
          <a href="/nmp/measure/9564204">Льготное кредитование АПК</a>
        </body></html>
        """
        page_two_html = """
        <html><body>
          <a href="/nmp/measure/9512857">Поддержка экспорта</a>
          <a href="/nmp/measure/12446930">Субсидия на кооперацию</a>
          <a href="/nmp/main/3?recommended=0&searchstr=%D0%90%D0%9F%D0%9A">Следующая</a>
        </body></html>
        """

        class Response:
            def __init__(self, text: str, url: str) -> None:
                self.text = text
                self.url = url

        def fake_get(url: str):
            if url == page_one_url:
                return Response(page_one_html, page_one_url)
            if url == page_two_url:
                return Response(page_two_html, page_two_url)
            raise AssertionError(f"Unexpected GISP page fetch: {url}")

        source.get = fake_get  # type: ignore[method-assign]

        def fake_extract_document(item: CollectedItem, source_config: SourceConfig) -> ExtractionResult:
            return ExtractionResult(
                raw_text=f"Карточка меры поддержки: {item.title}",
                document_type="html",
                extracted_text_length=len(item.title),
            )

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=source):
                with patch("app.pipeline.collect.extract_document", side_effect=fake_extract_document):
                    saved_count = run_collect_with_options(
                        source_name=source_config.name,
                        limit=10,
                        audit_existing=False,
                        db_path=str(db_path),
                    )

        self.assertEqual(saved_count, 3)
        documents = list_documents(db_path=db_path)
        self.assertEqual(
            {document.url for document in documents},
            {
                "https://gisp.gov.ru/nmp/measure/9564204",
                "https://gisp.gov.ru/nmp/measure/9512857",
                "https://gisp.gov.ru/nmp/measure/12446930",
            },
        )
        audit_rows = list_latest_source_audit(db_path=db_path)
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0]["fetched_count"], 3)
        self.assertGreaterEqual(int(audit_rows[0]["links_filtered_count"] or 0), 2)


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


class SourceRegistryTest(unittest.TestCase):
    def test_create_source_uses_promote_budget_parser(self) -> None:
        config = SourceConfig(
            name="promote.budget.gov.ru / Минфин - отборы и меры поддержки",
            url="https://promote.budget.gov.ru/public/minfin/activity",
            level="support_measures",
            region="federal",
            source_role="active_support_measures",
            parser="promote_budget",
            description="test",
        )

        source = create_source(config)

        self.assertIsInstance(source, PromoteBudgetSource)

    def test_create_source_uses_pravo_stavregion_api_parser(self) -> None:
        config = SourceConfig(
            name="Право Ставропольского края",
            url="https://pravo.stavregion.ru/",
            level="regional",
            region="stavropol",
            source_role="regional_npa",
            parser="pravo_stavregion_api",
            description="test",
        )

        source = create_source(config)

        self.assertIsInstance(source, PravoStavregionApiSource)

    def test_audit_existing_prefers_prefilled_raw_text_for_promote_budget_items(self) -> None:
        db_path = Path("data/test_artifacts/collect_promote_budget_audit.db")
        if db_path.exists():
            db_path.unlink()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        init_db(db_path)

        source_config = SourceConfig(
            name="promote.budget.gov.ru / Минфин - отборы и меры поддержки",
            url="https://promote.budget.gov.ru/public/minfin/activity",
            level="support_measures",
            region="federal",
            source_role="active_support_measures",
            parser="promote_budget",
            description="test",
        )
        item = CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="Грант Агростартап",
            url="https://promote.budget.gov.ru/public/minfin/activity#activityId=a1&competitionId=c1&id=i1",
            document_type="html",
            raw_text="title: Грант Агростартап\nendDate: 2026-05-14T20:59:00Z",
        )
        save_document(
            RawDocument(
                source_name=source_config.name,
                source_url=source_config.url,
                level=source_config.level,
                region=source_config.region,
                title=item.title,
                url=item.url,
                published_at=datetime.now(timezone.utc),
                collected_at=datetime.now(timezone.utc),
                content_hash="existing-promote-budget",
                raw_text=item.raw_text or "",
                document_type="html",
                status="collected",
            ),
            db_path,
        )

        class FakeSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {"links_found_count": 1, "html_links_count": 1}

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch("app.pipeline.collect.extract_document") as mock_extract:
                    saved_count = run_collect_with_options(
                        source_name=source_config.name,
                        audit_existing=True,
                        db_path=str(db_path),
                    )

        self.assertEqual(saved_count, 0)
        mock_extract.assert_not_called()


class RefreshExistingUrlTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path(f"data/test_artifacts/{name}")
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _source_config(self, source_role: str = "support_documents") -> SourceConfig:
        return SourceConfig(
            name="Тестовый источник",
            url="https://mcx.gov.ru/activity/state-support/measures/",
            level="federal",
            region="federal",
            source_role=source_role,  # type: ignore[arg-type]
            parser="mcx",
            description="test",
        )

    def _saved_doc(self, db_path: Path, url: str, raw_text: str, content_hash: str) -> RawDocument:
        doc = RawDocument(
            source_name="Тестовый источник",
            source_url="https://mcx.gov.ru/activity/state-support/measures/",
            level="federal",
            region="federal",
            title="Мера поддержки",
            url=url,
            published_at=None,
            collected_at=datetime.now(timezone.utc),
            content_hash=content_hash,
            raw_text=raw_text,
            document_type="html",
            status="collected",
        )
        save_document(doc, db_path)
        return doc

    def _item(self, source_config: SourceConfig, url: str, raw_text: str) -> CollectedItem:
        return CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="Мера поддержки",
            url=url,
            document_type="html",
            raw_text=raw_text,
        )

    def test_refresh_updates_document_when_prefilled_raw_text_changed(self) -> None:
        db_path = self._db_path("collect_refresh_changed.db")
        init_db(db_path)
        source_config = self._source_config()
        url = "https://mcx.gov.ru/activity/state-support/measures/12345/"
        old_text = "Старый текст меры господдержки"
        new_text = "Обновлённый текст меры с новыми условиями"
        old_hash = compute_content_hash(old_text, fallback=f"Мера поддержки\n{url}")
        self._saved_doc(db_path, url, old_text, old_hash)

        item = self._item(source_config, url, new_text)

        class FakeSource:
            last_fetch_stats: dict = {}

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                saved = run_collect_with_options(
                    source_name=source_config.name,
                    audit_existing=False,
                    db_path=str(db_path),
                )

        self.assertEqual(saved, 0)
        self.assertEqual(len(list_documents(db_path=db_path)), 1)
        refreshed = get_document_by_url(url, db_path=db_path)
        assert refreshed is not None
        self.assertEqual(refreshed.raw_text, new_text)
        new_hash = compute_content_hash(new_text, fallback=f"Мера поддержки\n{url}")
        self.assertEqual(refreshed.content_hash, new_hash)

    def test_refresh_skips_update_when_content_unchanged(self) -> None:
        db_path = self._db_path("collect_refresh_unchanged.db")
        init_db(db_path)
        source_config = self._source_config()
        url = "https://mcx.gov.ru/activity/state-support/measures/12345/"
        text = "Текст меры господдержки без изменений"
        content_hash = compute_content_hash(text, fallback=f"Мера поддержки\n{url}")
        self._saved_doc(db_path, url, text, content_hash)

        item = self._item(source_config, url, text)

        update_calls: list[str] = []

        class FakeSource:
            last_fetch_stats: dict = {}

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch(
                    "app.pipeline.collect.update_document_text_by_url",
                    side_effect=lambda **kw: update_calls.append(kw["document_url"]),
                ):
                    run_collect_with_options(
                        source_name=source_config.name,
                        audit_existing=False,
                        db_path=str(db_path),
                    )

        self.assertEqual(update_calls, [])

    def test_existing_url_not_refreshed_for_non_matching_source(self) -> None:
        db_path = self._db_path("collect_refresh_no_match.db")
        init_db(db_path)
        source_config = self._source_config(source_role="regional_npa")
        url = "https://example.com/doc.html"
        self._saved_doc(db_path, url, "Старый текст", "old-hash")

        item = CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="Документ",
            url=url,
            document_type="html",
            raw_text=None,
        )

        update_calls: list[str] = []

        class FakeSource:
            last_fetch_stats: dict = {}

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch(
                    "app.pipeline.collect.update_document_text_by_url",
                    side_effect=lambda **kw: update_calls.append(kw["document_url"]),
                ):
                    run_collect_with_options(
                        source_name=source_config.name,
                        audit_existing=False,
                        db_path=str(db_path),
                    )

        self.assertEqual(update_calls, [])

    def test_refresh_active_support_measures_updates_on_changed_content(self) -> None:
        db_path = self._db_path("collect_refresh_gisp.db")
        init_db(db_path)
        source_config = self._source_config(source_role="active_support_measures")
        url = "https://gisp.gov.ru/nmp/measure/12345/"
        old_text = "Мера: субсидирование процентной ставки. Срок подачи: 01.04.2026."
        new_text = "Мера: субсидирование процентной ставки. Срок подачи: 01.06.2026."
        old_hash = compute_content_hash(old_text, fallback=f"Мера поддержки\n{url}")
        self._saved_doc(db_path, url, old_text, old_hash)

        item = CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="Мера поддержки",
            url=url,
            document_type="html",
            raw_text=None,
        )

        class FakeSource:
            last_fetch_stats: dict = {}

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch(
                    "app.pipeline.collect.extract_document",
                    return_value=ExtractionResult(
                        raw_text=new_text,
                        document_type="html",
                        extracted_text_length=len(new_text),
                    ),
                ):
                    saved = run_collect_with_options(
                        source_name=source_config.name,
                        audit_existing=False,
                        db_path=str(db_path),
                    )

        self.assertEqual(saved, 0)
        self.assertEqual(len(list_documents(db_path=db_path)), 1)
        refreshed = get_document_by_url(url, db_path=db_path)
        assert refreshed is not None
        self.assertEqual(refreshed.raw_text, new_text)
        new_hash = compute_content_hash(new_text, fallback=f"Мера поддержки\n{url}")
        self.assertEqual(refreshed.content_hash, new_hash)


if __name__ == "__main__":
    unittest.main()
