from __future__ import annotations

import unittest
from contextlib import nullcontext
from datetime import datetime, time, timezone
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
                with patch.object(scheduler, "run_digest", return_value=report_path) as run_digest:
                    with patch.object(scheduler, "_build_visible_digest_documents", return_value=[document]):
                        with patch.object(scheduler, "send_daily_report_digest", return_value=True) as send_daily:
                            result = scheduler.run_daily_report_cycle(
                                now=datetime(2026, 5, 18, 9, 0, tzinfo=scheduler.SCHEDULER_TIMEZONE)
                            )

        self.assertEqual(result, str(report_path))
        run_digest.assert_called_once_with(days=7)
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
        scheduler._shutdown_event.clear()
        try:
            with patch.object(scheduler, "_install_shutdown_handlers"):
                with patch.object(scheduler, "run_hourly_cycle"):
                    with patch.object(scheduler, "_daily_digest_due", return_value=True) as due_check:
                        with patch.object(scheduler, "run_daily_report_cycle") as run_daily:
                            with patch.object(
                                scheduler, "_interruptible_sleep", side_effect=RuntimeError("stop")
                            ):
                                with self.assertRaises(RuntimeError):
                                    scheduler._run_loop_scheduler(days=3)
        finally:
            scheduler._shutdown_event.clear()

        due_check.assert_called_once()
        run_daily.assert_called_once()

    def test_loop_scheduler_exits_when_shutdown_event_set(self) -> None:
        scheduler._shutdown_event.clear()
        try:
            with patch.object(scheduler, "_install_shutdown_handlers"):
                with patch.object(scheduler, "run_hourly_cycle") as hourly:
                    with patch.object(scheduler, "SCHEDULER_COLLECTION_TIMES", ()):
                        with patch.object(scheduler, "_daily_digest_due", return_value=False):
                            with patch.object(scheduler, "_interruptible_sleep") as interruptible:
                                def _stop(*_args, **_kwargs):
                                    scheduler._shutdown_event.set()
                                    return True

                                interruptible.side_effect = _stop
                                scheduler._run_loop_scheduler(days=3)
        finally:
            scheduler._shutdown_event.clear()

        hourly.assert_called_once()

    def test_request_shutdown_sets_event(self) -> None:
        scheduler._shutdown_event.clear()
        try:
            scheduler._request_shutdown(15, None)
            self.assertTrue(scheduler._shutdown_event.is_set())
        finally:
            scheduler._shutdown_event.clear()

    def test_hourly_cycle_touches_heartbeat_on_success(self) -> None:
        heartbeat = Path("data/test_artifacts/last_success.cycle.test")
        if heartbeat.exists():
            heartbeat.unlink()
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        with patch.object(scheduler, "HEARTBEAT_HOURLY_PATH", heartbeat):
            with patch.object(scheduler, "writer_lock", return_value=nullcontext()):
                with patch.object(scheduler, "run_collect", return_value=0):
                    with patch.object(scheduler, "run_analyze", return_value=0):
                        with patch.object(scheduler, "notify_new_requires_attention", return_value=0):
                            with patch.object(scheduler, "count_documents_by_action_level", return_value=0):
                                scheduler.run_hourly_cycle()

        self.assertTrue(heartbeat.exists())

    def test_collection_cycle_suppresses_urgent_alerts_when_disabled(self) -> None:
        heartbeat = Path("data/test_artifacts/last_success.cycle.quiet.test")
        if heartbeat.exists():
            heartbeat.unlink()
        heartbeat.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(scheduler, "TELEGRAM_URGENT_ALERTS_ENABLED", False):
            with patch.object(scheduler, "HEARTBEAT_HOURLY_PATH", heartbeat):
                with patch.object(scheduler, "writer_lock", return_value=nullcontext()):
                    with patch.object(scheduler, "run_collect", return_value=3) as collect:
                        with patch.object(scheduler, "run_analyze", return_value=2) as analyze:
                            with patch.object(scheduler, "notify_new_requires_attention") as notify:
                                with patch.object(scheduler, "count_documents_by_action_level", return_value=1):
                                    result = scheduler.run_hourly_cycle()

        self.assertEqual(result, (3, 2, 0))
        collect.assert_called_once()
        analyze.assert_called_once()
        notify.assert_not_called()
        self.assertTrue(heartbeat.exists())

    def test_scheduled_collection_due_supports_configured_times(self) -> None:
        scheduler._completed_collection_slots.clear()
        try:
            with patch.object(
                scheduler,
                "SCHEDULER_COLLECTION_TIMES",
                (time(8, 30), time(12, 0), time(18, 0)),
            ):
                self.assertTrue(
                    scheduler._scheduled_collection_due(
                        datetime(2026, 5, 18, 8, 30, tzinfo=scheduler.SCHEDULER_TIMEZONE)
                    )
                )
                self.assertFalse(
                    scheduler._scheduled_collection_due(
                        datetime(2026, 5, 18, 8, 30, tzinfo=scheduler.SCHEDULER_TIMEZONE)
                    )
                )
                self.assertFalse(
                    scheduler._scheduled_collection_due(
                        datetime(2026, 5, 18, 9, 0, tzinfo=scheduler.SCHEDULER_TIMEZONE)
                    )
                )
                self.assertTrue(
                    scheduler._scheduled_collection_due(
                        datetime(2026, 5, 18, 12, 0, tzinfo=scheduler.SCHEDULER_TIMEZONE)
                    )
                )
        finally:
            scheduler._completed_collection_slots.clear()

    def test_run_scheduler_uses_interval_when_collection_times_are_empty(self) -> None:
        class FakeBlockingScheduler:
            instances: list["FakeBlockingScheduler"] = []

            def __init__(self, *, timezone):
                self.timezone = timezone
                self.jobs: list[dict[str, object]] = []
                FakeBlockingScheduler.instances.append(self)

            def add_job(self, func, trigger, **kwargs):
                self.jobs.append({"func": func, "trigger": trigger, **kwargs})

            def start(self):
                raise RuntimeError("stop")

            def shutdown(self, wait=False):
                return None

        with patch.object(scheduler, "BlockingScheduler", FakeBlockingScheduler):
            with patch.object(scheduler, "SCHEDULER_COLLECTION_TIMES", ()):
                with patch.object(scheduler, "init_db"):
                    with self.assertRaises(RuntimeError):
                        scheduler.run_scheduler()

        jobs = FakeBlockingScheduler.instances[0].jobs
        collection_jobs = [job for job in jobs if job["id"] == "hourly_collect_analyze"]
        self.assertEqual(len(collection_jobs), 1)
        self.assertEqual(collection_jobs[0]["trigger"], "interval")
        self.assertEqual(collection_jobs[0]["minutes"], scheduler.SCHEDULER_HOURLY_INTERVAL_MINUTES)

    def test_run_scheduler_uses_configured_collection_times(self) -> None:
        class FakeBlockingScheduler:
            instances: list["FakeBlockingScheduler"] = []

            def __init__(self, *, timezone):
                self.timezone = timezone
                self.jobs: list[dict[str, object]] = []
                FakeBlockingScheduler.instances.append(self)

            def add_job(self, func, trigger, **kwargs):
                self.jobs.append({"func": func, "trigger": trigger, **kwargs})

            def start(self):
                raise RuntimeError("stop")

            def shutdown(self, wait=False):
                return None

        with patch.object(scheduler, "BlockingScheduler", FakeBlockingScheduler):
            with patch.object(
                scheduler,
                "SCHEDULER_COLLECTION_TIMES",
                (time(8, 30), time(12, 0), time(18, 0)),
            ):
                with patch.object(scheduler, "init_db"):
                    with self.assertRaises(RuntimeError):
                        scheduler.run_scheduler()

        jobs = FakeBlockingScheduler.instances[0].jobs
        collection_jobs = [
            job for job in jobs if str(job["id"]).startswith("collect_analyze_")
        ]
        self.assertEqual(
            [(job["trigger"], job["hour"], job["minute"]) for job in collection_jobs],
            [("cron", 8, 30), ("cron", 12, 0), ("cron", 18, 0)],
        )

    def test_hourly_cycle_does_not_touch_heartbeat_when_lock_held(self) -> None:
        from app.run_lock import WriterLockHeldError

        heartbeat = Path("data/test_artifacts/last_success.cycle.held.test")
        if heartbeat.exists():
            heartbeat.unlink()
        heartbeat.parent.mkdir(parents=True, exist_ok=True)

        def _raise(*_args, **_kwargs):
            raise WriterLockHeldError(lock_path=Path("dummy"))

        with patch.object(scheduler, "HEARTBEAT_HOURLY_PATH", heartbeat):
            with patch.object(scheduler, "writer_lock", side_effect=_raise):
                result = scheduler.run_hourly_cycle()

        self.assertEqual(result, (0, 0, 0))
        self.assertFalse(heartbeat.exists())

    def test_daily_cycle_touches_heartbeat_on_success(self) -> None:
        db_path = self._db_path("scheduler_daily_heartbeat.db")
        init_db(db_path)
        heartbeat = Path("data/test_artifacts/last_success.daily.test")
        if heartbeat.exists():
            heartbeat.unlink()
        report_path = Path("data/test_artifacts/scheduler_heartbeat_report.md")
        report_path.write_text("# report", encoding="utf-8")

        with patch.object(scheduler, "DB_PATH", db_path):
            with patch.object(scheduler, "HEARTBEAT_DAILY_PATH", heartbeat):
                with patch.object(scheduler, "writer_lock", return_value=nullcontext()):
                    with patch.object(scheduler, "run_digest", return_value=report_path):
                        with patch.object(scheduler, "_build_visible_digest_documents", return_value=[]):
                            with patch.object(scheduler, "send_daily_report_digest", return_value=True):
                                scheduler.run_daily_report_cycle(
                                    now=datetime(2026, 5, 18, 9, 0, tzinfo=scheduler.SCHEDULER_TIMEZONE)
                                )

        self.assertTrue(heartbeat.exists())


if __name__ == "__main__":
    unittest.main()
