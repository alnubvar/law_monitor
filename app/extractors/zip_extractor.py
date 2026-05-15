from __future__ import annotations

import io
import logging
import warnings
import zipfile
from collections.abc import Mapping

import fitz
import requests
from docx import Document as DocxDocument
from urllib3.exceptions import InsecureRequestWarning

from app.config import DEFAULT_REQUEST_HEADERS, REQUEST_TIMEOUT
from app.extractors.xlsx_extractor import extract_from_xlsx_bytes
from app.models import ExtractionResult

logger = logging.getLogger(__name__)

MAX_ZIP_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_ZIP_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_ZIP_ENTRY_BYTES = 15 * 1024 * 1024
MAX_ZIP_TEXT_CHARS = 300_000

_EXTRACTABLE_EXTENSIONS = frozenset({".pdf", ".docx", ".xlsx", ".xls"})


def _extract_pdf_bytes(data: bytes, name: str) -> str:
    try:
        doc = fitz.open(stream=io.BytesIO(data), filetype="pdf")
        return " ".join(page.get_text() for page in doc).strip()
    except Exception as exc:
        logger.debug("PDF extraction from ZIP entry %s failed: %s", name, exc)
        return ""


def _extract_docx_bytes(data: bytes, name: str) -> str:
    try:
        doc = DocxDocument(io.BytesIO(data))
        return "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())
    except Exception as exc:
        logger.debug("DOCX extraction from ZIP entry %s failed: %s", name, exc)
        return ""


def _extract_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    if info.file_size > MAX_ZIP_ENTRY_BYTES:
        logger.debug("ZIP entry %s too large (%s bytes), skipping", info.filename, info.file_size)
        return ""
    try:
        data = zf.read(info.filename)
    except Exception as exc:
        logger.debug("ZIP entry %s read error: %s", info.filename, exc)
        return ""

    name_lower = info.filename.lower()
    if name_lower.endswith(".pdf"):
        return _extract_pdf_bytes(data, info.filename)
    if name_lower.endswith(".docx"):
        return _extract_docx_bytes(data, info.filename)
    if name_lower.endswith(".xlsx"):
        result = extract_from_xlsx_bytes(data, source_hint=info.filename)
        return result.raw_text or ""
    if name_lower.endswith(".xls"):
        # Legacy binary XLS: no parser available without xlrd; return metadata note.
        return f"[XLS] {info.filename} — содержимое не извлечено (бинарный формат XLS не поддерживается без xlrd)"
    return ""


def extract_text_from_zip(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: int | None = None,
    verify_ssl: bool = True,
) -> ExtractionResult:
    req_kwargs: dict[str, object] = dict(
        headers=dict(headers or DEFAULT_REQUEST_HEADERS),
        timeout=timeout or REQUEST_TIMEOUT,
    )
    try:
        if not verify_ssl:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", InsecureRequestWarning)
                response = requests.get(url, verify=False, **req_kwargs)
        else:
            response = requests.get(url, verify=True, **req_kwargs)
        response.raise_for_status()
    except Exception as exc:
        return ExtractionResult(raw_text="", document_type="zip", error=f"ZIP fetch error: {exc}")

    data = response.content
    if len(data) > MAX_ZIP_DOWNLOAD_BYTES:
        return ExtractionResult(
            raw_text="",
            document_type="zip",
            error=f"ZIP download too large ({len(data)} bytes > {MAX_ZIP_DOWNLOAD_BYTES})",
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        return ExtractionResult(raw_text="", document_type="zip", error=f"ZIP bad format: {exc}")

    total_uncompressed = sum(info.file_size for info in zf.infolist())
    if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
        return ExtractionResult(
            raw_text="",
            document_type="zip",
            error=f"ZIP uncompressed too large ({total_uncompressed} bytes > {MAX_ZIP_UNCOMPRESSED_BYTES})",
        )

    texts: list[str] = []
    for info in zf.infolist():
        name_lower = info.filename.lower()
        # No nested ZIPs — prevents recursive extraction and zip bombs.
        if name_lower.endswith(".zip"):
            continue
        # Skip macOS/Windows metadata artifacts.
        parts = info.filename.split("/")
        if any(p.startswith(".") or p.startswith("__") for p in parts):
            continue
        ext = "." + name_lower.rsplit(".", 1)[-1] if "." in name_lower else ""
        if ext not in _EXTRACTABLE_EXTENSIONS:
            continue
        entry_text = _extract_entry(zf, info)
        if entry_text:
            texts.append(f"[{info.filename}]\n{entry_text}")

    combined = "\n\n".join(texts)
    if len(combined) > MAX_ZIP_TEXT_CHARS:
        combined = combined[:MAX_ZIP_TEXT_CHARS]

    if not combined:
        all_names = [i.filename for i in zf.infolist()]
        return ExtractionResult(
            raw_text="",
            document_type="zip",
            error=f"ZIP contains no extractable text (entries: {len(all_names)})",
        )

    logger.debug("Extracted %s chars from ZIP %s (%s sections)", len(combined), url, len(texts))
    return ExtractionResult(
        raw_text=combined,
        document_type="zip",
        extracted_text_length=len(combined.strip()),
    )
