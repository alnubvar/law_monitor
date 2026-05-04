from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from app import config
from app.config import load_sources
from app.models import RawDocument
from app.notify.telegram_formatter import build_digest_message
from app.pipeline.diagnostics import build_diagnostics_snapshot
from app.reports.markdown_report import select_visible_report_documents
from app.storage import count_documents_by_action_level, init_db, list_documents, list_recent_documents

logger = logging.getLogger(__name__)

TELEGRAM_SEND_ATTEMPTS = 3
TELEGRAM_RETRY_BACKOFF_SECONDS = 1.0
TELEGRAM_COMMANDS = ("/status", "/today", "/urgent", "/report", "/sources", "/help")


def is_configured() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def is_proxy_configured() -> bool:
    return bool(config.TELEGRAM_PROXY_URL)


def get_diagnostic_status() -> dict[str, bool | int]:
    return {
        "telegram_configured": is_configured(),
        "proxy_configured": is_proxy_configured(),
        "timeout_seconds": config.TELEGRAM_API_TIMEOUT,
    }


def _build_proxies() -> dict[str, str] | None:
    if not is_proxy_configured():
        return None
    return {
        "http": config.TELEGRAM_PROXY_URL,
        "https": config.TELEGRAM_PROXY_URL,
    }


def _describe_request_error(exc: requests.RequestException) -> str:
    response = getattr(exc, "response", None)
    if response is not None and response.status_code:
        return f"http_{response.status_code}"
    if isinstance(exc, requests.exceptions.ProxyError):
        return "proxy_error"
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection_error"
    if isinstance(exc, requests.exceptions.InvalidSchema):
        return "invalid_proxy_schema_or_missing_socks_support"
    return exc.__class__.__name__.lower()


def send_message(text: str) -> bool:
    if not is_configured():
        logger.info("Telegram is not configured. Skipping message send.")
        return False

    proxies = _build_proxies()
    if proxies:
        logger.info("Using Telegram proxy.")

    for attempt in range(1, TELEGRAM_SEND_ATTEMPTS + 1):
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": config.TELEGRAM_CHAT_ID,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=config.TELEGRAM_API_TIMEOUT,
                proxies=proxies,
            )
            response.raise_for_status()
            logger.info("Telegram message sent successfully.")
            return True
        except requests.RequestException as exc:
            error_code = _describe_request_error(exc)
            logger.warning(
                "Telegram send attempt %s/%s failed: %s",
                attempt,
                TELEGRAM_SEND_ATTEMPTS,
                error_code,
            )
            if attempt < TELEGRAM_SEND_ATTEMPTS:
                time.sleep(TELEGRAM_RETRY_BACKOFF_SECONDS * attempt)

    logger.error("Telegram send failed after %s attempts.", TELEGRAM_SEND_ATTEMPTS)
    return False


def send_test_message(command_name: str = "notify-test") -> bool:
    return send_message(
        "Law Monitor MVP: Telegram check.\n"
        f"Command: {command_name}\n"
        "Scheduler, logging and Telegram integration are configured."
    )


def send_digest(documents: Sequence[RawDocument]) -> bool:
    if not documents:
        logger.info("No documents for Telegram digest. Skipping.")
        return False

    return send_message(build_digest_message(documents))


def build_command_response(
    command_text: str,
    *,
    db_path: Path | str | None = None,
) -> str:
    normalized_command = (command_text or "").strip().split()[0].lower()
    resolved_db_path = Path(db_path) if db_path is not None else config.DB_PATH
    init_db(resolved_db_path)

    if normalized_command == "/status":
        return _build_status_message(resolved_db_path)
    if normalized_command == "/today":
        return _build_today_message(resolved_db_path)
    if normalized_command == "/urgent":
        return _build_urgent_message(resolved_db_path)
    if normalized_command == "/report":
        return _build_report_message(resolved_db_path)
    if normalized_command == "/sources":
        return _build_sources_message(resolved_db_path)
    return _build_help_message()


def send_command_response(
    command_text: str,
    *,
    db_path: Path | str | None = None,
) -> bool:
    return send_message(build_command_response(command_text, db_path=db_path))


