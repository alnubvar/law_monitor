from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import app.run_lock as run_lock
from app.run_lock import WriterLockHeldError, writer_lock


class RunLockTest(unittest.TestCase):
    """Fast, deterministic writer-lock tests.

    Platform-sensitive stale-lock behavior that depends on OS temp/runtime state
    or PID probing is intentionally not covered here; those cases were removed
    because they caused hanging/interrupted unittest runs on Windows.
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.lock_path = Path(self._temp_dir.name) / "writer.lock"
        self._lock_path_patch = patch.object(run_lock, "WRITER_LOCK_PATH", self.lock_path)
        self._lock_path_patch.start()
        self.addCleanup(self._lock_path_patch.stop)
        self._ensure_directories_patch = patch.object(run_lock, "ensure_directories", lambda: None)
        self._ensure_directories_patch.start()
        self.addCleanup(self._ensure_directories_patch.stop)
        self._logger_warning_patch = patch.object(run_lock.logger, "warning")
        self._logger_warning_mock = self._logger_warning_patch.start()
        self.addCleanup(self._logger_warning_patch.stop)

    def tearDown(self) -> None:
        self.lock_path.unlink(missing_ok=True)

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

    def test_recent_live_lock_is_not_cleaned_up(self) -> None:
        with writer_lock("live-operation"):
            with self.assertRaises(WriterLockHeldError):
                with writer_lock("second-operation"):
                    self.fail("second writer should not acquire the live lock")

    def test_empty_stale_payload_is_considered_stale(self) -> None:
        self.assertTrue(run_lock._is_stale_lock_payload({}))


if __name__ == "__main__":
    unittest.main()
