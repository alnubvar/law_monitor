from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.pipeline.smoke import run_smoke_check
from app.storage import init_db

REGRESSION_FIXTURES_DIR = Path("tests/fixtures/regression")


class SmokeCheckTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path(f"data/test_artifacts/{name}")
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def test_smoke_check_passes_without_telegram_env(self) -> None:
        db_path = self._db_path("smoke_without_telegram.db")
        init_db(db_path)

        with mock.patch(
            "app.notify.telegram.get_diagnostic_status",
            return_value={
                "telegram_configured": False,
                "proxy_configured": False,
                "timeout_seconds": 30,
            },
        ):
            result = run_smoke_check(db_path=db_path)

        self.assertTrue(result.passed)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.warning_count, 1)
        self.assertIn("[WARN] Telegram credentials not configured", result.render_text())

    def test_smoke_check_detects_missing_or_empty_sources(self) -> None:
        db_path = self._db_path("smoke_empty_sources.db")
        init_db(db_path)

        tmp_path = Path("data/test_artifacts/smoke_sources")
        tmp_path.mkdir(parents=True, exist_ok=True)
        sources_path = tmp_path / "sources.yaml"
        keywords_path = tmp_path / "keywords.yaml"
        sources_path.write_text("[]\n", encoding="utf-8")
        keywords_path.write_text("keywords:\n  - субсидии\n", encoding="utf-8")

        with mock.patch(
            "app.notify.telegram.get_diagnostic_status",
            return_value={
                "telegram_configured": False,
                "proxy_configured": False,
                "timeout_seconds": 30,
            },
        ):
            result = run_smoke_check(
                db_path=db_path,
                sources_path=sources_path,
                keywords_path=keywords_path,
            )

        self.assertFalse(result.passed)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("[FAIL] sources loaded: 0", result.render_text())

    def test_smoke_check_does_not_send_telegram(self) -> None:
        db_path = self._db_path("smoke_no_telegram_send.db")
        init_db(db_path)

        with mock.patch("app.notify.telegram.send_message") as send_message_mock:
            with mock.patch(
                "app.notify.telegram.get_diagnostic_status",
                return_value={
                    "telegram_configured": True,
                    "proxy_configured": True,
                    "timeout_seconds": 30,
                },
            ):
                result = run_smoke_check(db_path=db_path)

        self.assertTrue(result.passed)
        send_message_mock.assert_not_called()

    def test_smoke_check_finds_regression_fixtures(self) -> None:
        db_path = self._db_path("smoke_fixtures.db")
        init_db(db_path)

        with mock.patch(
            "app.notify.telegram.get_diagnostic_status",
            return_value={
                "telegram_configured": False,
                "proxy_configured": False,
                "timeout_seconds": 30,
            },
        ):
            result = run_smoke_check(db_path=db_path)

        expected_count = len(list(REGRESSION_FIXTURES_DIR.glob("*.json")))
        self.assertIn(f"[OK] regression fixtures found: {expected_count}", result.render_text())

    def test_smoke_check_returns_structured_result_and_text(self) -> None:
        db_path = self._db_path("smoke_structured.db")
        init_db(db_path)

        with mock.patch(
            "app.notify.telegram.get_diagnostic_status",
            return_value={
                "telegram_configured": False,
                "proxy_configured": False,
                "timeout_seconds": 30,
            },
        ):
            result = run_smoke_check(db_path=db_path)

        self.assertIsInstance(result.items, list)
        self.assertGreater(len(result.items), 0)
        self.assertIn("AHSTEP Smoke Check", result.render_text())
        self.assertIn("Smoke check passed with warnings", result.render_text())

    def test_smoke_check_reports_system_proxy_env_vars(self) -> None:
        db_path = self._db_path("smoke_system_proxy_env.db")
        init_db(db_path)

        with mock.patch(
            "app.notify.telegram.get_diagnostic_status",
            return_value={
                "telegram_configured": True,
                "proxy_configured": False,
                "timeout_seconds": 30,
            },
        ):
            with mock.patch.dict(
                os.environ,
                {"HTTP_PROXY": "http://localhost:8888", "HTTPS_PROXY": "http://localhost:8888"},
                clear=False,
            ):
                result = run_smoke_check(db_path=db_path)

        self.assertIn("system HTTP proxy env vars", result.render_text())
        self.assertIn("detected:", result.render_text())


if __name__ == "__main__":
    unittest.main()
