from __future__ import annotations

import logging
import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from urllib3.exceptions import InsecureRequestWarning

from app.config import DEFAULT_REQUEST_HEADERS, REQUEST_TIMEOUT
from app.extractors.docx_extractor import extract_text_from_docx
from app.extractors.pdf_extractor import extract_text_from_pdf
from app.models import CollectedItem, ExtractionResult

logger = logging.getLogger(__name__)

DONLAND_MAX_DETAIL_PAGES = 3
DONLAND_MAX_ATTACHMENT_CANDIDATES = 5
DONLAND_MAX_HTML_BYTES = 2 * 1024 * 1024
DONLAND_MAX_TEXT_ATTACHMENT_BYTES = 2 * 1024 * 1024
DONLAND_DETAIL_PATH_RE = re.compile(r"^/doc/view/[^?#]+(?:/page/\d+/)?$", re.IGNORECASE)
DONLAND_PAGE_SEGMENT_RE = re.compile(r"/page/\d+/?$", re.IGNORECASE)
DONLAND_TEXT_EXTENSIONS = {
    ".txt": "txt",
    ".doc": "doc",
    ".docx": "docx",
    ".pdf": "pdf",
    ".sig": "sig",
}
DONLAND_FOOTER_MARKERS = (
    "свидетельство о регистрации сми",
    "правила использования материалов",
    "контактная информация сайта",
    "этот сайт использует cookie",
)
DONLAND_NAVIGATION_PATTERNS = (
    re.compile(r"в начало\s+предыдущая.*?в конец", re.IGNORECASE),
    re.compile(r"страница\s+\d+\s+из\s+\d+", re.IGNORECASE),
    re.compile(r"выбор страниц документа", re.IGNORECASE),
    re.compile(r"количество страниц на экране\s*\(для печати\)", re.IGNORECASE),
)
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class _AttachmentCandidate:
    url: str
    document_type: str


def maybe_extract_donland_detail(
    item: CollectedItem,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: int | None = None,
    verify_ssl: bool = True,
) -> ExtractionResult | None:
    if item.document_type not in {"html", "xml"}:
        return None
    if not _is_donland_detail_url(item.url):
        return None

    page_texts: list[str] = []
    attachment_candidates: list[_AttachmentCandidate] = []
    seen_attachment_urls: set[str] = set()
    seen_page_signatures: set[str] = set()
    resolved_timeout = timeout or REQUEST_TIMEOUT
    resolved_headers = dict(headers or DEFAULT_REQUEST_HEADERS)

    for page_number in range(1, DONLAND_MAX_DETAIL_PAGES + 1):
        page_url = _detail_page_url(item.url, page_number)
        try:
            response = _fetch_response(
                page_url,
                headers=resolved_headers,
                timeout=resolved_timeout,
                verify_ssl=verify_ssl,
            )
        except Exception:
            if page_number == 1:
                raise
            break

        page_html = _decode_limited_content(response.content, response.encoding, DONLAND_MAX_HTML_BYTES)
        page_text = _extract_clean_page_text(page_html, item.title)
        page_signature = _page_signature(page_text)
        if page_signature and page_signature in seen_page_signatures:
            break
        if page_signature:
            seen_page_signatures.add(page_signature)
        if page_text:
            page_texts.append(page_text)

        page_candidates = _extract_attachment_candidates(
            page_html,
            response.url,
            seen_urls=seen_attachment_urls,
            remaining=max(0, DONLAND_MAX_ATTACHMENT_CANDIDATES - len(attachment_candidates)),
        )
        attachment_candidates.extend(page_candidates)
        if len(attachment_candidates) >= DONLAND_MAX_ATTACHMENT_CANDIDATES:
            break

    merged_html_text = _merge_page_texts(page_texts)
    html_result = ExtractionResult(
        raw_text=merged_html_text,
        document_type=item.document_type,
        extracted_text_length=len(merged_html_text.strip()),
    )

    for preferred_type in ("txt", "docx", "pdf"):
        for candidate in attachment_candidates:
            if candidate.document_type != preferred_type:
                continue
            try:
                extracted = _extract_attachment_text(
                    candidate,
                    headers=resolved_headers,
                    timeout=resolved_timeout,
                    verify_ssl=verify_ssl,
                )
            except Exception as exc:
                logger.warning("Donland attachment fetch failed for %s: %s", candidate.url, exc)
                continue
            if not (extracted.raw_text or "").strip():
                continue
            return html_result.model_copy(
                update={
                    "raw_text": extracted.raw_text,
                    "local_file_path": extracted.local_file_path,
                    "document_type": item.document_type,
                    "needs_ocr": extracted.needs_ocr,
                    "page_count": extracted.page_count,
                    "extracted_text_length": extracted.extracted_text_length,
                    "ocr_status": extracted.ocr_status,
                    "ocr_text_length": extracted.ocr_text_length,
                    "ocr_error": extracted.ocr_error,
                    "ocr_pages_processed": extracted.ocr_pages_processed,
                    "error": extracted.error,
                }
            )

    return html_result


def _is_donland_detail_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.lower() == "pravo.donland.ru" and DONLAND_DETAIL_PATH_RE.match(parsed.path) is not None


def _detail_page_url(url: str, page_number: int) -> str:
    if page_number <= 1:
        return DONLAND_PAGE_SEGMENT_RE.sub("/", url.rstrip("/") + "/")
    base_url = DONLAND_PAGE_SEGMENT_RE.sub("/", url.rstrip("/") + "/")
    return f"{base_url}page/{page_number}/"


