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
        self.assertIn("LAW_MONITOR_DATA_DIR=", content)
        self.assertIn("LAW_MONITOR_REPORTS_DIR=", content)
        self.assertIn("LAW_MONITOR_BACKUP_DIR=", content)
        self.assertIn("LAW_MONITOR_TMP_DIR=", content)
        self.assertIn("LAW_MONITOR_LOG_DIR=", content)
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
        self.assertTrue(Path("scripts/backup_db.sh").exists())
        self.assertTrue(Path("scripts/restore_db.sh").exists())

    def test_systemd_service_templates_exist(self) -> None:
        scheduler = Path("deploy/systemd/ahstep-scheduler.service")
        telegram_bot = Path("deploy/systemd/ahstep-telegram-bot.service")

        self.assertTrue(scheduler.exists())
        self.assertTrue(telegram_bot.exists())

        scheduler_content = scheduler.read_text(encoding="utf-8")
        bot_content = telegram_bot.read_text(encoding="utf-8")
        for content in (scheduler_content, bot_content):
            self.assertIn("WorkingDirectory=/opt/ahstep/law_monitor", content)
            self.assertIn("EnvironmentFile=/etc/ahstep-law-monitor/law-monitor.env", content)
            self.assertIn("Restart=always", content)
            self.assertIn("RestartSec=10", content)
            self.assertIn("/opt/ahstep/law_monitor/.venv/bin/python", content)
        self.assertIn("main.py run-scheduler", scheduler_content)
        self.assertIn("main.py run-telegram-bot", bot_content)

    def test_linux_backup_scripts_are_safe_by_default(self) -> None:
        backup = Path("scripts/backup_db.sh").read_text(encoding="utf-8")
        restore = Path("scripts/restore_db.sh").read_text(encoding="utf-8")

        self.assertIn(".backup", backup)
        self.assertIn("LAW_MONITOR_BACKUP_DIR", backup)
        self.assertIn("date +%Y%m%d_%H%M%S", backup)
        self.assertIn("--confirm", restore)
        self.assertIn("Pre-restore backup", restore)
        self.assertIn("systemctl is-active --quiet", restore)
        self.assertIn("Refusing to restore while services are active", restore)
        self.assertIn("writer.lock", restore)
        self.assertIn("Refusing to restore while app writer lock exists", restore)


if __name__ == "__main__":
    unittest.main()
