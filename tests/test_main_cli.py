from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import main as cli_main
from app.pipeline.ocr_runtime import OCRBackfillResult, OCRRunResult


class MainCliOcrQueueTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
