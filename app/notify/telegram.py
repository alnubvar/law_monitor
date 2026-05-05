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
from app.reports.markdown_report import classify_display_section, select_visible_report_documents
from app.storage import count_documents_by_action_level, init_db, list_documents, list_recent_documents

logger = logging.getLogger(__name__)

TELEGRAM_SEND_ATTEMPTS = 3
TELEGRAM_RETRY_BACKOFF_SECONDS = 1.0
TELEGRAM_COMMANDS = (
    "/status",
    "/today",
    "/urgent",
    "/watchlist",
    "/report",
    "/sources",
    "/help",
)
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
TELEGRAM_SAFE_MESSAGE_LENGTH = 3900
TELEGRAM_LIST_LIMIT = 10
TELEGRAM_WATCHLIST_USER_LIMIT = 5


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

    chunks = _split_message_chunks(text)
    for chunk in chunks:
        if not _send_message_chunk(chunk, proxies=proxies):
            return False
    return True


def _send_message_chunk(text: str, *, proxies: dict[str, str] | None) -> bool:
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
    if normalized_command == "/watchlist":
        return _build_watchlist_message(resolved_db_path)
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
    snapshot = build_diagnostics_snapshot(list_documents(db_path=db_path, days=7), days=7)
    active_source_rows = [row for row in snapshot.rows if row.total_documents > 0]
    latest_collect = max((document.collected_at for document in all_documents), default=None)
    latest_publish = max(
        (
            document.published_at
            for document in all_documents
            if document.published_at is not None
        ),
        default=None,
    )
    latest_report = _find_latest_report_file()
    requires_attention_count = count_documents_by_action_level("requires_attention", db_path=db_path)
    watchlist_count = count_documents_by_action_level("watchlist", db_path=db_path)
    lines = [
        "📊 Статус AHSTEP GR Monitor",
        f"Обновлено: {now.strftime('%Y-%m-%d %H:%M')}",
        f"Документов в базе: {len(all_documents)}",
        (
            "Уровни за 7 дней: "
            f"требует внимания — {requires_attention_count}, "
            f"наблюдение — {watchlist_count}, "
            f"всего видимых — {len(visible_documents)}"
        ),
        f"Источники: активных {len(sources)}, с новыми публикациями за 7 дней — {len(active_source_rows)}",
        f"Последний сбор: {_fmt_dt(latest_collect) or 'дата не определена'}",
        f"Последняя публикация: {_fmt_dt(latest_publish) or 'дата не определена'}",
        f"Отчет: {'доступен' if latest_report else 'пока не сформирован'}",
        f"Telegram-уведомления: {'включены' if is_configured() else 'не настроены'}",
    ]
    return _cap_message("\n".join(lines))


def _build_today_message(db_path: Path | str) -> str:
    today_documents = _select_today_visible_documents(db_path)
    if not today_documents:
        return "📅 Сегодня новых срочных документов нет."
    urgent_count = sum(1 for document in today_documents if document.action_level == "requires_attention")
    watchlist_documents = [document for document in today_documents if document.action_level == "watchlist"]
    urgent_documents = [document for document in today_documents if document.action_level == "requires_attention"]
    lines = [
        f"📅 Сегодня ({_today_utc().isoformat()})",
    ]
    if urgent_count == 0:
        lines.append("Сегодня новых срочных документов нет.")
    else:
        lines.append(f"Требует внимания GR: {urgent_count}")
        lines.extend(_format_document_lines(urgent_documents, include_summary=False))
    if watchlist_documents:
        lines.append("")
        lines.append(f"📰 Отраслевые сигналы: {len(watchlist_documents)}")
        lines.extend(_format_document_lines(watchlist_documents, include_summary=False))
    return _cap_message("\n".join(lines))


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
        return "🚨 Требует внимания GR: новых документов нет."
    lines = [
        "🚨 Требует внимания GR",
        f"Найдено документов: {len(urgent_documents)}",
    ]
    lines.extend(_format_document_lines(urgent_documents, include_summary=False))
    return _cap_message("\n".join(lines))


def _build_watchlist_message(db_path: Path | str) -> str:
    watchlist_documents = list_recent_documents(
        db_path=db_path,
        days=7,
        relevant_only=False,
        action_levels=["watchlist"],
    )
    if not watchlist_documents:
        return "👀 Документов на наблюдении сейчас нет."
    shown_count = min(TELEGRAM_WATCHLIST_USER_LIMIT, len(watchlist_documents))
    lines = [
        "👀 Документы на наблюдении",
    ]
    lines.extend(
        _format_document_lines(
            watchlist_documents[:TELEGRAM_WATCHLIST_USER_LIMIT],
            include_summary=False,
            max_items=TELEGRAM_WATCHLIST_USER_LIMIT,
            include_hidden_hint=False,
        )
    )
    if len(watchlist_documents) > shown_count:
        lines.append(f"Показано {shown_count} из {len(watchlist_documents)}. Для общей сводки нажмите 📄 Отчёт.")
    return _cap_message("\n".join(lines))


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
    return _cap_message(_build_short_report_text(visible_documents))


