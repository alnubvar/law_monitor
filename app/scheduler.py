from __future__ import annotations

import logging
import time
from datetime import datetime

from app.config import (
    DB_PATH,
    SCHEDULER_DAILY_REPORT_HOUR,
    SCHEDULER_HOURLY_INTERVAL_MINUTES,
    SCHEDULER_TIMEZONE,
    SCHEDULER_TIMEZONE_NAME,
)
from app.notify.telegram import (
    is_configured,
    send_daily_report_digest,
    send_digest,
    send_test_message,
)
from app.pipeline.analyze import run_analyze
from app.pipeline.collect import run_collect
from app.pipeline.digest import run_digest
from app.run_lock import WriterLockHeldError, writer_lock
from app.reports.markdown_report import select_visible_report_documents
from app.storage import (
    count_documents_by_action_level,
    init_db,
    list_recent_documents,
    list_unnotified_requires_attention,
    mark_documents_notified,
    get_runtime_event,
    mark_runtime_event,
)
from app.user_facing import user_facing_action_level

logger = logging.getLogger(__name__)
DAILY_DIGEST_EVENT_NAME = "daily_digest"

try:
    from apscheduler.schedulers.blocking import BlockingScheduler
except Exception:  # pragma: no cover - optional dependency fallback
    BlockingScheduler = None


def _build_visible_digest_documents(days: int = 1) -> list:
    documents = list_recent_documents(
        days=days,
        relevant_only=False,
        action_levels=["requires_attention", "watchlist"],
    )
    return select_visible_report_documents(
        documents,
        relevant_only=False,
        action_levels=["requires_attention", "watchlist"],
        include_market_background=False,
    )


def notify_new_requires_attention() -> int:
    documents = list_unnotified_requires_attention()
    if not documents:
        logger.info("No new requires_attention documents to notify.")
        return 0

    alert_documents = [
        document
        for document in documents
        if user_facing_action_level(document) == "requires_attention"
    ]
    suppressed_documents = [
        document
        for document in documents
        if user_facing_action_level(document) != "requires_attention"
    ]
    if suppressed_documents:
        mark_documents_notified(
            [document.id for document in suppressed_documents if document.id is not None]
        )
        logger.info(
            "Suppressed %s market/background documents from hourly requires_attention alert.",
            len(suppressed_documents),
        )
    if not alert_documents:
        logger.info("No user-facing requires_attention documents to notify.")
        return 0

    logger.info("Preparing Telegram alerts for %s new requires_attention documents.", len(alert_documents))
    sent = send_digest(alert_documents, db_path=DB_PATH)
    if not sent:
        logger.info("Telegram notification was not sent. Documents remain unnotified.")
        return 0

    updated = mark_documents_notified(
        [document.id for document in alert_documents if document.id is not None]
    )
    logger.info("Marked %s documents as notified.", updated)
    return updated


def run_hourly_cycle() -> tuple[int, int, int]:
    try:
        with writer_lock("scheduler-hourly-cycle"):
            logger.info("Hourly cycle started.")
            collected_count = run_collect()
            analyzed_count = run_analyze()
            notified_count = notify_new_requires_attention()
            requires_attention_count = count_documents_by_action_level("requires_attention")
            logger.info(
                "Hourly cycle finished: collected=%s analyzed=%s requires_attention=%s notified=%s",
                collected_count,
                analyzed_count,
                requires_attention_count,
                notified_count,
            )
            return collected_count, analyzed_count, notified_count
    except WriterLockHeldError:
        logger.warning("Hourly cycle skipped because another write operation is already running.")
        return 0, 0, 0


