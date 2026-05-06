from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import main as cli_main


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


if __name__ == "__main__":
    unittest.main()
