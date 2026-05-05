from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from app import config
from app.notify.telegram import (
    TELEGRAM_COMMANDS,
    build_command_response,
    get_latest_report_file_path,
)

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
TELEGRAM_POLL_TIMEOUT_SECONDS = 25
TELEGRAM_SEND_ATTEMPTS = 3
TELEGRAM_POLL_BACKOFF_BASE_SECONDS = 1.0
TELEGRAM_POLL_BACKOFF_MAX_SECONDS = 30.0
TELEGRAM_POLL_IDLE_SLEEP_SECONDS = 0.3

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
)
REPLY_KEYBOARD_LAYOUT: tuple[tuple[str, ...], ...] = (
    ("📊 Статус", "🚨 Срочное"),
    ("📅 Сегодня", "👀 Наблюдение"),
    ("📄 Отчёт", "🛰 Источники"),
    ("ℹ️ Помощь",),
)
BUTTON_TO_COMMAND: Mapping[str, str] = {
    "📊 Статус": "/status",
    "🚨 Срочное": "/urgent",
    "📅 Сегодня": "/today",
    "👀 Наблюдение": "/watchlist",
    "📄 Отчёт": "/report",
    "🛰 Источники": "/sources",
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
        except Exception:
            logger.exception("Telegram offset bootstrap failed. Continue with live polling.")

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
            logger.exception(
                "Unexpected telegram polling error. Backoff %.1fs.",
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
                logger.exception("Failed to process update payload: %s", update)
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
) -> DispatchResult:
    command = normalize_incoming_command(text)
    if command == "/start":
        return DispatchResult(command=command, response_text=START_MESSAGE)
    if command in TELEGRAM_COMMANDS:
        response = build_command_response(command, db_path=db_path)
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
    dispatch_result = dispatch_input_text(text, db_path=db_path)
    _send_response(chat_id=chat_id, text=dispatch_result.response_text, proxies=proxies)
    if dispatch_result.command == "/report":
        _send_report_attachment(chat_id=chat_id, proxies=proxies)


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
        logger.exception("Failed to configure Telegram commands via setMyCommands.")


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
        except Exception:
            logger.exception("Failed to send Telegram response.")
            return False
    return True


def _send_report_attachment(
    *,
    chat_id: int | str,
    proxies: dict[str, str] | None,
) -> bool:
    report_path = get_latest_report_file_path()
    if report_path is None or not report_path.exists():
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
    txt_report_path = report_path.with_suffix(".txt")
    created_txt_copy = False
    try:
        txt_report_path.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
        created_txt_copy = True
    except Exception:
        logger.exception("Failed to prepare .txt report copy from %s", report_path)
        txt_report_path = report_path

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
            except Exception:
                logger.exception(
                    "Failed to send report attachment via Telegram (attempt %s/%s).",
                    attempt,
                    TELEGRAM_SEND_ATTEMPTS,
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
        if created_txt_copy:
            try:
                txt_report_path.unlink(missing_ok=True)
            except Exception:
                logger.warning("Failed to remove temporary txt report: %s", txt_report_path)


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
                description = data.get("description", "unknown telegram error")
                raise RuntimeError(f"Telegram API {method} failed: {description}")
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
