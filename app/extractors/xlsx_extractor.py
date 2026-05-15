from __future__ import annotations

import io
import logging
import warnings
import zipfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping

import requests
from urllib3.exceptions import InsecureRequestWarning

from app.config import DEFAULT_REQUEST_HEADERS, REQUEST_TIMEOUT
from app.models import ExtractionResult

logger = logging.getLogger(__name__)

MAX_XLSX_DOWNLOAD_BYTES = 10 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_XLSX_TEXT_CHARS = 200_000

_SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_T = f"{{{_SPREADSHEET_NS}}}t"
_SI = f"{{{_SPREADSHEET_NS}}}si"
_ROW = f"{{{_SPREADSHEET_NS}}}row"
_C = f"{{{_SPREADSHEET_NS}}}c"
_V = f"{{{_SPREADSHEET_NS}}}v"
_IS = f"{{{_SPREADSHEET_NS}}}is"


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    result: list[str] = []
    if "xl/sharedStrings.xml" not in zf.namelist():
        return result
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        for si in root.iter(_SI):
            texts = [t.text or "" for t in si.iter(_T)]
            result.append("".join(texts).strip())
    except ET.ParseError as exc:
        logger.debug("sharedStrings.xml parse error: %s", exc)
    return result


def _sheet_rows(zf: zipfile.ZipFile, entry: str, shared: list[str]) -> list[str]:
    rows: list[str] = []
    try:
        root = ET.fromstring(zf.read(entry))
        for row in root.iter(_ROW):
            cells: list[str] = []
            for c in row.iter(_C):
                t_attr = c.get("t", "")
                v_el = c.find(_V)
                if t_attr == "inlineStr":
                    is_el = c.find(_IS)
                    if is_el is not None:
                        val = "".join(t.text or "" for t in is_el.iter(_T)).strip()
                        if val:
                            cells.append(val)
                    continue
                if v_el is None or not v_el.text:
                    continue
                raw = v_el.text.strip()
                if t_attr == "s":
                    idx = int(raw) if raw.isdigit() else -1
                    val = shared[idx] if 0 <= idx < len(shared) else ""
                elif t_attr == "b":
                    val = "TRUE" if raw == "1" else "FALSE"
                else:
                    val = raw
                if val:
                    cells.append(val)
            if cells:
                rows.append(" | ".join(cells))
    except ET.ParseError as exc:
        logger.debug("Sheet %s parse error: %s", entry, exc)
    return rows


def extract_from_xlsx_bytes(data: bytes, *, source_hint: str = "") -> ExtractionResult:
    if len(data) > MAX_XLSX_DOWNLOAD_BYTES:
        return ExtractionResult(
            raw_text="",
            document_type="xlsx",
            error=f"XLSX too large ({len(data)} bytes > {MAX_XLSX_DOWNLOAD_BYTES})",
        )
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        return ExtractionResult(raw_text="", document_type="xlsx", error=f"XLSX bad zip: {exc}")

    total_uncompressed = sum(info.file_size for info in zf.infolist())
    if total_uncompressed > MAX_XLSX_UNCOMPRESSED_BYTES:
        return ExtractionResult(
            raw_text="",
            document_type="xlsx",
            error=f"XLSX uncompressed too large ({total_uncompressed} bytes)",
        )

    shared = _shared_strings(zf)
    all_rows: list[str] = []
    for name in sorted(zf.namelist()):
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
            all_rows.extend(_sheet_rows(zf, name, shared))

    text = "\n".join(all_rows)
    if len(text) > MAX_XLSX_TEXT_CHARS:
        text = text[:MAX_XLSX_TEXT_CHARS]

    logger.debug("Extracted %s chars from XLSX %s", len(text), source_hint or "(bytes)")
    return ExtractionResult(
        raw_text=text,
        document_type="xlsx",
        extracted_text_length=len(text.strip()),
    )


def extract_text_from_xlsx(
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
        return ExtractionResult(raw_text="", document_type="xlsx", error=f"XLSX fetch error: {exc}")

    return extract_from_xlsx_bytes(response.content, source_hint=url)
