from __future__ import annotations

import logging
import hashlib
from pathlib import Path
from urllib.parse import urlparse

import fitz
import requests
from collections.abc import Mapping

from app.config import (
    DEFAULT_REQUEST_HEADERS,
    DOCUMENTS_DIR,
    REQUEST_TIMEOUT,
    ensure_directories,
)
from app.models import ExtractionResult

logger = logging.getLogger(__name__)


def _safe_filename(url: str, suffix: str = ".pdf") -> Path:
    parsed = urlparse(url)
    base_name = Path(parsed.path).name or "document.pdf"
    if not base_name.lower().endswith(suffix):
        base_name = f"{base_name}{suffix}"
    stem = Path(base_name).stem
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return DOCUMENTS_DIR / f"{stem}_{digest}{suffix}"


def extract_text_from_pdf(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: int | None = None,
    verify_ssl: bool = True,
) -> ExtractionResult:
    ensure_directories()
    response = requests.get(
        url,
        headers=dict(headers or DEFAULT_REQUEST_HEADERS),
        timeout=timeout or REQUEST_TIMEOUT,
        verify=verify_ssl,
    )
    response.raise_for_status()

    file_path = _safe_filename(url)
    file_path.write_bytes(response.content)

    text_parts: list[str] = []
    with fitz.open(stream=response.content, filetype="pdf") as pdf:
        page_count = len(pdf)
        for page in pdf:
            text_parts.append(page.get_text("text"))
    text = "\n".join(part.strip() for part in text_parts if part.strip())
    needs_ocr = len(text.strip()) < 500

    logger.debug(
        "Extracted %s characters from PDF %s (needs_ocr=%s)",
        len(text),
        url,
        needs_ocr,
    )
    return ExtractionResult(
        raw_text=text,
        local_file_path=str(file_path),
        document_type="pdf",
        needs_ocr=needs_ocr,
        page_count=page_count,
        extracted_text_length=len(text.strip()),
    )
