from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app import config
from app.notify.telegram import (
    TELEGRAM_COMMANDS,
    build_command_response,
    sanitize_telegram_exception_message,
)
from app.reports.markdown_report import generate_markdown_report
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
MANUAL_REFRESH_COOLDOWN_SECONDS = 3600
_refresh_lock = threading.Lock()

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
    ("status", "состояние системы"),
    ("today", "сводка за сегодня"),
    ("urgent", "требует внимания"),
    ("watchlist", "наблюдение"),
    ("report", "последний отчет"),
    ("sources", "источники"),
    ("ocr", "OCR triage"),
    ("search", "поиск по архиву"),
    ("track", "добавить в отслеживание"),
    ("untrack", "убрать из отслеживания"),
    ("tracked", "отслеживаемые документы"),
    ("refresh", "обновить данные"),
)
REPLY_KEYBOARD_LAYOUT: tuple[tuple[str, ...], ...] = (
    ("📊 Статус", "🚨 Срочное"),
    ("📅 Сегодня", "👀 Наблюдение"),
    ("📄 Отчёт", "🛰 Источники"),
    ("🔎 Поиск", "⭐ Отслеживаемое"),
    ("🔄 Обновить данные",),
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
    "⭐ Отслеживаемое": "/tracked",
    "🔄 Обновить данные": "/refresh",
    "ℹ️ Помощь": "/help",
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
            except Exception:
                logger.warning("Failed to process update payload (telegram_api_error).")
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


def normalize_incoming_command(text: str) -> str | None:
    normalized = (text or "").strip()
    if not normalized:
        return None
    mapped_button = BUTTON_TO_COMMAND.get(normalized)
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
        return DispatchResult(command=command, response_text="⏳ Обновляю данные, подождите...")
    if command in TELEGRAM_COMMANDS:
        response = build_command_response(
            text,
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
    resolved_text = text
    default_days = 7
    if incoming_command in {"/report", "/urgent", "/watchlist"}:
        default_days, resolved_text = _resolve_period_command_text(
            text=text,
            chat_id=chat_id,
            db_path=db_path,
        )
    if incoming_command == "/refresh":
        refresh_text = _run_manual_refresh(db_path=db_path)
        _send_response(chat_id=chat_id, text=refresh_text, proxies=proxies)
        return
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
            days=_extract_report_days(resolved_text, default_days=default_days),
            db_path=db_path,
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
) -> bool:
    chunks = _split_message_chunks(text)
    for chunk in chunks:
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
            "reply_markup": build_reply_keyboard_payload(),
        }
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
    days: int,
    db_path: Path | str | None,
) -> bool:
    txt_report_path = _build_period_report_attachment(days=days, db_path=db_path)
    if txt_report_path is None or not txt_report_path.exists():
        return _send_response(
            chat_id=chat_id,
            text="Полный отчет временно недоступен, используйте краткую сводку выше",
            proxies=proxies,
        )

    if not _send_response(chat_id=chat_id, text="📎 Полный отчет во вложении", proxies=proxies):
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
    if len(tokens) > 1 and tokens[1].isdigit():
        explicit_days = max(1, min(int(tokens[1]), 365))
        set_user_default_period_days(chat_id, explicit_days, db_path=resolved_db_path)
        return explicit_days, f"{tokens[0]} {explicit_days}"
    default_days = get_user_default_period_days(chat_id, db_path=resolved_db_path, fallback=7)
    return default_days, f"{tokens[0]} {default_days}"


def _extract_report_days(text: str, *, default_days: int) -> int:
    tokens = (text or "").strip().split()
    if len(tokens) > 1 and tokens[1].isdigit():
        return max(1, min(int(tokens[1]), 365))
    return max(1, min(int(default_days), 365))


def _build_period_report_attachment(*, days: int, db_path: Path | str | None) -> Path | None:
    try:
        resolved_db_path = db_path or config.DB_PATH
        init_db(resolved_db_path)
        backfill_missing_published_at(resolved_db_path)
        documents = list_recent_documents(
            db_path=resolved_db_path,
            days=days,
            relevant_only=False,
            action_levels=None,
        )
        source_errors = list_recent_source_errors(db_path=resolved_db_path, days=days)
        markdown = generate_markdown_report(
            documents,
            report_date=datetime.now().strftime("%Y-%m-%d"),
            period_days=days,
            source_errors=source_errors,
        )
        txt_content = _markdown_to_plain_text(markdown)
        timestamp = datetime.now().strftime("%Y-%m-%d")
        attachment_dir = config.DATA_DIR / "telegram_attachments"
        attachment_dir.mkdir(parents=True, exist_ok=True)
        txt_path = attachment_dir / f"gr_monitoring_{timestamp}_{days}d.txt"
        txt_path.write_text(txt_content, encoding="utf-8")
        return txt_path
    except Exception:
        logger.exception("Failed to build report attachment for %s days.", days)
        return None


def _run_manual_refresh(*, db_path: Path | str | None) -> str:
    if not _refresh_lock.acquire(blocking=False):
        return "⏳ Обновление уже выполняется. Дождитесь завершения текущего запуска."
    try:
        last_refresh = get_runtime_event("manual_refresh", db_path=db_path or config.DB_PATH)
        if last_refresh and last_refresh.get("updated_at") is not None:
            refreshed_at = last_refresh["updated_at"]
            if refreshed_at.tzinfo is None:
                refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
            elapsed_seconds = (datetime.now(timezone.utc) - refreshed_at.astimezone(timezone.utc)).total_seconds()
            if elapsed_seconds < MANUAL_REFRESH_COOLDOWN_SECONDS:
                wait_minutes = int((MANUAL_REFRESH_COOLDOWN_SECONDS - elapsed_seconds) // 60) + 1
                return f"⏳ Обновление запускалось недавно. Повторите через {wait_minutes} мин."

        collected = run_collect()
        analyzed = run_analyze()
        run_digest(days=7)
        requires_attention = count_documents_by_action_level("requires_attention")
        watchlist = count_documents_by_action_level("watchlist")
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
            f"Требует внимания: {requires_attention}\n"
            f"Наблюдение: {watchlist}"
        ]
        if problematic_sources > 0:
            lines.append(f"Проблемных источников: {problematic_sources}")
        else:
            lines.append("Ошибки источников: 0")
        return "\n".join(lines)
    except Exception:
        logger.exception("Manual refresh failed.")
        return "❌ Обновление завершилось с ошибкой. Проверьте /sources и повторите позже."
    finally:
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
                raise RuntimeError("telegram_api_error")
            return data.get("result")
        except requests.RequestException as exc:
            last_exception = exc
            if attempt < attempts:
                time.sleep(attempt)
                continue
            raise
        except (ValueError, RuntimeError) as exc:
            last_exception = exc
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
