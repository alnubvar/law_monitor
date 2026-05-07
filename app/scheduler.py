from __future__ import annotations

import logging
import time
from datetime import datetime

from app.config import (
    DB_PATH,
    SCHEDULER_DAILY_REPORT_HOUR,
    SCHEDULER_HOURLY_INTERVAL_MINUTES,
)
from app.notify.telegram import (
    is_configured,
    send_digest,
    send_message,
    send_test_message,
)
from app.pipeline.analyze import run_analyze
from app.pipeline.collect import run_collect
from app.pipeline.digest import run_digest
from app.reports.markdown_report import select_visible_report_documents
from app.storage import (
    count_documents_by_action_level,
    init_db,
    list_recent_documents,
    list_unnotified_requires_attention,
    mark_documents_notified,
)
from app.user_facing import user_facing_action_level

logger = logging.getLogger(__name__)

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


def run_daily_report_cycle(days: int = 7) -> str:
    logger.info("Daily report cycle started.")
    report_path = str(run_digest(days=days))
    visible_documents = _build_visible_digest_documents(days=days)
    if visible_documents:
        send_digest(visible_documents, db_path=DB_PATH)
    logger.info(
        "Daily report cycle finished: report=%s visible_documents=%s",
        report_path,
        len(visible_documents),
    )
    return report_path


def run_scheduler(*, once: bool = False, days: int = 7) -> None:
    init_db()

    if once:
        logger.info("Running scheduler in single-cycle mode.")
        run_hourly_cycle()
        run_daily_report_cycle(days=days)
        return

    if BlockingScheduler is None:
        logger.warning(
            "APScheduler is not available. Falling back to simple loop scheduler."
        )
        _run_loop_scheduler(days=days)
        return

    scheduler = BlockingScheduler()
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
        "Scheduler started. Hourly interval=%s minutes, daily report at %02d:00",
        SCHEDULER_HOURLY_INTERVAL_MINUTES,
        SCHEDULER_DAILY_REPORT_HOUR,
    )
    scheduler.start()


def _run_loop_scheduler(*, days: int = 7) -> None:
    logger.info(
        "Loop scheduler started. Hourly interval=%s minutes, daily report at %02d:00",
        SCHEDULER_HOURLY_INTERVAL_MINUTES,
        SCHEDULER_DAILY_REPORT_HOUR,
    )
    last_report_date: str | None = None
    interval_seconds = max(SCHEDULER_HOURLY_INTERVAL_MINUTES, 1) * 60
    while True:
        run_hourly_cycle()
        now = datetime.now()
        current_date = now.strftime("%Y-%m-%d")
        if now.hour == SCHEDULER_DAILY_REPORT_HOUR and last_report_date != current_date:
            run_daily_report_cycle(days=days)
            last_report_date = current_date
        time.sleep(interval_seconds)


def send_test_notification(command_name: str = "notify-test") -> bool:
    if not is_configured():
        logger.info(
            "Telegram is not configured. %s skipped safely.", command_name
        )
        return False
    return send_test_message(command_name=command_name)
