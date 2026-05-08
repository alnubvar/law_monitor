from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.run_lock import WRITER_LOCK_PATH, WriterLockHeldError, writer_lock


class RunLockTest(unittest.TestCase):
    def tearDown(self) -> None:
        WRITER_LOCK_PATH.unlink(missing_ok=True)

    def test_lock_acquire_and_release(self) -> None:
        self.assertFalse(WRITER_LOCK_PATH.exists())
        with writer_lock("test-operation"):
            self.assertTrue(WRITER_LOCK_PATH.exists())
        self.assertFalse(WRITER_LOCK_PATH.exists())

    def test_second_writer_is_blocked(self) -> None:
        with writer_lock("first-operation"):
            with self.assertRaises(WriterLockHeldError):
                with writer_lock("second-operation"):
                    self.fail("second writer should not acquire the lock")

    def test_lock_releases_on_exception(self) -> None:
        with self.assertRaises(RuntimeError):
            with writer_lock("failing-operation"):
                self.assertTrue(WRITER_LOCK_PATH.exists())
                raise RuntimeError("boom")
        self.assertFalse(WRITER_LOCK_PATH.exists())

    def test_stale_lock_with_dead_pid_is_cleaned_up(self) -> None:
        WRITER_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        WRITER_LOCK_PATH.write_text(
            json.dumps(
                {
                    "operation": "stale-operation",
                    "pid": 999999,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
            encoding="utf-8",
        )

        with patch("app.run_lock.os.kill", side_effect=ProcessLookupError):
            with writer_lock("fresh-operation"):
                self.assertTrue(WRITER_LOCK_PATH.exists())

        self.assertFalse(WRITER_LOCK_PATH.exists())

    def test_stale_lock_with_old_timestamp_is_cleaned_up(self) -> None:
        WRITER_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        WRITER_LOCK_PATH.write_text(
            json.dumps(
                {
                    "operation": "stale-operation",
                    "pid": 424242,
                    "started_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                }
            ),
            encoding="utf-8",
        )

        with patch("app.run_lock.os.kill", return_value=None):
            with writer_lock("fresh-operation"):
                self.assertTrue(WRITER_LOCK_PATH.exists())

        self.assertFalse(WRITER_LOCK_PATH.exists())

    def test_recent_live_lock_is_not_cleaned_up(self) -> None:
        WRITER_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        WRITER_LOCK_PATH.write_text(
            json.dumps(
                {
                    "operation": "live-operation",
                    "pid": 12345,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
            encoding="utf-8",
        )

        with patch("app.run_lock.os.kill", return_value=None):
            with self.assertRaises(WriterLockHeldError):
                with writer_lock("second-operation"):
                    self.fail("second writer should not acquire the live lock")


if __name__ == "__main__":
    unittest.main()
