from __future__ import annotations

import io
import sys
import unittest
from contextlib import contextmanager, redirect_stdout
from unittest.mock import patch

import main as cli_main
from app.pipeline.enrich import EnrichDocsResult
from app.pipeline.ocr_runtime import OCRBackfillResult, OCRRunResult
from app.run_lock import WriterLockHeldError
from app.pipeline.donland_backfill import DonlandBackfillResult


class MainCliOcrQueueTest(unittest.TestCase):
    @contextmanager
    def _blocked_writer_lock(self, *_args, **_kwargs):
        raise WriterLockHeldError(lock_path=cli_main.Path("data/runtime/writer.lock"))
        yield

    def test_ocr_queue_cli_lists_pending(self) -> None:
        with patch("main.setup_logging"):
            with patch(
                "main.summarize_ocr_queue",
                return_value={
                    "pending": 2,
                    "high_priority": 1,
                    "high_priority_pending": 1,
                    "in_review": 0,
                    "done": 0,
                    "skipped": 0,
                    "done_skipped": 0,
                },
            ):
                with patch(
                    "main.list_ocr_queue",
                    return_value=[
                        {
                            "title": "Скан 1",
                            "priority": "high",
                            "status": "pending",
                            "source_name": "Нормативные акты Краснодарского края",
                            "document_url": "https://example.com/scan1.pdf",
                        }
                    ],
                ) as list_queue:
                    with patch.object(sys, "argv", ["main.py", "ocr-queue", "--status", "pending"]):
                        buffer = io.StringIO()
                        with redirect_stdout(buffer):
                            exit_code = cli_main.main()

        self.assertEqual(exit_code, 0)
        list_queue.assert_called_once_with(statuses=["pending"], priorities=None, limit=10)
        output = buffer.getvalue()
        self.assertIn("OCR triage queue", output)
        self.assertIn("Pending: 2", output)
        self.assertIn("scan1.pdf", output)

    def test_ocr_queue_cli_supports_priority_filter(self) -> None:
        with patch("main.setup_logging"):
            with patch(
                "main.summarize_ocr_queue",
                return_value={
                    "pending": 1,
                    "high_priority": 1,
                    "high_priority_pending": 1,
                    "in_review": 0,
                    "done": 0,
                    "skipped": 0,
                    "done_skipped": 0,
                },
            ):
                with patch("main.list_ocr_queue", return_value=[]) as list_queue:
                    with patch.object(sys, "argv", ["main.py", "ocr-queue", "--priority", "high"]):
                        buffer = io.StringIO()
                        with redirect_stdout(buffer):
                            exit_code = cli_main.main()

        self.assertEqual(exit_code, 0)
        list_queue.assert_called_once_with(statuses=["pending", "in_review"], priorities=["high"], limit=10)
        self.assertIn("Priority: high", buffer.getvalue())

    def test_ocr_mark_cli_changes_status(self) -> None:
        with patch("main.setup_logging"):
            with patch("main.update_ocr_queue_status", return_value=True) as update_status:
                with patch.object(
                    sys,
                    "argv",
                    [
                        "main.py",
                        "ocr-mark",
                        "https://example.com/scan2.pdf",
                        "--status",
                        "done",
                        "--notes",
                        "manual check",
                    ],
                ):
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        exit_code = cli_main.main()

        self.assertEqual(exit_code, 0)
        update_status.assert_called_once_with(
            document_url="https://example.com/scan2.pdf",
            status="done",
            notes="manual check",
        )
        output = buffer.getvalue()
        self.assertIn("OCR queue updated: status=done", output)

    def test_collect_cli_is_blocked_when_writer_lock_is_held(self) -> None:
        with patch("main.setup_logging"):
            with patch("main.writer_lock", side_effect=self._blocked_writer_lock):
                with patch.object(sys, "argv", ["main.py", "collect"]):
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        exit_code = cli_main.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("Another write operation is already running", buffer.getvalue())

    def test_enrich_docs_cli_prints_summary_without_collect(self) -> None:
        result = EnrichDocsResult(selected=2, enriched=1, skipped_cached=1, failed=0)
        with patch("main.setup_logging"):
            with patch("main.run_enrich_docs", return_value=result) as run_enrich:
                with patch.object(
                    sys,
                    "argv",
                    [
                        "main.py",
                        "enrich-docs",
                        "--days",
                        "7",
                        "--action-level",
                        "requires_attention",
                        "--limit",
                        "10",
                    ],
                ):
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        exit_code = cli_main.main()

        self.assertEqual(exit_code, 0)
        run_enrich.assert_called_once_with(
            days=7,
            action_levels=["requires_attention"],
            limit=10,
            force=False,
        )
        output = buffer.getvalue()
        self.assertIn("selected=2", output)
        self.assertIn("enriched=1", output)
        self.assertIn("skipped_cached=1", output)
        self.assertIn("failed=0", output)

    def test_read_only_ocr_queue_is_unaffected_by_writer_lock(self) -> None:
        with patch("main.setup_logging"):
            with patch(
                "main.summarize_ocr_queue",
                return_value={
                    "pending": 0,
                    "high_priority": 0,
                    "high_priority_pending": 0,
                    "in_review": 0,
                    "done": 0,
                    "skipped": 0,
                    "done_skipped": 0,
                },
            ):
                with patch("main.list_ocr_queue", return_value=[]):
                    with patch("main.writer_lock") as writer_lock:
                        with patch.object(sys, "argv", ["main.py", "ocr-queue"]):
                            buffer = io.StringIO()
                            with redirect_stdout(buffer):
                                exit_code = cli_main.main()

        self.assertEqual(exit_code, 0)
        writer_lock.assert_not_called()

    def test_ocr_run_cli_works_with_mocked_runtime(self) -> None:
        mocked_result = OCRRunResult(
            checked=2,
            updated=1,
            success=1,
            failed=0,
            unavailable=1,
            skipped=0,
        )
        with patch("main.setup_logging"):
            with patch("main.run_ocr_queue", return_value=mocked_result) as run_ocr:
                with patch.object(
                    sys,
                    "argv",
                    ["main.py", "ocr-run", "--source", "Нормативные акты Краснодарского края", "--limit", "10"],
                ):
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        exit_code = cli_main.main()

        self.assertEqual(exit_code, 0)
        run_ocr.assert_called_once_with(
            source_name="Нормативные акты Краснодарского края",
            limit=10,
        )
        output = buffer.getvalue()
        self.assertIn("OCR runtime run completed.", output)
        self.assertIn("Updated=1", output)

    def test_ocr_check_cli_works(self) -> None:
        runtime_payload = {
            "enabled": True,
            "available": True,
            "language": "rus+eng",
            "max_pages": 5,
            "timeout_seconds": 120,
            "tessdata_path": r"C:\Program Files\Tesseract-OCR\tessdata",
            "reason": None,
            "available_languages": ["eng", "osd", "rus"],
        }
        with patch("main.setup_logging"):
            with patch("main.get_ocr_runtime_status", return_value=runtime_payload):
                with patch.object(sys, "argv", ["main.py", "ocr-check"]):
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        exit_code = cli_main.main()

        self.assertEqual(exit_code, 0)
        output = buffer.getvalue()
        self.assertIn("OCR runtime check", output)
        self.assertIn("OCR enabled: true", output)
        self.assertIn("OCR available: true", output)
        self.assertIn("available languages: eng, osd, rus", output)

    def test_ocr_backfill_cli_prints_counts(self) -> None:
        mocked_result = OCRBackfillResult(scanned=4, queued=2, existing=1, skipped=1)
        with patch("main.setup_logging"):
            with patch("main.backfill_ocr_queue_from_audit", return_value=mocked_result) as backfill:
                with patch.object(
                    sys,
                    "argv",
                    ["main.py", "ocr-backfill", "--source", "Нормативные акты Краснодарского края", "--limit", "50"],
                ):
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        exit_code = cli_main.main()

        self.assertEqual(exit_code, 0)
        backfill.assert_called_once_with(
            source_name="Нормативные акты Краснодарского края",
            limit=50,
        )
        self.assertIn("scanned=4; queued=2; existing=1; skipped=1", buffer.getvalue())

    def test_donland_backfill_cli_prints_counts(self) -> None:
        mocked_result = DonlandBackfillResult(scanned=10, updated=4, unchanged=5, errors=1, skipped=0)
        with patch("main.setup_logging"):
            with patch("main.run_donland_backfill", return_value=mocked_result) as backfill:
                with patch.object(
                    sys,
                    "argv",
                    ["main.py", "donland-backfill", "--source", "Право Ростовской области", "--limit", "25"],
                ):
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        exit_code = cli_main.main()

        self.assertEqual(exit_code, 0)
        backfill.assert_called_once_with(
            source_name="Право Ростовской области",
            limit=25,
        )
        self.assertIn("scanned=10; updated=4; unchanged=5; errors=1; skipped=0", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
