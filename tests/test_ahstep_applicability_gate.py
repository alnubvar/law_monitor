from __future__ import annotations

import json
import importlib
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.config import load_keyword_groups, load_keywords
from app.llm.enrichment import DocumentCardFacts, EnrichmentResult
from app.llm.mock_client import MockLLMClient
from app.models import RawDocument
from app.reports.markdown_report import generate_markdown_report, select_visible_report_documents
from app.storage import init_db, save_document_enrichment

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "regression"
PROMOTE_FIXTURE_IDS = {
    "promote_nao_reindeer_subsidy",
    "promote_nao_kmns_family_communities",
    "promote_buryatia_elite_seed",
    "promote_kaluga_investment_credit",
    "promote_krasnodar_subsidy",
    "promote_rostov_support",
    "promote_stavropol_support",
    "promote_moscow_region_support",
    "federal_export_grain_support",
    "promote_generic_all_russia_noise",
}
VISIBLE_FIXTURE_IDS = {
    "promote_krasnodar_subsidy",
    "promote_rostov_support",
    "promote_stavropol_support",
    "promote_moscow_region_support",
    "federal_export_grain_support",
}


def _load_fixture(fixture_id: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{fixture_id}.json").read_text(encoding="utf-8"))


def _analyzed_document(fixture: dict, result, *, doc_id: int) -> RawDocument:
    now = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)
    return RawDocument(
        id=doc_id,
        source_name=fixture["source_name"],
        source_url=fixture["url"],
        level=fixture.get("level", "support_measures"),
        region=fixture.get("region", "federal"),
        title=fixture["title"],
        url=fixture["url"],
        published_at=now,
        collected_at=now,
        content_hash=f"fixture-{fixture['id']}",
        raw_text=fixture["raw_text"],
        is_relevant=result.is_relevant,
        relevance_reason=result.relevance_reason,
        topic=result.topic,
        importance=result.importance,
        action_level=result.action_level,
        page_type=result.page_type,
        summary=result.summary,
        impact=result.impact,
        support_status=result.support_status,
        is_active=result.is_active,
        is_continuous=result.is_continuous,
        application_status=result.application_status,
        npa_number=result.npa_number,
        deadline_text=result.deadline_text,
        terms_text=result.terms_text,
        business_signal=result.business_signal,
        risk_notes=result.risk_notes,
    )


class AhstepApplicabilityGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = MockLLMClient(
            load_keywords(),
            keyword_groups=load_keyword_groups(),
        )

    def test_promote_budget_target_region_gate_controls_report_visibility(self) -> None:
        documents: list[RawDocument] = []
        for index, fixture_id in enumerate(sorted(PROMOTE_FIXTURE_IDS), start=1):
            fixture = _load_fixture(fixture_id)
            result = self.client.analyze_document(
                fixture["title"],
                fixture["raw_text"],
                source_name=fixture["source_name"],
                url=fixture["url"],
                level=fixture.get("level", "support_measures"),
                region=fixture.get("region", "federal"),
            )
            documents.append(_analyzed_document(fixture, result, doc_id=index))

        visible = select_visible_report_documents(
            documents,
            action_levels=["requires_attention", "watchlist"],
        )
        visible_ids = {
            document.content_hash.replace("fixture-", "") for document in visible
        }

        self.assertEqual(visible_ids, VISIBLE_FIXTURE_IDS)

    def test_stale_wrong_llm_enrichment_cannot_surface_hidden_reindeer_item(self) -> None:
        fixture = _load_fixture("promote_nao_reindeer_subsidy")
        result = self.client.analyze_document(
            fixture["title"],
            fixture["raw_text"],
            source_name=fixture["source_name"],
            url=fixture["url"],
            level=fixture.get("level", "support_measures"),
            region=fixture.get("region", "federal"),
        )
        document = _analyzed_document(fixture, result, doc_id=101)
        db_path = Path("data/test_artifacts/ahstep_applicability_gate.db")
        if db_path.exists():
            db_path.unlink()
        init_db(db_path)
        enrichment = EnrichmentResult.from_facts(
            DocumentCardFacts(
                document_type="отбор",
                region="Ненецкий автономный округ",
                status="прием открыт",
                support_type="субсидия",
                short_summary="Открыт отбор на элитное семеноводство.",
                what_changed="Опубликована поддержка элитного семеноводства.",
                why_matters="Может быть важна для семеноводства.",
                what_to_check="Проверить условия по семенам.",
                confidence="high",
            )
        )
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="",
            enrichment=enrichment,
            db_path=db_path,
        )

        try:
            markdown = generate_markdown_report(
                [document],
                report_date="2026-05-22",
                db_path=db_path,
            )
        finally:
            if db_path.exists():
                db_path.unlink()

        self.assertNotIn("элитное семеноводство", markdown.lower())
        self.assertNotIn(document.url, markdown)

    def test_ahstep_target_regions_env_defaults_and_override_are_loaded(self) -> None:
        import app.config as config_module

        with patch.dict(os.environ, {"AHSTEP_TARGET_REGIONS": ""}, clear=False):
            reloaded = importlib.reload(config_module)
            self.assertIn("Ростовская область", reloaded.AHSTEP_TARGET_REGIONS)
            self.assertIn("Московская область", reloaded.AHSTEP_TARGET_REGIONS)

        with patch.dict(
            os.environ,
            {"AHSTEP_TARGET_REGIONS": "Россия, Республика Бурятия, Республика Бурятия"},
            clear=False,
        ):
            reloaded = importlib.reload(config_module)
            self.assertEqual(
                reloaded.AHSTEP_TARGET_REGIONS,
                ("Россия", "Республика Бурятия"),
            )
        importlib.reload(config_module)


if __name__ == "__main__":
    unittest.main()