def _build_short_report_text(documents: Sequence[RawDocument]) -> str:
    sections = {
        "requires_attention": [],
        "measures_and_selections": [],
        "regional_npa": [],
        "strategy_signals": [],
        "news_signals": [],
    }
    for document in documents:
        section = classify_display_section(document)
        if section in {"active_support_measures", "support_documents"}:
            section = "measures_and_selections"
        if section in sections:
            sections[section].append(document)

    lines = [
        "🧾 GR-сводка за 7 дней",
        f"📊 Всего видимых материалов: {len(documents)}",
        (
            f"🚨 Требует внимания: {len(sections['requires_attention'])} | "
            f"📢 Меры: {len(sections['measures_and_selections'])} | "
            f"⚖️ Региональные изменения: {len(sections['regional_npa'])}"
        ),
        "",
    ]
    block_order = [
        ("🚨 Требует внимания", sections["requires_attention"]),
        ("📢 Меры и отборы", sections["measures_and_selections"]),
        ("⚖️ Региональные изменения", sections["regional_npa"]),
        ("🏛 Стратегические сигналы", sections["strategy_signals"]),
        ("📰 Отраслевые сигналы", sections["news_signals"]),
    ]
    shown_blocks = 0
    for title, section_documents in block_order:
        if not section_documents:
            continue
        if shown_blocks >= 3:
            break
        shown_blocks += 1
        lines.append(title)
        for document in section_documents[:2]:
            reason = (document.business_signal or document.impact or document.summary or "").strip()
            lines.append(f"- {document.title[:150]}")
            if reason:
                lines.append(f"  Почему важно: {reason[:120]}")
            lines.append(f"  Ссылка: {document.url}")
        lines.append("")

    lines.append("Полная версия во вложении .txt")
    return "\n".join(lines).strip()


def _build_sources_message(db_path: Path | str) -> str:
    sources = [source for source in load_sources() if source.enabled]
    recent_documents = list_documents(db_path=db_path, days=7)
    snapshot = build_diagnostics_snapshot(recent_documents, days=7)
    rows_by_name = {row.source_name: row for row in snapshot.rows}
    lines = [f"🛰 Источники (активных: {len(sources)})"]
    for source in sources:
        row = rows_by_name.get(source.name)
        if row is None or row.total_documents == 0:
            lines.append(f"⚠️ {source.name} — нет документов за 7 дней")
            continue
        hints: list[str] = []
        if row.noisy_ratio >= 0.7:
            hints.append("много нерелевантных материалов")
        if row.total_documents > 0 and row.missing_published_at_count / row.total_documents >= 0.6:
            hints.append("часть дат не определена")
        hint_suffix = f" ({', '.join(hints)})" if hints else ""
        lines.append(f"✅ {source.name} — найдено {row.total_documents} документов{hint_suffix}")
    return _cap_message("\n".join(lines))


def _build_help_message() -> str:
    return _cap_message("\n".join(
        [
            "🤖 AHSTEP GR-monitoring команды:",
            "/start — открыть меню GR-монитора",
            "/status — состояние мониторинга",
            "/today — сводка за сегодня",
            "/urgent — документы, требующие внимания GR",
            "/watchlist — документы на наблюдении",
            "/report — краткая сводка за 7 дней",
            "/sources — статус источников за 7 дней",
            "/help — список команд",
        ]
    ))


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
    max_items: int = TELEGRAM_LIST_LIMIT,
    include_hidden_hint: bool = True,
) -> list[str]:
    lines: list[str] = []
    for document in documents[:max_items]:
        published_label = _fmt_dt(document.published_at)
        lines.append(f"- {document.title[:160]}")
        meta_parts = [f"Источник: {document.source_name}"]
        if published_label:
            meta_parts.append(f"Дата: {published_label}")
        meta_parts.append(f"Уровень: {_format_action_level(document.action_level)}")
        lines.append(f"  {' | '.join(meta_parts)}")
        reason = (document.business_signal or document.summary or "").strip()
        if reason:
            lines.append(f"  Почему важно: {reason[:140]}")
        if include_summary and document.summary:
            lines.append(f"  Кратко: {document.summary[:120]}")
        lines.append(f"  {document.url}")
    hidden_count = len(documents) - min(len(documents), max_items)
    if include_hidden_hint and hidden_count > 0:
        lines.append(f"... и еще {hidden_count}.")
    return lines


def _fmt_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%Y-%m-%d")


def _format_action_level(action_level: str | None) -> str:
    labels = {
        "requires_attention": "требует внимания",
        "watchlist": "наблюдение",
        "background": "фон",
        "irrelevant": "скрыто",
    }
    return labels.get(action_level or "", "наблюдение")


def _find_latest_report_file() -> str | None:
    reports_dir = Path("reports")
    if not reports_dir.exists():
        return None
    report_files = sorted(
        reports_dir.glob("gr_monitoring_*.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not report_files:
        return None
    return str(report_files[0])


def get_latest_report_file_path() -> Path | None:
    latest = _find_latest_report_file()
    if latest is None:
        return None
    return Path(latest)


def _cap_message(text: str) -> str:
    normalized = text.strip()
    if len(normalized) <= TELEGRAM_SAFE_MESSAGE_LENGTH:
        return normalized
    return f"{normalized[: TELEGRAM_SAFE_MESSAGE_LENGTH - 20].rstrip()}\n\n... (truncated)"


def _split_message_chunks(text: str) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return [""]
    if len(normalized) <= TELEGRAM_MAX_MESSAGE_LENGTH:
        return [normalized]
    chunks: list[str] = []
    buffer = normalized
    while len(buffer) > TELEGRAM_MAX_MESSAGE_LENGTH:
        split_at = buffer.rfind("\n", 0, TELEGRAM_MAX_MESSAGE_LENGTH)
        if split_at < 200:
            split_at = TELEGRAM_MAX_MESSAGE_LENGTH
        chunks.append(buffer[:split_at].rstrip())
        buffer = buffer[split_at:].lstrip()
    if buffer:
        chunks.append(buffer)
    return chunks
