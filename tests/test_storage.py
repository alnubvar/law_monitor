from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import storage
from app.models import AnalysisResult, RawDocument
from app.storage import init_db


class StorageSmokeTest(unittest.TestCase):
    def test_init_db_creates_tables(self) -> None:
        db_path = Path("data/test_artifacts/test.db")
        if db_path.exists():
            db_path.unlink()

        init_db(db_path)

        self.assertTrue(db_path.exists())

        with closing(sqlite3.connect(db_path)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        self.assertIn("documents", tables)
        self.assertIn("source_errors", tables)
        self.assertIn("source_audit", tables)
        self.assertIn("document_extraction_audit", tables)
        self.assertIn("ocr_queue", tables)
        self.assertIn("document_enrichments", tables)

        with closing(sqlite3.connect(db_path)) as connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(documents)").fetchall()
            }
        self.assertIn("page_type", columns)
        self.assertIn("support_status", columns)
        self.assertIn("application_status", columns)
        self.assertIn("terms_text", columns)

        with closing(sqlite3.connect(db_path)) as connection:
            enrichment_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(document_enrichments)"
                ).fetchall()
            }
        self.assertIn("prompt_version", enrichment_columns)
        self.assertIn("status", enrichment_columns)
        self.assertIn("facts_json", enrichment_columns)
        self.assertIn("source_hash", enrichment_columns)
        self.assertIn("enriched_at", enrichment_columns)

    def test_ocr_queue_upsert_and_status_update(self) -> None:
        db_path = Path("data/test_artifacts/test_ocr_queue.db")
        if db_path.exists():
            db_path.unlink()
        init_db(db_path)

        first_id = storage.upsert_ocr_queue_item(
            document_url="https://example.com/scan.pdf",
            source_name="Нормативные акты Краснодарского края",
            title="Скан приказа",
            priority="high",
            reason="scan_candidate_pdf",
            db_path=db_path,
        )
        second_id = storage.upsert_ocr_queue_item(
            document_url="https://example.com/scan.pdf",
            source_name="Нормативные акты Краснодарского края",
            title="Скан приказа обновлен",
            priority="high",
            reason="scan_candidate_pdf",
            db_path=db_path,
        )

        self.assertEqual(first_id, second_id)
        rows = storage.list_ocr_queue(db_path=db_path, statuses=["pending"], limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["priority"], "high")

        updated = storage.update_ocr_queue_status(
            document_url="https://example.com/scan.pdf",
            status="done",
            notes="checked",
            db_path=db_path,
        )
        self.assertTrue(updated)
        summary = storage.summarize_ocr_queue(db_path=db_path)
        self.assertEqual(summary["pending"], 0)
        self.assertEqual(summary["done_skipped"], 1)

    def test_init_db_closes_sqlite_connections(self) -> None:
        original_connect = sqlite3.connect

        class TrackingConnection(sqlite3.Connection):
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)
                self.closed_called = False

            def close(self) -> None:
                self.closed_called = True
                super().close()

        created_connections: list[TrackingConnection] = []

        def _connect(*args: object, **kwargs: object) -> TrackingConnection:
            connection = original_connect(":memory:", factory=TrackingConnection)
            created_connections.append(connection)
            return connection

        with patch("app.storage.sqlite3.connect", side_effect=_connect):
            storage.init_db(Path("data/test_artifacts/tracking.db"))

        self.assertTrue(created_connections)
        self.assertTrue(all(connection.closed_called for connection in created_connections))

    def test_runtime_connections_apply_production_sqlite_pragmas(self) -> None:
        db_path = Path("data/test_artifacts/test_sqlite_pragmas.db")
        if db_path.exists():
            db_path.unlink()
        init_db(db_path)

        settings = storage.get_sqlite_runtime_settings(db_path)

        self.assertEqual(settings["journal_mode"].lower(), "wal")
        self.assertEqual(settings["busy_timeout"], 5000)
        self.assertEqual(settings["synchronous"], "NORMAL")
        self.assertEqual(settings["foreign_keys"], 1)

    def test_update_analysis_persists_normalized_title(self) -> None:
        db_path = Path("data/test_artifacts/test_update_analysis.db")
        if db_path.exists():
            db_path.unlink()
        init_db(db_path)

        document = RawDocument(
            source_name="Нормативные акты Краснодарского края",
            source_url="https://admkrai.krasnodar.ru/upload/test.pdf",
            level="regional",
            region="krasnodar",
            title="Просмотр",
            url="https://admkrai.krasnodar.ru/upload/test.pdf",
            content_hash="hash-title-normalized",
            raw_text="Текст документа",
        )
        document_id = storage.save_document(document, db_path)

        analysis = AnalysisResult(
            is_relevant=True,
            relevance_reason="reason",
            normalized_title="О внесении изменений в приказ министерства",
            topic="topic",
            importance="medium",
            action_level="watchlist",
            page_type="news_background",
            summary="summary",
            impact="impact",
        )

        storage.update_analysis(document_id, analysis, db_path)

        stored_documents = storage.list_documents(db_path)
        self.assertEqual(len(stored_documents), 1)
        self.assertEqual(
            stored_documents[0].title,
            "О внесении изменений в приказ министерства",
        )


if __name__ == "__main__":
    unittest.main()
