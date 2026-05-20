from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from app.llm.enrichment import (
    DOCUMENT_CARD_PROMPT_VERSION,
    DocumentCardFacts,
    DocumentEnricher,
    EnrichmentResult,
    MockEnrichmentProvider,
    OpenAICompatibleEnrichmentProvider,
    build_document_card_input,
    build_document_enricher,
    compute_document_card_source_hash,
    get_display_enrichment,
    is_enrichment_eligible,
)
from app.llm.prompts import DOCUMENT_CARD_SYSTEM_PROMPT
from app.models import AnalysisResult, RawDocument
from app.pipeline.enrich import run_enrich_docs
from app.pipeline import analyze as analyze_pipeline
from app.storage import (
    count_document_enrichments,
    get_document_enrichment,
    init_db,
    list_documents,
    save_document,
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


def _analyzed_raw_document(*, doc_id: int = 1, url: str = "https://example.test/doc") -> RawDocument:
    analysis = _analysis_result("requires_attention")
    document = _raw_document().model_copy(
        update={
            "id": doc_id,
            "url": url,
            "title": "О проведении отбора на предоставление субсидии",
            "raw_text": (
                "Открыт прием заявок на предоставление субсидии "
                "сельхозтоваропроизводителям. Срок подачи заявок до 1 июня 2026 года."
            ),
            "is_relevant": analysis.is_relevant,
            "relevance_reason": analysis.relevance_reason,
            "importance": analysis.importance,
            "action_level": analysis.action_level,
            "page_type": analysis.page_type,
            "summary": analysis.summary,
            "impact": analysis.impact,
            "application_status": analysis.application_status,
            "deadline_text": analysis.deadline_text,
            "status": "analyzed",
        }
    )
    return document


class _SpyProvider(MockEnrichmentProvider):
    def __init__(self) -> None:
        self.calls = 0

    def enrich_document(self, **kwargs) -> EnrichmentResult:
        self.calls += 1
        return super().enrich_document(**kwargs)


class _FailingProvider(MockEnrichmentProvider):
    def enrich_document(self, **kwargs) -> EnrichmentResult:
        raise RuntimeError("provider timeout")


class _CountingProvider(MockEnrichmentProvider):
    def __init__(self) -> None:
        self.calls = 0

    def enrich_document(self, **kwargs) -> EnrichmentResult:
        self.calls += 1
        return super().enrich_document(**kwargs)


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

    def test_mock_provider_requires_no_real_api_key(self) -> None:
        with mock.patch("app.config.LLM_DOCUMENT_ENRICHMENT_ENABLED", True):
            with mock.patch("app.config.LLM_PROVIDER", "mock"):
                with mock.patch("app.config.LLM_API_KEY", ""):
                    enricher = build_document_enricher()

        result = enricher.maybe_enrich_document(
            title="Тест",
            raw_text="Открыт прием заявок на субсидию.",
            analysis=_analysis_result("requires_attention"),
            source_name="Источник",
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsNone(result.error)

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
        self.assertNotIn("AI-", result.executive_summary)
        self.assertNotIn("AI-", result.business_impact)
        self.assertNotIn("AI-", result.recommended_action)
        self.assertEqual(
            result.executive_summary,
            "Нужно проверить применимость меры, условия участия и рабочие сроки.",
        )
        self.assertEqual(result.deadline_hint, "Срок: до 01.06.2026")
        self.assertGreaterEqual(result.confidence or 0.0, 0.8)
        facts = result.facts_payload()
        self.assertIsInstance(facts, dict)
        assert facts is not None
        self.assertEqual(facts["document_type"], "отбор")
        self.assertEqual(facts["status"], "прием открыт")
        self.assertEqual(facts["deadline"], "2026-06-01")
        self.assertIsNone(facts["support_type"])
        self.assertEqual(facts["confidence"], "high")
        self.assertTrue(facts["source_quotes"])

    def test_mock_provider_handles_raw_document_without_key_dates(self) -> None:
        provider = MockEnrichmentProvider()
        document = _analyzed_raw_document().model_copy(
            update={
                "deadline_text": None,
                "summary": "Открыт прием заявок на получение субсидии.",
            }
        )

        result = provider.enrich_document(
            title=document.title,
            raw_text=document.raw_text,
            analysis=document,
            source_name=document.source_name,
            url=document.url,
            region=document.region,
        )

        self.assertIsNone(result.error)
        self.assertIsNone(result.deadline_hint)
        self.assertIsNotNone(result.facts_payload())

    def test_mock_enrichment_uses_safe_generic_regional_npa_summary(self) -> None:
        analysis = _analysis_result("requires_attention").model_copy(
            update={
                "page_type": "new_rule",
                "summary": "МИНИСТЕРСТВО ФИЗИЧЕСКОЙ КУЛЬТУРЫ И СПОРТА ... OCR шум",
            }
        )
        provider = MockEnrichmentProvider()

        result = provider.enrich_document(
            title="О внесении изменений в порядок предоставления субсидий",
            raw_text="OCR NOISE",
            analysis=analysis,
            source_name="Право Ставропольского края",
            region="stavropol",
        )

        self.assertEqual(
            result.executive_summary,
            "Документ меняет действующий порядок поддержки; нужно проверить, что именно изменилось для получателей и сроков.",
        )
        self.assertNotIn("МИНИСТЕРСТВО ФИЗИЧЕСКОЙ КУЛЬТУРЫ", result.executive_summary)

    def test_display_enrichment_strips_legacy_ai_prefixes(self) -> None:
        display = get_display_enrichment(
            {
                "executive_summary": "AI-сводка: Кратко для пользователя.",
                "business_impact": "AI-оценка влияния: Влияние на условия участия.",
                "recommended_action": "AI-рекомендация: Проверить сроки подачи.",
                "deadline_hint": "AI-сводка: До 1 июня 2026 года.",
                "confidence": 0.8,
                "error": None,
            }
        )

        self.assertIsNotNone(display)
        assert display is not None
        self.assertEqual(display["executive_summary"], "Кратко для пользователя.")
        self.assertEqual(display["business_impact"], "Влияние на условия участия.")
        self.assertEqual(display["recommended_action"], "Проверить сроки подачи.")
        self.assertEqual(display["deadline_hint"], "До 1 июня 2026 года.")

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

    def test_provider_failure_returns_fallback_facts(self) -> None:
        enricher = DocumentEnricher(enabled=True, provider=_FailingProvider())

        result = enricher.maybe_enrich_document(
            title="Тест",
            raw_text="Текст",
            analysis=_analysis_result("watchlist"),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("provider timeout", result.error or "")
        self.assertEqual(result.status, "fallback")
        self.assertGreater(result.confidence or 0.0, 0.0)
        self.assertTrue(result.executive_summary)

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

    def test_display_enrichment_accepts_fallback_rows_with_facts(self) -> None:
        display = get_display_enrichment(
            {
                "status": "fallback",
                "error": "RuntimeError: timeout",
                "facts_json": {
                    "document_type": "проект НПА",
                    "status": "проект обсуждается",
                    "deadline": "2026-06-30",
                    "short_summary": "Проект НПА вынесен на публичное обсуждение.",
                    "why_matters": "Нужно заранее оценить влияние на порядок поддержки.",
                    "what_to_check": "Проверить проект и при необходимости подготовить позицию.",
                    "confidence": "high",
                    "source_quotes": ["публичное обсуждение"],
                },
                "confidence": 0.8,
            }
        )

        self.assertIsNotNone(display)
        assert display is not None
        self.assertEqual(display["deadline_hint"], "Конец обсуждения: 30.06.2026")

    def test_display_enrichment_filters_raw_synthetic_fields(self) -> None:
        display = get_display_enrichment(
            {
                "executive_summary": "title: Мера shortName: Поддержка",
                "business_impact": "endDate: 2026-06-30",
                "recommended_action": "acceptingApplicationsInfo: прием идет",
                "confidence": 0.9,
                "error": None,
            }
        )
        self.assertIsNone(display)

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

    def test_invalid_json_from_openai_compatible_provider_becomes_fallback(self) -> None:
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
        self.assertEqual(result.status, "fallback")
        self.assertTrue(result.executive_summary)

    def test_openai_compatible_provider_extracts_json_from_code_fence(self) -> None:
        provider = OpenAICompatibleEnrichmentProvider(
            base_url="http://127.0.0.1:1234/v1",
            api_key="",
            model="test-model",
            timeout_seconds=5,
        )
        payload = """```json
{"document_type":"проект НПА","region":"РФ","authority":"Минсельхоз России","status":"проект обсуждается","deadline":"2026-06-30","effective_date":null,"support_type":"субсидия","target_recipients":["сельхозтоваропроизводители"],"what_changed":"Проект уточняет порядок предоставления субсидии.","why_matters":"Нужно заранее оценить влияние проекта на порядок поддержки.","what_to_check":"Проверить текст проекта и подготовить позицию при необходимости.","applicability_note":"Нужна отдельная проверка применимости к AHSTEP.","short_summary":"Проект НПА вынесен на публичное обсуждение.","confidence":"high","source_quotes":["Конец обсуждения","порядок предоставления субсидии"]}
```"""

        with mock.patch.object(
            provider,
            "_post_json",
            return_value={"choices": [{"message": {"content": payload}}]},
        ):
            result = provider.enrich_document(
                title="Тест",
                raw_text="Текст",
                analysis=_analysis_result("requires_attention"),
                source_name="Источник",
                url="https://regulation.gov.ru/projects/1",
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.deadline_hint, "Конец обсуждения: 30.06.2026")

    def test_weak_llm_json_uses_deterministic_fallback(self) -> None:
        provider = OpenAICompatibleEnrichmentProvider(
            base_url="http://127.0.0.1:1234/v1",
            api_key="",
            model="test-model",
            timeout_seconds=5,
        )
        enricher = DocumentEnricher(enabled=True, provider=provider)
        weak_payload = {
            "document_type": "отбор",
            "region": "РФ",
            "authority": "Минсельхоз России",
            "status": "прием открыт",
            "deadline": "2026-06-01",
            "effective_date": None,
            "support_type": "субсидия",
            "target_recipients": ["сельхозтоваропроизводители"],
            "what_changed": "Открыт прием заявок.",
            "why_matters": "Открыт прием заявок",
            "what_to_check": "Оставить в наблюдении до следующего подтверждающего обновления.",
            "applicability_note": "Проверить применимость.",
            "short_summary": "Открыт прием заявок на субсидию.",
            "confidence": "high",
            "source_quotes": ["Открыт прием заявок"],
        }

        with mock.patch.object(
            provider,
            "_post_json",
            return_value={"choices": [{"message": {"content": json.dumps(weak_payload, ensure_ascii=False)}}]},
        ):
            result = enricher.maybe_enrich_document(
                title="О проведении отбора на предоставление субсидии",
                raw_text="Открыт прием заявок на предоставление субсидии сельхозтоваропроизводителям.",
                analysis=_analysis_result("requires_attention"),
                source_name="Минсельхоз России",
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "fallback")
        self.assertIn("LLM enrichment fallback:", result.error or "")
        self.assertNotIn("Оставить в наблюдении", result.recommended_action or "")

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
        self.assertEqual(enrichment["status"], "fallback")

    def test_prompt_contains_no_hallucination_and_null_if_missing_rules(self) -> None:
        prompt = DOCUMENT_CARD_SYSTEM_PROMPT.lower()

        self.assertIn("не придумывай", prompt)
        self.assertIn("null", prompt)
        self.assertIn("неизвестно", prompt)
        self.assertIn("только исходный текст", prompt)
        self.assertIn("не меняй", prompt)
        self.assertIn("action_level", prompt)
        self.assertIn("regulation.gov.ru", prompt)
        self.assertIn("effective_date", prompt)

    def test_source_quotes_are_preserved_in_facts_json(self) -> None:
        db_path = self._db_path("llm_enrichment_source_quotes.db")
        init_db(db_path)
        facts = DocumentCardFacts(
            document_type="отбор",
            status="прием открыт",
            short_summary="Открыт прием заявок на субсидию.",
            why_matters="Нужно проверить применимость для GR.",
            what_to_check="Проверить условия участия.",
            confidence="high",
            source_quotes=["Открыт прием заявок", "сельхозтоваропроизводителям"],
        )
        save_document_enrichment(
            document_id=1,
            document_url="https://example.test/doc-quotes",
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult.from_facts(facts),
            db_path=db_path,
        )

        enrichment = get_document_enrichment(
            "https://example.test/doc-quotes",
            provider="mock",
            model="mock-enrichment",
            db_path=db_path,
        )

        self.assertIsNotNone(enrichment)
        assert enrichment is not None
        self.assertIn('"source_quotes"', enrichment["facts_json"])
        self.assertIn("Открыт прием заявок", enrichment["facts_json"])

    def test_enrichment_command_skips_cached_docs(self) -> None:
        db_path = self._db_path("llm_enrichment_command_cached.db")
        init_db(db_path)
        document = _analyzed_raw_document(url="https://example.test/cached")
        document_id = save_document(document, db_path=db_path)
        stored_document = list_documents(db_path=db_path)[0]
        prepared = build_document_card_input(
            title=stored_document.title,
            raw_text=stored_document.raw_text,
            analysis=stored_document,
            source_name=stored_document.source_name,
            url=stored_document.url,
            level=stored_document.level,
            region=stored_document.region,
            published_at=stored_document.published_at,
            document_type=stored_document.document_type,
        )
        source_hash = compute_document_card_source_hash(prepared)
        save_document_enrichment(
            document_id=document_id,
            document_url=stored_document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult.from_facts(
                DocumentCardFacts(
                    short_summary="Cached summary.",
                    why_matters="Cached impact.",
                    what_to_check="Cached action.",
                    confidence="high",
                    source_quotes=["Cached quote"],
                ),
                source_hash=source_hash,
            ),
            prompt_version=DOCUMENT_CARD_PROMPT_VERSION,
            source_hash=source_hash,
            db_path=db_path,
        )
        provider = _CountingProvider()
        enricher = DocumentEnricher(
            enabled=True,
            provider=provider,
            provider_name="mock",
            model_name="mock-enrichment",
        )

        with mock.patch("app.pipeline.enrich.get_document_enricher", return_value=enricher):
            result = run_enrich_docs(days=7, limit=5, db_path=db_path)

        self.assertEqual(result.selected, 1)
        self.assertEqual(result.skipped_cached, 1)
        self.assertEqual(result.enriched, 0)
        self.assertEqual(provider.calls, 0)

    def test_force_recomputes_cached_docs(self) -> None:
        db_path = self._db_path("llm_enrichment_command_force.db")
        init_db(db_path)
        document = _analyzed_raw_document(url="https://example.test/force")
        document_id = save_document(document, db_path=db_path)
        stored_document = list_documents(db_path=db_path)[0]
        prepared = build_document_card_input(
            title=stored_document.title,
            raw_text=stored_document.raw_text,
            analysis=stored_document,
            source_name=stored_document.source_name,
            url=stored_document.url,
            level=stored_document.level,
            region=stored_document.region,
            published_at=stored_document.published_at,
            document_type=stored_document.document_type,
        )
        source_hash = compute_document_card_source_hash(prepared)
        save_document_enrichment(
            document_id=document_id,
            document_url=stored_document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult.from_facts(
                DocumentCardFacts(
                    short_summary="Cached summary.",
                    why_matters="Cached impact.",
                    what_to_check="Cached action.",
                    confidence="high",
                    source_quotes=["Cached quote"],
                ),
                source_hash=source_hash,
            ),
            prompt_version=DOCUMENT_CARD_PROMPT_VERSION,
            source_hash=source_hash,
            db_path=db_path,
        )
        provider = _CountingProvider()
        enricher = DocumentEnricher(
            enabled=True,
            provider=provider,
            provider_name="mock",
            model_name="mock-enrichment",
        )

        with mock.patch("app.pipeline.enrich.get_document_enricher", return_value=enricher):
            result = run_enrich_docs(days=7, limit=5, force=True, db_path=db_path)

        self.assertEqual(result.selected, 1)
        self.assertEqual(result.skipped_cached, 0)
        self.assertEqual(result.enriched, 1)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(count_document_enrichments(db_path=db_path), 1)

    def test_action_level_is_not_changed_by_enrichment_command(self) -> None:
        db_path = self._db_path("llm_enrichment_action_level_unchanged.db")
        init_db(db_path)
        save_document(_analyzed_raw_document(url="https://example.test/action-level"), db_path=db_path)
        provider = _CountingProvider()
        enricher = DocumentEnricher(
            enabled=True,
            provider=provider,
            provider_name="mock",
            model_name="mock-enrichment",
        )

        with mock.patch("app.pipeline.enrich.get_document_enricher", return_value=enricher):
            result = run_enrich_docs(days=7, limit=5, db_path=db_path)

        documents = list_documents(db_path=db_path)
        self.assertEqual(result.enriched, 1)
        self.assertEqual(documents[0].action_level, "requires_attention")

    def test_enrichment_is_limited_to_selected_visible_docs(self) -> None:
        db_path = self._db_path("llm_enrichment_limited_visible.db")
        init_db(db_path)
        save_document(_analyzed_raw_document(doc_id=1, url="https://example.test/visible-1"), db_path=db_path)
        save_document(_analyzed_raw_document(doc_id=2, url="https://example.test/visible-2"), db_path=db_path)
        hidden = _analyzed_raw_document(doc_id=3, url="https://admkrai.krasnodar.ru/content/1270/").model_copy(
            update={
                "source_name": "Нормативные акты Краснодарского края",
                "region": "krasnodar",
                "title": "Формы документов, связанных с противодействием коррупции",
                "page_type": "reference_page",
                "action_level": "watchlist",
                "importance": "medium",
                "summary": "Формы документов по противодействию коррупции.",
            }
        )
        save_document(hidden, db_path=db_path)
        provider = _CountingProvider()
        enricher = DocumentEnricher(
            enabled=True,
            provider=provider,
            provider_name="mock",
            model_name="mock-enrichment",
        )

        with mock.patch("app.pipeline.enrich.get_document_enricher", return_value=enricher):
            result = run_enrich_docs(days=7, limit=1, db_path=db_path)

        self.assertEqual(result.selected, 1)
        self.assertEqual(result.enriched, 1)
        self.assertEqual(provider.calls, 1)


if __name__ == "__main__":
    unittest.main()
