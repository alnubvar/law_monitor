from __future__ import annotations

import logging
import importlib
import os
import unittest
from datetime import datetime, time
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

    def test_collection_times_env_is_parsed(self) -> None:
        import app.config as config_module

        with patch.dict(
            os.environ,
            {
                "LAW_MONITOR_COLLECTION_TIMES": "08:30,12:00,18:00",
            },
            clear=False,
        ):
            reloaded = importlib.reload(config_module)
            self.assertEqual(
                reloaded.SCHEDULER_COLLECTION_TIMES,
                (time(8, 30), time(12, 0), time(18, 0)),
            )
        importlib.reload(config_module)

    def test_empty_collection_times_keeps_interval_scheduler_available(self) -> None:
        import app.config as config_module

        with patch.dict(
            os.environ,
            {
                "LAW_MONITOR_COLLECTION_TIMES": "",
                "LAW_MONITOR_HOURLY_INTERVAL_MINUTES": "45",
            },
            clear=False,
        ):
            reloaded = importlib.reload(config_module)
            self.assertEqual(reloaded.SCHEDULER_COLLECTION_TIMES, ())
            self.assertEqual(reloaded.SCHEDULER_HOURLY_INTERVAL_MINUTES, 45)
        importlib.reload(config_module)

    def test_telegram_urgent_alerts_flag_is_loaded(self) -> None:
        import app.config as config_module

        with patch.dict(
            os.environ,
            {"TELEGRAM_URGENT_ALERTS_ENABLED": "false"},
            clear=False,
        ):
            reloaded = importlib.reload(config_module)
            self.assertFalse(reloaded.TELEGRAM_URGENT_ALERTS_ENABLED)
        importlib.reload(config_module)

    def test_generated_report_docx_files_are_gitignored(self) -> None:
        ignore_text = Path(".gitignore").read_text(encoding="utf-8")

        self.assertIn("reports/*.docx", ignore_text)

    def test_persistent_path_env_vars_are_loaded(self) -> None:
        import app.config as config_module

        with patch.dict(
            os.environ,
            {
                "LAW_MONITOR_DATA_DIR": "prod_data",
                "LAW_MONITOR_REPORTS_DIR": "prod_reports",
                "LAW_MONITOR_TMP_DIR": "prod_tmp",
                "LAW_MONITOR_LOG_DIR": "prod_logs",
                "LAW_MONITOR_DB_PATH": "",
                "LAW_MONITOR_LOG_FILE": "",
            },
            clear=False,
        ):
            reloaded = importlib.reload(config_module)
            self.assertEqual(reloaded.DATA_DIR, Path("prod_data"))
            self.assertEqual(reloaded.REPORTS_DIR, Path("prod_reports"))
            self.assertEqual(reloaded.TMP_DIR, Path("prod_tmp"))
            self.assertEqual(reloaded.LOGS_DIR, Path("prod_logs"))
            self.assertEqual(reloaded.DB_PATH, Path("prod_data") / "law_monitor.db")
            self.assertEqual(reloaded.LOG_FILE_PATH, Path("prod_logs") / "app.log")
        importlib.reload(config_module)

    def test_persistent_path_alias_env_vars_are_loaded(self) -> None:
        import app.config as config_module

        with patch.dict(
            os.environ,
            {
                "LAW_MONITOR_DATA_DIR": "",
                "LAW_MONITOR_REPORTS_DIR": "",
                "LAW_MONITOR_TMP_DIR": "",
                "LAW_MONITOR_LOG_DIR": "",
                "LAW_MONITOR_DB_PATH": "",
                "LAW_MONITOR_LOG_FILE": "",
                "APP_DATA_DIR": "alias_data",
                "REPORTS_DIR": "alias_reports",
                "APP_TMP_DIR": "alias_tmp",
                "LOG_DIR": "alias_logs",
            },
            clear=False,
        ):
            reloaded = importlib.reload(config_module)
            self.assertEqual(reloaded.DATA_DIR, Path("alias_data"))
            self.assertEqual(reloaded.REPORTS_DIR, Path("alias_reports"))
            self.assertEqual(reloaded.TMP_DIR, Path("alias_tmp"))
            self.assertEqual(reloaded.LOGS_DIR, Path("alias_logs"))
            self.assertEqual(reloaded.DB_PATH, Path("alias_data") / "law_monitor.db")
            self.assertEqual(reloaded.LOG_FILE_PATH, Path("alias_logs") / "app.log")
        importlib.reload(config_module)

    def test_scheduler_alias_env_vars_are_loaded(self) -> None:
        import app.config as config_module

        with patch.dict(
            os.environ,
            {
                "LAW_MONITOR_HOURLY_INTERVAL_MINUTES": "",
                "LAW_MONITOR_DAILY_REPORT_HOUR": "",
                "SCHEDULER_INTERVAL_MINUTES": "15",
                "SCHEDULER_DAILY_REPORT_HOUR": "8",
            },
            clear=False,
        ):
            reloaded = importlib.reload(config_module)
            self.assertEqual(reloaded.SCHEDULER_HOURLY_INTERVAL_MINUTES, 15)
            self.assertEqual(reloaded.SCHEDULER_DAILY_REPORT_HOUR, 8)
        importlib.reload(config_module)

    def test_llm_document_enrichment_defaults_are_safe(self) -> None:
        import app.config as config_module

        with patch.dict(
            os.environ,
            {
                "LLM_DOCUMENT_ENRICHMENT_ENABLED": "",
                "LLM_ENRICHMENT_ENABLED": "",
                "LLM_PROVIDER": "",
                "LLM_BASE_URL": "",
                "LLM_API_KEY": "",
                "LLM_MODEL": "",
                "LLM_TIMEOUT_SECONDS": "",
                "LLM_PROXY_URL": "",
                "LLM_RESPONSE_FORMAT": "",
                "LLM_MAX_RETRIES": "",
                "LLM_RETRY_BACKOFF_SECONDS": "",
                "LLM_RETRY_MAX_BACKOFF_SECONDS": "",
                "LLM_MAX_DOCUMENT_CHARS": "",
                "LLM_ENRICHMENT_LIMIT": "",
            },
            clear=False,
        ):
            reloaded = importlib.reload(config_module)
            self.assertFalse(reloaded.LLM_DOCUMENT_ENRICHMENT_ENABLED)
            self.assertFalse(reloaded.LLM_ENRICHMENT_ENABLED)
            self.assertEqual(reloaded.LLM_PROVIDER, "mock")
            self.assertEqual(reloaded.LLM_API_KEY, "")
            self.assertEqual(reloaded.LLM_TIMEOUT_SECONDS, 60)
            self.assertEqual(reloaded.LLM_PROXY_URL, "")
            self.assertEqual(reloaded.LLM_RESPONSE_FORMAT, "auto")
            self.assertEqual(reloaded.LLM_MAX_RETRIES, 2)
            self.assertEqual(reloaded.LLM_RETRY_BACKOFF_SECONDS, 2)
            self.assertEqual(reloaded.LLM_RETRY_MAX_BACKOFF_SECONDS, 10)
            self.assertEqual(reloaded.LLM_MAX_DOCUMENT_CHARS, 12000)
            self.assertEqual(reloaded.LLM_ENRICHMENT_LIMIT, 20)
        importlib.reload(config_module)

    def test_reads_llm_retry_settings_from_env(self) -> None:
        import app.config as config_module

        with patch.dict(
            os.environ,
            {
                "LLM_MAX_RETRIES": "3",
                "LLM_RETRY_BACKOFF_SECONDS": "1",
                "LLM_RETRY_MAX_BACKOFF_SECONDS": "8",
            },
            clear=False,
        ):
            reloaded = importlib.reload(config_module)
            self.assertEqual(reloaded.LLM_MAX_RETRIES, 3)
            self.assertEqual(reloaded.LLM_RETRY_BACKOFF_SECONDS, 1)
            self.assertEqual(reloaded.LLM_RETRY_MAX_BACKOFF_SECONDS, 8)
        importlib.reload(config_module)

    def test_reads_llm_proxy_url_from_env(self) -> None:
        import app.config as config_module

        with patch.dict(
            os.environ,
            {"LLM_PROXY_URL": "http://user:secret@proxy.local:8080"},
            clear=False,
        ):
            reloaded = importlib.reload(config_module)
            self.assertEqual(
                reloaded.LLM_PROXY_URL,
                "http://user:secret@proxy.local:8080",
            )
        importlib.reload(config_module)

    def test_reads_telegram_user_allowlist_env_vars(self) -> None:
        import app.config as config_module

        with patch.dict(
            os.environ,
            {
                "TELEGRAM_ADMIN_USER_IDS": "1001, 1002",
                "TELEGRAM_ALLOWED_USER_IDS": "2001,2002, bad-value",
            },
            clear=False,
        ):
            reloaded = importlib.reload(config_module)
            self.assertEqual(reloaded.TELEGRAM_ADMIN_USER_IDS, frozenset({1001, 1002}))
            self.assertEqual(reloaded.TELEGRAM_ALLOWED_USER_IDS, frozenset({2001, 2002}))
        importlib.reload(config_module)


if __name__ == "__main__":
    unittest.main()
