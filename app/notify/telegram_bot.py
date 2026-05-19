from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app import config
from app.notify.telegram import (
    SEARCH_PROMPT_MESSAGE,
    TELEGRAM_COMMANDS,
    build_command_response,
    get_interface_summary,
    sanitize_telegram_exception_message,
)
from app.operational_health import collect_operational_notices
from app.periods import (
    PeriodSpec,
    build_rolling_period,
    build_today_period,
    build_yesterday_period,
    filter_documents_for_period,
    format_period_label,
    parse_period_spec,
)
from app.reports.markdown_report import generate_markdown_report
from app.run_lock import WriterLockHeldError, writer_lock
from app.pipeline.analyze import run_analyze
from app.pipeline.collect import run_collect
from app.pipeline.digest import run_digest
from app.storage import (
    backfill_missing_published_at,
    count_documents_by_action_level,
    get_user_default_period_days,
    get_runtime_event,
    init_db,
    list_recent_documents,
    list_recent_source_errors,
    list_latest_source_audit,
    mark_runtime_event,
    set_user_default_period_days,
)

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
TELEGRAM_POLL_TIMEOUT_SECONDS = 25
TELEGRAM_SEND_ATTEMPTS = 3
TELEGRAM_POLL_BACKOFF_BASE_SECONDS = 1.0
TELEGRAM_POLL_BACKOFF_MAX_SECONDS = 30.0
TELEGRAM_POLL_IDLE_SLEEP_SECONDS = 0.3
MANUAL_REFRESH_COOLDOWN_SECONDS = 3 * 3600
_refresh_lock = threading.Lock()
_pending_search_chats: set[str] = set()
_pending_report_period_chats: set[str] = set()

START_MESSAGE = (
    "AHSTEP GR Monitor запущен ✅\n\n"
    "Система отслеживает меры поддержки, НПА и сигналы по АПК.\n\n"
    "Выберите действие ниже или используйте команды через /."
)
UNKNOWN_COMMAND_MESSAGE = (
    "Не понял команду 🤔\n\n"
    "Используй кнопки ниже или введи /help"
)

BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "открыть меню"),
    ("help", "помощь"),
    ("urgent", "требует внимания"),
    ("report", "последний отчет"),
    ("search", "поиск по архиву"),
    ("refresh", "обновить данные"),
)
REPLY_KEYBOARD_LAYOUT: tuple[tuple[str, ...], ...] = (
    ("🚨 Срочное", "📄 Отчёт"),
    ("🔎 Поиск", "🔄 Обновить"),
    ("ℹ️ Помощь",),
)
BUTTON_TO_COMMAND: Mapping[str, str] = {
    "📊 Статус": "/status",
    "🚨 Срочное": "/urgent",
    "📅 Сегодня": "/today",
    "👀 Наблюдение": "/watchlist",
    "📄 Отчёт": "/report",
    "🛰 Источники": "/sources",
    "🔎 Поиск": "/search",
    "🔄 Обновить": "/refresh",
    "🔄 Обновить данные": "/refresh",
    "ℹ️ Помощь": "/help",
}
REPORT_PERIOD_BUTTON_TO_COMMAND: Mapping[str, str] = {
    "Сегодня": "/report today",
    "Вчера": "/report yesterday",
    "3 дня": "/report 3",
    "7 дней": "/report 7",
    "14 дней": "/report 14",
}
REPORT_PERIOD_KEYBOARD_LAYOUT: tuple[tuple[str, ...], ...] = (
    ("Сегодня", "Вчера"),
    ("3 дня", "7 дней"),
    ("14 дней",),
    ("Отмена",),
)
NORMALIZED_BUTTON_TO_COMMAND: Mapping[str, str] = {
    re.sub(r"\s+", " ", button.replace("\ufe0f", "")).strip(): command
    for button, command in BUTTON_TO_COMMAND.items()
}
@dataclass(frozen=True)
class DispatchResult:
    command: str | None
    response_text: str


