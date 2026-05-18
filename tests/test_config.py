from __future__ import annotations

import logging
import importlib
import os
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.config import load_keyword_groups, load_sources, setup_logging


class ConfigSmokeTest(unittest.TestCase):
    def test_sources_have_source_role(self) -> None:
        sources = load_sources()

        self.assertTrue(sources)
        self.assertTrue(all(source.source_role for source in sources))

    def test_keyword_groups_are_loaded(self) -> None:
        groups = load_keyword_groups()

        self.assertIn("support_measures", groups)
        self.assertIn("public_discussion", groups)
        self.assertTrue(groups["support_measures"])

    def test_setup_logging_does_not_duplicate_handlers(self) -> None:
        root_logger = logging.getLogger()
        original_handlers = list(root_logger.handlers)
        log_path = Path("data/test_artifacts/test_app.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists():
            log_path.unlink()

        try:
            with patch("app.config.LOG_FILE_PATH", log_path):
                setup_logging("INFO")
                first_handlers = [
                    handler
                    for handler in root_logger.handlers
                    if getattr(handler, "_law_monitor_handler", False)
                ]
                setup_logging("INFO")
                second_handlers = [
                    handler
                    for handler in root_logger.handlers
                    if getattr(handler, "_law_monitor_handler", False)
                ]

            self.assertEqual(len(first_handlers), 2)
            self.assertEqual(len(second_handlers), 2)
        finally:
            for handler in list(root_logger.handlers):
                if getattr(handler, "_law_monitor_handler", False):
                    root_logger.removeHandler(handler)
                    handler.close()
            for handler in original_handlers:
                if handler not in root_logger.handlers:
                    root_logger.addHandler(handler)
            if log_path.exists():
                log_path.unlink()

    def test_reads_ocr_tessdata_path_from_env(self) -> None:
        expected_path = r"C:\Program Files\Tesseract-OCR\tessdata"
        import app.config as config_module

        with patch.dict(
            os.environ,
            {"LAW_MONITOR_OCR_TESSDATA_PATH": expected_path},
            clear=False,
        ):
            reloaded = importlib.reload(config_module)
            self.assertEqual(reloaded.OCR_TESSDATA_PATH, expected_path)
        importlib.reload(config_module)

    def test_scheduler_interval_and_timezone_env_are_loaded(self) -> None:
        import app.config as config_module

        with patch.dict(
            os.environ,
            {
                "LAW_MONITOR_HOURLY_INTERVAL_MINUTES": "360",
                "LAW_MONITOR_TIMEZONE": "Europe/Moscow",
            },
            clear=False,
        ):
            reloaded = importlib.reload(config_module)
            self.assertEqual(reloaded.SCHEDULER_HOURLY_INTERVAL_MINUTES, 360)
            self.assertEqual(reloaded.SCHEDULER_TIMEZONE_NAME, "Europe/Moscow")
            self.assertEqual(
                datetime(2026, 5, 18).replace(tzinfo=reloaded.SCHEDULER_TIMEZONE).utcoffset().total_seconds(),
                3 * 3600,
            )
        importlib.reload(config_module)


if __name__ == "__main__":
    unittest.main()
