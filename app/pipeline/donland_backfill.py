from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlparse

from app.config import DB_PATH, load_sources
from app.models import CollectedItem, ExtractionResult, RawDocument, SourceConfig
from app.pipeline.collect import _is_scan_candidate, extract_document
from app.pipeline.deduplicate import compute_content_hash
from app.storage import (
    list_documents,
    mark_runtime_event,
    save_document_extraction_audit,
    update_document_text_by_url,
)

logger = logging.getLogger(__name__)

DONLAND_BACKFILL_SOURCE_NAMES = (
    "Право Ростовской области",
    "Проекты правовых актов Ростовской области",
)
DONLAND_BACKFILL_EVENT_NAME = "donland_backfill"
DONLAND_LOW_QUALITY_RATIO = 0.5
DONLAND_TITLE_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{3,}")


@dataclass(slots=True)
class DonlandBackfillResult:
    scanned: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: int = 0
    skipped: int = 0


def run_donland_backfill(
    *,
    source_name: str | None = None,
    limit: int | None = None,
    db_path: str | Path = DB_PATH,
) -> DonlandBackfillResult:
    target_names = _resolve_target_source_names(source_name)
    source_configs = _load_target_source_configs(target_names)
    documents = _select_target_documents(
        source_names=target_names,
        limit=limit,
        db_path=db_path,
    )
    result = DonlandBackfillResult()

    for document in documents:
        source_config = source_configs.get(document.source_name)
        if source_config is None or not _is_canonical_donland_doc_view_url(document.url):
            result.skipped += 1
            continue

        item = _document_to_item(document)
        result.scanned += 1

        try:
            extracted = extract_document(item, source_config)
        except Exception as exc:
            result.errors += 1
            logger.warning(
                "Donland backfill extraction failed for source=%s url=%s: %s",
                document.source_name,
                document.url,
                exc,
            )
            _save_extraction_audit(
                document=document,
                item=item,
                extracted=ExtractionResult(
                    raw_text="",
                    document_type=document.document_type,
                    error=str(exc),
                ),
                db_path=db_path,
            )
            continue

        _save_extraction_audit(
            document=document,
            item=item,
            extracted=extracted,
            db_path=db_path,
        )

        skip_reason = _guard_skip_reason(document=document, extracted=extracted)
        if skip_reason is not None:
            result.skipped += 1
            logger.warning(
                "Skipping Donland backfill update for source=%s url=%s: %s",
                document.source_name,
                document.url,
                skip_reason,
            )
            continue

        new_hash = compute_content_hash(
            extracted.raw_text,
            fallback=f"{document.title}\n{document.url}",
        )
        if new_hash == document.content_hash:
            result.unchanged += 1
            continue

        update_document_text_by_url(
            document_url=document.url,
            raw_text=extracted.raw_text,
            content_hash=new_hash,
            local_file_path=extracted.local_file_path,
            document_type=extracted.document_type or document.document_type,
            error=extracted.error,
            db_path=db_path,
        )
        result.updated += 1

    mark_runtime_event(
        DONLAND_BACKFILL_EVENT_NAME,
        details=(
            f"sources={','.join(target_names)}; "
            f"scanned={result.scanned}; updated={result.updated}; "
            f"unchanged={result.unchanged}; errors={result.errors}; skipped={result.skipped}"
        ),
        db_path=db_path,
    )
    return result


def _resolve_target_source_names(source_name: str | None) -> tuple[str, ...]:
    if source_name is None:
        return DONLAND_BACKFILL_SOURCE_NAMES
    normalized = source_name.strip()
    if normalized not in DONLAND_BACKFILL_SOURCE_NAMES:
        raise ValueError(
            "donland-backfill supports only these sources: "
            + ", ".join(DONLAND_BACKFILL_SOURCE_NAMES)
        )
    return (normalized,)


def _load_target_source_configs(source_names: tuple[str, ...]) -> dict[str, SourceConfig]:
    configured = {
        source.name: source
        for source in load_sources()
        if source.name in source_names and source.parser == "donland"
    }
    missing = [name for name in source_names if name not in configured]
    if missing:
        raise ValueError(
            "Donland source config not found or parser is not donland for: "
            + ", ".join(missing)
        )
    return configured


def _select_target_documents(
    *,
    source_names: tuple[str, ...],
    limit: int | None,
    db_path: str | Path,
) -> list[RawDocument]:
    documents = [
        document
        for document in list_documents(db_path=db_path)
        if document.source_name in source_names and _is_canonical_donland_doc_view_url(document.url)
    ]
    if limit is not None:
        return documents[: max(0, int(limit))]
    return documents


def _is_canonical_donland_doc_view_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    if parsed.netloc.lower() != "pravo.donland.ru":
        return False
    normalized_path = parsed.path.rstrip("/").lower()
    if not normalized_path.startswith("/doc/view/"):
        return False
    return "/page/" not in normalized_path


def _document_to_item(document: RawDocument) -> CollectedItem:
    return CollectedItem(
        source_name=document.source_name,
        source_url=document.source_url,
        level=document.level,
        region=document.region,
        title=document.title,
        url=document.url,
        published_at=document.published_at,
        document_type=document.document_type,
    )


def _save_extraction_audit(
    *,
    document: RawDocument,
    item: CollectedItem,
    extracted: ExtractionResult,
    db_path: str | Path,
) -> None:
    raw_text_length = extracted.extracted_text_length
    if raw_text_length is None:
        raw_text_length = len((extracted.raw_text or "").strip())
    save_document_extraction_audit(
        source_name=document.source_name,
        source_url=document.source_url,
        document_url=document.url,
        attachment_url=None,
        file_type=item.document_type,
        extracted_type=extracted.document_type or item.document_type,
        raw_text_length=raw_text_length,
        has_text=raw_text_length > 0,
        scan_candidate=_is_scan_candidate(file_type=item.document_type, extracted=extracted),
        needs_ocr=bool(extracted.needs_ocr),
        ocr_status=extracted.ocr_status,
        ocr_text_length=extracted.ocr_text_length,
        ocr_error=extracted.ocr_error,
        ocr_pages_processed=extracted.ocr_pages_processed,
        page_count=extracted.page_count,
        extraction_error=extracted.error,
        db_path=db_path,
    )


def _guard_skip_reason(
    *,
    document: RawDocument,
    extracted: ExtractionResult,
) -> str | None:
    new_text = (extracted.raw_text or "").strip()
    if not new_text:
        return "skipped_empty_text"

    existing_text = (document.raw_text or "").strip()
    if not existing_text:
        return None

    if (
        len(new_text) < int(len(existing_text) * DONLAND_LOW_QUALITY_RATIO)
        and not _contains_title_signal(document.title, new_text)
    ):
        return "skipped_low_quality"
    return None


def _contains_title_signal(title: str, text: str) -> bool:
    normalized_text = _normalize_for_title_match(text)
    if not normalized_text:
        return False

    normalized_title = _normalize_for_title_match(title)
    if normalized_title and normalized_title in normalized_text:
        return True

    title_words = DONLAND_TITLE_WORD_RE.findall(normalized_title)
    if len(title_words) >= 4:
        fragment = " ".join(title_words[:6]).strip()
        if fragment and fragment in normalized_text:
            return True
    return False


def _normalize_for_title_match(value: str) -> str:
    return " ".join((value or "").lower().split())
