from __future__ import annotations

import logging

from app.config import load_keyword_groups, load_keywords
from app.llm.mock_client import MockLLMClient
from app.storage import (
    count_documents_by_action_level,
    init_db,
    list_unanalyzed_documents,
    mark_runtime_event,
    update_analysis,
)

logger = logging.getLogger(__name__)


def run_analyze(limit: int | None = None, *, reanalyze: bool = False) -> int:
    init_db()
    client = MockLLMClient(
        load_keywords(),
        keyword_groups=load_keyword_groups(),
    )
    documents = list_unanalyzed_documents(limit=limit, reanalyze=reanalyze)
    analyzed_count = 0

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
            update_analysis(document.id, analysis)
            analyzed_count += 1
        except Exception as exc:
            logger.exception(
                "Analysis failed for document id=%s url=%s: %s",
                document.id,
                document.url,
                exc,
            )
            continue

    requires_attention_count = count_documents_by_action_level("requires_attention")
    logger.info(
        "Analysis completed. Processed %s documents. requires_attention=%s",
        analyzed_count,
        requires_attention_count,
    )
    mark_runtime_event("analyze", details=f"processed={analyzed_count}")
    return analyzed_count