def _fetch_response(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: int,
    verify_ssl: bool,
) -> requests.Response:
    request_kwargs: dict[str, object] = {
        "headers": dict(headers),
        "timeout": timeout,
        "verify": verify_ssl,
    }
    if verify_ssl:
        response = requests.get(url, **request_kwargs)
    else:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)
            response = requests.get(url, **request_kwargs)
    response.raise_for_status()
    return response


def _decode_limited_content(content: bytes, encoding: str | None, max_bytes: int) -> str:
    if len(content) > max_bytes:
        raise ValueError(f"Donland content exceeded safety limit of {max_bytes} bytes")
    resolved_encoding = encoding or "utf-8"
    return content.decode(resolved_encoding, errors="ignore")


def _extract_clean_page_text(html: str, title: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag_name in ("script", "style", "nav", "footer", "header", "noscript", "svg"):
        for tag in soup.find_all(tag_name):
            tag.decompose()
    text = " ".join(chunk.strip() for chunk in soup.stripped_strings if chunk.strip())
    return _clean_donland_text(text, title)


def _clean_donland_text(text: str, title: str) -> str:
    cleaned = WHITESPACE_RE.sub(" ", (text or "").replace("\xa0", " ")).strip()
    if not cleaned:
        return ""

    title_text = WHITESPACE_RE.sub(" ", (title or "").strip())
    title_lower = title_text.lower()
    cleaned_lower = cleaned.lower()
    if title_text and title_lower in cleaned_lower:
        positions = _find_occurrences(cleaned_lower, title_lower)
        near_start = [position for position in positions if position < 1600]
        start_index = near_start[-1] if near_start else positions[0]
        cleaned = cleaned[start_index:]
        cleaned_lower = cleaned.lower()

    footer_indexes = [cleaned_lower.find(marker) for marker in DONLAND_FOOTER_MARKERS if marker in cleaned_lower]
    footer_indexes = [index for index in footer_indexes if index >= 0]
    if footer_indexes:
        cleaned = cleaned[: min(footer_indexes)].strip()

    for pattern in DONLAND_NAVIGATION_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = WHITESPACE_RE.sub(" ", cleaned).strip()

    if title_text and not cleaned.lower().startswith(title_lower):
        cleaned = f"{title_text} {cleaned}".strip()
    return cleaned


def _find_occurrences(text: str, needle: str) -> list[int]:
    if not needle:
        return []
    positions: list[int] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index == -1:
            break
        positions.append(index)
        start = index + len(needle)
    return positions


def _page_signature(text: str) -> str:
    normalized = WHITESPACE_RE.sub(" ", (text or "").strip().lower())
    return normalized[:1200]


def _merge_page_texts(page_texts: list[str]) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for page_text in page_texts:
        normalized = _page_signature(page_text)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(page_text.strip())
    return "\n\n".join(part for part in merged if part)


def _extract_attachment_candidates(
    html: str,
    base_url: str,
    *,
    seen_urls: set[str],
    remaining: int,
) -> list[_AttachmentCandidate]:
    if remaining <= 0:
        return []

    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(base_url).netloc.lower()
    candidates: list[_AttachmentCandidate] = []

    for link in soup.find_all("a", href=True):
        if not isinstance(link, Tag):
            continue
        raw_href = str(link.get("href", "")).strip()
        if not raw_href:
            continue
        normalized_url = urljoin(base_url, raw_href)
        parsed = urlparse(normalized_url)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc.lower() != base_host:
            continue

        suffix = PurePosixPath(parsed.path).suffix.lower()
        document_type = DONLAND_TEXT_EXTENSIONS.get(suffix)
        if document_type is None or document_type == "sig":
            continue
        if normalized_url in seen_urls:
            continue

        candidates.append(_AttachmentCandidate(url=normalized_url, document_type=document_type))
        seen_urls.add(normalized_url)
        if len(candidates) >= remaining:
            break

    return candidates


def _extract_attachment_text(
    candidate: _AttachmentCandidate,
    *,
    headers: Mapping[str, str],
    timeout: int,
    verify_ssl: bool,
) -> ExtractionResult:
    if candidate.document_type == "txt":
        return _extract_text_attachment(
            candidate.url,
            headers=headers,
            timeout=timeout,
            verify_ssl=verify_ssl,
        )
    if candidate.document_type == "docx":
        return extract_text_from_docx(
            candidate.url,
            headers=headers,
            timeout=timeout,
            verify_ssl=verify_ssl,
        )
    if candidate.document_type == "pdf":
        return extract_text_from_pdf(
            candidate.url,
            headers=headers,
            timeout=timeout,
            verify_ssl=verify_ssl,
        )
    if candidate.document_type == "doc":
        return ExtractionResult(
            raw_text="",
            document_type="doc",
            error="DOC extraction is not implemented",
        )
    return ExtractionResult(raw_text="", document_type=candidate.document_type)


def _extract_text_attachment(
    url: str,
    *,
    headers: Mapping[str, str],
    timeout: int,
    verify_ssl: bool,
) -> ExtractionResult:
    response = _fetch_response(
        url,
        headers=headers,
        timeout=timeout,
        verify_ssl=verify_ssl,
    )
    text = _decode_limited_content(response.content, response.encoding, DONLAND_MAX_TEXT_ATTACHMENT_BYTES)
    text = WHITESPACE_RE.sub(" ", text.replace("\xa0", " ")).strip()
    return ExtractionResult(
        raw_text=text,
        document_type="txt",
        extracted_text_length=len(text),
    )
