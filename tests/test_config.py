from __future__ import annotations

import logging
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import setup_logging


class ConfigSmokeTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
