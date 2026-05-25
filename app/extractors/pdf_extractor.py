from __future__ import annotations

import logging
import hashlib
import warnings
from pathlib import Path
from urllib.parse import urlparse

import fitz
import requests
from collections.abc import Mapping
from urllib3.exceptions import InsecureRequestWarning

from app.config import (
    DEFAULT_REQUEST_HEADERS,
    DOCUMENTS_DIR,
    REQUEST_TIMEOUT,
    ensure_directories,
)
from app.extractors.ocr_extractor import (
    OCR_STATUS_DISABLED,
    OCR_STATUS_NOT_NEEDED,
    OCR_STATUS_SUCCESS,
    extract_text_with_ocr,
)
from app.models import ExtractionResult

logger = logging.getLogger(__name__)

MAX_PDF_DOWNLOAD_BYTES = 50 * 1024 * 1024
PDF_EMPTY_RESPONSE_ERROR = "PDF download returned an empty response body"
PDF_INVALID_HEADER_ERROR = "PDF download did not return a PDF body"


def _read_limited_bytes(
    response: requests.Response,
    *,
    max_bytes: int = MAX_PDF_DOWNLOAD_BYTES,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(
                f"PDF download exceeded safety limit of {max_bytes} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


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
    request_kwargs: dict[str, object] = dict(
        headers=dict(headers or DEFAULT_REQUEST_HEADERS),
        timeout=timeout or REQUEST_TIMEOUT,
        stream=True,
    )
    if verify_ssl:
        response = requests.get(url, verify=True, **request_kwargs)
    else:
        # Suppress only the known urllib3 SSL warning for explicitly non-verified sources.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)
            response = requests.get(url, verify=False, **request_kwargs)
    try:
        response.raise_for_status()
        content = _read_limited_bytes(response)
    finally:
        response.close()

    if not content:
        logger.warning("PDF extraction skipped for %s: empty response body", url)
        return ExtractionResult(
            raw_text="",
            document_type="pdf",
            error=PDF_EMPTY_RESPONSE_ERROR,
            extracted_text_length=0,
            needs_ocr=False,
        )

    if not content.lstrip().startswith(b"%PDF"):
        logger.warning("PDF extraction skipped for %s: response is not a PDF", url)
        return ExtractionResult(
            raw_text="",
            document_type="pdf",
            error=PDF_INVALID_HEADER_ERROR,
            extracted_text_length=0,
            needs_ocr=False,
        )

    file_path = _safe_filename(url)
    file_path.write_bytes(content)

    text_parts: list[str] = []
    try:
        with fitz.open(stream=content, filetype="pdf") as pdf:
            page_count = len(pdf)
            for page in pdf:
                text_parts.append(page.get_text("text"))
    except Exception as exc:
        logger.warning("PDF extraction failed for %s: %s", url, exc)
        return ExtractionResult(
            raw_text="",
            local_file_path=str(file_path),
            document_type="pdf",
            error=f"PDF extraction failed: {exc}",
            extracted_text_length=0,
            needs_ocr=False,
        )
    text = "\n".join(part.strip() for part in text_parts if part.strip()).strip()
    text_length = len(text)
    needs_ocr = text_length < 500
    low_text_density = page_count >= 3 and text_length < page_count * 120
    should_try_ocr = text_length == 0 or needs_ocr or low_text_density
    ocr_status = OCR_STATUS_NOT_NEEDED
    ocr_text_length = 0
    ocr_error: str | None = None
    ocr_pages_processed = 0

    if should_try_ocr:
        ocr_result = extract_text_with_ocr(file_path)
        ocr_status = str(ocr_result.get("status") or OCR_STATUS_NOT_NEEDED)
        ocr_text_length = int(ocr_result.get("text_length") or 0)
        ocr_pages_processed = int(ocr_result.get("pages_processed") or 0)
        ocr_error = str(ocr_result.get("error")) if ocr_result.get("error") else None
        if ocr_status == OCR_STATUS_SUCCESS and ocr_text_length > 0:
            text = str(ocr_result.get("text") or "").strip()
            text_length = len(text)
            needs_ocr = False
        elif ocr_status in {OCR_STATUS_DISABLED, OCR_STATUS_NOT_NEEDED}:
            # keep default extracted text, no extra error state
            ocr_error = None

    logger.debug(
        "Extracted %s characters from PDF %s (needs_ocr=%s, ocr_status=%s)",
        len(text),
        url,
        needs_ocr,
        ocr_status,
    )
    return ExtractionResult(
        raw_text=text,
        local_file_path=str(file_path),
        document_type="pdf",
        needs_ocr=needs_ocr,
        page_count=page_count,
        extracted_text_length=len(text.strip()),
        ocr_status=ocr_status,
        ocr_text_length=ocr_text_length,
        ocr_error=ocr_error,
        ocr_pages_processed=ocr_pages_processed,
    )
