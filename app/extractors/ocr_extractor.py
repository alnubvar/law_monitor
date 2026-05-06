from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import fitz

from app.config import (
    OCR_ENABLED,
    OCR_LANGUAGE,
    OCR_MAX_PAGES,
    OCR_TESSDATA_PATH,
    OCR_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

OCR_STATUS_DISABLED = "disabled"
OCR_STATUS_NOT_NEEDED = "not_needed"
OCR_STATUS_SUCCESS = "success"
OCR_STATUS_FAILED = "failed"
OCR_STATUS_UNAVAILABLE = "unavailable"


def _resolve_tessdata_path(tessdata_path: str | None = None) -> str | None:
    raw_value = (tessdata_path if tessdata_path is not None else OCR_TESSDATA_PATH).strip()
    if not raw_value:
        return None
    return str(Path(raw_value).expanduser())


def _split_ocr_languages(language: str) -> list[str]:
    return [chunk.strip() for chunk in (language or "").split("+") if chunk.strip()]


def _list_languages_from_tessdata(tessdata_path: str | None) -> list[str]:
    if not tessdata_path:
        return []
    path = Path(tessdata_path)
    if not path.exists() or not path.is_dir():
        return []
    discovered = sorted({entry.stem for entry in path.glob("*.traineddata") if entry.is_file()})
    return discovered


def _list_languages_from_tesseract_binary() -> list[str]:
    tesseract_executable = shutil.which("tesseract")
    if not tesseract_executable:
        return []
    try:
        completed = subprocess.run(
            [tesseract_executable, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return []
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    langs: list[str] = []
    for line in output.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered.startswith("list of available languages"):
            continue
        if "error" in lowered:
            continue
        langs.append(normalized)
    return sorted(set(langs))


def get_available_ocr_languages(*, tessdata_path: str | None = None) -> list[str]:
    resolved = _resolve_tessdata_path(tessdata_path)
    from_tessdata = _list_languages_from_tessdata(resolved)
    if from_tessdata:
        return from_tessdata
    return _list_languages_from_tesseract_binary()


def _probe_ocr_availability(language: str, *, tessdata_path: str | None = None) -> tuple[bool, str | None]:
    resolved_tessdata = _resolve_tessdata_path(tessdata_path)
    if resolved_tessdata is not None:
        tessdata_dir = Path(resolved_tessdata)
        if not tessdata_dir.exists():
            return False, f"Configured tessdata path does not exist: {resolved_tessdata}"
        if not tessdata_dir.is_dir():
            return False, f"Configured tessdata path is not a directory: {resolved_tessdata}"
        required_languages = _split_ocr_languages(language)
        available_languages = set(_list_languages_from_tessdata(resolved_tessdata))
        missing = [lang for lang in required_languages if lang not in available_languages]
        if missing:
            return (
                False,
                f"Missing traineddata in tessdata path: {', '.join(missing)} ({resolved_tessdata})",
            )
    test_doc = fitz.open()
    try:
        page = test_doc.new_page(width=16, height=16)
        kwargs: dict[str, Any] = {
            "language": language,
            "dpi": 72,
            "full": True,
        }
        if resolved_tessdata is not None:
            kwargs["tessdata"] = resolved_tessdata
        textpage = page.get_textpage_ocr(**kwargs)
        _ = page.get_text("text", textpage=textpage)
        return True, None
    except Exception as exc:
        return False, str(exc)
    finally:
        test_doc.close()


def get_ocr_runtime_status() -> dict[str, Any]:
    resolved_tessdata = _resolve_tessdata_path()
    available_languages = get_available_ocr_languages(tessdata_path=resolved_tessdata)
    if not OCR_ENABLED:
        return {
            "enabled": False,
            "available": False,
            "reason": "OCR disabled by LAW_MONITOR_OCR_ENABLED",
            "language": OCR_LANGUAGE,
            "max_pages": OCR_MAX_PAGES,
            "timeout_seconds": OCR_TIMEOUT_SECONDS,
            "tessdata_path": resolved_tessdata,
            "available_languages": available_languages,
        }
    available, reason = _probe_ocr_availability(
        OCR_LANGUAGE,
        tessdata_path=resolved_tessdata,
    )
    return {
        "enabled": True,
        "available": available,
        "reason": reason,
        "language": OCR_LANGUAGE,
        "max_pages": OCR_MAX_PAGES,
        "timeout_seconds": OCR_TIMEOUT_SECONDS,
        "tessdata_path": resolved_tessdata,
        "available_languages": available_languages,
    }


def extract_text_with_ocr(
    file_path: str | Path,
    *,
    language: str | None = None,
    max_pages: int | None = None,
    timeout_seconds: int | None = None,
    tessdata_path: str | None = None,
) -> dict[str, Any]:
    resolved_path = Path(file_path)
    effective_language = (language or OCR_LANGUAGE).strip() or OCR_LANGUAGE
    effective_max_pages = max(1, int(max_pages or OCR_MAX_PAGES))
    effective_timeout = max(1, int(timeout_seconds or OCR_TIMEOUT_SECONDS))
    resolved_tessdata = _resolve_tessdata_path(tessdata_path)

    if not OCR_ENABLED:
        return {
            "status": OCR_STATUS_DISABLED,
            "text": "",
            "text_length": 0,
            "pages_processed": 0,
            "error": "OCR disabled by LAW_MONITOR_OCR_ENABLED",
            "tessdata_path": resolved_tessdata,
        }
    if not resolved_path.exists():
        return {
            "status": OCR_STATUS_FAILED,
            "text": "",
            "text_length": 0,
            "pages_processed": 0,
            "error": f"file not found: {resolved_path}",
            "tessdata_path": resolved_tessdata,
        }
    available, availability_error = _probe_ocr_availability(
        effective_language,
        tessdata_path=resolved_tessdata,
    )
    if not available:
        return {
            "status": OCR_STATUS_UNAVAILABLE,
            "text": "",
            "text_length": 0,
            "pages_processed": 0,
            "error": availability_error or "OCR runtime unavailable",
            "tessdata_path": resolved_tessdata,
        }

    start_time = time.monotonic()
    text_parts: list[str] = []
    pages_processed = 0
    try:
        with fitz.open(str(resolved_path)) as pdf:
            pages_limit = min(len(pdf), effective_max_pages)
            for page_index in range(pages_limit):
                elapsed = time.monotonic() - start_time
                if elapsed > effective_timeout:
                    return {
                        "status": OCR_STATUS_FAILED,
                        "text": "",
                        "text_length": 0,
                        "pages_processed": pages_processed,
                        "error": f"OCR timeout exceeded ({effective_timeout}s)",
                        "tessdata_path": resolved_tessdata,
                    }
                page = pdf[page_index]
                kwargs: dict[str, Any] = {
                    "language": effective_language,
                    "dpi": 200,
                    "full": True,
                }
                if resolved_tessdata is not None:
                    kwargs["tessdata"] = resolved_tessdata
                textpage = page.get_textpage_ocr(
                    **kwargs
                )
                page_text = (page.get_text("text", textpage=textpage) or "").strip()
                if page_text:
                    text_parts.append(page_text)
                pages_processed += 1
    except Exception as exc:
        message = str(exc)
        if "No OCR support" in message or "TESSDATA_PREFIX" in message:
            return {
                "status": OCR_STATUS_UNAVAILABLE,
                "text": "",
                "text_length": 0,
                "pages_processed": pages_processed,
                "error": message,
                "tessdata_path": resolved_tessdata,
            }
        logger.warning("OCR failed for %s: %s", resolved_path, message)
        return {
            "status": OCR_STATUS_FAILED,
            "text": "",
            "text_length": 0,
            "pages_processed": pages_processed,
            "error": message,
            "tessdata_path": resolved_tessdata,
        }

    ocr_text = "\n".join(part for part in text_parts if part).strip()
    ocr_text_length = len(ocr_text)
    if ocr_text_length == 0:
        return {
            "status": OCR_STATUS_FAILED,
            "text": "",
            "text_length": 0,
            "pages_processed": pages_processed,
            "error": "OCR produced empty text",
            "tessdata_path": resolved_tessdata,
        }
    return {
        "status": OCR_STATUS_SUCCESS,
        "text": ocr_text,
        "text_length": ocr_text_length,
        "pages_processed": pages_processed,
        "error": None,
        "tessdata_path": resolved_tessdata,
    }
