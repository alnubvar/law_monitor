from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.models import RawDocument
from app.notify import telegram
from app.pipeline.tracking import run_check_tracked
from app.storage import get_document_by_url, init_db, save_document


class TrackingPipelineTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path("data/test_artifacts") / name
        if path.exists():
            path.unlink()
        return path

    def _doc(self, *, title: str, summary: str = "summary") -> RawDocument:
        now = datetime.now(timezone.utc)
        return RawDocument(
            id=1,
            source_name="ГИСП - меры поддержки АПК",
            source_url="https://gisp.gov.ru/nmp/measure/9564204",
            level="federal",
            region="federal",
            title=title,
            url="https://gisp.gov.ru/nmp/measure/9564204",
            published_at=now,
            collected_at=now,
            content_hash=f"hash-{title}",
            raw_text="text",
            is_relevant=True,
            relevance_reason="reason",
            topic="topic",
            importance="high",
            action_level="requires_attention",
            page_type="measure_card",
            summary=summary,
            support_status="active",
            is_active=True,
            application_status="regular",
            status="analyzed",
        )

    def _count_tracking_events(self, db_path: Path) -> int:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute("SELECT COUNT(*) FROM tracking_events").fetchone()
        assert row is not None
        return int(row[0])

    def test_check_tracked_creates_event_when_hash_changes(self) -> None:
        db_path = self._db_path("tracking_changed.db")
        init_db(db_path)
        save_document(self._doc(title="Льготное кредитование АПК", summary="старое"), db_path)
        telegram.build_command_response(
            "/track https://gisp.gov.ru/nmp/measure/9564204",
            db_path=db_path,
            chat_id="123",
        )

        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE documents SET summary = ? WHERE url = ?",
                ("новое описание", "https://gisp.gov.ru/nmp/measure/9564204"),
            )
            connection.commit()

        with patch("app.pipeline.tracking._refresh_document_for_tracking") as refresh:
            refresh.side_effect = lambda **kwargs: get_document_by_url(
                kwargs["document_url"], db_path=kwargs["db_path"]
            )
            result = run_check_tracked(db_path=db_path, notify=False)

        self.assertEqual(result.changed, 1)
        self.assertEqual(self._count_tracking_events(db_path), 1)

    def test_check_tracked_no_event_when_unchanged(self) -> None:
        db_path = self._db_path("tracking_unchanged.db")
        init_db(db_path)
        save_document(self._doc(title="Льготное кредитование АПК"), db_path)
        telegram.build_command_response(
            "/track https://gisp.gov.ru/nmp/measure/9564204",
            db_path=db_path,
            chat_id="123",
        )

        with patch("app.pipeline.tracking._refresh_document_for_tracking") as refresh:
            refresh.side_effect = lambda **kwargs: get_document_by_url(
                kwargs["document_url"], db_path=kwargs["db_path"]
            )
            result = run_check_tracked(db_path=db_path, notify=False)

        self.assertEqual(result.changed, 0)
        self.assertEqual(self._count_tracking_events(db_path), 0)


if __name__ == "__main__":
    unittest.main()
