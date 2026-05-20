from __future__ import annotations

import logging
import mimetypes
import re
import time
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from app import config
from app.config import load_sources
from app.llm.enrichment import get_display_enrichment
from app.models import RawDocument
from app.notify.telegram_formatter import build_digest_message
from app.operational_health import (
    OperationalNotice,
    build_source_health_summary,
    collect_operational_notices,
    format_operational_notices_telegram,
)
from app.periods import (
    PeriodSpec,
    build_rolling_period,
    build_today_period,
    document_event_date,
    filter_documents_for_period,
    format_period_label,
    parse_period_spec,
)
from app.user_facing import (
    build_executive_action,
    build_executive_reason,
    select_executive_summary,
    user_facing_action_level,
    user_facing_title,
)
from app.pipeline.diagnostics import build_diagnostics_snapshot
from app.reports.markdown_report import select_visible_report_documents
from app.storage import (
    create_tracking_item,
    deactivate_tracking_item,
    count_documents_by_action_level,
    list_document_enrichments,
    get_active_tracking_item,
    get_document_by_url,
    init_db,
    list_active_tracking_items,
    list_documents,
    list_recent_documents,
    save_tracking_snapshot,
    compute_tracking_status_hash,
    search_documents,
    list_ocr_queue,
    summarize_ocr_queue,
)
from app.storage import get_runtime_event, list_latest_source_audit
from app.visibility import classify_display_section, should_show_document
from app.visibility import deduplicate_user_facing_documents

logger = logging.getLogger(__name__)
TELEGRAM_URL_TOKEN_RE = re.compile(r"(https://api\.telegram\.org/bot)[^/\s]+", re.IGNORECASE)

TELEGRAM_SEND_ATTEMPTS = 3
TELEGRAM_RETRY_BACKOFF_SECONDS = 1.0
TELEGRAM_COMMANDS = (
    "/status",
    "/today",
    "/urgent",
    "/watchlist",
    "/report",
    "/sources",
    "/ocr",
    "/search",
    "/track",
    "/untrack",
    "/tracked",
    "/help",
)
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
TELEGRAM_SAFE_MESSAGE_LENGTH = 3900
TELEGRAM_LIST_LIMIT = 10
TELEGRAM_WATCHLIST_USER_LIMIT = 5
REFRESH_INTERFACE_PERIOD_DAYS = 14
REFRESH_SUMMARY_PERIOD_DAYS = 7
SEARCH_PROMPT_MESSAGE = "🔎 Введите запрос для поиска по архиву."
PUBLISHED_AT_FUTURE_TOLERANCE = timedelta(days=2)


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
        return "telegram_api_error"
    if isinstance(exc, requests.exceptions.ProxyError):
        return "proxy_error"
    if isinstance(exc, (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout, requests.exceptions.Timeout)):
        return "timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection_error"
    if isinstance(exc, requests.exceptions.InvalidSchema):
        return "proxy_error"
    return "telegram_api_error"


def redact_telegram_secrets(text: str) -> str:
    return TELEGRAM_URL_TOKEN_RE.sub(r"\1<redacted>", text or "")


def sanitize_telegram_exception_message(exc: Exception) -> str:
    return redact_telegram_secrets(str(exc))


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


def send_message_to_chat(*, chat_id: str | int, text: str) -> bool:
    if not config.TELEGRAM_BOT_TOKEN:
        logger.info("Telegram token is not configured. Skipping message send.")
        return False
    proxies = _build_proxies()
    chunks = _split_message_chunks(text)
    for chunk in chunks:
        if not _send_message_chunk(chunk, proxies=proxies, chat_id=str(chat_id)):
            return False
    return True


def _send_message_chunk(
    text: str,
    *,
    proxies: dict[str, str] | None,
    chat_id: str | None = None,
) -> bool:
    for attempt in range(1, TELEGRAM_SEND_ATTEMPTS + 1):
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id or config.TELEGRAM_CHAT_ID,
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


def send_digest(
    documents: Sequence[RawDocument],
    *,
    db_path: Path | str | None = None,
) -> bool:
    if not documents:
        logger.info("No documents for Telegram digest. Skipping.")
        return False

    notices = collect_operational_notices(db_path=db_path) if db_path is not None else []
    return send_message(build_digest_message(documents, operational_notices=notices))


