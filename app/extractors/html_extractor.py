from __future__ import annotations

import logging
import warnings
from collections.abc import Mapping

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from app.config import DEFAULT_REQUEST_HEADERS, REQUEST_TIMEOUT
from app.extractors.date_extractor import extract_published_at_from_html
from app.extractors.site_extractors import (
    extract_government_content,
    extract_zol_content,
)
from app.models import ExtractionResult

logger = logging.getLogger(__name__)

MAX_HTML_BYTES = 2 * 1024 * 1024


def _is_html_like(content_type: str) -> bool:
    normalized = (content_type or "").lower()
    return any(marker in normalized for marker in ("text/html", "application/xhtml+xml"))


def _is_xml_like(content_type: str) -> bool:
    normalized = (content_type or "").lower()
    return "xml" in normalized


def _read_limited_text(response: requests.Response, max_bytes: int = MAX_HTML_BYTES) -> str:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=8192, decode_unicode=False):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(
                f"HTML content exceeded safety limit of {max_bytes} bytes"
            )
        chunks.append(chunk)
    encoding = response.encoding or response.apparent_encoding or "utf-8"
    return b"".join(chunks).decode(encoding, errors="ignore")


def extract_text_from_html(
    url: str,
    *,
    source_name: str | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: int | None = None,
    verify_ssl: bool = True,
) -> ExtractionResult:
    response = requests.get(
        url,
        headers=dict(headers or DEFAULT_REQUEST_HEADERS),
        timeout=timeout or REQUEST_TIMEOUT,
        stream=True,
        verify=verify_ssl,
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")

    if not (_is_html_like(content_type) or _is_xml_like(content_type) or not content_type):
        return ExtractionResult(
            raw_text="",
            document_type="unknown",
            error=f"Unsupported content type for HTML extractor: {content_type}",
        )

    text_body = _read_limited_text(response)
    lowered_url = url.lower()
    extracted_published_at = extract_published_at_from_html(
        text_body,
        source_name or "",
        response.url,
    )
    published_at_value = None
    if extracted_published_at is not None:
        from datetime import datetime, time, timezone

        published_at_value = datetime.combine(
            extracted_published_at,
            time.min,
            tzinfo=timezone.utc,
        )

    if "government.ru" in lowered_url:
        extracted = extract_government_content(text_body, response.url)
        return ExtractionResult(
            raw_text=extracted.text,
            document_type="xml" if _is_xml_like(content_type) else "html",
            published_at=published_at_value,
            extracted_text_length=len((extracted.text or "").strip()),
            error=None
            if extracted.content_quality == "good"
            else f"content_quality={extracted.content_quality}",
        )
    if "zol.ru" in lowered_url:
        extracted = extract_zol_content(text_body, response.url)
        return ExtractionResult(
            raw_text=extracted.text,
            document_type="xml" if _is_xml_like(content_type) else "html",
            published_at=published_at_value,
            extracted_text_length=len((extracted.text or "").strip()),
            error=None
            if extracted.content_quality == "good"
            else f"content_quality={extracted.content_quality}",
        )

    parser = "xml" if _is_xml_like(content_type) else "html.parser"

    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    soup = BeautifulSoup(text_body, parser)
    for tag_name in ("script", "style", "nav", "footer", "header", "noscript", "svg"):
        for tag in soup.find_all(tag_name):
            tag.decompose()

    text = " ".join(chunk.strip() for chunk in soup.stripped_strings if chunk.strip())
    logger.debug(
        "Extracted %s characters from markup %s with content-type=%s",
        len(text),
        url,
        content_type,
    )
    return ExtractionResult(
        raw_text=text,
        document_type="xml" if _is_xml_like(content_type) else "html",
        published_at=published_at_value,
        extracted_text_length=len(text.strip()),
    )
