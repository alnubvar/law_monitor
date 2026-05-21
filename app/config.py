from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import timedelta, timezone
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import load_dotenv

from app.models import SourceConfig, SourceRole

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DOCS_DIR = BASE_DIR / "docs"


def _get_env_str(name: str, default: str = "", *, aliases: tuple[str, ...] = ()) -> str:
    for candidate in (name, *aliases):
        value = os.getenv(candidate)
        if value is not None and str(value).strip():
            return str(value).strip()
    return str(default).strip()


def _get_env_path(name: str, default: str | Path, *, aliases: tuple[str, ...] = ()) -> Path:
    return Path(_get_env_str(name, str(default), aliases=aliases))


def _get_env_int(
    name: str,
    default: int,
    *,
    aliases: tuple[str, ...] = (),
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    raw_value = _get_env_str(name, str(default), aliases=aliases)
    try:
        value = int(raw_value)
    except ValueError:
        logging.getLogger(__name__).warning(
            "Invalid integer env value for %s=%r. Using default=%s.",
            name,
            raw_value,
            default,
        )
        return default
    if min_value is not None and value < min_value:
        logging.getLogger(__name__).warning(
            "Env value %s=%s is below minimum %s. Using default=%s.",
            name,
            value,
            min_value,
            default,
        )
        return default
    if max_value is not None and value > max_value:
        logging.getLogger(__name__).warning(
            "Env value %s=%s is above maximum %s. Using default=%s.",
            name,
            value,
            max_value,
            default,
        )
        return default
    return value


def _get_env_float(name: str, default: float) -> float:
    raw_value = _get_env_str(name, str(default))
    try:
        return float(raw_value)
    except ValueError:
        logging.getLogger(__name__).warning(
            "Invalid float env value for %s=%r. Using default=%s.",
            name,
            raw_value,
            default,
        )
        return default


def _get_timezone(name: str, default: str = "Europe/Moscow") -> ZoneInfo | timezone:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logging.getLogger(__name__).warning(
            "Invalid timezone env value LAW_MONITOR_TIMEZONE=%r. Using %s.",
            name,
            default,
        )
        try:
            return ZoneInfo(default)
        except ZoneInfoNotFoundError:
            return timezone(timedelta(hours=3), "MSK")


def _get_env_bool(name: str, default: bool) -> bool:
    raw = _get_env_str(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "y", "on"}


DATA_DIR = _get_env_path(
    "LAW_MONITOR_DATA_DIR",
    BASE_DIR / "data",
    aliases=("APP_DATA_DIR",),
)
RUNTIME_DIR = _get_env_path("LAW_MONITOR_RUNTIME_DIR", DATA_DIR / "runtime")
DOCUMENTS_DIR = _get_env_path("LAW_MONITOR_DOCUMENTS_DIR", DATA_DIR / "documents")
TMP_DIR = _get_env_path(
    "LAW_MONITOR_TMP_DIR",
    DATA_DIR,
    aliases=("APP_TMP_DIR",),
)
REPORTS_DIR = _get_env_path(
    "LAW_MONITOR_REPORTS_DIR",
    BASE_DIR / "reports",
    aliases=("REPORTS_DIR",),
)
LOGS_DIR = _get_env_path(
    "LAW_MONITOR_LOG_DIR",
    BASE_DIR / "logs",
    aliases=("LOG_DIR",),
)
DB_PATH = _get_env_path("LAW_MONITOR_DB_PATH", DATA_DIR / "law_monitor.db")
REQUEST_TIMEOUT = _get_env_int("LAW_MONITOR_REQUEST_TIMEOUT", 30, min_value=1)
REQUEST_RETRIES = _get_env_int("LAW_MONITOR_REQUEST_RETRIES", 2, min_value=0)
REQUEST_BACKOFF_FACTOR = _get_env_float("LAW_MONITOR_REQUEST_BACKOFF_FACTOR", 1.0)
USER_AGENT = _get_env_str("LAW_MONITOR_USER_AGENT", "law-monitor-mvp/0.1")
LOG_LEVEL = _get_env_str("LAW_MONITOR_LOG_LEVEL", "INFO").upper()
LOG_FILE_PATH = _get_env_path("LAW_MONITOR_LOG_FILE", LOGS_DIR / "app.log")
LOG_MAX_BYTES = _get_env_int("LAW_MONITOR_LOG_MAX_BYTES", 5 * 1024 * 1024, min_value=1024)
LOG_BACKUP_COUNT = _get_env_int("LAW_MONITOR_LOG_BACKUP_COUNT", 5, min_value=0)
TELEGRAM_BOT_TOKEN = _get_env_str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _get_env_str("TELEGRAM_CHAT_ID")
TELEGRAM_OPERATOR_CHAT_ID = _get_env_str("TELEGRAM_OPERATOR_CHAT_ID")
TELEGRAM_PROXY_URL = _get_env_str("TELEGRAM_PROXY_URL")
TELEGRAM_API_TIMEOUT = _get_env_int("TELEGRAM_API_TIMEOUT", 30, min_value=1)
TELEGRAM_PROXY_ENABLED = bool(TELEGRAM_PROXY_URL)
OCR_ENABLED = _get_env_bool("LAW_MONITOR_OCR_ENABLED", False)
OCR_LANGUAGE = _get_env_str("LAW_MONITOR_OCR_LANGUAGE", "rus+eng")
OCR_MAX_PAGES = max(1, _get_env_int("LAW_MONITOR_OCR_MAX_PAGES", 5, min_value=1))
OCR_TIMEOUT_SECONDS = max(1, _get_env_int("LAW_MONITOR_OCR_TIMEOUT", 120, min_value=1))
OCR_TESSDATA_PATH = _get_env_str("LAW_MONITOR_OCR_TESSDATA_PATH", "")
LLM_DOCUMENT_ENRICHMENT_ENABLED = _get_env_bool("LLM_DOCUMENT_ENRICHMENT_ENABLED", False)
LLM_ENRICHMENT_ENABLED = _get_env_bool(
    "LLM_ENRICHMENT_ENABLED",
    LLM_DOCUMENT_ENRICHMENT_ENABLED,
)
LLM_PROVIDER = _get_env_str("LLM_PROVIDER", "mock")
LLM_BASE_URL = _get_env_str("LLM_BASE_URL", "")
LLM_API_KEY = _get_env_str("LLM_API_KEY", "")
LLM_MODEL = _get_env_str("LLM_MODEL", "")
LLM_TIMEOUT_SECONDS = _get_env_int("LLM_TIMEOUT_SECONDS", 60, min_value=1)
LLM_MAX_DOCUMENT_CHARS = _get_env_int(
    "LLM_MAX_DOCUMENT_CHARS",
    12000,
    min_value=1000,
)
LLM_ENRICHMENT_LIMIT = _get_env_int(
    "LLM_ENRICHMENT_LIMIT",
    20,
    min_value=1,
    max_value=200,
)
SCHEDULER_TIMEZONE_NAME = _get_env_str("LAW_MONITOR_TIMEZONE", "Europe/Moscow")
SCHEDULER_TIMEZONE = _get_timezone(SCHEDULER_TIMEZONE_NAME)
SCHEDULER_DAILY_REPORT_HOUR = _get_env_int(
    "LAW_MONITOR_DAILY_REPORT_HOUR",
    9,
    aliases=("SCHEDULER_DAILY_REPORT_HOUR",),
    min_value=0,
    max_value=23,
)
SCHEDULER_HOURLY_INTERVAL_MINUTES = _get_env_int(
    "LAW_MONITOR_HOURLY_INTERVAL_MINUTES",
    360,
    aliases=("SCHEDULER_INTERVAL_MINUTES",),
    min_value=1,
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
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
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
def load_keyword_groups(path: Path | None = None) -> dict[str, list[str]]:
    config_path = path or CONFIG_DIR / "keywords.yaml"
    data = _load_yaml(config_path)
    if isinstance(data, dict):
        grouped_keywords = data
    else:
        grouped_keywords = {"keywords": data}

    normalized: dict[str, list[str]] = {}
    for group_name, values in grouped_keywords.items():
        if isinstance(values, Mapping):
            continue
        if isinstance(values, str):
            candidates = [values]
        else:
            candidates = list(values or [])
        normalized[str(group_name).strip()] = [
            str(keyword).strip()
            for keyword in candidates
            if str(keyword).strip()
        ]
    return normalized


@lru_cache(maxsize=1)
def load_keywords(path: Path | None = None) -> list[str]:
    grouped_keywords = load_keyword_groups(path)
    flattened: list[str] = []
    seen: set[str] = set()
    for values in grouped_keywords.values():
        for keyword in values:
            normalized = keyword.strip()
            if normalized and normalized not in seen:
                flattened.append(normalized)
                seen.add(normalized)
    return flattened


@lru_cache(maxsize=1)
def load_gr_topic_families(path: Path | None = None) -> dict[str, dict[str, object]]:
    config_path = path or CONFIG_DIR / "keywords.yaml"
    data = _load_yaml(config_path)
    if not isinstance(data, Mapping):
        return {}
    raw_families = data.get("topic_families", {})
    if not isinstance(raw_families, Mapping):
        return {}
    families: dict[str, dict[str, object]] = {}
    for family_name, raw_config in raw_families.items():
        if not isinstance(raw_config, Mapping):
            continue
        normalized_name = str(family_name).strip()
        if not normalized_name:
            continue
        families[normalized_name] = dict(raw_config)
    return families


@lru_cache(maxsize=1)
def load_source_map(config_path: Path | None = None) -> dict[str, SourceConfig]:
    return {source.name: source for source in load_sources(config_path)}


def get_source_config(source_name: str | None) -> SourceConfig | None:
    if not source_name:
        return None
    return load_source_map().get(source_name)


def get_source_role(source_name: str | None) -> SourceRole | None:
    source_config = get_source_config(source_name)
    if source_config is None:
        return None
    return source_config.source_role