def send_daily_report_digest(
    documents: Sequence[RawDocument],
    *,
    report_path: Path | str | None,
    db_path: Path | str | None = None,
) -> bool:
    notices = collect_operational_notices(db_path=db_path) if db_path is not None else []
    if documents:
        text = build_digest_message(
            documents,
            operational_notices=notices,
            force_daily=True,
        )
    else:
        lines = [
            "🧾 Ежедневная GR-сводка",
            "Срочных изменений не найдено, источники проверены.",
        ]
        if notices:
            lines.append("")
            lines.extend(format_operational_notices_telegram(notices))
        text = "\n".join(lines)

    if report_path is not None:
        text = f"{text}\n\nПолная версия отчета — во вложении."

    if not send_message(text):
        return False

    if report_path is None:
        return True
    return send_document(Path(report_path))


def send_document(path: Path | str) -> bool:
    if not is_configured():
        logger.info("Telegram is not configured. Skipping document send.")
        return False

    document_path = Path(path)
    if not document_path.exists():
        logger.warning("Telegram document path does not exist: %s", document_path.name)
        return False

    proxies = _build_proxies()
    mime_type = mimetypes.guess_type(document_path.name)[0] or "text/plain"
    timeout = max(config.TELEGRAM_API_TIMEOUT + 5, 10)
    for attempt in range(1, TELEGRAM_SEND_ATTEMPTS + 1):
        try:
            with document_path.open("rb") as document_file:
                response = requests.post(
                    f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendDocument",
                    data={"chat_id": config.TELEGRAM_CHAT_ID},
                    files={"document": (document_path.name, document_file, mime_type)},
                    timeout=timeout,
                    proxies=proxies,
                )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok", True):
                raise RuntimeError("telegram_api_error")
            logger.info("Telegram document sent successfully.")
            return True
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            error_code = _describe_request_error(exc) if isinstance(exc, requests.RequestException) else "telegram_api_error"
            logger.warning(
                "Telegram document send attempt %s/%s failed: %s",
                attempt,
                TELEGRAM_SEND_ATTEMPTS,
                error_code,
            )
            if attempt < TELEGRAM_SEND_ATTEMPTS:
                time.sleep(TELEGRAM_RETRY_BACKOFF_SECONDS * attempt)

    logger.error("Telegram document send failed after %s attempts.", TELEGRAM_SEND_ATTEMPTS)
    return False


def build_command_response(
    command_text: str,
    *,
    db_path: Path | str | None = None,
    default_days: int = 7,
    chat_id: str | int | None = None,
) -> str:
    normalized_command, command_args = _parse_command_request(command_text)
    resolved_db_path = Path(db_path) if db_path is not None else config.DB_PATH
    init_db(resolved_db_path)
    report_period = parse_period_spec(
        command_args[0] if command_args else None,
        default_days=default_days,
    ) if normalized_command == "/report" else build_rolling_period(
        _extract_period_days(command_args, default_days=default_days)
    )
    period_days = report_period.days

    if normalized_command == "/status":
        return _build_status_message(resolved_db_path)
    if normalized_command == "/today":
        return _build_today_message(resolved_db_path)
    if normalized_command == "/urgent":
        return _build_urgent_message(resolved_db_path, days=period_days)
    if normalized_command == "/watchlist":
        return _build_watchlist_message(resolved_db_path, days=period_days)
    if normalized_command == "/report":
        return _build_report_message(resolved_db_path, period=report_period)
    if normalized_command == "/sources":
        return _build_sources_message(resolved_db_path)
    if normalized_command == "/ocr":
        return _build_ocr_queue_message(resolved_db_path)
    if normalized_command == "/search":
        return _build_search_message(resolved_db_path, query=" ".join(command_args).strip())
    if normalized_command == "/track":
        return _build_track_message(resolved_db_path, chat_id=chat_id, url_arg=" ".join(command_args).strip())
    if normalized_command == "/untrack":
        return _build_untrack_message(resolved_db_path, chat_id=chat_id, url_arg=" ".join(command_args).strip())
    if normalized_command == "/tracked":
        return _build_tracked_message(resolved_db_path, chat_id=chat_id)
    return _build_help_message()


