from __future__ import annotations

import unittest
from pathlib import Path


class DeploymentArtifactsTest(unittest.TestCase):
    def test_env_example_exists_and_does_not_contain_real_tokens(self) -> None:
        env_example = Path(".env.example")
        self.assertTrue(env_example.exists())

        content = env_example.read_text(encoding="utf-8")
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        self.assertIn("TELEGRAM_BOT_TOKEN=", content)
        self.assertIn("TELEGRAM_CHAT_ID=", content)
        self.assertIn("TELEGRAM_PROXY_URL=", content)
        self.assertIn("LAW_MONITOR_DB_PATH=", content)
        self.assertIn("LAW_MONITOR_LOG_LEVEL=", content)
        self.assertIn("LAW_MONITOR_LOG_FILE=", content)
        self.assertIn("LAW_MONITOR_TIMEZONE=Europe/Moscow", content)
        self.assertIn("LAW_MONITOR_DAILY_REPORT_HOUR=", content)
        self.assertIn("LAW_MONITOR_HOURLY_INTERVAL_MINUTES=360", content)
        self.assertIn("LAW_MONITOR_REQUEST_TIMEOUT=", content)
        self.assertIn("LAW_MONITOR_REQUEST_RETRIES=", content)
        self.assertIn("LAW_MONITOR_REQUEST_BACKOFF_FACTOR=", content)
        self.assertIn("LAW_MONITOR_USER_AGENT=", content)
        self.assertIn("TELEGRAM_API_TIMEOUT=", content)
        self.assertFalse(any(line.startswith("APP_DATA_DIR=") for line in lines))
        self.assertFalse(any(line.startswith("LOG_LEVEL=") for line in lines))
        self.assertFalse(any(line.startswith("DAILY_REPORT_HOUR=") for line in lines))
        self.assertFalse(any(line.startswith("TIMEZONE=") for line in lines))
        self.assertNotIn("YOUR_TELEGRAM_BOT_TOKEN", content)
        self.assertNotIn("YOUR_TELEGRAM_CHAT_ID", content)

    def test_deployment_docs_and_scripts_exist(self) -> None:
        self.assertTrue(Path("docs/deployment.md").exists())
        self.assertTrue(Path("scripts/run_smoke_check.ps1").exists())
        self.assertTrue(Path("scripts/run_scheduler_once.ps1").exists())
        self.assertTrue(Path("scripts/run_scheduler.ps1").exists())
        self.assertTrue(Path("scripts/backup_sqlite.ps1").exists())


if __name__ == "__main__":
    unittest.main()
