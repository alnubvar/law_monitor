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
from app.rules.ahstep_applicability import evaluate_support_measure_applicability
from app.storage import init_db, save_document_enrichment
from app.visibility import effective_user_action_level, should_show_document

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

    def test_rostov_fishery_and_aquaculture_feed_are_not_visible_priority_items(self) -> None:
        cases = [
            (
                "Постановление о внесении изменений в порядок субсидий на развитие рыбохозяйственного комплекса",
                (
                    "Министерство сельского хозяйства и продовольствия Ростовской области. "
                    "Порядок предоставления субсидий на развитие рыбохозяйственного комплекса."
                ),
            ),
            (
                "Постановление о субсидиях на корма для аквакультуры",
                (
                    "Ростовская область предоставляет субсидии на возмещение части затрат "
                    "на приобретение кормов для производства продукции аквакультуры."
                ),
            ),
        ]
        documents: list[RawDocument] = []
        for index, (title, raw_text) in enumerate(cases, start=1):
            result = self.client.analyze_document(
                title,
                raw_text,
                source_name="Право Ростовской области",
                url=f"https://pravo.donland.ru/doc/view/id/fish-{index}/",
                level="regional",
                region="rostov",
            )
            self.assertNotEqual(result.action_level, "requires_attention")
            document = _analyzed_document(
                {
                    "id": f"rostov_fish_{index}",
                    "source_name": "Право Ростовской области",
                    "url": f"https://pravo.donland.ru/doc/view/id/fish-{index}/",
                    "level": "regional",
                    "region": "rostov",
                    "title": title,
                    "raw_text": raw_text,
                },
                result,
                doc_id=200 + index,
            )
            documents.append(document)
            self.assertNotEqual(effective_user_action_level(document), "requires_attention")
            self.assertFalse(
                should_show_document(
                    document,
                    surface="report",
                    relevant_only=False,
                    action_levels=["requires_attention", "watchlist"],
                )
            )
            self.assertFalse(should_show_document(document, surface="telegram_digest", relevant_only=False))

        markdown = generate_markdown_report(
            documents,
            report_date="2026-05-22",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )
        self.assertNotIn("рыбохозяйственного", markdown.lower())
        self.assertNotIn("аквакульт", markdown.lower())

    def test_non_target_regional_support_is_hidden_by_default(self) -> None:
        cases = [
            (
                "buryatia",
                "Отбор на предоставление субсидий на поддержку элитного семеноводства",
                "Министерство сельского хозяйства и продовольствия Республики Бурятия. Прием заявок открыт до 23.05.2026.",
            ),
            (
                "oryol",
                "Открыт прием заявок на субсидии для предприятий АПК",
                "Департамент сельского хозяйства Орловской области предоставляет субсидии на кадровое обеспечение.",
            ),
            (
                "kaluga",
                "Отбор на инвестиционные кредиты для АПК",
                "Министерство сельского хозяйства Калужской области объявило отбор на компенсацию затрат.",
            ),
            (
                "nao_reindeer",
                "Открыт прием заявок на возмещение затрат",
                "Ненецкий автономный округ. Субсидия на сохранение поголовья северных оленей для КМНС.",
            ),
            (
                "generic_non_target",
                "Открыт прием заявок на субсидии АПК",
                "Министерство сельского хозяйства Пензенской области объявило региональный отбор.",
            ),
        ]
        documents: list[RawDocument] = []
        for index, (case_id, title, raw_text) in enumerate(cases, start=1):
            result = self.client.analyze_document(
                title,
                raw_text,
                source_name="promote.budget.gov.ru / Минфин - отборы и меры поддержки",
                url=f"https://promote.budget.gov.ru/public/minfin/selection/view/{case_id}",
                level="support_measures",
                region="federal",
            )
            self.assertNotEqual(result.action_level, "requires_attention", case_id)
            document = _analyzed_document(
                {
                    "id": case_id,
                    "source_name": "promote.budget.gov.ru / Минфин - отборы и меры поддержки",
                    "url": f"https://promote.budget.gov.ru/public/minfin/selection/view/{case_id}",
                    "level": "support_measures",
                    "region": "federal",
                    "title": title,
                    "raw_text": raw_text,
                },
                result,
                doc_id=300 + index,
            )
            documents.append(document)
            self.assertFalse(
                should_show_document(
                    document,
                    surface="report",
                    relevant_only=False,
                    action_levels=["requires_attention", "watchlist"],
                ),
                case_id,
            )
            self.assertFalse(should_show_document(document, surface="telegram_digest", relevant_only=False), case_id)

        visible = select_visible_report_documents(
            documents,
            action_levels=["requires_attention", "watchlist"],
        )
        self.assertEqual(visible, [])

    def test_non_target_support_can_be_enabled_by_target_region_config(self) -> None:
        import app.config as config_module

        try:
            with patch.dict(
                os.environ,
                {"AHSTEP_TARGET_REGIONS": "Россия,РФ,Республика Бурятия,Орловская область,Калужская область"},
                clear=False,
            ):
                importlib.reload(config_module)
                for region_text in (
                    "Министерство сельского хозяйства Республики Бурятия объявило отбор на субсидии.",
                    "Департамент сельского хозяйства Орловской области объявил отбор на субсидии.",
                    "Министерство сельского хозяйства Калужской области объявило отбор на субсидии.",
                ):
                    decision = evaluate_support_measure_applicability(
                        title="Открыт прием заявок на субсидии АПК",
                        raw_text=region_text,
                        source_name="promote.budget.gov.ru / Минфин - отборы и меры поддержки",
                        url="https://promote.budget.gov.ru/public/minfin/selection/view/target-override",
                        level="support_measures",
                        region="federal",
                        source_role="active_support_measures",
                        page_type="selection_announcement",
                    )
                    self.assertTrue(decision.is_applicable, region_text)
        finally:
            importlib.reload(config_module)

    def test_valid_target_and_federal_profile_support_remains_visible(self) -> None:
        cases = [
            (
                "rostov_seed",
                "Отбор на субсидии на элитное семеноводство картофеля",
                "pppItemName: Министерство сельского хозяйства Ростовской области. Активная мера поддержки. Прием заявок открыт до 23.05.2026. Субсидии сельхозтоваропроизводителям на семеноводство.",
                "promote.budget.gov.ru / Минфин - отборы и меры поддержки",
                "support_measures",
                "federal",
            ),
            (
                "krasnodar_processing",
                "Субсидии на модернизацию переработки молочной продукции",
                "pppItemName: Министерство сельского хозяйства Краснодарского края. Активная мера поддержки. Прием заявок открыт до 23.05.2026. Субсидии предприятиям АПК на переработку и молочное скотоводство.",
                "promote.budget.gov.ru / Минфин - отборы и меры поддержки",
                "support_measures",
                "federal",
            ),
        ]
        documents: list[RawDocument] = []
        for index, (case_id, title, raw_text, source_name, level, region) in enumerate(cases, start=1):
            result = self.client.analyze_document(
                title,
                raw_text,
                source_name=source_name,
                url=f"https://example.test/{case_id}",
                level=level,
                region=region,
            )
            document = _analyzed_document(
                {
                    "id": case_id,
                    "source_name": source_name,
                    "url": f"https://example.test/{case_id}",
                    "level": level,
                    "region": region,
                    "title": title,
                    "raw_text": raw_text,
                },
                result,
                doc_id=400 + index,
            )
            documents.append(document)
            self.assertIn(effective_user_action_level(document), {"requires_attention", "watchlist"}, case_id)
            self.assertTrue(should_show_document(document, surface="report", relevant_only=False), case_id)

        visible_urls = {
            document.url
            for document in select_visible_report_documents(
                documents,
                action_levels=["requires_attention", "watchlist"],
            )
        }
        self.assertEqual(visible_urls, {document.url for document in documents})

        fixture = _load_fixture("federal_export_grain_support")
        result = self.client.analyze_document(
            fixture["title"],
            fixture["raw_text"],
            source_name=fixture["source_name"],
            url=fixture["url"],
            level=fixture.get("level", "support_measures"),
            region=fixture.get("region", "federal"),
        )
        federal_document = _analyzed_document(fixture, result, doc_id=450)
        self.assertIn(effective_user_action_level(federal_document), {"requires_attention", "watchlist"})
        self.assertTrue(
            should_show_document(
                federal_document,
                surface="report",
                relevant_only=False,
                action_levels=["requires_attention", "watchlist"],
            )
        )

    def test_stale_urgent_fish_enrichment_cannot_surface_visible_item(self) -> None:
        document = RawDocument(
            id=501,
            source_name="Право Ростовской области",
            source_url="https://pravo.donland.ru/",
            level="regional",
            region="rostov",
            title="Изменены субсидии на развитие рыбохозяйственного комплекса",
            url="https://pravo.donland.ru/doc/view/id/stale-fish/",
            published_at=datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc),
            collected_at=datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc),
            content_hash="stale-fish",
            raw_text="Ростовская область. Субсидии на развитие рыбохозяйственного комплекса и аквакультуры.",
            is_relevant=True,
            relevance_reason="old cached reason",
            topic="субсидии",
            importance="high",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Старая сводка про субсидии.",
            impact="old cached impact",
        )
        db_path = Path("data/test_artifacts/ahstep_stale_fish.db")
        if db_path.exists():
            db_path.unlink()
        init_db(db_path)
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult.from_facts(
                DocumentCardFacts(
                    document_type="НПА",
                    region="Ростовская область",
                    status="принято",
                    support_type="субсидия",
                    short_summary="Обновлены правила субсидий для АПК в Ростовской области.",
                    why_matters="Может быть важно для AHSTEP.",
                    what_to_check="Проверить условия получения субсидии.",
                    confidence="high",
                )
            ),
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

        self.assertNotEqual(effective_user_action_level(document), "requires_attention")
        self.assertNotIn(document.url, markdown)
        self.assertNotIn("рыбохозяйственного", markdown.lower())

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