def run_daily_report_cycle(
    days: int = 7,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> str:
    try:
        with writer_lock("scheduler-daily-report"):
            current_time = _scheduler_now(now)
            digest_date = _scheduler_date(current_time)
            if not force and _daily_digest_already_sent(digest_date):
                logger.info("Daily report cycle skipped: digest already sent for %s.", digest_date)
                return ""
            logger.info("Daily report cycle started.")
            report_path = str(run_digest(days=days))
            visible_documents = _build_visible_digest_documents(days=days)
            sent = send_daily_report_digest(
                visible_documents,
                report_path=report_path,
                db_path=DB_PATH,
            )
            if sent:
                _mark_daily_digest_sent(
                    digest_date=digest_date,
                    report_path=report_path,
                    sent_at=current_time,
                )
            logger.info(
                "Daily report cycle finished: report=%s visible_documents=%s sent=%s",
                report_path,
                len(visible_documents),
                sent,
            )
            return report_path
    except WriterLockHeldError:
        logger.warning("Daily report cycle skipped because another write operation is already running.")
        return ""


def run_scheduler(*, once: bool = False, days: int = 7, force_daily_digest: bool = False) -> None:
    init_db()

    if once:
        logger.info("Running scheduler in single-cycle mode.")
        run_hourly_cycle()
        run_daily_report_cycle(days=days, force=force_daily_digest)
        return

    if BlockingScheduler is None:
        logger.warning(
            "APScheduler is not available. Falling back to simple loop scheduler."
        )
        _run_loop_scheduler(days=days)
        return

    scheduler = BlockingScheduler(timezone=SCHEDULER_TIMEZONE)
    scheduler.add_job(
        run_hourly_cycle,
        "interval",
        minutes=SCHEDULER_HOURLY_INTERVAL_MINUTES,
        id="hourly_collect_analyze",
        replace_existing=True,
    )
    scheduler.add_job(
        run_daily_report_cycle,
        "cron",
        hour=SCHEDULER_DAILY_REPORT_HOUR,
        minute=0,
        id="daily_report",
        replace_existing=True,
        kwargs={"days": days},
    )
    logger.info(
        "Scheduler started. Hourly interval=%s minutes, daily report at %02d:00 %s",
        SCHEDULER_HOURLY_INTERVAL_MINUTES,
        SCHEDULER_DAILY_REPORT_HOUR,
        SCHEDULER_TIMEZONE_NAME,
    )
    scheduler.start()


def _run_loop_scheduler(*, days: int = 7) -> None:
    logger.info(
        "Loop scheduler started. Hourly interval=%s minutes, daily report at %02d:00 %s",
        SCHEDULER_HOURLY_INTERVAL_MINUTES,
        SCHEDULER_DAILY_REPORT_HOUR,
        SCHEDULER_TIMEZONE_NAME,
    )
    interval_seconds = max(SCHEDULER_HOURLY_INTERVAL_MINUTES, 1) * 60
    while True:
        run_hourly_cycle()
        now = datetime.now(SCHEDULER_TIMEZONE)
        if _daily_digest_due(now):
            run_daily_report_cycle(days=days, now=now)
        time.sleep(interval_seconds)


def _scheduler_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(SCHEDULER_TIMEZONE)
    if now.tzinfo is None:
        return now.replace(tzinfo=SCHEDULER_TIMEZONE)
    return now.astimezone(SCHEDULER_TIMEZONE)


def _scheduler_date(now: datetime | None = None) -> str:
    return _scheduler_now(now).date().isoformat()


def _daily_digest_due(now: datetime | None = None) -> bool:
    current_time = _scheduler_now(now)
    digest_date = _scheduler_date(current_time)
    return (
        current_time.hour >= SCHEDULER_DAILY_REPORT_HOUR
        and not _daily_digest_already_sent(digest_date)
    )


def _daily_digest_already_sent(digest_date: str) -> bool:
    event = get_runtime_event(DAILY_DIGEST_EVENT_NAME, db_path=DB_PATH)
    if not event:
        return False
    return _runtime_details_value(str(event.get("details") or ""), "sent_date") == digest_date


def _mark_daily_digest_sent(
    *,
    digest_date: str,
    report_path: str,
    sent_at: datetime,
) -> None:
    mark_runtime_event(
        DAILY_DIGEST_EVENT_NAME,
        details=f"sent_date={digest_date}; report={report_path}",
        occurred_at=sent_at,
        db_path=DB_PATH,
    )


def _runtime_details_value(details: str, key: str) -> str | None:
    prefix = f"{key}="
    for part in details.split(";"):
        normalized = part.strip()
        if normalized.startswith(prefix):
            return normalized[len(prefix):].strip()
    return None


def send_test_notification(command_name: str = "notify-test") -> bool:
    if not is_configured():
        logger.info(
            "Telegram is not configured. %s skipped safely.", command_name
        )
        return False
    return send_test_message(command_name=command_name)
