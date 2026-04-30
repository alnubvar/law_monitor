from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.pipeline.analyze import run_analyze
from app.pipeline.collect import run_collect
from app.pipeline.digest import run_digest
from app.storage import init_db

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PipelineRunResult:
    collected_count: int
    analyzed_count: int
    report_path: Path


def run_pipeline(
    *,
    days: int = 7,
    output_path: str | None = None,
    analyze_limit: int | None = None,
    force_reanalyze: bool = False,
) -> PipelineRunResult:
    logger.info("Starting full pipeline run")
    init_db()
    collected_count = run_collect()
    analyzed_count = run_analyze(limit=analyze_limit, reanalyze=force_reanalyze)
    report_path = run_digest(days=days, output_path=output_path)
    logger.info(
        "Pipeline run completed: collected=%s analyzed=%s report=%s",
        collected_count,
        analyzed_count,
        report_path,
    )
    return PipelineRunResult(
        collected_count=collected_count,
        analyzed_count=analyzed_count,
        report_path=report_path,
    )
