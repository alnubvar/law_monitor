from __future__ import annotations

import logging
import hashlib
from pathlib import Path
from urllib.parse import urlparse

import requests
from docx import Document
from collections.abc import Mapping

from app.config import (
    DEFAULT_REQUEST_HEADERS,
    DOCUMENTS_DIR,
    REQUEST_TIMEOUT,
    ensure_directories,
)
from app.models import ExtractionResult

logger = logging.getLogger(__name__)

MAX_DOCX_DOWNLOAD_BYTES = 50 * 1024 * 1024


def _read_limited_bytes(
    response: requests.Response,
    *,
    max_bytes: int = MAX_DOCX_DOWNLOAD_BYTES,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(
                f"DOCX download exceeded safety limit of {max_bytes} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _safe_filename(url: str) -> Path:
    parsed = urlparse(url)
    base_name = Path(parsed.path).name or "document.docx"
    if not base_name.lower().endswith(".docx"):
        base_name = f"{base_name}.docx"
    stem = Path(base_name).stem
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return DOCUMENTS_DIR / f"{stem}_{digest}.docx"


def extract_text_from_docx(
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
        stream=True,
    )
    try:
        response.raise_for_status()
        content = _read_limited_bytes(response)
    finally:
        response.close()

    file_path = _safe_filename(url)
    file_path.write_bytes(content)

    document = Document(str(file_path))
    text = "\n".join(
        paragraph.text.strip()
        for paragraph in document.paragraphs
        if paragraph.text.strip()
    )
    logger.debug("Extracted %s characters from DOCX %s", len(text), url)
    return ExtractionResult(
        raw_text=text,
        local_file_path=str(file_path),
        document_type="docx",
        extracted_text_length=len(text.strip()),
    )
