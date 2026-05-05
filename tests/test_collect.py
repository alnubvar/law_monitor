from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

import requests

from app.models import CollectedItem, ExtractionResult, RawDocument, SourceConfig
from app.pipeline.collect import run_collect_with_options
from app.storage import (
    init_db,
    list_documents,
    list_latest_source_audit,
    list_recent_document_extraction_audit,
    save_document,
)


class CollectAuditTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path(f"data/test_artifacts/{name}")
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def test_extraction_audit_for_existing_document_without_duplicate_insert(self) -> None:
        db_path = self._db_path("collect_audit_existing.db")
        init_db(db_path)
        existing = RawDocument(
            source_name="Нормативные акты Краснодарского края",
            source_url="https://admkrai.krasnodar.ru/content/1291/",
            level="regional",
            region="krasnodar",
            title="Приказ о субсидии",
            url="https://example.com/subsidy.pdf",
            published_at=datetime.now(timezone.utc),
            collected_at=datetime.now(timezone.utc),
            content_hash="existing-hash",
            raw_text="Ранее сохраненный текст",
            document_type="pdf",
            status="collected",
        )
        save_document(existing, db_path)

        source_config = SourceConfig(
            name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/content/1291/",
            level="regional",
            region="krasnodar",
            source_role="regional_npa",
            parser="krasnodar",
            description="test",
        )
        item = CollectedItem(
            source_name=source_config.name,
            source_url=source_config.url,
            level=source_config.level,
            region=source_config.region,
            title="Приказ о субсидии",
            url="https://example.com/subsidy.pdf",
            document_type="pdf",
        )

        class FakeSource:
            def __init__(self) -> None:
                self.last_fetch_stats = {
                    "links_found_count": 1,
                    "links_filtered_count": 0,
                    "pdf_links_count": 1,
                }

            def fetch_items(self) -> list[CollectedItem]:
                return [item]

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                with patch(
                    "app.pipeline.collect.extract_document",
                    return_value=ExtractionResult(
                        raw_text="Обновленный текст",
                        document_type="pdf",
                        needs_ocr=False,
                        page_count=1,
                        extracted_text_length=15,
                    ),
                ):
                    saved_count = run_collect_with_options(
                        source_name=source_config.name,
                        limit=1,
                        audit_existing=True,
                        db_path=str(db_path),
                    )

        self.assertEqual(saved_count, 0)
        self.assertEqual(len(list_documents(db_path=db_path)), 1)
        audits = list_recent_document_extraction_audit(db_path=db_path, days=7)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["document_url"], "https://example.com/subsidy.pdf")

    def test_source_access_error_creates_audit_warning(self) -> None:
        db_path = self._db_path("collect_source_access_warning.db")
        init_db(db_path)
        source_config = SourceConfig(
            name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/35217/",
            level="regional",
            region="rostov",
            source_role="support_documents",
            parser="donland",
            description="test",
        )

        class FakeSource:
            def fetch_items(self) -> list[CollectedItem]:
                response = requests.Response()
                response.status_code = 403
                error = requests.exceptions.HTTPError("403 Client Error")
                error.response = response
                raise error

        with patch("app.pipeline.collect.load_sources", return_value=[source_config]):
            with patch("app.pipeline.collect.create_source", return_value=FakeSource()):
                saved_count = run_collect_with_options(
                    source_name=source_config.name,
                    audit_existing=False,
                    db_path=str(db_path),
                )

        self.assertEqual(saved_count, 0)
        audit_rows = list_latest_source_audit(db_path=db_path)
        self.assertEqual(len(audit_rows), 1)
        self.assertIn("source access blocked (HTTP 403)", audit_rows[0].get("error_message") or "")


if __name__ == "__main__":
    unittest.main()
