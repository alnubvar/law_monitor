from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import DB_PATH, get_source_config
from app.extractors.ocr_extractor import OCR_STATUS_SUCCESS
from app.extractors.pdf_extractor import extract_text_from_pdf
from app.pipeline.deduplicate import compute_content_hash
from app.storage import (
    determine_ocr_priority,
    get_document_by_url,
    list_unresolved_scan_candidate_audit,
    list_pending_ocr_queue,
    mark_runtime_event,
    ocr_queue_item_exists,
    save_document_extraction_audit,
    upsert_ocr_queue_item,
    update_document_text_by_url,
    update_ocr_queue_status,
)


@dataclass(slots=True)
class OCRRunResult:
    checked: int = 0
    updated: int = 0
    success: int = 0
    failed: int = 0
    unavailable: int = 0
    skipped: int = 0


@dataclass(slots=True)
class OCRBackfillResult:
    scanned: int = 0
    queued: int = 0
    existing: int = 0
    skipped: int = 0


def _is_scan_candidate_after_extraction(*, extracted_text_length: int, page_count: int, ocr_status: str) -> bool:
    if ocr_status == OCR_STATUS_SUCCESS:
        return False
    if extracted_text_length == 0:
        return True
    if page_count >= 3 and extracted_text_length < page_count * 120:
        return True
    if extracted_text_length < 500:
        return True
    return False


def backfill_ocr_queue_from_audit(
    *,
    source_name: str | None = None,
    limit: int = 200,
    db_path: Path | str = DB_PATH,
) -> OCRBackfillResult:
    result = OCRBackfillResult()
    unresolved_rows = list_unresolved_scan_candidate_audit(
        source_name=source_name,
        limit=limit,
        db_path=db_path,
    )
    for row in unresolved_rows:
        result.scanned += 1
        document_url = str(row.get("document_url") or "").strip()
        if not document_url:
            result.skipped += 1
            continue
        if ocr_queue_item_exists(document_url=document_url, db_path=db_path):
            result.existing += 1
            continue
        source = str(row.get("source_name") or source_name or "unknown").strip() or "unknown"
        document = get_document_by_url(document_url, db_path=db_path)
        action_level = document.action_level if document is not None else None
        title = document.title if document is not None else None
        if not (title or "").strip():
            title = str(row.get("document_url") or "").strip()
        priority = determine_ocr_priority(
            source_name=source,
            action_level=action_level,
        )
        upsert_ocr_queue_item(
            document_url=document_url,
            source_name=source,
            title=title,
            priority=priority,
            reason="scan_candidate_pdf",
            db_path=db_path,
        )
        result.queued += 1

    mark_runtime_event(
        "ocr_backfill",
        details=(
            f"source={source_name or 'all'}; scanned={result.scanned}; queued={result.queued}; "
            f"existing={result.existing}; skipped={result.skipped}"
        ),
        db_path=db_path,
    )
    return result


def run_ocr_queue(
    *,
    source_name: str | None = None,
    limit: int = 10,
    db_path: Path | str = DB_PATH,
) -> OCRRunResult:
    result = OCRRunResult()
    queue_rows = list_pending_ocr_queue(
        source_name=source_name,
        limit=limit,
        db_path=db_path,
    )
    for queue_row in queue_rows:
        result.checked += 1
        url = str(queue_row.get("document_url") or "").strip()
        source = str(queue_row.get("source_name") or "").strip()
        if not url:
            result.skipped += 1
            continue
        source_config = get_source_config(source)
        request_options: dict[str, object] = {}
        source_url = str(queue_row.get("document_url") or "")
        if source_config is not None:
            source_url = source_config.url
            request_options = {
                "headers": source_config.request_headers,
                "timeout": source_config.request_timeout,
                "verify_ssl": source_config.verify_ssl,
            }
        try:
            extracted = extract_text_from_pdf(url, **request_options)
        except Exception as exc:
            message = str(exc).strip()
            result.failed += 1
            update_ocr_queue_status(
                document_url=url,
                status="pending",
                notes=f"OCR fetch failed: {message[:300]}",
                db_path=db_path,
            )
            save_document_extraction_audit(
                source_name=source or "unknown",
                source_url=source_url,
                document_url=url,
                attachment_url=url,
                file_type="pdf",
                extracted_type="pdf",
                raw_text_length=0,
                has_text=False,
                scan_candidate=True,
                needs_ocr=True,
                ocr_status="failed",
                ocr_text_length=0,
                ocr_error=message,
                ocr_pages_processed=0,
                page_count=None,
                extraction_error=message,
                db_path=db_path,
            )
            continue
        raw_text = (extracted.raw_text or "").strip()
        extracted_length = extracted.extracted_text_length or len(raw_text)
        page_count = extracted.page_count or 0
        scan_candidate = _is_scan_candidate_after_extraction(
            extracted_text_length=extracted_length,
            page_count=page_count,
            ocr_status=extracted.ocr_status,
        )
        has_text = extracted_length > 0

        if extracted.ocr_status == OCR_STATUS_SUCCESS and has_text:
            content_hash = compute_content_hash(raw_text, fallback=url)
            updated_rows = update_document_text_by_url(
                document_url=url,
                raw_text=raw_text,
                content_hash=content_hash,
                local_file_path=extracted.local_file_path,
                document_type=extracted.document_type or "pdf",
                error=extracted.error,
                db_path=db_path,
            )
            if updated_rows > 0:
                result.updated += 1
            update_ocr_queue_status(
                document_url=url,
                status="done",
                notes="OCR completed automatically",
                db_path=db_path,
            )
            result.success += 1
        elif extracted.ocr_status == "unavailable":
            message = (extracted.ocr_error or "OCR runtime unavailable").strip()
            update_ocr_queue_status(
                document_url=url,
                status="pending",
                notes=f"OCR unavailable: {message[:300]}",
                db_path=db_path,
            )
            result.unavailable += 1
        else:
            message = (extracted.ocr_error or extracted.error or "OCR failed").strip()
            update_ocr_queue_status(
                document_url=url,
                status="pending",
                notes=f"OCR failed: {message[:300]}",
                db_path=db_path,
            )
            result.failed += 1

        document = get_document_by_url(url, db_path=db_path)
        save_document_extraction_audit(
            source_name=source or "unknown",
            source_url=source_url,
            document_url=url,
            attachment_url=url,
            file_type="pdf",
            extracted_type=extracted.document_type or "pdf",
            raw_text_length=extracted_length,
            has_text=has_text,
            scan_candidate=scan_candidate,
            needs_ocr=scan_candidate,
            ocr_status=extracted.ocr_status,
            ocr_text_length=extracted.ocr_text_length,
            ocr_error=extracted.ocr_error,
            ocr_pages_processed=extracted.ocr_pages_processed,
            page_count=extracted.page_count,
            extraction_error=extracted.error,
            db_path=db_path,
        )
        if document is None:
            result.skipped += 1

    mark_runtime_event(
        "ocr_run",
        details=(
            f"checked={result.checked}; updated={result.updated}; success={result.success}; "
            f"failed={result.failed}; unavailable={result.unavailable}; skipped={result.skipped}"
        ),
        db_path=db_path,
    )
    return result
