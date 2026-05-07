from __future__ import annotations

import logging
from collections.abc import Iterable

from app.config import DB_PATH, load_keyword_groups, load_keywords
from app.llm.enrichment import DocumentEnricher, get_document_enricher
from app.llm.mock_client import MockLLMClient
from app.storage import (
    count_documents_by_action_level,
    save_document_enrichment,
    get_document_by_url,
    init_db,
    list_unanalyzed_documents,
    mark_runtime_event,
    reprioritize_high_value_ocr_queue,
    update_analysis,
)

logger = logging.getLogger(__name__)


def _build_client() -> MockLLMClient:
    return MockLLMClient(
        load_keywords(),
        keyword_groups=load_keyword_groups(),
    )


def _analyze_documents(documents: Iterable, *, client: MockLLMClient, db_path) -> int:
    analyzed_count = 0
    enricher = get_document_enricher()
    for document in documents:
        try:
            analysis = client.analyze_document(
                document.title,
                document.raw_text,
                source_name=document.source_name,
                url=document.url,
                level=document.level,
                region=document.region,
            )
            if document.id is None:
                raise ValueError("Document id is missing")
            update_analysis(document.id, analysis, db_path=db_path)
            analyzed_count += 1
            _run_optional_enrichment(
                document=document,
                analysis=analysis,
                enricher=enricher,
                db_path=db_path,
            )
        except Exception as exc:
            logger.exception(
                "Analysis failed for document id=%s url=%s: %s",
                document.id,
                document.url,
                exc,
            )
            continue
    return analyzed_count


def _run_optional_enrichment(
    *,
    document,
    analysis,
    enricher: DocumentEnricher,
    db_path,
) -> None:
    enrichment = enricher.maybe_enrich_document(
        title=document.title,
        raw_text=document.raw_text,
        analysis=analysis,
        source_name=document.source_name,
        url=document.url,
        level=document.level,
        region=document.region,
    )
    if enrichment is None:
        return
    try:
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider=enricher.provider_name,
            model=enricher.model_name,
            enrichment=enrichment,
            db_path=db_path,
        )
    except Exception as exc:
        logger.warning(
            "Failed to persist LLM enrichment for document id=%s url=%s: %s",
            document.id,
            document.url,
            exc,
        )
    if enrichment.error:
        logger.warning(
            "LLM enrichment unavailable for document id=%s url=%s: %s",
            document.id,
            document.url,
            enrichment.error,
        )


def reanalyze_documents_by_url(
    document_urls: Iterable[str],
    *,
    db_path,
) -> int:
    init_db(db_path)
    seen_urls: set[str] = set()
    documents = []
    for value in document_urls:
        normalized_url = (value or "").strip()
        if not normalized_url or normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        document = get_document_by_url(normalized_url, db_path=db_path)
        if document is not None:
            documents.append(document)
    if not documents:
        return 0
    client = _build_client()
    analyzed_count = _analyze_documents(documents, client=client, db_path=db_path)
    reprioritized = reprioritize_high_value_ocr_queue(db_path=db_path)
    if reprioritized:
        logger.info("OCR queue: %s items upgraded to high priority after analysis", reprioritized)
    return analyzed_count


def run_analyze(limit: int | None = None, *, reanalyze: bool = False) -> int:
    init_db()
    client = _build_client()
    documents = list_unanalyzed_documents(limit=limit, reanalyze=reanalyze)
    analyzed_count = _analyze_documents(documents, client=client, db_path=DB_PATH)

    reprioritized = reprioritize_high_value_ocr_queue()
    if reprioritized:
        logger.info("OCR queue: %s items upgraded to high priority after analysis", reprioritized)
    requires_attention_count = count_documents_by_action_level("requires_attention")
    logger.info(
        "Analysis completed. Processed %s documents. requires_attention=%s",
        analyzed_count,
        requires_attention_count,
    )
    mark_runtime_event("analyze", details=f"processed={analyzed_count}")
    return analyzed_count
