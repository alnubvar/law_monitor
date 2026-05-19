from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app import config
from app.llm.enrichment import (
    DOCUMENT_CARD_PROMPT_VERSION,
    build_document_card_input,
    compute_document_card_source_hash,
    get_document_enricher,
)
from app.reports.markdown_report import select_visible_report_documents
from app.storage import (
    get_document_enrichment,
    init_db,
    list_recent_documents,
    save_document_enrichment,
)


@dataclass(slots=True)
class EnrichDocsResult:
    selected: int = 0
    enriched: int = 0
    skipped_cached: int = 0
    failed: int = 0


def run_enrich_docs(
    *,
    days: int = 7,
    action_levels: list[str] | None = None,
    limit: int | None = None,
    force: bool = False,
    db_path: Path | str = config.DB_PATH,
) -> EnrichDocsResult:
    init_db(db_path)
    enricher = get_document_enricher()
    if not enricher.enabled:
        return EnrichDocsResult()

    selected_action_levels = action_levels or ["requires_attention", "watchlist"]
    safe_limit = max(1, int(limit or config.LLM_ENRICHMENT_LIMIT))
    recent_documents = list_recent_documents(
        db_path=db_path,
        days=max(1, int(days)),
        relevant_only=True,
        action_levels=selected_action_levels,
    )
    analyzed_documents = [
        document
        for document in recent_documents
        if document.action_level in selected_action_levels
        and document.is_relevant is True
        and document.raw_text is not None
    ]
    visible_documents = select_visible_report_documents(
        analyzed_documents,
        relevant_only=True,
        action_levels=selected_action_levels,
        max_items=safe_limit,
    )
    result = EnrichDocsResult(selected=len(visible_documents))

    for document in visible_documents:
        prepared = build_document_card_input(
            title=document.title,
            raw_text=document.raw_text,
            analysis=document,
            source_name=document.source_name,
            url=document.url,
            level=document.level,
            region=document.region,
            published_at=document.published_at,
            document_type=document.document_type,
            max_document_chars=config.LLM_MAX_DOCUMENT_CHARS,
        )
        source_hash = compute_document_card_source_hash(prepared)
        if not force:
            cached = get_document_enrichment(
                document.url,
                provider=enricher.provider_name,
                model=enricher.model_name,
                prompt_version=DOCUMENT_CARD_PROMPT_VERSION,
                source_hash=source_hash,
                db_path=db_path,
            )
            if cached is not None:
                result.skipped_cached += 1
                continue

        enrichment = enricher.maybe_enrich_document(
            title=document.title,
            raw_text=document.raw_text,
            analysis=document,
            source_name=document.source_name,
            url=document.url,
            level=document.level,
            region=document.region,
            published_at=document.published_at,
            document_type=document.document_type,
            max_document_chars=config.LLM_MAX_DOCUMENT_CHARS,
        )
        if enrichment is None:
            result.failed += 1
            continue
        if not enrichment.source_hash:
            enrichment.source_hash = source_hash
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider=enricher.provider_name,
            model=enricher.model_name,
            enrichment=enrichment,
            prompt_version=enrichment.prompt_version,
            source_hash=enrichment.source_hash,
            db_path=db_path,
        )
        if enrichment.error or enrichment.status == "failed":
            result.failed += 1
        else:
            result.enriched += 1

    return result