def run_polling_listener(
    *,
    db_path: Path | str | None = None,
    max_cycles: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    if not _is_bot_configured():
        raise RuntimeError(
            "Telegram bot is not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
        )

    proxies = _build_proxies()
    _configure_bot_commands(proxies=proxies)
    offset_path = _offset_store_path()
    offset = load_offset(offset_path)
    if offset is None:
        try:
            offset = _bootstrap_offset(proxies=proxies)
            if offset is not None:
                save_offset(offset, offset_path)
        except requests.RequestException as exc:
            logger.warning(
                "Telegram offset bootstrap failed (%s). Continue with live polling.",
                _describe_request_error(exc),
            )
        except Exception as exc:
            logger.warning(
                "Telegram offset bootstrap failed (%s). Continue with live polling.",
                _describe_telegram_error(exc),
            )

    failures = 0
    cycle = 0
    while max_cycles is None or cycle < max_cycles:
        cycle += 1
        try:
            updates = _get_updates(offset=offset, proxies=proxies)
            failures = 0
        except requests.RequestException as exc:
            failures += 1
            backoff = min(
                TELEGRAM_POLL_BACKOFF_BASE_SECONDS * (2 ** (failures - 1)),
                TELEGRAM_POLL_BACKOFF_MAX_SECONDS,
            )
            logger.warning(
                "Telegram polling failed (%s). Backoff %.1fs.",
                _describe_request_error(exc),
                backoff,
            )
            sleep_fn(backoff)
            continue
        except Exception:
            failures += 1
            backoff = min(
                TELEGRAM_POLL_BACKOFF_BASE_SECONDS * (2 ** (failures - 1)),
                TELEGRAM_POLL_BACKOFF_MAX_SECONDS,
            )
            logger.warning(
                "Unexpected telegram polling error (%s). Backoff %.1fs.",
                "telegram_api_error",
                backoff,
            )
            sleep_fn(backoff)
            continue

        if not updates:
            sleep_fn(TELEGRAM_POLL_IDLE_SLEEP_SECONDS)
            continue

        for update in updates:
            next_offset = _extract_next_offset(update, current_offset=offset)
            try:
                _process_update(update, db_path=db_path, proxies=proxies)
            except Exception as exc:
                message = update.get("message") if isinstance(update, Mapping) else None
                chat = message.get("chat") if isinstance(message, Mapping) else None
                logger.warning(
                    "Failed to process update payload (telegram_api_error). "
                    "update_id=%s chat_id=%s text=%r error=%s",
                    update.get("update_id") if isinstance(update, Mapping) else None,
                    chat.get("id") if isinstance(chat, Mapping) else None,
                    message.get("text") if isinstance(message, Mapping) else None,
                    _describe_telegram_error(exc),
                )
            finally:
                if next_offset is not None and (offset is None or next_offset > offset):
                    offset = next_offset
                    save_offset(offset, offset_path)


def build_set_my_commands_payload() -> dict[str, list[dict[str, str]]]:
    return {
        "commands": [
            {"command": command, "description": description}
            for command, description in BOT_COMMANDS
        ]
    }


def build_reply_keyboard_payload() -> dict[str, Any]:
    keyboard = [
        [{"text": button_text} for button_text in row]
        for row in REPLY_KEYBOARD_LAYOUT
    ]
    return {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "is_persistent": True,
        "input_field_placeholder": "Выберите действие",
    }


def build_report_period_keyboard_payload() -> dict[str, Any]:
    keyboard = [
        [{"text": button_text} for button_text in row]
        for row in REPORT_PERIOD_KEYBOARD_LAYOUT
    ]
    return {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": True,
        "is_persistent": False,
        "input_field_placeholder": "Выберите период отчета",
    }


def normalize_incoming_command(text: str) -> str | None:
    normalized = (text or "").strip()
    if not normalized:
        return None
    mapped_button = BUTTON_TO_COMMAND.get(normalized)
    if not mapped_button:
        mapped_button = NORMALIZED_BUTTON_TO_COMMAND.get(_normalize_button_text(normalized))
    if mapped_button:
        return mapped_button
    if not normalized.startswith("/"):
        return None
    token = normalized.split()[0].lower()
    return token.split("@", maxsplit=1)[0]


def dispatch_input_text(
    text: str,
    *,
    db_path: Path | str | None = None,
    default_days: int = 7,
    chat_id: str | int | None = None,
) -> DispatchResult:
    command = normalize_incoming_command(text)
    if command == "/start":
        return DispatchResult(command=command, response_text=START_MESSAGE)
    if command == "/refresh":
        return DispatchResult(command=command, response_text="⏳ Обновление запущено...")
    if command == "/search" and not _has_command_arguments(text):
        return DispatchResult(command=command, response_text=SEARCH_PROMPT_MESSAGE)
    if command in TELEGRAM_COMMANDS:
        command_text = _command_text_for_dispatch(command, text)
        response = build_command_response(
            command_text,
            db_path=db_path,
            default_days=default_days,
            chat_id=chat_id,
        )
        return DispatchResult(command=command, response_text=response)
    return DispatchResult(command=None, response_text=UNKNOWN_COMMAND_MESSAGE)


def load_offset(path: Path | None = None) -> int | None:
    offset_path = path or _offset_store_path()
    if not offset_path.exists():
        return None
    raw_value = offset_path.read_text(encoding="utf-8").strip()
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except ValueError:
        logger.warning("Invalid telegram offset value in %s: %s", offset_path, raw_value)
        return None


def save_offset(offset: int, path: Path | None = None) -> None:
    offset_path = path or _offset_store_path()
    offset_path.parent.mkdir(parents=True, exist_ok=True)
    offset_path.write_text(str(offset), encoding="utf-8")


def _process_update(
    update: Mapping[str, Any],
    *,
    db_path: Path | str | None,
    proxies: dict[str, str] | None,
) -> None:
    message = update.get("message")
    if not isinstance(message, Mapping):
        return

    chat = message.get("chat")
    chat_id = chat.get("id") if isinstance(chat, Mapping) else None
    if chat_id is None:
        return

    allowed_chat_id = str(config.TELEGRAM_CHAT_ID).strip()
    if str(chat_id) != allowed_chat_id:
        logger.info("Ignoring update from unauthorized chat_id=%s", chat_id)
        return

    text = str(message.get("text") or "")
    incoming_command = normalize_incoming_command(text)
    chat_key = str(chat_id)
    if incoming_command is None and chat_key in _pending_search_chats:
        _pending_search_chats.discard(chat_key)
        response_text = build_command_response(
            f"/search {text.strip()}",
            db_path=db_path,
            default_days=7,
            chat_id=chat_id,
        )
        _send_response(chat_id=chat_id, text=response_text, proxies=proxies)
        return
    if incoming_command is None and chat_key in _pending_report_period_chats:
        normalized_period = _normalize_report_period_choice(text)
        if normalized_period == "отмена":
            _pending_report_period_chats.discard(chat_key)
            _send_response(chat_id=chat_id, text="Отменено. Возвращаюсь в основное меню.", proxies=proxies)
            return
        selected_command = REPORT_PERIOD_BUTTON_TO_COMMAND.get(normalized_period) if normalized_period else None
        if selected_command is not None:
            _pending_report_period_chats.discard(chat_key)
            _pending_search_chats.discard(chat_key)
            _send_response(
                chat_id=chat_id,
                text="⏳ Подождите немного, формируется GR-отчет...",
                proxies=proxies,
            )
            report_command = selected_command
            prepared_attachment = _build_period_report_attachment(command_text=report_command, db_path=db_path)
            dispatch_result = dispatch_input_text(
                report_command,
                db_path=db_path,
                default_days=7,
                chat_id=chat_id,
            )
            _send_response(chat_id=chat_id, text=dispatch_result.response_text, proxies=proxies)
            _send_report_attachment(
                chat_id=chat_id,
                proxies=proxies,
                command_text=report_command,
                days=_resolve_report_period(report_command).days,
                db_path=db_path,
                prepared_path=prepared_attachment,
            )
            return
    if incoming_command is not None and incoming_command != "/search":
        _pending_search_chats.discard(chat_key)
    if incoming_command is not None and incoming_command != "/report":
        _pending_report_period_chats.discard(chat_key)
    resolved_text = _command_text_for_dispatch(incoming_command, text)
    default_days = 7
    if incoming_command in {"/report", "/urgent", "/watchlist"}:
        default_days, resolved_text = _resolve_period_command_text(
            text=resolved_text,
            chat_id=chat_id,
            db_path=db_path,
        )
    if incoming_command == "/refresh":
        _send_response(chat_id=chat_id, text="⏳ Обновление запущено...", proxies=proxies)
        refresh_text = _run_manual_refresh(db_path=db_path)
        _send_response(chat_id=chat_id, text=refresh_text, proxies=proxies)
        return
    if incoming_command == "/search" and not _has_command_arguments(text):
        _pending_search_chats.add(chat_key)
        _send_response(chat_id=chat_id, text=SEARCH_PROMPT_MESSAGE, proxies=proxies)
        return
    if incoming_command == "/report" and not _has_command_arguments(text) and text.strip() == "📄 Отчёт":
        _pending_report_period_chats.add(chat_key)
        _send_response(
            chat_id=chat_id,
            text="📄 Выберите период отчёта:",
            proxies=proxies,
            reply_markup=build_report_period_keyboard_payload(),
        )
        return
    prepared_attachment: Path | None = None
    if incoming_command == "/report":
        _send_response(
            chat_id=chat_id,
            text="⏳ Подождите немного, формируется GR-отчет...",
            proxies=proxies,
        )
        prepared_attachment = _build_period_report_attachment(command_text=resolved_text, db_path=db_path)
    dispatch_result = dispatch_input_text(
        resolved_text,
        db_path=db_path,
        default_days=default_days,
        chat_id=chat_id,
    )
    _send_response(chat_id=chat_id, text=dispatch_result.response_text, proxies=proxies)
    if dispatch_result.command == "/report":
        _send_report_attachment(
            chat_id=chat_id,
            proxies=proxies,
            command_text=resolved_text,
            days=_resolve_report_period(resolved_text, default_days=default_days).days,
            db_path=db_path,
            prepared_path=prepared_attachment,
        )


def _extract_next_offset(
    update: Mapping[str, Any],
    *,
    current_offset: int | None,
) -> int | None:
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return current_offset
    return update_id + 1


def _configure_bot_commands(*, proxies: dict[str, str] | None) -> None:
    try:
        _call_telegram_api(
            "setMyCommands",
            payload=build_set_my_commands_payload(),
            proxies=proxies,
            attempts=TELEGRAM_SEND_ATTEMPTS,
        )
        logger.info("Telegram bot commands configured.")
    except Exception:
        logger.warning("Failed to configure Telegram commands via setMyCommands (telegram_api_error).")


def _bootstrap_offset(*, proxies: dict[str, str] | None) -> int | None:
    result = _call_telegram_api(
        "getUpdates",
        params={
            "offset": -1,
            "timeout": 0,
            "allowed_updates": json.dumps(["message"]),
        },
        proxies=proxies,
    )
    updates = _ensure_updates_sequence(result)
    if not updates:
        return None
    last_update_id = updates[-1].get("update_id")
    if isinstance(last_update_id, int):
        return last_update_id + 1
    return None


def _get_updates(
    *,
    offset: int | None,
    proxies: dict[str, str] | None,
) -> list[Mapping[str, Any]]:
    poll_timeout = max(min(config.TELEGRAM_API_TIMEOUT, TELEGRAM_POLL_TIMEOUT_SECONDS), 1)
    params: dict[str, Any] = {
        "timeout": poll_timeout,
        "allowed_updates": json.dumps(["message"]),
    }
    if offset is not None:
        params["offset"] = offset
    result = _call_telegram_api(
        "getUpdates",
        params=params,
        proxies=proxies,
    )
    return _ensure_updates_sequence(result)


def _ensure_updates_sequence(result: Any) -> list[Mapping[str, Any]]:
    if not isinstance(result, Sequence):
        logger.warning("Unexpected updates payload type: %s", type(result).__name__)
        return []
    normalized: list[Mapping[str, Any]] = []
    for item in result:
        if isinstance(item, Mapping):
            normalized.append(item)
    return normalized


def _send_response(
    *,
    chat_id: int | str,
    text: str,
    proxies: dict[str, str] | None,
    reply_markup: Mapping[str, Any] | None = None,
    include_default_keyboard: bool = True,
) -> bool:
    chunks = _split_message_chunks(text)
    for chunk in chunks:
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = dict(reply_markup)
        elif include_default_keyboard:
            payload["reply_markup"] = build_reply_keyboard_payload()
        try:
            _call_telegram_api(
                "sendMessage",
                payload=payload,
                proxies=proxies,
                attempts=TELEGRAM_SEND_ATTEMPTS,
            )
        except Exception as exc:
            logger.warning("Failed to send Telegram response (%s).", _describe_telegram_error(exc))
            return False
    return True


def _send_report_attachment(
    *,
    chat_id: int | str,
    proxies: dict[str, str] | None,
    command_text: str | None = None,
    days: int | None = None,
    db_path: Path | str | None,
    prepared_path: Path | None = None,
) -> bool:
    resolved_command_text = command_text or f"/report {max(1, min(int(days or 7), 365))}"
    txt_report_path = prepared_path or _build_period_report_attachment(
        command_text=resolved_command_text,
        days=days,
        db_path=db_path,
    )
    if txt_report_path is None or not txt_report_path.exists():
        return _send_response(
            chat_id=chat_id,
            text="Полный отчет временно недоступен, используйте краткую сводку выше",
            proxies=proxies,
        )

    if not _send_response(
        chat_id=chat_id,
        text="📎 Полный отчет во вложении",
        proxies=proxies,
        include_default_keyboard=False,
    ):
        return False

    timeout = max(config.TELEGRAM_API_TIMEOUT + 5, 10)
    token = config.TELEGRAM_BOT_TOKEN
    url = f"{TELEGRAM_API_BASE_URL}/bot{token}/sendDocument"

    try:
        for attempt in range(1, TELEGRAM_SEND_ATTEMPTS + 1):
            try:
                with txt_report_path.open("rb") as document_file:
                    response = requests.post(
                        url,
                        data={"chat_id": str(chat_id)},
                        files={"document": (txt_report_path.name, document_file, "text/plain")},
                        timeout=timeout,
                        proxies=proxies,
                    )
                response.raise_for_status()
                payload = response.json()
                if not payload.get("ok"):
                    raise RuntimeError(payload.get("description", "sendDocument failed"))
                return True
            except Exception as exc:
                logger.warning(
                    "Failed to send report attachment via Telegram (attempt %s/%s, %s).",
                    attempt,
                    TELEGRAM_SEND_ATTEMPTS,
                    _describe_telegram_error(exc),
                )
                if attempt < TELEGRAM_SEND_ATTEMPTS:
                    time.sleep(attempt)
                    continue

        return _send_response(
            chat_id=chat_id,
            text="Полный отчет временно недоступен, используйте краткую сводку выше",
            proxies=proxies,
        )
    finally:
        try:
            txt_report_path.unlink(missing_ok=True)
        except Exception:
            logger.warning("Failed to remove temporary txt report: %s", txt_report_path)


def _markdown_to_plain_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"^\s*#{1,6}\s*", "", raw_line)
        line = line.replace("### ", "")
        line = line.replace("**", "")
        line = line.replace("__", "")
        line = re.sub(r"^\s*-\s+", "- ", line)
        lines.append(line.rstrip())
    normalized = "\n".join(lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    return normalized + "\n"


def _resolve_period_command_text(
    *,
    text: str,
    chat_id: int | str,
    db_path: Path | str | None,
) -> tuple[int, str]:
    resolved_db_path = db_path or config.DB_PATH
    init_db(resolved_db_path)
    tokens = (text or "").strip().split()
    if not tokens:
        return 7, text
    if len(tokens) > 1 and tokens[1].lower() in {"today", "yesterday"}:
        return 7, f"{tokens[0]} {tokens[1].lower()}"
    if len(tokens) > 1 and tokens[1].isdigit():
        explicit_days = max(1, min(int(tokens[1]), 365))
        set_user_default_period_days(chat_id, explicit_days, db_path=resolved_db_path)
        return explicit_days, f"{tokens[0]} {explicit_days}"
    default_days = get_user_default_period_days(chat_id, db_path=resolved_db_path, fallback=7)
    return default_days, f"{tokens[0]} {default_days}"


def _command_text_for_dispatch(command: str | None, text: str) -> str:
    if command is None:
        return text
    normalized = (text or "").strip()
    if normalized.startswith("/"):
        return normalized
    return command


def _normalize_button_text(text: str) -> str:
    without_variation_selectors = (text or "").replace("\ufe0f", "")
    return re.sub(r"\s+", " ", without_variation_selectors).strip()


def _normalize_report_period_choice(text: str) -> str | None:
    normalized = (text or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"сегодня", "вчера", "3 дня", "7 дней", "14 дней", "отмена"}:
        if normalized == "сегодня":
            return "Сегодня"
        if normalized == "вчера":
            return "Вчера"
        if normalized == "3 дня":
            return "3 дня"
        if normalized == "7 дней":
            return "7 дней"
        if normalized == "14 дней":
            return "14 дней"
        return "отмена"
    return None


def _has_command_arguments(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized or not normalized.startswith("/"):
        return False
    return len(normalized.split(maxsplit=1)) > 1


def _resolve_report_period(command_text: str, *, default_days: int = 7) -> PeriodSpec:
    tokens = (command_text or "").strip().split()
    token = tokens[1] if len(tokens) > 1 else None
    return parse_period_spec(token, default_days=default_days)


def _build_period_report_attachment(
    *,
    command_text: str | None = None,
    days: int | None = None,
    db_path: Path | str | None,
) -> Path | None:
    try:
        with writer_lock("telegram-report-attachment"):
            resolved_db_path = db_path or config.DB_PATH
            init_db(resolved_db_path)
            backfill_missing_published_at(resolved_db_path)
            resolved_command_text = command_text or f"/report {max(1, min(int(days or 7), 365))}"
            period = _resolve_report_period(resolved_command_text)
            documents = list_recent_documents(
                db_path=resolved_db_path,
                days=period.days,
                relevant_only=False,
                action_levels=None,
            )
            documents = filter_documents_for_period(documents, period)
            source_errors = list_recent_source_errors(db_path=resolved_db_path, days=period.days)
            period_context_lines: list[str] = []
            if period.kind == "today":
                summary = get_interface_summary(db_path=resolved_db_path, days=14)
                has_today_urgent = any(
                    (document.action_level == "requires_attention")
                    and (document.published_at or document.collected_at)
                    for document in documents
                )
                if not has_today_urgent:
                    period_context_lines.append("Сегодня новых срочных документов нет.")
                    if summary["requires_attention"] > 0:
                        period_context_lines.append(
                            f"Активные срочные вопросы за последние 14 дней: {summary['requires_attention']}. Откройте 🚨 Срочное."
                        )
            markdown = generate_markdown_report(
                documents,
                report_date=datetime.now(config.SCHEDULER_TIMEZONE).strftime("%Y-%m-%d"),
                period_days=period.days,
                period_label=format_period_label(period),
                period_context_lines=period_context_lines,
                operational_notices=collect_operational_notices(db_path=resolved_db_path),
                source_errors=source_errors,
                db_path=resolved_db_path,
            )
            txt_content = _markdown_to_plain_text(markdown)
            timestamp = datetime.now(config.SCHEDULER_TIMEZONE).strftime("%Y-%m-%d")
            suffix = uuid.uuid4().hex[:8]
            attachment_dir = config.TMP_DIR / "telegram_attachments"
            attachment_dir.mkdir(parents=True, exist_ok=True)
            txt_path = attachment_dir / f"gr_monitoring_{timestamp}_{period.kind}_{period.days}d_{suffix}.txt"
            txt_path.write_text(txt_content, encoding="utf-8")
            return txt_path
    except WriterLockHeldError:
        logger.info("Report attachment skipped because another write operation is running.")
        return None
    except Exception:
        logger.exception("Failed to build report attachment for command %s.", command_text or days)
        return None


def _run_manual_refresh(*, db_path: Path | str | None) -> str:
    if not _refresh_lock.acquire(blocking=False):
        return "⏳ Обновление уже выполняется, попробуйте позже."
    lock_context = None
    try:
        try:
            lock_context = writer_lock("telegram-refresh")
            lock_context.__enter__()
        except WriterLockHeldError:
            return "⏳ Обновление уже выполняется, попробуйте позже."
        last_refresh = get_runtime_event("manual_refresh", db_path=db_path or config.DB_PATH)
        if last_refresh and last_refresh.get("updated_at") is not None:
            refreshed_at = last_refresh["updated_at"]
            if refreshed_at.tzinfo is None:
                refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
            elapsed_seconds = (datetime.now(timezone.utc) - refreshed_at.astimezone(timezone.utc)).total_seconds()
            if elapsed_seconds < MANUAL_REFRESH_COOLDOWN_SECONDS:
                wait_hours = int((MANUAL_REFRESH_COOLDOWN_SECONDS - elapsed_seconds) // 3600) + 1
                return f"⏳ Обновление запускалось недавно. Повторите через {wait_hours} ч."

        collected = run_collect()
        analyzed = run_analyze()
        run_digest(days=7)
        interface_summary = get_interface_summary(
            db_path=db_path or config.DB_PATH,
            days=7,
        )
        audit_rows = list_latest_source_audit(db_path=db_path or config.DB_PATH)
        problematic_sources = sum(1 for row in audit_rows if row.get("error_message"))
        mark_runtime_event(
            "manual_refresh",
            details=f"collected={collected}; analyzed={analyzed}",
            db_path=db_path or config.DB_PATH,
        )
        lines = [
            "✅ Обновление завершено\n"
            f"Новых документов: {collected}\n"
            f"Обработано: {analyzed}\n"
            f"Включено в интерфейс: {interface_summary['visible_total']}\n"
            f"Требует реакции: {interface_summary['requires_attention']}\n"
            f"Отраслевых сигналов: {interface_summary['watchlist']}\n"
            "Период проверки: последние 7 дней"
        ]
        if problematic_sources > 0:
            lines.append(f"Проблемных источников: {problematic_sources}")
        else:
            lines.append("Ошибки источников: 0")
        return "\n".join(lines)
    except Exception:
        logger.exception("Manual refresh failed.")
        return "❌ Обновление завершилось с ошибкой. Повторите позже."
    finally:
        try:
            lock_context.__exit__(None, None, None)  # type: ignore[union-attr]
        except Exception:
            pass
        _refresh_lock.release()


def _call_telegram_api(
    method: str,
    *,
    payload: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
    proxies: dict[str, str] | None = None,
    attempts: int = 1,
) -> Any:
    token = config.TELEGRAM_BOT_TOKEN
    url = f"{TELEGRAM_API_BASE_URL}/bot{token}/{method}"
    timeout = max(config.TELEGRAM_API_TIMEOUT + 5, 10)
    last_exception: Exception | None = None
    safe_payload_preview = None
    if payload is not None:
        safe_payload_preview = {
            "chat_id": payload.get("chat_id"),
            "text_preview": str(payload.get("text", ""))[:120],
            "reply_markup": payload.get("reply_markup"),
        }
    for attempt in range(1, attempts + 1):
        try:
            if payload is not None:
                response = requests.post(
                    url,
                    json=payload,
                    timeout=timeout,
                    proxies=proxies,
                )
            else:
                response = requests.get(
                    url,
                    params=params,
                    timeout=timeout,
                    proxies=proxies,
                )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                logger.warning(
                    "Telegram API returned not-ok response: method=%s attempt=%s payload=%s response=%s",
                    method,
                    attempt,
                    safe_payload_preview,
                    data,
                )
                raise RuntimeError("telegram_api_error")
            return data.get("result")
        except requests.RequestException as exc:
            last_exception = exc
            response = getattr(exc, "response", None)
            response_text = ""
            if response is not None:
                try:
                    response_text = response.text[:1000]
                except Exception:
                    response_text = ""
            logger.warning(
                "Telegram API request failed: method=%s attempt=%s error=%s payload=%s response_body=%r",
                method,
                attempt,
                _describe_request_error(exc),
                safe_payload_preview,
                response_text,
            )
            if attempt < attempts:
                time.sleep(attempt)
                continue
            raise
        except (ValueError, RuntimeError) as exc:
            last_exception = exc
            logger.warning(
                "Telegram API payload/response error: method=%s attempt=%s payload=%s error=%s",
                method,
                attempt,
                safe_payload_preview,
                _describe_telegram_error(exc),
            )
            if attempt < attempts:
                time.sleep(attempt)
                continue
            raise
    if last_exception is not None:
        raise last_exception
    raise RuntimeError(f"Telegram API {method} call failed unexpectedly.")


def _split_message_chunks(text: str) -> list[str]:
    normalized = (text or "").strip()
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


def _offset_store_path() -> Path:
    return config.DATA_DIR / "telegram_bot_offset.txt"


def _is_bot_configured() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def _build_proxies() -> dict[str, str] | None:
    if not config.TELEGRAM_PROXY_URL:
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


def _describe_telegram_error(exc: Exception) -> str:
    if isinstance(exc, requests.RequestException):
        return _describe_request_error(exc)
    if isinstance(exc, RuntimeError):
        return "telegram_api_error"
    _ = sanitize_telegram_exception_message(exc)
    return "telegram_api_error"