def send_command_response(
    command_text: str,
    *,
    db_path: Path | str | None = None,
) -> bool:
    return send_message(build_command_response(command_text, db_path=db_path))


def _parse_command_request(command_text: str) -> tuple[str, list[str]]:
    raw = (command_text or "").strip()
    if not raw:
        return "", []
    parts = raw.split()
    command = parts[0].lower().split("@", maxsplit=1)[0]
    return command, parts[1:]


def _extract_period_days(args: list[str], *, default_days: int) -> int:
    safe_default = max(1, min(int(default_days), 365))
    if not args:
        return safe_default
    first = args[0].strip()
    if not first.isdigit():
        return safe_default
    return max(1, min(int(first), 365))


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
    valid_published_dates = [
        candidate
        for candidate in (
            _sanitize_published_at_for_status(document.published_at)
            for document in all_documents
        )
        if candidate is not None
    ]
    latest_publish = max(valid_published_dates, default=None)
    latest_report = _find_latest_report_file()
    requires_attention_count = sum(
        1
        for document in visible_documents
        if user_facing_action_level(document) == "requires_attention"
    )
    watchlist_count = sum(
        1
        for document in visible_documents
        if user_facing_action_level(document) == "watchlist"
    )
    lines = [
        "ℹ️ Состояние AHSTEP GR Monitor",
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
    notices = collect_operational_notices(db_path=db_path)
    if notices:
        lines.append("")
        lines.extend(format_operational_notices_telegram(notices))
    lines.extend(_build_freshness_lines(db_path))
    return _cap_message("\n".join(lines))


def _build_today_message(db_path: Path | str) -> str:
    today_documents = _select_today_visible_documents(db_path)
    active_urgent_last_14_days = get_interface_summary(db_path=db_path, days=14)["requires_attention"]
    today_period = build_today_period()
    today_label = (
        today_period.target_date.strftime("%d.%m.%Y")
        if today_period.target_date is not None
        else datetime.now(timezone.utc).strftime("%d.%m.%Y")
    )
    if not today_documents:
        lines = ["Новых срочных документов сегодня нет."]
        if active_urgent_last_14_days > 0:
            lines.append(
                f"Активные срочные вопросы за последние 14 дней: {active_urgent_last_14_days}. Откройте 🚨 Срочное."
            )
        return "\n".join(lines)
    enrichment_by_url = list_document_enrichments([document.url for document in today_documents], db_path=db_path)
    urgent_count = sum(1 for document in today_documents if user_facing_action_level(document) == "requires_attention")
    watchlist_documents = [document for document in today_documents if user_facing_action_level(document) == "watchlist"]
    urgent_documents = [document for document in today_documents if user_facing_action_level(document) == "requires_attention"]
    lines = [
        f"Новые сигналы сегодня ({today_label})",
    ]
    if urgent_count == 0:
        lines.append("Новых срочных документов сегодня нет.")
        if active_urgent_last_14_days > 0:
            lines.append(
                f"Активные срочные вопросы за последние 14 дней: {active_urgent_last_14_days}. Откройте 🚨 Срочное."
            )
    else:
        lines.append(f"Требует внимания GR: {urgent_count}")
        lines.extend(_format_document_lines(urgent_documents, include_summary=False, enrichment_by_url=enrichment_by_url))
    if watchlist_documents:
        lines.append("")
        lines.append(f"📰 Отраслевые сигналы: {len(watchlist_documents)}")
        lines.extend(_format_document_lines(watchlist_documents, include_summary=False, enrichment_by_url=enrichment_by_url))
    return _cap_message("\n".join(lines))


def _build_urgent_message(db_path: Path | str, *, days: int) -> str:
    documents = list_recent_documents(
        db_path=db_path,
        days=days,
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
        return (
            f"🚨 Требует внимания GR: новых документов нет за {days} дней.\n"
            "Для общей сводки используйте 📄 Отчёт."
        )
    enrichment_by_url = list_document_enrichments([document.url for document in urgent_documents], db_path=db_path)
    lines = [
        f"🚨 Требует внимания GR (за {days} дней)",
        f"Найдено документов: {len(urgent_documents)}",
    ]
    lines.extend(_format_document_lines(urgent_documents, include_summary=False, enrichment_by_url=enrichment_by_url))
    lines.append("Для общей сводки используйте 📄 Отчёт.")
    return _cap_message("\n".join(lines))


def get_interface_summary(
    db_path: Path | str,
    *,
    days: int = REFRESH_INTERFACE_PERIOD_DAYS,
) -> dict[str, int]:
    recent_documents = list_recent_documents(
        db_path=db_path,
        days=days,
        relevant_only=False,
        action_levels=["requires_attention", "watchlist"],
    )
    visible_documents = select_visible_report_documents(
        recent_documents,
        relevant_only=False,
        action_levels=["requires_attention", "watchlist"],
        include_market_background=False,
    )
    requires_attention_count = sum(
        1
        for document in visible_documents
        if user_facing_action_level(document) == "requires_attention"
    )
    watchlist_count = sum(
        1
        for document in visible_documents
        if user_facing_action_level(document) == "watchlist"
    )
    return {
        "visible_total": len(visible_documents),
        "requires_attention": requires_attention_count,
        "watchlist": watchlist_count,
    }


def get_interface_counts(
    db_path: Path | str,
    *,
    days: int = REFRESH_INTERFACE_PERIOD_DAYS,
) -> tuple[int, int]:
    summary = get_interface_summary(db_path=db_path, days=days)
    return summary["requires_attention"], summary["watchlist"]


def _build_watchlist_message(db_path: Path | str, *, days: int) -> str:
    recent_documents = list_recent_documents(
        db_path=db_path,
        days=days,
        relevant_only=False,
        action_levels=["watchlist"],
    )
    watchlist_documents = [
        document
        for document in deduplicate_user_facing_documents(recent_documents)
        if should_show_document(document, surface="telegram_list", relevant_only=False)
    ]
    if not watchlist_documents:
        return f"Отраслевых сигналов за {days} дней нет."
    enrichment_by_url = list_document_enrichments([document.url for document in watchlist_documents], db_path=db_path)
    shown_count = min(TELEGRAM_WATCHLIST_USER_LIMIT, len(watchlist_documents))
    lines = [
        f"Отраслевые сигналы (за {days} дней)",
    ]
    lines.extend(
        _format_document_lines(
            watchlist_documents[:TELEGRAM_WATCHLIST_USER_LIMIT],
            include_summary=False,
            max_items=TELEGRAM_WATCHLIST_USER_LIMIT,
            include_hidden_hint=False,
            enrichment_by_url=enrichment_by_url,
        )
    )
    if len(watchlist_documents) > shown_count:
        lines.append(f"Показано {shown_count} из {len(watchlist_documents)}. Для общей сводки нажмите 📄 Отчёт.")
    return _cap_message("\n".join(lines))


def _build_report_message(db_path: Path | str, *, period: PeriodSpec) -> str:
    documents = list_recent_documents(
        db_path=db_path,
        days=period.days,
        relevant_only=False,
        action_levels=["requires_attention", "watchlist"],
    )
    documents = filter_documents_for_period(documents, period)
    visible_documents = select_visible_report_documents(
        documents,
        relevant_only=False,
        action_levels=["requires_attention", "watchlist"],
        include_market_background=False,
    )
    notices = collect_operational_notices(db_path=db_path)
    return _cap_message(
        _build_short_report_text(
            visible_documents,
            period=period,
            operational_notices=notices,
            db_path=db_path,
        )
    )


def _build_short_report_text(
    documents: Sequence[RawDocument],
    *,
    period: PeriodSpec,
    operational_notices: Sequence[OperationalNotice] = (),
    db_path: Path | str | None = None,
) -> str:
    enrichment_by_url = list_document_enrichments(
        [document.url for document in documents],
        db_path=db_path or config.DB_PATH,
    )
    sections = {
        "requires_attention": [],
        "measures_and_selections": [],
        "regional_npa": [],
        "strategy_signals": [],
        "news_signals": [],
    }
    for document in documents:
        section = classify_display_section(document)
        if section in sections:
            sections[section].append(document)

    lines = [
        "🧾 GR-сводка",
        f"Период: {format_period_label(period)}",
        f"📊 Включено в краткую сводку: {len(documents)}",
        (
            f"🚨 Требует реакции: {len(sections['requires_attention'])} | "
            f"📢 Меры и отборы: {len(sections['measures_and_selections'])} | "
            f"⚖️ Региональные изменения: {len(sections['regional_npa'])}"
        ),
        "",
    ]
    if period.kind == "today" and not sections["requires_attention"]:
        lines.append("Сегодня новых срочных документов нет.")
        if db_path is not None:
            active_urgent_last_14_days = get_interface_summary(db_path=db_path, days=14)["requires_attention"]
            if active_urgent_last_14_days > 0:
                lines.append(
                    f"Активные срочные вопросы за последние 14 дней: {active_urgent_last_14_days}. Откройте 🚨 Срочное."
                )
        lines.append("")
    if operational_notices:
        lines.extend(format_operational_notices_telegram(operational_notices))
        lines.append("")
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
            enrichment = get_display_enrichment(enrichment_by_url.get(document.url))
            reason = _build_user_facing_reason(document, enrichment=enrichment)
            lines.append(f"- {user_facing_title(document, max_chars=95)}")
            if reason:
                lines.append(f"  Почему важно: {reason[:90]}")
            summary_text = select_executive_summary(
                document,
                enrichment_text=enrichment.get("executive_summary") if enrichment else None,
                fallback_text=document.summary,
                max_chars=95,
            )
            if summary_text:
                lines.append(f"  Кратко: {summary_text}")
            if enrichment and enrichment.get("deadline_hint"):
                lines.append(f"  {_format_deadline_line(enrichment['deadline_hint'])}")
            hint = _build_report_summary_action_hint(document, section=section, enrichment=enrichment)
            if hint:
                lines.append(f"  Что проверить: {hint}")
            lines.append(f"  Источник: {document.url}")
        lines.append("")

    lines.append("Полная версия отчета — во вложении .txt")
    return "\n".join(lines).strip()


def _build_sources_message(db_path: Path | str) -> str:
    sources = [source for source in load_sources() if source.enabled]
    recent_documents = list_documents(db_path=db_path, days=7)
    snapshot = build_diagnostics_snapshot(recent_documents, days=7)
    rows_by_name = {row.source_name: row for row in snapshot.rows}
    audit_by_source = {row["source_name"]: row for row in list_latest_source_audit(db_path=db_path)}
    lines = [f"Проверка источников (активных: {len(sources)})"]
    for source in sources:
        row = rows_by_name.get(source.name)
        audit_row = audit_by_source.get(source.name)
        last_success = _fmt_dt(audit_row["success_at"]) if audit_row else None
        last_error = _fmt_dt(audit_row["error_at"]) if audit_row else None
        if audit_row and _is_unavailable_source_audit(audit_row):
            lines.append(f"❌ {source.name} — временно недоступен")
            if last_error:
                lines.append(f"  Последняя проблема: {last_error}")
            continue
        if row is None or row.total_documents == 0:
            lines.append(f"ℹ️ {source.name} — новых публикаций не найдено")
            if last_success:
                lines.append(f"  Последний успешный сбор: {last_success}")
            continue
        hints: list[str] = []
        if row.noisy_ratio >= 0.7:
            hints.append("есть материалы для фильтрации")
        if row.total_documents > 0 and row.missing_published_at_count / row.total_documents >= 0.6:
            hints.append("часть публикаций без даты")
        hint_suffix = f" ({', '.join(hints)})" if hints else ""
        lines.append(f"✅ {source.name} — работает{hint_suffix}")
        if last_success:
            lines.append(f"  Последний успешный сбор: {last_success}")
    return _cap_message("\n".join(lines))


def _build_search_message(db_path: Path | str, *, query: str) -> str:
    normalized_query = query.strip()
    if not normalized_query:
        return SEARCH_PROMPT_MESSAGE
    results = search_documents(normalized_query, db_path=db_path, limit=5)
    if not results:
        return f"🔎 По запросу «{normalized_query}» ничего не найдено."
    lines = [
        f"🔎 Результаты поиска: {normalized_query}",
        f"Найдено (показано до 5): {len(results)}",
    ]
    lines.extend(
        _format_document_lines(
            results,
            include_summary=False,
            max_items=5,
            include_hidden_hint=False,
            enrichment_by_url=list_document_enrichments([document.url for document in results], db_path=db_path),
        )
    )
    lines.append(f"Показано {len(results)} результатов. Уточните запрос, чтобы сузить поиск.")
    return _cap_message("\n".join(lines))


def _build_report_summary_action_hint(
    document: RawDocument,
    *,
    section: str,
    enrichment: dict[str, str] | None = None,
) -> str:
    action_text = build_executive_action(
        document,
        enrichment_text=enrichment.get("recommended_action") if enrichment else None,
        section=section,
    )
    if action_text:
        return action_text[:90]
    if document.application_status == "open" and document.deadline_text:
        return document.deadline_text[:90]
    return ""


def _build_help_message() -> str:
    return _cap_message(
        "\n".join(
            [
                "ℹ️ AHSTEP GR Monitor",
                "",
                "Система отслеживает:",
                "• меры господдержки",
                "• нормативные акты",
                "• отборы и субсидии",
                "• отраслевые GR-сигналы",
                "",
                "Основные разделы:",
                "",
                "🚨 Срочное — документы, требующие внимания",
                "📄 Отчёт — ежедневная сводка и новые сигналы",
                "🔎 Поиск — поиск по документам и мерам поддержки",
                "🔄 Обновить — запустить проверку новых данных",
                "",
                "Рекомендация:",
                "начинайте работу с раздела «📄 Отчёт».",
            ]
        )
    )


def _build_ocr_queue_message(db_path: Path | str) -> str:
    def _short_title(value: str, *, max_len: int = 90) -> str:
        normalized = value.strip()
        if len(normalized) <= max_len:
            return normalized
        return f"{normalized[: max_len - 3].rstrip()}..."

    def _short_url(value: str, *, max_len: int = 60) -> str:
        normalized = value.strip()
        parsed = urlparse(normalized)
        compact = f"{parsed.netloc}{parsed.path}" if parsed.netloc else normalized
        if len(compact) <= max_len:
            return compact
        return f"{compact[: max_len - 3].rstrip()}..."

    priority_icons = {
        "high": "🔴",
        "medium": "🟡",
        "low": "⚪",
    }
    summary = summarize_ocr_queue(db_path=db_path)
    rows = list_ocr_queue(
        statuses=["pending"],
        limit=5,
        db_path=db_path,
    )
    lines = [
        "🧾 OCR triage queue",
        f"Pending: {summary['pending']}",
        f"High priority pending: {summary['high_priority_pending']}",
    ]
    if not rows:
        lines.append("Top pending: none")
        return _cap_message("\n".join(lines))

    lines.append("Top-5 pending:")
    for row in rows:
        priority = str(row.get("priority") or "medium")
        icon = priority_icons.get(priority, "⚪")
        title = _short_title(str(row.get("title") or "(no title)"))
        source_name = str(row.get("source_name") or "unknown")
        short_url = _short_url(str(row.get("document_url") or ""))
        lines.append(f"{icon} {title}")
        lines.append(f"  {source_name}")
        lines.append(f"  {short_url}")
    return _cap_message("\n".join(lines))


def _build_track_message(
    db_path: Path | str,
    *,
    chat_id: str | int | None,
    url_arg: str,
) -> str:
    normalized_url = _normalize_track_url(url_arg)
    if chat_id is None:
        return "⭐ Отслеживание доступно в интерактивном Telegram-боте."
    if not normalized_url:
        return "⭐ Укажи ссылку: /track <url>"
    existing_item = get_active_tracking_item(chat_id, normalized_url, db_path=db_path)
    if existing_item is not None:
        return "⭐ Этот документ уже в отслеживании."

    document = get_document_by_url(normalized_url, db_path=db_path)
    if document is None:
        return "⭐ Документ не найден в базе. Сначала запусти /refresh или найди его через /search."

    tracking_item_id = create_tracking_item(
        chat_id=chat_id,
        document_url=normalized_url,
        document_id=document.id,
        db_path=db_path,
    )
    initial_hash = compute_tracking_status_hash(
        support_status=document.support_status,
        application_status=document.application_status,
        deadline_text=document.deadline_text,
        terms_text=document.terms_text,
        is_active=document.is_active,
        title=document.title,
        summary=document.summary,
    )
    save_tracking_snapshot(
        tracking_item_id=tracking_item_id,
        status_hash=initial_hash,
        support_status=document.support_status,
        application_status=document.application_status,
        deadline_text=document.deadline_text,
        terms_text=document.terms_text,
        is_active=document.is_active,
        title=document.title,
        summary=document.summary,
        db_path=db_path,
    )
    return "⭐ Документ добавлен в отслеживание."


def _build_untrack_message(
    db_path: Path | str,
    *,
    chat_id: str | int | None,
    url_arg: str,
) -> str:
    normalized_url = _normalize_track_url(url_arg)
    if chat_id is None:
        return "⭐ Отслеживание доступно в интерактивном Telegram-боте."
    if not normalized_url:
        return "⭐ Укажи ссылку: /untrack <url>"
    updated = deactivate_tracking_item(chat_id=chat_id, document_url=normalized_url, db_path=db_path)
    if updated == 0:
        return "⭐ Этот документ не найден в активном отслеживании."
    return "⭐ Документ убран из отслеживания."


def _build_tracked_message(
    db_path: Path | str,
    *,
    chat_id: str | int | None,
) -> str:
    if chat_id is None:
        return "⭐ Отслеживание доступно в интерактивном Telegram-боте."
    items = list_active_tracking_items(chat_id=chat_id, limit=10, db_path=db_path)
    if not items:
        return "⭐ Активных отслеживаемых документов пока нет."
    lines = ["⭐ Отслеживаемые документы (до 10):"]
    for item in items:
        title = str(item.get("document_title") or item.get("document_url"))
        source_name = str(item.get("source_name") or "источник не определен")
        last_checked_at = item.get("last_checked_at")
        last_checked = _fmt_dt(last_checked_at) if isinstance(last_checked_at, datetime) else "еще не проверялся"
        lines.append(f"- {title[:140]}")
        lines.append(f"  Источник: {source_name}")
        lines.append(f"  Последняя проверка: {last_checked}")
        lines.append(f"  {item.get('document_url')}")
    return _cap_message("\n".join(lines))


def _normalize_track_url(url: str) -> str:
    normalized = (url or "").strip()
    return normalized.rstrip(".,;")


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
        if document_event_date(document) == today
    ]


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _format_document_lines(
    documents: Sequence[RawDocument],
    *,
    include_summary: bool,
    max_items: int = TELEGRAM_LIST_LIMIT,
    include_hidden_hint: bool = True,
    enrichment_by_url: dict[str, dict[str, object]] | None = None,
) -> list[str]:
    lines: list[str] = []
    for document in documents[:max_items]:
        enrichment = get_display_enrichment(
            (enrichment_by_url or {}).get(document.url)
        )
        published_label = _fmt_dt(document.published_at)
        lines.append(f"- {user_facing_title(document, max_chars=100)}")
        meta_parts = [f"Источник: {document.source_name}"]
        if published_label:
            meta_parts.append(f"Дата: {published_label}")
        meta_parts.append(f"Уровень: {_format_action_level(user_facing_action_level(document))}")
        lines.append(f"  {' | '.join(meta_parts)}")
        reason = _build_user_facing_reason(document, enrichment=enrichment)
        if reason:
            lines.append(f"  Почему важно: {reason[:95]}")
        summary_text = select_executive_summary(
            document,
            enrichment_text=enrichment.get("executive_summary") if enrichment else None,
            fallback_text=document.summary,
            max_chars=95,
        )
        if include_summary and summary_text:
            lines.append(f"  Кратко: {summary_text}")
        if enrichment and enrichment.get("deadline_hint"):
            lines.append(f"  {_format_deadline_line(enrichment['deadline_hint'])}")
        lines.append(f"  {document.url}")
    hidden_count = len(documents) - min(len(documents), max_items)
    if include_hidden_hint and hidden_count > 0:
        lines.append(f"... и еще {hidden_count}.")
    return lines


def _build_user_facing_reason(
    document: RawDocument,
    *,
    enrichment: dict[str, str] | None = None,
) -> str:
    return build_executive_reason(
        document,
        enrichment_text=enrichment.get("business_impact") if enrichment else None,
        fallback_text=document.business_signal or document.impact or document.summary,
        section=classify_display_section(document),
        max_chars=95,
    )


def _fmt_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%Y-%m-%d")


def _format_deadline_line(text: str) -> str:
    normalized = text[:90]
    lowered = normalized.lower()
    if lowered.startswith("срок:") or lowered.startswith("срок истёк:") or lowered.startswith("конец обсуждения:"):
        return normalized
    return f"Срок: {normalized}"


def _is_unavailable_source_audit(audit_row: dict[str, object]) -> bool:
    return bool(audit_row.get("error_message")) and audit_row.get("success_at") is None


def _sanitize_published_at_for_status(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if normalized.astimezone(timezone.utc) > datetime.now(timezone.utc) + PUBLISHED_AT_FUTURE_TOLERANCE:
        return None
    return normalized


def _format_action_level(action_level: str | None) -> str:
    labels = {
        "requires_attention": "требует внимания",
        "watchlist": "наблюдение",
        "background": "фон",
        "irrelevant": "скрыто",
    }
    return labels.get(action_level or "", "наблюдение")


def _build_freshness_lines(db_path: Path | str) -> list[str]:
    collect_event = get_runtime_event("collect", db_path=db_path)
    analyze_event = get_runtime_event("analyze", db_path=db_path)
    report_event = get_runtime_event("report", db_path=db_path)
    source_health = build_source_health_summary(db_path=db_path)
    collect_at = collect_event["updated_at"] if collect_event else None
    analyze_at = analyze_event["updated_at"] if analyze_event else None
    report_at = report_event["updated_at"] if report_event else None
    source_success_at = source_health.latest_success_at
    lines = [
        f"Последний collect: {_fmt_dt(collect_at) or 'дата не определена'}",
        f"Последний успешный сбор источников: {_fmt_dt(source_success_at) or 'дата не определена'}",
        f"Последний analyze: {_fmt_dt(analyze_at) or 'дата не определена'}",
        f"Последний report: {_fmt_dt(report_at) or 'дата не определена'}",
    ]
    degraded_source_count = len(
        set(source_health.failed_sources)
        | {source.source_name for source in source_health.stale_sources}
    )
    if degraded_source_count:
        lines.append(f"⚠️ Есть проблемные источники: {degraded_source_count}")
    if source_health.latest_attempt_at is not None and source_success_at is None:
        lines.append("⚠️ Данные могут быть неполными: успешный сбор источников пока не подтвержден.")
        return lines
    latest = source_success_at
    if latest is None:
        latest = max((dt for dt in (collect_at, analyze_at, report_at) if dt is not None), default=None)
    if latest is None:
        lines.append("⚠️ Свежесть данных не определена.")
        return lines
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - latest.astimezone(timezone.utc)
    minutes = int(delta.total_seconds() // 60)
    hours = max(1, minutes // 60)
    if degraded_source_count:
        lines.append("⚠️ Данные могут быть неполными: часть источников не прошла последнюю проверку")
    elif source_health.latest_attempt_at is not None and minutes > 24 * 60:
        lines.append(f"⚠️ Данные могут быть не полностью свежими: последний успешный сбор был {hours} часов назад")
    elif minutes <= 60:
        lines.append(f"✅ Данные свежие: обновлены {minutes} минут назад")
    else:
        lines.append(f"⚠️ Данные устарели: последнее обновление было {hours} часов назад")
    return lines


def _find_latest_report_file() -> str | None:
    reports_dir = Path(config.REPORTS_DIR)
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
