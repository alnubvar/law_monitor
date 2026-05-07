from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
