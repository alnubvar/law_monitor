from __future__ import annotations

import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.models import RawDocument
from app.storage import init_db
import app.scheduler as scheduler


class SchedulerAutonomyTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path("data/test_artifacts") / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        return path

    def _doc(self, *, document_id: int = 1, notified: bool = False) -> RawDocument:
        now = datetime(2026, 5, 18, 6, 0, tzinfo=timezone.utc)
        return RawDocument(
            id=document_id,
            source_name="ГИСП - меры поддержки АПК",
            source_url="https://gisp.gov.ru",
            level="federal",
            region="federal",
            title="Льготное кредитование АПК",
            url=f"https://example.com/doc-{document_id}",
            published_at=now,
            collected_at=now,
            content_hash=f"hash-{document_id}",
            raw_text="text",
            is_relevant=True,
            relevance_reason="reason",
            importance="high",
            action_level="requires_attention",
            page_type="measure_card",
            summary="summary",
            impact="impact",
            business_signal="signal",
            notified=notified,
            status="analyzed",
        )

    def test_daily_digest_sends_attachment_with_visible_documents(self) -> None:
        db_path = self._db_path("scheduler_daily_attachment.db")
        init_db(db_path)
        report_path = Path("data/test_artifacts/scheduler_report.md")
        report_path.write_text("# report", encoding="utf-8")
        document = self._doc()

        with patch.object(scheduler, "DB_PATH", db_path):
            with patch.object(scheduler, "writer_lock", return_value=nullcontext()):
                with patch.object(scheduler, "run_digest", return_value=report_path):
                    with patch.object(scheduler, "_build_visible_digest_documents", return_value=[document]):
                        with patch.object(scheduler, "send_daily_report_digest", return_value=True) as send_daily:
                            result = scheduler.run_daily_report_cycle(
                                now=datetime(2026, 5, 18, 9, 0, tzinfo=scheduler.SCHEDULER_TIMEZONE)
                            )

        self.assertEqual(result, str(report_path))
        send_daily.assert_called_once_with([document], report_path=str(report_path), db_path=db_path)

    def test_daily_digest_sends_even_with_no_visible_documents(self) -> None:
        db_path = self._db_path("scheduler_daily_empty.db")
        init_db(db_path)
        report_path = Path("data/test_artifacts/scheduler_empty_report.md")
        report_path.write_text("# report", encoding="utf-8")

        with patch.object(scheduler, "DB_PATH", db_path):
            with patch.object(scheduler, "writer_lock", return_value=nullcontext()):
                with patch.object(scheduler, "run_digest", return_value=report_path):
                    with patch.object(scheduler, "_build_visible_digest_documents", return_value=[]):
                        with patch.object(scheduler, "send_daily_report_digest", return_value=True) as send_daily:
                            scheduler.run_daily_report_cycle(
                                now=datetime(2026, 5, 18, 9, 0, tzinfo=scheduler.SCHEDULER_TIMEZONE)
                            )

        send_daily.assert_called_once_with([], report_path=str(report_path), db_path=db_path)

    def test_daily_digest_idempotency_prevents_duplicate_same_day_send(self) -> None:
        db_path = self._db_path("scheduler_daily_idempotent.db")
        init_db(db_path)
        report_path = Path("data/test_artifacts/scheduler_idempotent_report.md")
        report_path.write_text("# report", encoding="utf-8")

        with patch.object(scheduler, "DB_PATH", db_path):
            with patch.object(scheduler, "writer_lock", return_value=nullcontext()):
                with patch.object(scheduler, "run_digest", return_value=report_path) as run_digest:
                    with patch.object(scheduler, "_build_visible_digest_documents", return_value=[]):
                        with patch.object(scheduler, "send_daily_report_digest", return_value=True) as send_daily:
                            now = datetime(2026, 5, 18, 9, 0, tzinfo=scheduler.SCHEDULER_TIMEZONE)
                            scheduler.run_daily_report_cycle(now=now)
                            scheduler.run_daily_report_cycle(now=now)

        self.assertEqual(run_digest.call_count, 1)
        self.assertEqual(send_daily.call_count, 1)

    def test_daily_digest_force_allows_duplicate_same_day_send(self) -> None:
        db_path = self._db_path("scheduler_daily_force.db")
        init_db(db_path)
        report_path = Path("data/test_artifacts/scheduler_force_report.md")
        report_path.write_text("# report", encoding="utf-8")

        with patch.object(scheduler, "DB_PATH", db_path):
            with patch.object(scheduler, "writer_lock", return_value=nullcontext()):
                with patch.object(scheduler, "run_digest", return_value=report_path) as run_digest:
                    with patch.object(scheduler, "_build_visible_digest_documents", return_value=[]):
                        with patch.object(scheduler, "send_daily_report_digest", return_value=True) as send_daily:
                            now = datetime(2026, 5, 18, 9, 0, tzinfo=scheduler.SCHEDULER_TIMEZONE)
                            scheduler.run_daily_report_cycle(now=now)
                            scheduler.run_daily_report_cycle(now=now, force=True)

        self.assertEqual(run_digest.call_count, 2)
        self.assertEqual(send_daily.call_count, 2)

    def test_urgent_notified_behavior_remains_after_successful_send(self) -> None:
        document = self._doc(document_id=10)
        with patch.object(scheduler, "list_unnotified_requires_attention", return_value=[document]):
            with patch.object(scheduler, "send_digest", return_value=True):
                with patch.object(scheduler, "mark_documents_notified", return_value=1) as mark_notified:
                    updated = scheduler.notify_new_requires_attention()

        self.assertEqual(updated, 1)
        mark_notified.assert_called_once_with([10])

    def test_urgent_notified_behavior_does_not_mark_after_failed_send(self) -> None:
        document = self._doc(document_id=11)
        with patch.object(scheduler, "list_unnotified_requires_attention", return_value=[document]):
            with patch.object(scheduler, "send_digest", return_value=False):
                with patch.object(scheduler, "mark_documents_notified") as mark_notified:
                    updated = scheduler.notify_new_requires_attention()

        self.assertEqual(updated, 0)
        mark_notified.assert_not_called()

    def test_scheduler_date_uses_moscow_timezone(self) -> None:
        utc_late_evening = datetime(2026, 5, 17, 21, 30, tzinfo=timezone.utc)
        self.assertEqual(scheduler._scheduler_date(utc_late_evening), "2026-05-18")

    def test_fallback_daily_digest_not_due_before_report_hour(self) -> None:
        db_path = self._db_path("scheduler_due_before_hour.db")
        init_db(db_path)

        with patch.object(scheduler, "DB_PATH", db_path):
            due = scheduler._daily_digest_due(
                datetime(2026, 5, 18, 8, 0, tzinfo=scheduler.SCHEDULER_TIMEZONE)
            )

        self.assertFalse(due)

    def test_fallback_daily_digest_due_at_and_after_report_hour_when_unsent(self) -> None:
        for hour in (9, 10, 16):
            with self.subTest(hour=hour):
                db_path = self._db_path(f"scheduler_due_unsent_{hour}.db")
                init_db(db_path)

                with patch.object(scheduler, "DB_PATH", db_path):
                    due = scheduler._daily_digest_due(
                        datetime(2026, 5, 18, hour, 0, tzinfo=scheduler.SCHEDULER_TIMEZONE)
                    )

                self.assertTrue(due)

    def test_fallback_daily_digest_not_due_after_same_day_send(self) -> None:
        db_path = self._db_path("scheduler_due_sent.db")
        init_db(db_path)
        sent_at = datetime(2026, 5, 18, 9, 5, tzinfo=scheduler.SCHEDULER_TIMEZONE)

        with patch.object(scheduler, "DB_PATH", db_path):
            scheduler._mark_daily_digest_sent(
                digest_date="2026-05-18",
                report_path="reports/gr_monitoring_2026-05-18.md",
                sent_at=sent_at,
            )
            due = scheduler._daily_digest_due(
                datetime(2026, 5, 18, 16, 0, tzinfo=scheduler.SCHEDULER_TIMEZONE)
            )

        self.assertFalse(due)

    def test_fallback_daily_digest_due_again_on_next_moscow_date(self) -> None:
        db_path = self._db_path("scheduler_due_next_date.db")
        init_db(db_path)
        sent_at = datetime(2026, 5, 18, 16, 0, tzinfo=scheduler.SCHEDULER_TIMEZONE)

        with patch.object(scheduler, "DB_PATH", db_path):
            scheduler._mark_daily_digest_sent(
                digest_date="2026-05-18",
                report_path="reports/gr_monitoring_2026-05-18.md",
                sent_at=sent_at,
            )
            due = scheduler._daily_digest_due(
                datetime(2026, 5, 19, 10, 0, tzinfo=scheduler.SCHEDULER_TIMEZONE)
            )

        self.assertTrue(due)

    def test_loop_scheduler_uses_daily_due_check(self) -> None:
        with patch.object(scheduler, "run_hourly_cycle"):
            with patch.object(scheduler, "_daily_digest_due", return_value=True) as due_check:
                with patch.object(scheduler, "run_daily_report_cycle") as run_daily:
                    with patch.object(scheduler.time, "sleep", side_effect=RuntimeError("stop")):
                        with self.assertRaises(RuntimeError):
                            scheduler._run_loop_scheduler(days=3)

        due_check.assert_called_once()
        run_daily.assert_called_once()


if __name__ == "__main__":
    unittest.main()
