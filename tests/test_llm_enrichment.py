from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app.llm.enrichment import (
    DocumentEnricher,
    EnrichmentResult,
    MockEnrichmentProvider,
    OpenAICompatibleEnrichmentProvider,
    build_document_enricher,
    is_enrichment_eligible,
)
from app.models import AnalysisResult, RawDocument
from app.pipeline import analyze as analyze_pipeline


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
        document = _raw_document()
        analysis = _analysis_result("requires_attention")
        client = mock.Mock()
        client.analyze_document.return_value = analysis
        enricher = DocumentEnricher(enabled=True, provider=_FailingProvider())

        with mock.patch(
            "app.pipeline.analyze.get_document_enricher",
            return_value=enricher,
        ):
            with mock.patch("app.pipeline.analyze.update_analysis") as update_analysis_mock:
                processed = analyze_pipeline._analyze_documents(
                    [document],
                    client=client,
                    db_path=Path("data/test_artifacts/llm_enrichment.db"),
                )

        self.assertEqual(processed, 1)
        update_analysis_mock.assert_called_once_with(
            document.id,
            analysis,
            db_path=Path("data/test_artifacts/llm_enrichment.db"),
        )


if __name__ == "__main__":
    unittest.main()
