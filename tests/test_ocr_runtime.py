from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.models import AnalysisResult, ExtractionResult, RawDocument
from app.pipeline.ocr_runtime import backfill_ocr_queue_from_audit, run_ocr_queue
from app.storage import (
    get_document_by_url,
    init_db,
    list_ocr_queue,
    reprioritize_high_value_ocr_queue,
    save_document,
    save_document_extraction_audit,
    upsert_ocr_queue_item,
    update_analysis,
)


class OcrRuntimeTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path(f"data/test_artifacts/{name}")
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _seed_document(self, *, db_path: Path, url: str, source_name: str, action_level: str | None) -> None:
        document = RawDocument(
            source_name=source_name,
            source_url="https://example.com/source",
            level="regional",
            region="krasnodar",
            title="Скан НПА",
            url=url,
            published_at=datetime.now(timezone.utc),
            collected_at=datetime.now(timezone.utc),
            content_hash=f"hash-{url}",
            raw_text="",
            document_type="pdf",
            action_level=action_level,
            status="collected",
        )
        save_document(document, db_path)

    def _seed_scan_candidate_audit(self, *, db_path: Path, source_name: str, url: str) -> None:
        save_document_extraction_audit(
            source_name=source_name,
            source_url="https://example.com/source",
            document_url=url,
            attachment_url=url,
            file_type="pdf",
            extracted_type="pdf",
            raw_text_length=0,
            has_text=False,
            scan_candidate=True,
            needs_ocr=True,
            ocr_status="unavailable",
            ocr_text_length=0,
            ocr_error="OCR runtime unavailable",
            ocr_pages_processed=0,
            page_count=4,
            extraction_error=None,
            db_path=db_path,
        )

    def test_ocr_backfill_creates_queue_items_from_scan_candidate_audit(self) -> None:
        db_path = self._db_path("ocr_backfill_create.db")
        init_db(db_path)
        source_name = "Нормативные акты Краснодарского края"
        url = "https://example.com/backfill-1.pdf"
        self._seed_document(
            db_path=db_path,
            url=url,
            source_name=source_name,
            action_level="watchlist",
        )
        self._seed_scan_candidate_audit(
            db_path=db_path,
            source_name=source_name,
            url=url,
        )

        result = backfill_ocr_queue_from_audit(
            source_name=source_name,
            limit=50,
            db_path=db_path,
        )

        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.queued, 1)
        self.assertEqual(result.existing, 0)
        queue_rows = list_ocr_queue(db_path=db_path, statuses=["pending"], limit=10)
        self.assertEqual(len(queue_rows), 1)
        self.assertEqual(queue_rows[0]["priority"], "high")

    def test_ocr_backfill_is_idempotent(self) -> None:
        db_path = self._db_path("ocr_backfill_idempotent.db")
        init_db(db_path)
        source_name = "Право Ростовской области"
        url = "https://example.com/backfill-2.pdf"
        self._seed_document(
            db_path=db_path,
            url=url,
            source_name=source_name,
            action_level="background",
        )
        self._seed_scan_candidate_audit(
            db_path=db_path,
            source_name=source_name,
            url=url,
        )

        first = backfill_ocr_queue_from_audit(db_path=db_path)
        second = backfill_ocr_queue_from_audit(db_path=db_path)

        self.assertEqual(first.queued, 1)
        self.assertEqual(second.queued, 0)
        self.assertEqual(second.existing, 1)
        rows = list_ocr_queue(db_path=db_path, limit=10)
        self.assertEqual(len(rows), 1)

    def test_ocr_run_checks_queued_item_and_sets_unavailable_note(self) -> None:
        db_path = self._db_path("ocr_run_unavailable.db")
        init_db(db_path)
        source_name = "Нормативные акты Краснодарского края"
        url = "https://example.com/queued-unavailable.pdf"
        self._seed_document(
            db_path=db_path,
            url=url,
            source_name=source_name,
            action_level="watchlist",
        )
        upsert_ocr_queue_item(
            document_url=url,
            source_name=source_name,
            title="Скан НПА",
            priority="high",
            reason="scan_candidate_pdf",
            db_path=db_path,
        )

        mocked = ExtractionResult(
            raw_text="",
            document_type="pdf",
            needs_ocr=True,
            page_count=3,
            extracted_text_length=0,
            ocr_status="unavailable",
            ocr_error="OCR runtime unavailable",
        )
        with patch("app.pipeline.ocr_runtime.extract_text_from_pdf", return_value=mocked):
            result = run_ocr_queue(
                source_name=source_name,
                limit=1,
                db_path=db_path,
            )

        self.assertEqual(result.checked, 1)
        self.assertEqual(result.unavailable, 1)
        pending_rows = list_ocr_queue(db_path=db_path, statuses=["pending"], limit=10)
        self.assertEqual(len(pending_rows), 1)
        self.assertIn("OCR unavailable", str(pending_rows[0].get("notes") or ""))

    def test_ocr_run_success_updates_document_and_marks_queue_done(self) -> None:
        db_path = self._db_path("ocr_run_success.db")
        init_db(db_path)
        source_name = "Нормативные акты Краснодарского края"
        url = "https://example.com/queued-success.pdf"
        self._seed_document(
            db_path=db_path,
            url=url,
            source_name=source_name,
            action_level="requires_attention",
        )
        upsert_ocr_queue_item(
            document_url=url,
            source_name=source_name,
            title="Скан НПА",
            priority="high",
            reason="scan_candidate_pdf",
            db_path=db_path,
        )

        mocked = ExtractionResult(
            raw_text="OCR extracted text",
            document_type="pdf",
            needs_ocr=False,
            page_count=2,
            extracted_text_length=18,
            ocr_status="success",
            ocr_text_length=18,
            ocr_pages_processed=2,
        )
        with patch("app.pipeline.ocr_runtime.extract_text_from_pdf", return_value=mocked):
            result = run_ocr_queue(
                source_name=source_name,
                limit=1,
                db_path=db_path,
            )

        self.assertEqual(result.checked, 1)
        self.assertEqual(result.success, 1)
        self.assertEqual(result.updated, 1)
        updated_doc = get_document_by_url(url, db_path=db_path)
        self.assertIsNotNone(updated_doc)
        assert updated_doc is not None
        self.assertIn("OCR extracted text", updated_doc.raw_text)
        done_rows = list_ocr_queue(db_path=db_path, statuses=["done"], limit=10)
        self.assertEqual(len(done_rows), 1)

    def test_ocr_run_handles_fetch_error_without_crash(self) -> None:
        db_path = self._db_path("ocr_run_fetch_error.db")
        init_db(db_path)
        source_name = "Нормативные акты Краснодарского края"
        url = "https://example.com/queued-fetch-error.pdf"
        self._seed_document(
            db_path=db_path,
            url=url,
            source_name=source_name,
            action_level="watchlist",
        )
        upsert_ocr_queue_item(
            document_url=url,
            source_name=source_name,
            title="Скан НПА",
            priority="high",
            reason="scan_candidate_pdf",
            db_path=db_path,
        )

        with patch(
            "app.pipeline.ocr_runtime.extract_text_from_pdf",
            side_effect=RuntimeError("network timeout"),
        ):
            result = run_ocr_queue(
                source_name=source_name,
                limit=1,
                db_path=db_path,
            )

        self.assertEqual(result.checked, 1)
        self.assertEqual(result.failed, 1)
        pending_rows = list_ocr_queue(db_path=db_path, statuses=["pending"], limit=10)
        self.assertEqual(len(pending_rows), 1)
        self.assertIn("OCR fetch failed", str(pending_rows[0].get("notes") or ""))

    def test_reprioritize_upgrades_pending_queue_item_after_analysis(self) -> None:
        db_path = self._db_path("reprioritize_after_analysis.db")
        init_db(db_path)
        document = RawDocument(
            source_name="ГИСП - меры поддержки АПК",
            source_url="https://gisp.gov.ru/",
            level="federal",
            region="federal",
            title="Постановление о льготном кредитовании",
            url="https://gisp.gov.ru/doc/scan.pdf",
            content_hash="abc123",
            raw_text="",
            status="collected",
        )
        save_document(document, db_path=db_path)

        saved_docs = list_ocr_queue(db_path=db_path, limit=10)
        doc_id = 1

        upsert_ocr_queue_item(
            document_url="https://gisp.gov.ru/doc/scan.pdf",
            source_name="ГИСП - меры поддержки АПК",
            title="Постановление о льготном кредитовании",
            priority="medium",
            reason="scan_candidate",
            db_path=db_path,
        )

        queue_before = list_ocr_queue(db_path=db_path, limit=10)
        self.assertEqual(len(queue_before), 1)
        self.assertEqual(queue_before[0]["priority"], "medium")

        analysis = AnalysisResult(
            is_relevant=True,
            relevance_reason="Федеральная мера поддержки АПК",
            normalized_title="Постановление о льготном кредитовании",
            topic="льготное кредитование",
            importance="high",
            action_level="watchlist",
            page_type="measure_card",
            summary="Льготное кредитование АПК",
            impact="Прямой доступ к льготным кредитам",
        )
        update_analysis(doc_id, analysis, db_path=db_path)

        upgraded = reprioritize_high_value_ocr_queue(db_path=db_path)
        self.assertEqual(upgraded, 1)

        queue_after = list_ocr_queue(db_path=db_path, limit=10)
        self.assertEqual(queue_after[0]["priority"], "high")


if __name__ == "__main__":
    unittest.main()
