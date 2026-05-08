from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from app.llm.enrichment import DocumentEnricher
from app.models import AnalysisResult, RawDocument
from app.pipeline import analyze as analyze_pipeline
from app.storage import get_runtime_event, init_db


class AnalyzeTelemetryTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path(f"data/test_artifacts/{name}")
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _document(self) -> RawDocument:
        now = datetime.now(timezone.utc)
        return RawDocument(
            id=1,
            source_name="Минсельхоз России",
            source_url="https://mcx.gov.ru/",
            level="federal",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://example.com/doc",
            published_at=now,
            collected_at=now,
            content_hash="hash-1",
            raw_text="Прием заявок открыт.",
            status="collected",
        )

    def _analysis(
        self,
        *,
        action_level: str,
        page_type: str,
        deadline_text: str | None,
    ) -> AnalysisResult:
        return AnalysisResult(
            is_relevant=True,
            relevance_reason="reason",
            normalized_title="Льготное кредитование АПК",
            topic="support",
            importance="high" if action_level == "requires_attention" else "medium",
            action_level=action_level,
            page_type=page_type,
            summary="summary",
            impact="impact",
            deadline_text=deadline_text,
        )

    def test_deadline_extraction_success_increments_found_telemetry(self) -> None:
        db_path = self._db_path("analyze_deadline_found.db")
        init_db(db_path)
        document = self._document()
        client = mock.Mock()
        client.analyze_document.return_value = self._analysis(
            action_level="requires_attention",
            page_type="selection_announcement",
            deadline_text="Прием заявок до 30 июня 2026 года.",
        )

        with mock.patch(
            "app.pipeline.analyze.get_document_enricher",
            return_value=DocumentEnricher(enabled=False),
        ):
            analyze_pipeline._analyze_documents([document], client=client, db_path=db_path)

        event = get_runtime_event("deadline_extraction", db_path=db_path)
        self.assertIsNotNone(event)
        payload = json.loads(event["details"])
        self.assertEqual(payload["attempted"], 1)
        self.assertEqual(payload["found"], 1)
        self.assertEqual(payload["missing"], 0)
        self.assertEqual(payload["urgent_missing"], 0)

    def test_deadline_extraction_missing_increments_urgent_missing_telemetry(self) -> None:
        db_path = self._db_path("analyze_deadline_missing.db")
        init_db(db_path)
        document = self._document()
        client = mock.Mock()
        client.analyze_document.return_value = self._analysis(
            action_level="requires_attention",
            page_type="new_rule",
            deadline_text=None,
        )

        with mock.patch(
            "app.pipeline.analyze.get_document_enricher",
            return_value=DocumentEnricher(enabled=False),
        ):
            analyze_pipeline._analyze_documents([document], client=client, db_path=db_path)

        event = get_runtime_event("deadline_extraction", db_path=db_path)
        self.assertIsNotNone(event)
        payload = json.loads(event["details"])
        self.assertEqual(payload["attempted"], 1)
        self.assertEqual(payload["found"], 0)
        self.assertEqual(payload["missing"], 1)
        self.assertEqual(payload["urgent_missing"], 1)
        self.assertEqual(payload["missing_by_source"], {"Минсельхоз России": 1})
        self.assertEqual(payload["missing_by_page_type"], {"new_rule": 1})

    def test_deadline_extraction_telemetry_accumulates_across_analyze_runs(self) -> None:
        db_path = self._db_path("analyze_deadline_accumulates.db")
        init_db(db_path)
        document = self._document()
        client = mock.Mock()

        with mock.patch(
            "app.pipeline.analyze.get_document_enricher",
            return_value=DocumentEnricher(enabled=False),
        ):
            client.analyze_document.return_value = self._analysis(
                action_level="watchlist",
                page_type="news_background",
                deadline_text=None,
            )
            analyze_pipeline._analyze_documents([document], client=client, db_path=db_path)
            client.analyze_document.return_value = self._analysis(
                action_level="requires_attention",
                page_type="selection_announcement",
                deadline_text="До 15 июня 2026 года.",
            )
            analyze_pipeline._analyze_documents([document], client=client, db_path=db_path)

        event = get_runtime_event("deadline_extraction", db_path=db_path)
        self.assertIsNotNone(event)
        payload = json.loads(event["details"])
        self.assertEqual(payload["attempted"], 2)
        self.assertEqual(payload["found"], 1)
        self.assertEqual(payload["missing"], 1)
        self.assertEqual(payload["urgent_missing"], 0)


if __name__ == "__main__":
    unittest.main()
