from __future__ import annotations

import logging
import os
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml
from dotenv import load_dotenv

from app.models import SourceConfig

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
DOCUMENTS_DIR = DATA_DIR / "documents"
REPORTS_DIR = BASE_DIR / "reports"
DOCS_DIR = BASE_DIR / "docs"
LOGS_DIR = BASE_DIR / "logs"


def _get_env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        value = default
    return str(value).strip()


def _get_env_int(name: str, default: int) -> int:
    return int(_get_env_str(name, str(default)))


def _get_env_float(name: str, default: float) -> float:
    return float(_get_env_str(name, str(default)))


DB_PATH = Path(_get_env_str("LAW_MONITOR_DB_PATH", str(DATA_DIR / "law_monitor.db")))
REQUEST_TIMEOUT = _get_env_int("LAW_MONITOR_REQUEST_TIMEOUT", 30)
REQUEST_RETRIES = _get_env_int("LAW_MONITOR_REQUEST_RETRIES", 2)
REQUEST_BACKOFF_FACTOR = _get_env_float("LAW_MONITOR_REQUEST_BACKOFF_FACTOR", 1.0)
USER_AGENT = _get_env_str("LAW_MONITOR_USER_AGENT", "law-monitor-mvp/0.1")
LOG_LEVEL = _get_env_str("LAW_MONITOR_LOG_LEVEL", "INFO").upper()
LOG_FILE_PATH = Path(_get_env_str("LAW_MONITOR_LOG_FILE", str(LOGS_DIR / "app.log")))
LOG_MAX_BYTES = _get_env_int("LAW_MONITOR_LOG_MAX_BYTES", 5 * 1024 * 1024)
LOG_BACKUP_COUNT = _get_env_int("LAW_MONITOR_LOG_BACKUP_COUNT", 5)
TELEGRAM_BOT_TOKEN = _get_env_str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _get_env_str("TELEGRAM_CHAT_ID")
TELEGRAM_PROXY_URL = _get_env_str("TELEGRAM_PROXY_URL")
TELEGRAM_API_TIMEOUT = _get_env_int("TELEGRAM_API_TIMEOUT", 30)
TELEGRAM_PROXY_ENABLED = bool(TELEGRAM_PROXY_URL)
SCHEDULER_DAILY_REPORT_HOUR = _get_env_int("LAW_MONITOR_DAILY_REPORT_HOUR", 9)
SCHEDULER_HOURLY_INTERVAL_MINUTES = _get_env_int(
    "LAW_MONITOR_HOURLY_INTERVAL_MINUTES", 60
)
DEFAULT_REQUEST_HEADERS = {"User-Agent": USER_AGENT}


def setup_logging(level: str | None = None) -> None:
    ensure_directories()
    resolved_level = getattr(logging, (level or LOG_LEVEL).upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(resolved_level)
    for handler in list(root_logger.handlers):
        if getattr(handler, "_law_monitor_handler", False):
            root_logger.removeHandler(handler)
            handler.close()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(resolved_level)
    console_handler.setFormatter(formatter)
    console_handler._law_monitor_handler = True  # type: ignore[attr-defined]
    console_handler._law_monitor_handler_name = "console"  # type: ignore[attr-defined]

    file_handler = RotatingFileHandler(
        LOG_FILE_PATH,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(resolved_level)
    file_handler.setFormatter(formatter)
    file_handler._law_monitor_handler = True  # type: ignore[attr-defined]
    file_handler._law_monitor_handler_name = "file"  # type: ignore[attr-defined]

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _load_yaml(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or []


@lru_cache(maxsize=1)
def load_sources(config_path: Path | None = None) -> list[SourceConfig]:
    path = config_path or CONFIG_DIR / "sources.yaml"
    data = _load_yaml(path)
    return [SourceConfig.model_validate(item) for item in data]


@lru_cache(maxsize=1)
def load_keywords(path: Path | None = None) -> list[str]:
    config_path = path or CONFIG_DIR / "keywords.yaml"
    data = _load_yaml(config_path)
    if isinstance(data, dict):
        keywords = data.get("keywords", [])
    else:
        keywords = data
    return [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
