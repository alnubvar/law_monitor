from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.models import RawDocument
from app.pipeline.collect import _is_scan_candidate
from app.pipeline.diagnostics import run_diagnostics
from app.pipeline.digest import run_demo_report
from unittest.mock import patch
from app.storage import (
    init_db,
    save_document,
    save_document_extraction_audit,
    save_source_audit_record,
    upsert_ocr_queue_item,
    update_ocr_queue_status,
)
from app.models import ExtractionResult


class DiagnosticsSmokeTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path(f"data/test_artifacts/{name}")
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _doc(
        self,
        *,
        doc_id: int,
        source_name: str,
        title: str,
        action_level: str,
        page_type: str,
        summary: str = "summary",
        raw_text: str = "raw text",
    ) -> RawDocument:
        return RawDocument(
            id=doc_id,
            source_name=source_name,
            source_url=f"https://example.com/{doc_id}",
            level="federal",
            region="federal",
            title=title,
            url=f"https://example.com/doc/{doc_id}",
            published_at=datetime.now(timezone.utc),
            collected_at=datetime.now(timezone.utc),
            content_hash=f"hash-{doc_id}",
            raw_text=raw_text,
            is_relevant=action_level != "irrelevant",
            relevance_reason="reason",
            importance="high" if action_level == "requires_attention" else "medium",
            action_level=action_level,
            page_type=page_type,
            summary=summary,
            impact="impact",
            topic="topic",
            support_status="active" if page_type == "measure_card" else "unknown",
            application_status="regular" if page_type == "measure_card" else "unknown",
            business_signal="signal",
            status="analyzed",
        )

    def test_diagnostics_does_not_fail_on_empty_database(self) -> None:
        db_path = self._db_path("diagnostics_empty.db")
        init_db(db_path)

        output = run_diagnostics(db_path=db_path)

        self.assertIn("Total documents: 0", output)
        self.assertIn("Published_at coverage: 0/0", output)
        self.assertIn("No documents found in the selected period.", output)

    def test_diagnostics_days_output_contains_period_and_filter(self) -> None:
        db_path = self._db_path("diagnostics_days.db")
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            title="Льготное кредитование АПК",
            action_level="requires_attention",
            page_type="measure_card",
        )
        document.published_at = None
        save_document(document, db_path)

        output = run_diagnostics(db_path=db_path, days=7)

        self.assertIn("Period: last 7 days", output)
        self.assertIn("Date filter: published_at with collected_at fallback", output)
        self.assertIn(
            "Warning: many documents have no published_at; --days uses published_at with collected_at fallback.",
            output,
        )

    def test_diagnostics_correctly_counts_action_levels(self) -> None:
        db_path = self._db_path("diagnostics_counts.db")
        init_db(db_path)
        first = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            title="Льготное кредитование АПК",
            action_level="requires_attention",
            page_type="measure_card",
        )
        second = self._doc(
            doc_id=2,
            source_name="ГИСП - меры поддержки АПК",
            title="Гарантия ВЭБ.РФ",
            action_level="watchlist",
            page_type="measure_card",
            summary="",
        )
        second.published_at = None
        third = self._doc(
            doc_id=3,
            source_name="ZOL.ru - зерновые новости",
            title="Навигационная страница",
            action_level="irrelevant",
            page_type="navigation",
            raw_text="",
        )

        for document in (first, second, third):
            save_document(document, db_path)

        output = run_diagnostics(db_path=db_path)

        self.assertIn("- requires_attention: 1", output)
        self.assertIn("- watchlist: 1", output)
        self.assertIn("- irrelevant: 1", output)
        self.assertIn("Published_at coverage: 2/3", output)
        self.assertIn("By source_role:", output)
        self.assertIn("- active_support_measures", output)
        self.assertIn("- news_signals", output)
        self.assertIn("- ГИСП - меры поддержки АПК [active_support_measures]", output)
        self.assertIn("total=2; RA=1; WL=1; BG=0; IRR=0", output)
        self.assertIn("missing published_at=1; coverage=1/2", output)
        self.assertIn("missing summary=1", output)
        self.assertIn("measure_card=2", output)
        self.assertIn("Low-signal sources:", output)

    def test_source_lines_are_multiline_and_not_glued(self) -> None:
        db_path = self._db_path("diagnostics_multiline.db")
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            title="Раздел субсидий",
            action_level="watchlist",
            page_type="reference_page",
        )
        save_document(document, db_path)

        output = run_diagnostics(db_path=db_path)

        self.assertIn("- Минсельхоз Краснодарского края - субсидирование и финансирование [support_documents]", output)
        self.assertIn("registry/results=", output)
        self.assertNotIn("missineference_page", output)
        self.assertNotIn("irrelev-", output)
        self.assertNotIn("registr-", output)

    def test_low_signal_score_does_not_count_plain_watchlist_as_noise(self) -> None:
        db_path = self._db_path("diagnostics_noise.db")
        init_db(db_path)
        watchlist_document = self._doc(
            doc_id=1,
            source_name="ZOL.ru - зерновые новости",
            title="Производство сельхозпродукции в РФ выросло",
            action_level="watchlist",
            page_type="news_background",
        )
        irrelevant_document = self._doc(
            doc_id=2,
            source_name="ZOL.ru - зерновые новости",
            title="Навигация",
            action_level="irrelevant",
            page_type="navigation",
        )
        save_document(watchlist_document, db_path)
        save_document(irrelevant_document, db_path)

        output = run_diagnostics(db_path=db_path)

        self.assertIn("ZOL.ru - зерновые новости: low_signal=1/2 (50%)", output)

    def test_demo_report_generation_does_not_require_telegram_or_env(self) -> None:
        db_path = self._db_path("demo_report.db")
        output_path = Path("data/test_artifacts/demo_report.md")
        if output_path.exists():
            output_path.unlink()
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            title="Льготное кредитование АПК",
            action_level="requires_attention",
            page_type="measure_card",
        )
        document.npa_number = "22-68850-00258-Р"
        document.terms_text = "Срок кредита: До 12 месяцев."
        document.business_signal = "Активная федеральная мера поддержки, действует на регулярной основе"
        save_document(document, db_path)

        path = run_demo_report(db_path=db_path, output_path=str(output_path))
        markdown = path.read_text(encoding="utf-8")

        self.assertTrue(path.exists())
        self.assertIn("# AHSTEP Demo Report", markdown)
        self.assertIn("Льготное кредитование АПК", markdown)
        self.assertIn("Почему важно:", markdown)
        self.assertIn("Активная федеральная мера поддержки", markdown)

    def test_diagnostics_includes_parser_quality_hints(self) -> None:
        db_path = self._db_path("diagnostics_hints.db")
        init_db(db_path)
        noisy = self._doc(
            doc_id=1,
            source_name="Нормативные акты Краснодарского края",
            title="Просмотр",
            action_level="irrelevant",
            page_type="unknown",
        )
        noisy.published_at = None
        save_document(noisy, db_path)

        output = run_diagnostics(db_path=db_path, days=7)

        self.assertIn("Parser quality hints:", output)
        self.assertIn("high missing published_at:", output)
        self.assertIn("high low_signal:", output)
        self.assertIn("many unknown page_type:", output)

    def test_diagnostics_groups_by_source_role(self) -> None:
        db_path = self._db_path("diagnostics_roles.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="Правительство РФ - документы",
                title="Постановление о господдержке АПК",
                action_level="watchlist",
                page_type="new_rule",
            ),
            db_path,
        )
        save_document(
            self._doc(
                doc_id=2,
                source_name="ZOL.ru - зерновые новости",
                title="Пошлина на экспорт пшеницы останется нулевой",
                action_level="watchlist",
                page_type="news_background",
            ),
            db_path,
        )

        output = run_diagnostics(db_path=db_path)

        self.assertIn("- strategy", output)
        self.assertIn("- news_signals", output)
        self.assertIn("coverage=1/1", output)

    def test_diagnostics_includes_source_coverage_audit_errors(self) -> None:
        db_path = self._db_path("diagnostics_source_audit.db")
        init_db(db_path)
        with patch(
            "app.pipeline.diagnostics.list_latest_source_audit",
            return_value=[
                {
                    "source_name": "ZOL.ru - зерновые новости",
                    "attempted_at": datetime.now(timezone.utc),
                    "success_at": None,
                    "error_at": datetime.now(timezone.utc),
                    "error_message": "timeout",
                    "fetched_count": 0,
                    "saved_count": 0,
                    "existing_count": 0,
                    "duplicates_count": 0,
                    "item_errors_count": 1,
                }
            ],
        ):
            output = run_diagnostics(db_path=db_path, days=7)
        self.assertIn("Source coverage audit:", output)
        self.assertIn("last_error_message=timeout", output)

    def test_text_pdf_is_not_scan_candidate(self) -> None:
        extracted = ExtractionResult(
            raw_text="Текст документа " * 120,
            document_type="pdf",
            needs_ocr=False,
            page_count=4,
            extracted_text_length=1600,
        )
        self.assertFalse(_is_scan_candidate(file_type="pdf", extracted=extracted))

    def test_empty_pdf_is_scan_candidate(self) -> None:
        extracted = ExtractionResult(
            raw_text="",
            document_type="pdf",
            needs_ocr=True,
            page_count=5,
            extracted_text_length=0,
        )
        self.assertTrue(_is_scan_candidate(file_type="pdf", extracted=extracted))

    def test_diagnostics_includes_extraction_quality_block_and_docx_success(self) -> None:
        db_path = self._db_path("diagnostics_extraction_quality.db")
        init_db(db_path)
        save_document_extraction_audit(
            source_name="Право Ростовской области",
            source_url="https://pravo.donland.ru/",
            document_url="https://pravo.donland.ru/files/postanovlenie.docx",
            attachment_url="https://pravo.donland.ru/files/postanovlenie.docx",
            file_type="docx",
            extracted_type="docx",
            raw_text_length=1200,
            has_text=True,
            scan_candidate=False,
            needs_ocr=False,
            page_count=None,
            extraction_error=None,
            db_path=db_path,
        )
        output = run_diagnostics(db_path=db_path, days=7)
        self.assertIn("Document extraction quality", output)
        self.assertIn("DOCX: total=1; with_text=1", output)

    def test_scan_candidate_is_not_treated_as_fully_extracted_and_shows_ocr_warning(self) -> None:
        db_path = self._db_path("diagnostics_scan_candidate_warning.db")
        init_db(db_path)
        save_document_extraction_audit(
            source_name="Нормативные акты Краснодарского края",
            source_url="https://admkrai.krasnodar.ru/content/1291/",
            document_url="https://admkrai.krasnodar.ru/upload/scan.pdf",
            attachment_url="https://admkrai.krasnodar.ru/upload/scan.pdf",
            file_type="pdf",
            extracted_type="pdf",
            raw_text_length=50,
            has_text=True,
            scan_candidate=True,
            needs_ocr=True,
            page_count=6,
            extraction_error=None,
            db_path=db_path,
        )

        output = run_diagnostics(db_path=db_path, days=7)
        self.assertIn("PDF fully extracted (text-layer ok): 0/1", output)
        self.assertIn("требуется OCR для полного анализа", output)

    def test_scan_candidate_for_visible_document_raises_priority_warning(self) -> None:
        db_path = self._db_path("diagnostics_scan_candidate_visible_warning.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=99,
                source_name="Нормативные акты Краснодарского края",
                title="Приказ о поддержке",
                action_level="requires_attention",
                page_type="new_rule",
            ).model_copy(update={"document_type": "pdf", "url": "https://admkrai.krasnodar.ru/upload/important-scan.pdf"}),
            db_path,
        )
        save_document_extraction_audit(
            source_name="Нормативные акты Краснодарского края",
            source_url="https://admkrai.krasnodar.ru/content/1291/",
            document_url="https://admkrai.krasnodar.ru/upload/important-scan.pdf",
            attachment_url="https://admkrai.krasnodar.ru/upload/important-scan.pdf",
            file_type="pdf",
            extracted_type="pdf",
            raw_text_length=0,
            has_text=False,
            scan_candidate=True,
            needs_ocr=True,
            page_count=5,
            extraction_error=None,
            db_path=db_path,
        )

        output = run_diagnostics(db_path=db_path, days=7)
        self.assertIn("Priority warning: среди видимых документов есть PDF/вложения, требующие OCR (1).", output)

    def test_missing_raw_text_appears_in_extraction_audit(self) -> None:
        db_path = self._db_path("diagnostics_missing_text.db")
        init_db(db_path)
        save_document_extraction_audit(
            source_name="Минсельхоз Ростовской области - господдержка",
            source_url="https://mcx.donland.ru/activity/35217/",
            document_url="https://mcx.donland.ru/activity/35217/",
            attachment_url=None,
            file_type="html",
            extracted_type="html",
            raw_text_length=0,
            has_text=False,
            scan_candidate=False,
            needs_ocr=False,
            page_count=None,
            extraction_error="empty",
            db_path=db_path,
        )
        output = run_diagnostics(db_path=db_path, days=7)
        self.assertIn("Top sources by missing raw_text:", output)
        self.assertIn("Минсельхоз Ростовской области - господдержка: 1", output)

    def test_diagnostics_includes_ocr_triage_queue_block(self) -> None:
        db_path = self._db_path("diagnostics_ocr_queue_block.db")
        init_db(db_path)
        upsert_ocr_queue_item(
            document_url="https://example.com/scan-pending.pdf",
            source_name="Право Ростовской области",
            title="Скан pending",
            priority="high",
            reason="scan_candidate_pdf",
            db_path=db_path,
        )
        upsert_ocr_queue_item(
            document_url="https://example.com/scan-done.pdf",
            source_name="Право Ростовской области",
            title="Скан done",
            priority="medium",
            reason="scan_candidate_pdf",
            db_path=db_path,
        )
        update_ocr_queue_status(
            document_url="https://example.com/scan-done.pdf",
            status="done",
            db_path=db_path,
        )

        output = run_diagnostics(db_path=db_path, days=7)
        self.assertIn("OCR runtime (last 7 days):", output)
        self.assertIn("OCR success count:", output)
        self.assertIn("OCR triage queue:", output)
        self.assertIn("pending: 1", output)
        self.assertIn("in_review: 0", output)
        self.assertIn("done: 1", output)
        self.assertIn("skipped: 0", output)
        self.assertIn("high priority pending: 1", output)

    def test_diagnostics_warns_when_scan_candidates_exist_but_queue_is_empty(self) -> None:
        db_path = self._db_path("diagnostics_ocr_backfill_warning.db")
        init_db(db_path)
        save_document_extraction_audit(
            source_name="Нормативные акты Краснодарского края",
            source_url="https://admkrai.krasnodar.ru/content/1291/",
            document_url="https://example.com/unresolved-scan.pdf",
            attachment_url="https://example.com/unresolved-scan.pdf",
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
            page_count=5,
            extraction_error=None,
            db_path=db_path,
        )

        output = run_diagnostics(db_path=db_path, days=7)
        self.assertIn(
            "OCR queue is empty but unresolved scan candidates exist. Run: python main.py ocr-backfill",
            output,
        )

    def test_ocr_success_document_is_excluded_from_documents_requiring_ocr(self) -> None:
        db_path = self._db_path("diagnostics_ocr_success_excluded.db")
        init_db(db_path)
        success_url = "https://example.com/ocr-success.pdf"
        unresolved_url = "https://example.com/ocr-unresolved.pdf"
        save_document_extraction_audit(
            source_name="Право Ростовской области",
            source_url="https://pravo.donland.ru/",
            document_url=success_url,
            attachment_url=success_url,
            file_type="pdf",
            extracted_type="pdf",
            raw_text_length=1200,
            has_text=True,
            scan_candidate=True,
            needs_ocr=False,
            ocr_status="success",
            ocr_text_length=1200,
            ocr_error=None,
            ocr_pages_processed=2,
            page_count=2,
            extraction_error=None,
            db_path=db_path,
        )
        save_document_extraction_audit(
            source_name="Право Ростовской области",
            source_url="https://pravo.donland.ru/",
            document_url=unresolved_url,
            attachment_url=unresolved_url,
            file_type="pdf",
            extracted_type="pdf",
            raw_text_length=0,
            has_text=False,
            scan_candidate=True,
            needs_ocr=True,
            ocr_status="failed",
            ocr_text_length=0,
            ocr_error="OCR failed",
            ocr_pages_processed=1,
            page_count=5,
            extraction_error=None,
            db_path=db_path,
        )
        output = run_diagnostics(db_path=db_path, days=7)
        self.assertIn("Documents requiring OCR:", output)
        self.assertIn(unresolved_url, output)
        self.assertNotIn(success_url, output)

    def test_documents_requiring_ocr_deduplicates_duplicate_urls(self) -> None:
        db_path = self._db_path("diagnostics_ocr_dedup_urls.db")
        init_db(db_path)
        duplicate_url = "https://example.com/duplicate-unresolved.pdf"
        save_document_extraction_audit(
            source_name="Нормативные акты Краснодарского края",
            source_url="https://admkrai.krasnodar.ru/content/1291/",
            document_url=duplicate_url,
            attachment_url=duplicate_url,
            file_type="pdf",
            extracted_type="pdf",
            raw_text_length=0,
            has_text=False,
            scan_candidate=True,
            needs_ocr=True,
            ocr_status="failed",
            ocr_text_length=0,
            ocr_error="OCR failed",
            ocr_pages_processed=1,
            page_count=5,
            extraction_error=None,
            db_path=db_path,
        )
        save_document_extraction_audit(
            source_name="Нормативные акты Краснодарского края",
            source_url="https://admkrai.krasnodar.ru/content/1291/",
            document_url=duplicate_url,
            attachment_url=duplicate_url,
            file_type="pdf",
            extracted_type="pdf",
            raw_text_length=10,
            has_text=True,
            scan_candidate=True,
            needs_ocr=True,
            ocr_status="unavailable",
            ocr_text_length=0,
            ocr_error="OCR runtime unavailable",
            ocr_pages_processed=1,
            page_count=5,
            extraction_error=None,
            db_path=db_path,
        )
        output = run_diagnostics(db_path=db_path, days=7)
        self.assertEqual(output.count(duplicate_url), 1)

    def test_diagnostics_shows_clear_ocr_queue_pending_done_counters(self) -> None:
        db_path = self._db_path("diagnostics_ocr_queue_counters.db")
        init_db(db_path)
        upsert_ocr_queue_item(
            document_url="https://example.com/pending.pdf",
            source_name="Право Ростовской области",
            title="Pending OCR",
            priority="high",
            reason="scan_candidate_pdf",
            db_path=db_path,
        )
        upsert_ocr_queue_item(
            document_url="https://example.com/done.pdf",
            source_name="Право Ростовской области",
            title="Done OCR",
            priority="medium",
            reason="scan_candidate_pdf",
            db_path=db_path,
        )
        update_ocr_queue_status(
            document_url="https://example.com/done.pdf",
            status="done",
            db_path=db_path,
        )
        output = run_diagnostics(db_path=db_path, days=7)
        self.assertIn("OCR queue pending: 1", output)
        self.assertIn("OCR queue done: 1", output)

    def test_source_depth_stats_do_not_break_empty_db(self) -> None:
        db_path = self._db_path("diagnostics_source_depth_empty.db")
        init_db(db_path)
        output = run_diagnostics(db_path=db_path, days=7)
        self.assertIn("Source depth audit", output)

    def test_existing_pdf_without_extraction_audit_is_reported_as_gap(self) -> None:
        db_path = self._db_path("diagnostics_pdf_gap.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=10,
                source_name="Нормативные акты Краснодарского края",
                title="Приказ о субсидии",
                action_level="watchlist",
                page_type="new_rule",
                raw_text="Текст",
            ).model_copy(update={"document_type": "pdf", "url": "https://example.com/a.pdf"}),
            db_path,
        )
        output = run_diagnostics(db_path=db_path, days=7)
        self.assertIn("PDF existing without extraction audit: 1", output)

    def test_pdf_link_found_but_not_extracted_is_reported(self) -> None:
        db_path = self._db_path("diagnostics_pdf_links_gap.db")
        init_db(db_path)
        now = datetime.now(timezone.utc)
        save_source_audit_record(
            source_name="Нормативные акты Краснодарского края",
            source_url="https://admkrai.krasnodar.ru/content/1291/",
            enabled=True,
            attempted_at=now,
            success_at=now,
            error_at=None,
            error_message=None,
            fetched_count=3,
            saved_count=0,
            existing_count=0,
            duplicates_count=0,
            item_errors_count=0,
            links_found_count=10,
            links_filtered_count=7,
            pdf_links_count=3,
            db_path=db_path,
        )
        output = run_diagnostics(db_path=db_path, days=7)
        self.assertIn("PDF links found but not extracted", output)
        self.assertIn("Нормативные акты Краснодарского края", output)

    def test_source_depth_uses_updated_listing_wording(self) -> None:
        db_path = self._db_path("diagnostics_listing_wording.db")
        init_db(db_path)
        now = datetime.now(timezone.utc)
        save_source_audit_record(
            source_name="Правительство РФ - документы",
            source_url="http://government.ru/docs/",
            enabled=True,
            attempted_at=now,
            success_at=now,
            error_at=None,
            error_message=None,
            fetched_count=2,
            saved_count=0,
            existing_count=2,
            duplicates_count=0,
            item_errors_count=0,
            pdf_links_count=2,
            db_path=db_path,
        )
        output = run_diagnostics(db_path=db_path, days=7)
        self.assertIn("note=no new saves in this run (existing-heavy or listing-heavy)", output)

    def test_high_filtered_ratio_produces_warning(self) -> None:
        db_path = self._db_path("diagnostics_filtered_warning.db")
        init_db(db_path)
        now = datetime.now(timezone.utc)
        save_source_audit_record(
            source_name="Правительство РФ - документы",
            source_url="http://government.ru/docs/",
            enabled=True,
            attempted_at=now,
            success_at=now,
            error_at=None,
            error_message=None,
            fetched_count=2,
            saved_count=0,
            existing_count=0,
            duplicates_count=0,
            item_errors_count=0,
            links_found_count=100,
            links_filtered_count=95,
            db_path=db_path,
        )
        output = run_diagnostics(db_path=db_path, days=7)
        self.assertIn("warning=high filtered ratio", output)

    def test_source_access_error_is_reflected_as_coverage_warning(self) -> None:
        db_path = self._db_path("diagnostics_source_access_warning.db")
        init_db(db_path)
        now = datetime.now(timezone.utc)
        save_source_audit_record(
            source_name="Минсельхоз Ростовской области - господдержка",
            source_url="https://mcx.donland.ru/activity/35217/",
            enabled=True,
            attempted_at=now,
            success_at=None,
            error_at=now,
            error_message="source access blocked (HTTP 403)",
            fetched_count=0,
            saved_count=0,
            existing_count=0,
            duplicates_count=0,
            item_errors_count=0,
            db_path=db_path,
        )
        output = run_diagnostics(db_path=db_path, days=7)
        self.assertIn("warning=source access blocked", output)

    def test_run_diagnostics_does_not_mutate_published_at(self) -> None:
        db_path = self._db_path("diagnostics_no_mutation.db")
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            title="Документ без даты",
            action_level="background",
            page_type="news_background",
        )
        document.published_at = None
        save_document(document, db_path)

        run_diagnostics(db_path=db_path)

        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute("SELECT published_at FROM documents WHERE id = 1").fetchone()
        self.assertIsNone(row[0], "run_diagnostics must not backfill published_at")

    def test_operational_warnings_section_appears_for_blocked_source(self) -> None:
        db_path = self._db_path("diagnostics_op_warnings_blocked.db")
        init_db(db_path)
        now = datetime.now(timezone.utc)
        save_source_audit_record(
            source_name="Минсельхоз Ростовской области - господдержка",
            source_url="https://mcx.donland.ru/activity/35217/",
            enabled=True,
            attempted_at=now,
            success_at=None,
            error_at=now,
            error_message="source access blocked (HTTP 403)",
            fetched_count=0,
            saved_count=0,
            existing_count=0,
            duplicates_count=0,
            item_errors_count=0,
            db_path=db_path,
        )

        output = run_diagnostics(db_path=db_path)

        self.assertIn("Operational warnings:", output)
        self.assertIn("WARN:", output)
        self.assertIn("source access blocked", output)

    def test_operational_warnings_section_appears_for_missing_published_at(self) -> None:
        db_path = self._db_path("diagnostics_op_warnings_dates.db")
        init_db(db_path)
        for doc_id in (1, 2):
            document = self._doc(
                doc_id=doc_id,
                source_name="ГИСП - меры поддержки АПК",
                title=f"Документ {doc_id}",
                action_level="background",
                page_type="news_background",
            )
            document.published_at = None
            save_document(document, db_path)

        output = run_diagnostics(db_path=db_path)

        self.assertIn("Operational warnings:", output)
        self.assertIn("WARN: published_at missing for", output)
        self.assertIn("backfill-dates", output)

    def test_source_status_tag_blocked_for_403_source(self) -> None:
        db_path = self._db_path("diagnostics_status_tag_blocked.db")
        init_db(db_path)
        now = datetime.now(timezone.utc)
        save_source_audit_record(
            source_name="Минсельхоз Ростовской области - господдержка",
            source_url="https://mcx.donland.ru/activity/35217/",
            enabled=True,
            attempted_at=now,
            success_at=None,
            error_at=now,
            error_message="source access blocked (HTTP 403)",
            fetched_count=0,
            saved_count=0,
            existing_count=0,
            duplicates_count=0,
            item_errors_count=0,
            db_path=db_path,
        )

        output = run_diagnostics(db_path=db_path)

        self.assertIn("[BLOCKED]", output)
        self.assertIn("last_success_age=never", output)

    def test_source_status_tag_stale_for_old_success_with_error(self) -> None:
        db_path = self._db_path("diagnostics_status_tag_stale.db")
        init_db(db_path)
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        old_success = now - timedelta(days=5)
        save_source_audit_record(
            source_name="Минсельхоз Ростовской области - господдержка",
            source_url="https://mcx.donland.ru/activity/35217/",
            enabled=True,
            attempted_at=now,
            success_at=old_success,
            error_at=now,
            error_message="source connection error",
            fetched_count=0,
            saved_count=0,
            existing_count=0,
            duplicates_count=0,
            item_errors_count=0,
            db_path=db_path,
        )

        output = run_diagnostics(db_path=db_path)

        self.assertIn("[STALE]", output)
        self.assertIn("last_success_age=5d ago", output)

    def test_source_status_tag_absent_for_healthy_source(self) -> None:
        db_path = self._db_path("diagnostics_status_tag_healthy.db")
        init_db(db_path)
        now = datetime.now(timezone.utc)
        save_source_audit_record(
            source_name="ZOL.ru - зерновые новости",
            source_url="https://www.zol.ru/news/grain/",
            enabled=True,
            attempted_at=now,
            success_at=now,
            error_at=None,
            error_message=None,
            fetched_count=40,
            saved_count=3,
            existing_count=37,
            duplicates_count=0,
            item_errors_count=0,
            db_path=db_path,
        )

        output = run_diagnostics(db_path=db_path)

        self.assertIn("ZOL.ru - зерновые новости", output)
        self.assertNotIn("[BLOCKED]", output)
        self.assertNotIn("[STALE]", output)
        self.assertNotIn("[NETWORK ERROR]", output)
        self.assertIn("last_success_age=today", output)


if __name__ == "__main__":
    unittest.main()
