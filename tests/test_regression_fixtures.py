from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_source_role, load_keyword_groups, load_keywords
from app.llm.mock_client import MockLLMClient
from app.models import RawDocument

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "regression"


def _load_regression_fixtures() -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        fixtures.append(json.loads(path.read_text(encoding="utf-8")))
    return fixtures


def _build_document(fixture: dict[str, Any]) -> RawDocument:
    now = datetime.now(timezone.utc)
    url = fixture["url"]
    return RawDocument(
        id=None,
        source_name=fixture["source_name"],
        source_url=url,
        level=fixture.get("level", "federal"),
        region=fixture.get("region", "federal"),
        title=fixture["title"],
        url=url,
        published_at=None,
        collected_at=now,
        content_hash=f"fixture-{fixture['id']}",
        raw_text=fixture["raw_text"],
        summary=fixture.get("summary"),
    )


class RegressionFixturesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = MockLLMClient(
            load_keywords(),
            keyword_groups=load_keyword_groups(),
        )

    def test_regression_fixtures_match_current_rule_behavior(self) -> None:
        fixtures = _load_regression_fixtures()
        self.assertGreaterEqual(len(fixtures), 8)

        seen_ids: set[str] = set()
        for fixture in fixtures:
            fixture_id = fixture["id"]
            with self.subTest(fixture=fixture_id):
                self.assertNotIn(fixture_id, seen_ids)
                seen_ids.add(fixture_id)

                document = _build_document(fixture)
                source_role = get_source_role(document.source_name)

                self.assertEqual(source_role, fixture["source_role"])
                self.assertEqual(source_role, fixture["expected_source_role"])

                result = self.client.analyze_document(
                    document.title,
                    document.raw_text,
                    source_name=document.source_name,
                    url=document.url,
                    level=document.level,
                    region=document.region,
                )

                self.assertEqual(result.action_level, fixture["expected_action_level"])
                self.assertEqual(result.page_type, fixture["expected_page_type"])

                expected_signal = fixture.get("expected_business_signal_contains")
                if expected_signal:
                    self.assertIn(expected_signal, result.business_signal or "")

                expected_not_action_level = fixture.get("expected_not_action_level")
                if expected_not_action_level:
                    self.assertNotEqual(result.action_level, expected_not_action_level)


if __name__ == "__main__":
    unittest.main()
