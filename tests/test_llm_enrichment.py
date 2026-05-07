from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from app.llm.enrichment import (
    DocumentEnricher,
    EnrichmentResult,
    MockEnrichmentProvider,
    OpenAICompatibleEnrichmentProvider,
    build_document_enricher,
    get_display_enrichment,
    is_enrichment_eligible,
)
from app.models import AnalysisResult, RawDocument
from app.pipeline import analyze as analyze_pipeline
from app.storage import (
    count_document_enrichments,
    get_document_enrichment,
    init_db,
    save_document_enrichment,
)


def _analysis_result(action_level: str) -> AnalysisResult:
    return AnalysisResult(
        is_relevant=action_level != "irrelevant",
        relevance_reason="test",
        normalized_title="Тестовый документ",
        topic="support",
        importance="high" if action_level == "requires_attention" else "medium",
        action_level=action_level,
        page_type="selection_announcement" if action_level != "background" else "news_background",
        summary="Открыт прием заявок на получение субсидии.",
        impact="Меняются условия участия в мере поддержки.",
        application_status="open",
        deadline_text="до 1 июня 2026 года",
        key_dates=["до 1 июня 2026 года"],
    )


def _raw_document() -> RawDocument:
    return RawDocument(
        id=1,
        source_name="Минсельхоз региона",
        source_url="https://example.test/source",
        level="regional",
        region="rostov",
        title="О проведении отбора на предоставление субсидии",
        url="https://example.test/doc",
        content_hash="hash-1",
        raw_text="Открыт прием заявок на предоставление субсидии сельхозтоваропроизводителям.",
    )


class _SpyProvider(MockEnrichmentProvider):
    def __init__(self) -> None:
        self.calls = 0

    def enrich_document(self, **kwargs) -> EnrichmentResult:
        self.calls += 1
        return super().enrich_document(**kwargs)


class _FailingProvider(MockEnrichmentProvider):
    def enrich_document(self, **kwargs) -> EnrichmentResult:
        raise RuntimeError("provider timeout")


class LLMEnrichmentTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path("data/test_artifacts") / name
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def test_init_db_creates_document_enrichments_table(self) -> None:
        db_path = self._db_path("llm_enrichment_table.db")
        init_db(db_path)

        connection = sqlite3.connect(str(db_path))
        try:
            row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'document_enrichments'"
            ).fetchone()
        finally:
            connection.close()

        self.assertIsNotNone(row)

    def test_save_and_get_enrichment_work(self) -> None:
        db_path = self._db_path("llm_enrichment_save_get.db")
        init_db(db_path)
        row_id = save_document_enrichment(
            document_id=1,
            document_url="https://example.test/doc",
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult(
                executive_summary="Кратко",
                business_impact="Влияние",
                recommended_action="Действие",
                deadline_hint="Срок",
                confidence=0.5,
            ),
            db_path=db_path,
        )

        enrichment = get_document_enrichment(
            "https://example.test/doc",
            provider="mock",
            model="mock-enrichment",
            db_path=db_path,
        )

        self.assertGreater(row_id, 0)
        self.assertIsNotNone(enrichment)
        assert enrichment is not None
        self.assertEqual(enrichment["executive_summary"], "Кратко")
        self.assertEqual(enrichment["recommended_action"], "Действие")
        self.assertEqual(enrichment["confidence"], 0.5)

    def test_upsert_updates_existing_enrichment(self) -> None:
        db_path = self._db_path("llm_enrichment_upsert.db")
        init_db(db_path)
        first_id = save_document_enrichment(
            document_id=1,
            document_url="https://example.test/doc",
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult(executive_summary="Первая версия", confidence=0.2),
            db_path=db_path,
        )
        second_id = save_document_enrichment(
            document_id=1,
            document_url="https://example.test/doc",
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult(executive_summary="Вторая версия", confidence=0.7),
            db_path=db_path,
        )

        enrichment = get_document_enrichment(
            "https://example.test/doc",
            provider="mock",
            model="mock-enrichment",
            db_path=db_path,
        )

        self.assertEqual(first_id, second_id)
        self.assertEqual(count_document_enrichments(db_path=db_path), 1)
        assert enrichment is not None
        self.assertEqual(enrichment["executive_summary"], "Вторая версия")
        self.assertEqual(enrichment["confidence"], 0.7)

    def test_disabled_path_does_nothing(self) -> None:
        with mock.patch("app.config.LLM_ENRICHMENT_ENABLED", False):
            enricher = build_document_enricher()

        result = enricher.maybe_enrich_document(
            title="Тест",
            raw_text="Текст",
            analysis=_analysis_result("watchlist"),
            source_name="Источник",
        )

        self.assertIsNone(result)

    def test_mock_enrichment_returns_structured_object(self) -> None:
        enricher = DocumentEnricher(enabled=True, provider=MockEnrichmentProvider())

        result = enricher.maybe_enrich_document(
            title="Тест",
            raw_text="Текст",
            analysis=_analysis_result("requires_attention"),
            source_name="Источник",
            region="krasnodar",
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.error, None)
        self.assertTrue(result.executive_summary)
        self.assertTrue(result.business_impact)
        self.assertTrue(result.recommended_action)
        self.assertEqual(result.deadline_hint, "до 1 июня 2026 года")
        self.assertGreater(result.confidence or 0.0, 0.0)

    def test_only_requires_attention_and_watchlist_are_eligible(self) -> None:
        spy_provider = _SpyProvider()
        enricher = DocumentEnricher(enabled=True, provider=spy_provider)

        watchlist_result = enricher.maybe_enrich_document(
            title="Тест",
            raw_text="Текст",
            analysis=_analysis_result("watchlist"),
        )
        background_result = enricher.maybe_enrich_document(
            title="Тест",
            raw_text="Текст",
            analysis=_analysis_result("background"),
        )
        irrelevant_result = enricher.maybe_enrich_document(
            title="Тест",
            raw_text="Текст",
            analysis=_analysis_result("irrelevant"),
        )

        self.assertIsNotNone(watchlist_result)
        self.assertIsNone(background_result)
        self.assertIsNone(irrelevant_result)
        self.assertEqual(spy_provider.calls, 1)
        self.assertTrue(is_enrichment_eligible("requires_attention"))
        self.assertTrue(is_enrichment_eligible("watchlist"))
        self.assertFalse(is_enrichment_eligible("background"))
        self.assertFalse(is_enrichment_eligible("irrelevant"))

    def test_provider_failure_returns_structured_error(self) -> None:
        enricher = DocumentEnricher(enabled=True, provider=_FailingProvider())

        result = enricher.maybe_enrich_document(
            title="Тест",
            raw_text="Текст",
            analysis=_analysis_result("watchlist"),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("provider timeout", result.error or "")
        self.assertEqual(result.confidence, 0.0)

    def test_display_enrichment_ignores_error_rows(self) -> None:
        display = get_display_enrichment(
            {
                "business_impact": "Влияние",
                "recommended_action": "Действие",
                "confidence": 0.9,
                "error": "timeout",
            }
        )
        self.assertIsNone(display)

    def test_display_enrichment_ignores_low_confidence_rows(self) -> None:
        display = get_display_enrichment(
            {
                "business_impact": "Влияние",
                "recommended_action": "Действие",
                "confidence": 0.4,
                "error": None,
            }
        )
        self.assertIsNone(display)

    def test_display_enrichment_accepts_high_confidence_rows(self) -> None:
        display = get_display_enrichment(
            {
                "executive_summary": "Кратко",
                "business_impact": "Влияние",
                "recommended_action": "Действие",
                "deadline_hint": "Срок",
                "confidence": 0.8,
                "error": None,
            }
        )
        self.assertIsNotNone(display)
        assert display is not None
        self.assertEqual(display["business_impact"], "Влияние")

    def test_display_enrichment_ignores_empty_rows(self) -> None:
        display = get_display_enrichment(
            {
                "executive_summary": "   ",
                "business_impact": "",
                "recommended_action": None,
                "deadline_hint": "",
                "confidence": 0.9,
                "error": None,
            }
        )
        self.assertIsNone(display)

    def test_invalid_json_from_openai_compatible_provider_becomes_error(self) -> None:
        provider = OpenAICompatibleEnrichmentProvider(
            base_url="http://127.0.0.1:1234/v1",
            api_key="",
            model="test-model",
            timeout_seconds=5,
        )
        enricher = DocumentEnricher(enabled=True, provider=provider)

        with mock.patch.object(
            provider,
            "_post_json",
            return_value={"choices": [{"message": {"content": "not-json"}}]},
        ):
            result = enricher.maybe_enrich_document(
                title="Тест",
                raw_text="Текст",
                analysis=_analysis_result("requires_attention"),
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("invalid JSON", result.error or "")

    def test_analyze_pipeline_continues_when_enrichment_fails(self) -> None:
        db_path = self._db_path("llm_enrichment_failure_persist.db")
        init_db(db_path)
        document = _raw_document()
        analysis = _analysis_result("requires_attention")
        client = mock.Mock()
        client.analyze_document.return_value = analysis
        enricher = DocumentEnricher(
            enabled=True,
            provider=_FailingProvider(),
            provider_name="mock",
            model_name="mock-enrichment",
        )

        with mock.patch(
            "app.pipeline.analyze.get_document_enricher",
            return_value=enricher,
        ):
            with mock.patch("app.pipeline.analyze.update_analysis") as update_analysis_mock:
                processed = analyze_pipeline._analyze_documents(
                    [document],
                    client=client,
                    db_path=db_path,
                )

        self.assertEqual(processed, 1)
        update_analysis_mock.assert_called_once_with(
            document.id,
            analysis,
            db_path=db_path,
        )
        enrichment = get_document_enrichment(
            document.url,
            provider="mock",
            model="mock-enrichment",
            db_path=db_path,
        )
        self.assertIsNotNone(enrichment)

    def test_disabled_enrichment_does_not_write_rows(self) -> None:
        db_path = self._db_path("llm_enrichment_disabled_no_rows.db")
        init_db(db_path)
        document = _raw_document()
        analysis = _analysis_result("watchlist")
        client = mock.Mock()
        client.analyze_document.return_value = analysis
        enricher = DocumentEnricher(enabled=False)

        with mock.patch("app.pipeline.analyze.get_document_enricher", return_value=enricher):
            with mock.patch("app.pipeline.analyze.update_analysis"):
                processed = analyze_pipeline._analyze_documents(
                    [document],
                    client=client,
                    db_path=db_path,
                )

        self.assertEqual(processed, 1)
        self.assertEqual(count_document_enrichments(db_path=db_path), 0)

    def test_enabled_mock_enrichment_writes_rows_for_eligible_docs(self) -> None:
        db_path = self._db_path("llm_enrichment_mock_write.db")
        init_db(db_path)
        document = _raw_document()
        analysis = _analysis_result("requires_attention")
        client = mock.Mock()
        client.analyze_document.return_value = analysis
        enricher = DocumentEnricher(
            enabled=True,
            provider=MockEnrichmentProvider(),
            provider_name="mock",
            model_name="mock-enrichment",
        )

        with mock.patch("app.pipeline.analyze.get_document_enricher", return_value=enricher):
            with mock.patch("app.pipeline.analyze.update_analysis"):
                processed = analyze_pipeline._analyze_documents(
                    [document],
                    client=client,
                    db_path=db_path,
                )

        self.assertEqual(processed, 1)
        self.assertEqual(count_document_enrichments(db_path=db_path), 1)
        enrichment = get_document_enrichment(
            document.url,
            provider="mock",
            model="mock-enrichment",
            db_path=db_path,
        )
        self.assertIsNotNone(enrichment)
        assert enrichment is not None
        self.assertTrue(enrichment["executive_summary"])

    def test_background_and_irrelevant_do_not_write_rows(self) -> None:
        db_path = self._db_path("llm_enrichment_non_eligible.db")
        init_db(db_path)
        document = _raw_document()
        client = mock.Mock()
        enricher = DocumentEnricher(
            enabled=True,
            provider=MockEnrichmentProvider(),
            provider_name="mock",
            model_name="mock-enrichment",
        )

        with mock.patch("app.pipeline.analyze.get_document_enricher", return_value=enricher):
            with mock.patch("app.pipeline.analyze.update_analysis"):
                client.analyze_document.return_value = _analysis_result("background")
                background_processed = analyze_pipeline._analyze_documents(
                    [document],
                    client=client,
                    db_path=db_path,
                )
                client.analyze_document.return_value = _analysis_result("irrelevant")
                irrelevant_processed = analyze_pipeline._analyze_documents(
                    [document],
                    client=client,
                    db_path=db_path,
                )

        self.assertEqual(background_processed, 1)
        self.assertEqual(irrelevant_processed, 1)
        self.assertEqual(count_document_enrichments(db_path=db_path), 0)

    def test_provider_failure_does_not_break_analyze_and_stores_error(self) -> None:
        db_path = self._db_path("llm_enrichment_provider_error.db")
        init_db(db_path)
        document = _raw_document()
        analysis = _analysis_result("watchlist")
        client = mock.Mock()
        client.analyze_document.return_value = analysis
        enricher = DocumentEnricher(
            enabled=True,
            provider=_FailingProvider(),
            provider_name="mock",
            model_name="mock-enrichment",
        )

        with mock.patch("app.pipeline.analyze.get_document_enricher", return_value=enricher):
            with mock.patch("app.pipeline.analyze.update_analysis"):
                processed = analyze_pipeline._analyze_documents(
                    [document],
                    client=client,
                    db_path=db_path,
                )

        self.assertEqual(processed, 1)
        enrichment = get_document_enrichment(
            document.url,
            provider="mock",
            model="mock-enrichment",
            db_path=db_path,
        )
        self.assertIsNotNone(enrichment)
        assert enrichment is not None
        self.assertIn("provider timeout", enrichment["error"] or "")


if __name__ == "__main__":
    unittest.main()
