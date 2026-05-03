from __future__ import annotations

import unittest
from pathlib import Path


class DeploymentArtifactsTest(unittest.TestCase):
    def test_env_example_exists_and_does_not_contain_real_tokens(self) -> None:
        env_example = Path(".env.example")
        self.assertTrue(env_example.exists())

        content = env_example.read_text(encoding="utf-8")
        self.assertIn("TELEGRAM_BOT_TOKEN=", content)
        self.assertIn("TELEGRAM_CHAT_ID=", content)
        self.assertIn("TELEGRAM_PROXY_URL=", content)
        self.assertIn("APP_DATA_DIR=data", content)
        self.assertIn("LOG_LEVEL=INFO", content)
        self.assertIn("DAILY_REPORT_HOUR=", content)
        self.assertIn("TIMEZONE=", content)
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
