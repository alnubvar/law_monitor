from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from app import config
from app.config import DB_PATH, load_keyword_groups, load_keywords
from app.llm.enrichment import (
    DOCUMENT_CARD_PROMPT_VERSION,
    DocumentEnricher,
    build_document_card_input,
    compute_document_card_source_hash,
    get_document_enricher,
    is_enrichment_eligible,
)
from app.llm.mock_client import MockLLMClient
from app.storage import (
    count_documents_by_action_level,
    get_document_enrichment,
    get_runtime_event,
    get_document_by_url,
    init_db,
    list_unanalyzed_documents,
    mark_runtime_event,
    reprioritize_high_value_ocr_queue,
    save_document_enrichment,
    update_analysis,
)

logger = logging.getLogger(__name__)
_DEADLINE_BREAKDOWN_LIMIT = 5


@dataclass(slots=True)
class _DeadlineExtractionTelemetry:
    attempted: int = 0
    found: int = 0
    missing: int = 0
    urgent_missing: int = 0
    missing_by_source: Counter[str] = field(default_factory=Counter)
    missing_by_page_type: Counter[str] = field(default_factory=Counter)

    def record(self, *, document, analysis) -> None:
        self.attempted += 1
        if analysis.deadline_text:
            self.found += 1
            return
        self.missing += 1
        if analysis.action_level == "requires_attention":
            self.urgent_missing += 1
        source_name = (document.source_name or "").strip() or "unknown"
        page_type = (analysis.page_type or "").strip() or "unknown"
        self.missing_by_source[source_name] += 1
        self.missing_by_page_type[page_type] += 1

    def persist(self, *, db_path) -> None:
        if self.attempted <= 0:
            return
        existing = _load_deadline_extraction_telemetry(db_path=db_path)
        existing["attempted"] = int(existing.get("attempted", 0)) + self.attempted
        existing["found"] = int(existing.get("found", 0)) + self.found
        existing["missing"] = int(existing.get("missing", 0)) + self.missing
        existing["urgent_missing"] = int(existing.get("urgent_missing", 0)) + self.urgent_missing
        merged_source = Counter(_normalize_breakdown(existing.get("missing_by_source")))
        merged_source.update(self.missing_by_source)
        merged_page_type = Counter(_normalize_breakdown(existing.get("missing_by_page_type")))
        merged_page_type.update(self.missing_by_page_type)
        payload = {
            "attempted": existing["attempted"],
            "found": existing["found"],
            "missing": existing["missing"],
            "urgent_missing": existing["urgent_missing"],
            "missing_by_source": dict(merged_source.most_common(_DEADLINE_BREAKDOWN_LIMIT)),
            "missing_by_page_type": dict(
                merged_page_type.most_common(_DEADLINE_BREAKDOWN_LIMIT)
            ),
        }
        mark_runtime_event(
            "deadline_extraction",
            details=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            db_path=db_path,
        )


def _load_deadline_extraction_telemetry(*, db_path) -> dict[str, object]:
    event = get_runtime_event("deadline_extraction", db_path=db_path)
    if event is None:
        return {}
    details = event.get("details")
    if not details:
        return {}
    try:
        payload = json.loads(str(details))
    except json.JSONDecodeError:
        logger.warning("Could not parse deadline extraction telemetry payload")
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_breakdown(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, raw_count in value.items():
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        normalized_key = str(key).strip() or "unknown"
        if count > 0:
            result[normalized_key] = count
    return result


def _build_client() -> MockLLMClient:
    return MockLLMClient(
        load_keywords(),
        keyword_groups=load_keyword_groups(),
    )


def _analyze_documents(documents: Iterable, *, client: MockLLMClient, db_path) -> int:
    analyzed_count = 0
    enrichment_calls = 0
    max_enrichment_calls = max(
        1,
        int(getattr(config, "LLM_MAX_DOCS_PER_BATCH", 20)),
    )
    enricher = get_document_enricher()
    deadline_telemetry = _DeadlineExtractionTelemetry()
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
            deadline_telemetry.record(document=document, analysis=analysis)
            if (
                enricher.enabled
                and is_enrichment_eligible(analysis.action_level)
                and enrichment_calls >= max_enrichment_calls
            ):
                logger.info(
                    "LLM enrichment batch limit reached (%s documents); skipping enrichment for document id=%s url=%s",
                    max_enrichment_calls,
                    document.id,
                    document.url,
                )
            elif _run_optional_enrichment(
                document=document,
                analysis=analysis,
                enricher=enricher,
                db_path=db_path,
            ):
                enrichment_calls += 1
        except Exception as exc:
            logger.exception(
                "Analysis failed for document id=%s url=%s: %s",
                document.id,
                document.url,
                exc,
            )
            continue
    deadline_telemetry.persist(db_path=db_path)
    return analyzed_count


def _run_optional_enrichment(
    *,
    document,
    analysis,
    enricher: DocumentEnricher,
    db_path,
) -> bool:
    if not enricher.enabled or not is_enrichment_eligible(analysis.action_level):
        return False
    prepared = build_document_card_input(
        title=document.title,
        raw_text=document.raw_text,
        analysis=analysis,
        source_name=document.source_name,
        url=document.url,
        level=document.level,
        region=document.region,
        published_at=document.published_at,
        document_type=document.document_type,
    )
    source_hash = compute_document_card_source_hash(prepared)
    cached = next(
        (
            get_document_enrichment(
                document.url,
                provider=enricher.provider_name,
                model=model_name,
                prompt_version=DOCUMENT_CARD_PROMPT_VERSION,
                source_hash=source_hash,
                db_path=db_path,
            )
            for model_name in enricher.cache_model_names()
        ),
        None,
    )
    if cached is not None:
        return False
    enrichment = enricher.maybe_enrich_document(
        title=document.title,
        raw_text=document.raw_text,
        analysis=analysis,
        source_name=document.source_name,
        url=document.url,
        level=document.level,
        region=document.region,
        published_at=document.published_at,
        document_type=document.document_type,
    )
    if enrichment is None:
        return False
    if not enrichment.source_hash:
        enrichment.source_hash = source_hash
    try:
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider=enricher.provider_name,
            model=enricher.model_name_for_result(enrichment),
            enrichment=enrichment,
            prompt_version=enrichment.prompt_version,
            source_hash=enrichment.source_hash,
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
    return True


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
