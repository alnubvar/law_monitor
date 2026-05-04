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

        with closing(sqlite3.connect(db_path)) as connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(documents)").fetchall()
            }
        self.assertIn("page_type", columns)
        self.assertIn("support_status", columns)
        self.assertIn("application_status", columns)
        self.assertIn("terms_text", columns)

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
