from __future__ import annotations

import logging
import time
from collections.abc import Sequence

import requests

from app import config
from app.models import RawDocument
from app.notify.telegram_formatter import build_digest_message

logger = logging.getLogger(__name__)

TELEGRAM_SEND_ATTEMPTS = 3
TELEGRAM_RETRY_BACKOFF_SECONDS = 1.0


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