def _build_status_message(db_path: Path | str) -> str:
    now = datetime.now().astimezone()
    sources = [source for source in load_sources() if source.enabled]
    all_documents = list_documents(db_path=db_path)
    recent_documents = list_recent_documents(
        db_path=db_path,
        days=7,
        relevant_only=False,
        action_levels=["requires_attention", "watchlist"],
    )
    visible_documents = select_visible_report_documents(
        recent_documents,
        relevant_only=False,
        action_levels=["requires_attention", "watchlist"],
        include_market_background=False,
    )
    lines = [
        "AHSTEP GR-monitoring status",
        f"Время: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"Источники: {len(sources)} enabled",
        f"Всего документов: {len(all_documents)}",
        f"requires_attention: {count_documents_by_action_level('requires_attention', db_path=db_path)}",
        f"watchlist: {count_documents_by_action_level('watchlist', db_path=db_path)}",
        f"visible (7d): {len(visible_documents)}",
        f"Telegram configured: {'yes' if is_configured() else 'no'}",
        f"Proxy configured: {'yes' if is_proxy_configured() else 'no'}",
        "Последние циклы: n/a (scheduler state is not persisted)",
    ]
    return "\n".join(lines)


def _build_today_message(db_path: Path | str) -> str:
    today_documents = _select_today_visible_documents(db_path)
    if not today_documents:
        return f"Visible documents for today ({_today_utc().isoformat()}): 0"
    lines = [f"Visible documents for today ({_today_utc().isoformat()}): {len(today_documents)}"]
    lines.extend(_format_document_lines(today_documents, include_summary=True))
    return "\n".join(lines)


def _build_urgent_message(db_path: Path | str) -> str:
    documents = list_recent_documents(
        db_path=db_path,
        days=30,
        relevant_only=False,
        action_levels=["requires_attention"],
    )
    urgent_documents = select_visible_report_documents(
        documents,
        relevant_only=False,
        action_levels=["requires_attention"],
        include_market_background=False,
    )
    if not urgent_documents:
        return "Urgent documents: 0"
    lines = [f"Urgent documents: {len(urgent_documents)}"]
    lines.extend(_format_document_lines(urgent_documents, include_summary=True))
    return "\n".join(lines)


def _build_report_message(db_path: Path | str) -> str:
    documents = list_recent_documents(
        db_path=db_path,
        days=7,
        relevant_only=False,
        action_levels=["requires_attention", "watchlist"],
    )
    visible_documents = select_visible_report_documents(
        documents,
        relevant_only=False,
        action_levels=["requires_attention", "watchlist"],
        include_market_background=False,
    )
    return build_digest_message(visible_documents)


def _build_sources_message(db_path: Path | str) -> str:
    sources = [source for source in load_sources() if source.enabled]
    recent_documents = list_documents(db_path=db_path, days=7)
    snapshot = build_diagnostics_snapshot(recent_documents, days=7)
    rows_by_name = {row.source_name: row for row in snapshot.rows}
    lines = [f"Sources ({len(sources)} enabled)"]
    for source in sources:
        row = rows_by_name.get(source.name)
        if row is None:
            lines.append(f"- {source.name} [{source.source_role}] 7d=0")
            continue
        lines.append(
            f"- {source.name} [{source.source_role}] "
            f"7d={row.total_documents}; RA={row.requires_attention_count}; "
            f"WL={row.watchlist_count}; BG={row.background_count}; IRR={row.irrelevant_count}"
        )
    return "\n".join(lines)


def _build_help_message() -> str:
    return "\n".join(
        [
            "AHSTEP Telegram commands:",
            "/status - system status and counters",
            "/today - today's visible documents",
            "/urgent - current requires_attention documents",
            "/report - short digest for the last 7 days",
            "/sources - enabled sources and 7-day counts",
            "/help - command list",
        ]
    )


def _select_today_visible_documents(db_path: Path | str) -> list[RawDocument]:
    documents = list_recent_documents(
        db_path=db_path,
        days=2,
        relevant_only=False,
        action_levels=["requires_attention", "watchlist"],
    )
    visible_documents = select_visible_report_documents(
        documents,
        relevant_only=False,
        action_levels=["requires_attention", "watchlist"],
        include_market_background=False,
    )
    today = _today_utc()
    return [
        document
        for document in visible_documents
        if _document_event_date(document) == today
    ]


def _document_event_date(document: RawDocument) -> date:
    event_dt = document.published_at or document.collected_at
    if event_dt.tzinfo is None:
        event_dt = event_dt.replace(tzinfo=timezone.utc)
    return event_dt.astimezone(timezone.utc).date()


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _format_document_lines(
    documents: Sequence[RawDocument],
    *,
    include_summary: bool,
) -> list[str]:
    lines: list[str] = []
    for document in documents[:10]:
        lines.append(f"- {document.title}")
        lines.append(f"  {document.source_name} | {document.url}")
        if include_summary and document.summary:
            lines.append(f"  {document.summary[:180]}")
    hidden_count = len(documents) - min(len(documents), 10)
    if hidden_count > 0:
        lines.append(f"... and {hidden_count} more")
    return lines
