from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import app.run_lock as run_lock
from app.run_lock import WriterLockHeldError, writer_lock


class RunLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.lock_path = Path(self._temp_dir.name) / "writer.lock"
        self._lock_path_patch = patch.object(run_lock, "WRITER_LOCK_PATH", self.lock_path)
        self._lock_path_patch.start()

    def tearDown(self) -> None:
        self._lock_path_patch.stop()
        self.lock_path.unlink(missing_ok=True)
        self._temp_dir.cleanup()

    def test_lock_acquire_and_release(self) -> None:
        self.assertFalse(self.lock_path.exists())
        with writer_lock("test-operation"):
            self.assertTrue(self.lock_path.exists())
        self.assertFalse(self.lock_path.exists())

    def test_second_writer_is_blocked(self) -> None:
        with writer_lock("first-operation"):
            with self.assertRaises(WriterLockHeldError):
                with writer_lock("second-operation"):
                    self.fail("second writer should not acquire the lock")

    def test_lock_releases_on_exception(self) -> None:
        with self.assertRaises(RuntimeError):
            with writer_lock("failing-operation"):
                self.assertTrue(self.lock_path.exists())
                raise RuntimeError("boom")
        self.assertFalse(self.lock_path.exists())

    def test_stale_lock_with_dead_pid_is_cleaned_up(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(
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
                self.assertTrue(self.lock_path.exists())

        self.assertFalse(self.lock_path.exists())

    def test_stale_lock_with_old_timestamp_is_cleaned_up(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(
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
                self.assertTrue(self.lock_path.exists())

        self.assertFalse(self.lock_path.exists())

    def test_recent_live_lock_is_not_cleaned_up(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(
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
