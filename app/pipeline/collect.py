from __future__ import annotations

import logging
from collections.abc import Mapping

from requests import RequestException

from app.config import ensure_directories, load_sources
from app.extractors.docx_extractor import extract_text_from_docx
from app.extractors.html_extractor import extract_text_from_html
from app.extractors.ocr_extractor import extract_text_with_ocr
from app.extractors.pdf_extractor import extract_text_from_pdf
from app.models import CollectedItem, ExtractionResult, RawDocument, SourceConfig
from app.pipeline.deduplicate import compute_content_hash
from app.sources.base import BaseSource
from app.sources.generic_html_source import GenericHTMLSource
from app.sources.government_source import GovernmentSource
from app.sources.krasnodar_source import KrasnodarSource
from app.sources.regional_law_source import RegionalLawSource
from app.storage import (
    update_document_published_at_by_url,
    clear_source_errors,
    document_exists_by_hash,
    document_exists_by_url,
    init_db,
    save_source_error,
    save_document,
)

logger = logging.getLogger(__name__)
PROGRESS_LOG_EVERY = 25

PARSER_REGISTRY: dict[str, type[BaseSource]] = {
    "generic_html": GenericHTMLSource,
    "government": GovernmentSource,
    "krasnodar": KrasnodarSource,
    "regional_law": RegionalLawSource,
}


def create_source(config: SourceConfig) -> BaseSource:
    source_class = PARSER_REGISTRY.get(config.parser, GenericHTMLSource)
    return source_class(config)


def _request_options(source_config: SourceConfig) -> dict[str, object]:
    return {
        "headers": source_config.request_headers,
        "timeout": source_config.request_timeout,
        "verify_ssl": source_config.verify_ssl,
    }


def extract_document(item: CollectedItem, source_config: SourceConfig) -> ExtractionResult:
    request_options = _request_options(source_config)

    if item.document_type == "pdf":
        result = extract_text_from_pdf(item.url, **request_options)
        if result.needs_ocr and result.local_file_path:
            ocr_placeholder = extract_text_with_ocr(result.local_file_path)
            result.raw_text = (
                f"{result.raw_text}\n\n{ocr_placeholder}".strip()
            )
        return result

    if item.document_type == "docx":
        return extract_text_from_docx(item.url, **request_options)

    if item.document_type == "doc":
        return ExtractionResult(
            raw_text=(
                "Legacy DOC file detected. Native DOC extraction is not implemented "
                "in MVP yet."
            ),
            document_type="doc",
            error="DOC extraction is not implemented",
        )

    if item.document_type == "xml":
        return extract_text_from_html(
            item.url,
            source_name=source_config.name,
            **request_options,
        )

    if item.document_type == "unknown":
        return ExtractionResult(
            raw_text="",
            document_type="unknown",
            error="Unsupported document type for MVP extractor",
        )

    return extract_text_from_html(
        item.url,
        source_name=source_config.name,
        **request_options,
    )


def _should_log_progress(index: int, total_items: int) -> bool:
    if total_items <= 3:
        return index == 1 or index == total_items
    if index == 1 or index == total_items:
        return True
    return index % PROGRESS_LOG_EVERY == 0


def run_collect(source_name: str | None = None, limit: int | None = None) -> int:
    ensure_directories()
    init_db()
    sources = [source for source in load_sources() if source.enabled]
    if source_name:
        sources = [source for source in sources if source.name == source_name]
    if source_name and not sources:
        raise ValueError(f"Source not found or disabled: {source_name}")

    saved_count = 0

    for source_config in sources:
        logger.info("Collecting from source: %s", source_config.name)
        clear_source_errors(source_name=source_config.name)
        try:
            source = create_source(source_config)
            items = source.fetch_items()
        except RequestException as exc:
            logger.warning("Source collection failed for %s: %s", source_config.name, exc)
            save_source_error(source_config.name, source_config.url, str(exc))
            continue
        except Exception as exc:
            logger.exception("Source collection failed for %s: %s", source_config.name, exc)
            save_source_error(source_config.name, source_config.url, str(exc))
            continue

        effective_limit = limit if limit is not None else source_config.max_items
        if effective_limit is not None:
            items = items[:effective_limit]

        source_saved = 0
        source_skipped_existing = 0
        source_skipped_duplicates = 0
        source_errors = 0
        total_items = len(items)

        for index, item in enumerate(items, start=1):
            if _should_log_progress(index, total_items):
                logger.info(
                    "Source progress [%s]: %s/%s",
                    source_config.name,
                    index,
                    total_items,
                )
            try:
                if document_exists_by_url(item.url):
                    if item.published_at is not None:
                        update_document_published_at_by_url(
                            item.url,
                            item.published_at,
                        )
                    logger.debug("Skip existing URL: %s", item.url)
                    source_skipped_existing += 1
                    continue

                extracted = extract_document(item, source_config)
                content_hash = compute_content_hash(
                    extracted.raw_text, fallback=f"{item.title}\n{item.url}"
                )
                if document_exists_by_hash(content_hash):
                    logger.info("Skip duplicate content: %s", item.url)
                    source_skipped_duplicates += 1
                    continue

                document = RawDocument(
                    source_name=item.source_name,
                    source_url=item.source_url,
                    level=item.level,
                    region=item.region,
                    title=item.title,
                    url=item.url,
                    published_at=item.published_at or extracted.published_at,
                    content_hash=content_hash,
                    raw_text=extracted.raw_text,
                    local_file_path=extracted.local_file_path,
                    document_type=extracted.document_type or item.document_type,
                    status="collected" if not extracted.error else "collected_with_warning",
                    error=extracted.error,
                )
                save_document(document)
                saved_count += 1
                source_saved += 1
            except RequestException as exc:
                source_errors += 1
                logger.warning(
                    "Item request failed for source=%s url=%s: %s",
                    source_config.name,
                    item.url,
                    exc,
                )
                continue
            except Exception as exc:
                source_errors += 1
                logger.exception(
                    "Item processing failed for source=%s url=%s: %s",
                    source_config.name,
                    item.url,
                    exc,
                )
                continue

        if source_errors > 0:
            save_source_error(
                source_config.name,
                source_config.url,
                f"Item processing errors: {source_errors}",
            )
        logger.info(
            "Source finished [%s]: total=%s, saved=%s, existing=%s, duplicates=%s, errors=%s",
            source_config.name,
            total_items,
            source_saved,
            source_skipped_existing,
            source_skipped_duplicates,
            source_errors,
        )

    logger.info("Collection completed. Saved %s new documents.", saved_count)
    return saved_count
