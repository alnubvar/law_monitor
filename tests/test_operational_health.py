from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.models import RawDocument
from app.notify.telegram import build_command_response
from app.notify.telegram_formatter import build_digest_message
from app.operational_health import (
    OperationalNotice,
    collect_operational_notices,
)
from app.pipeline.digest import run_demo_report
from app.storage import (
    init_db,
    save_document,
    save_source_error,
    upsert_ocr_queue_item,
)


class OperationalHealthTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path("data/test_artifacts") / name
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _doc(
        self,
        *,
        doc_id: int,
        source_name: str,
        region: str,
        title: str,
        url: str,
        action_level: str,
        page_type: str,
        days_ago: int = 0,
    ) -> RawDocument:
        now = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return RawDocument(
            id=doc_id,
            source_name=source_name,
            source_url=url,
            level="federal" if region == "federal" else "regional",
            region=region,
            title=title,
            url=url,
            published_at=now,
            collected_at=now,
            content_hash=f"operational-{doc_id}",
            raw_text="text",
            is_relevant=action_level != "irrelevant",
            relevance_reason="reason",
            importance="high" if action_level == "requires_attention" else "medium",
            action_level=action_level,
            page_type=page_type,
            summary="summary",
            impact="impact",
            topic="topic",
            status="analyzed",
        )

    def test_stale_news_source_detection(self) -> None:
        db_path = self._db_path("operational_stale_news.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Старая новость",
                url="https://www.zol.ru/n/old",
                action_level="watchlist",
                page_type="news_background",
                days_ago=5,
            ),
            db_path,
        )

        notices = collect_operational_notices(db_path=db_path)

        self.assertTrue(
            any("ZOL.ru - зерновые новости: источник не обновлялся 5 дней" == notice.message for notice in notices)
        )

    def test_stale_support_source_detection(self) -> None:
        db_path = self._db_path("operational_stale_support.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="Минсельхоз Ставропольского края - господдержка",
                region="stavropol",
                title="Старый документ господдержки",
                url="https://mshsk.ru/old",
                action_level="watchlist",
                page_type="selection_announcement",
                days_ago=8,
            ),
            db_path,
        )

        notices = collect_operational_notices(db_path=db_path)

        self.assertTrue(
            any(
                "Минсельхоз Ставропольского края - господдержка: источник не обновлялся 8 дней"
                == notice.message
                for notice in notices
            )
        )

    def test_source_error_detection(self) -> None:
        db_path = self._db_path("operational_source_error.db")
        init_db(db_path)
        save_source_error(
            "Право Ставропольского края",
            "https://pravo.stavregion.ru",
            "403 Client Error",
            db_path=db_path,
        )

        notices = collect_operational_notices(db_path=db_path)

        self.assertTrue(
            any(
                notice.severity == "warning"
                and notice.message == "Право Ставропольского края: были ошибки доступа за последние 24 часа"
                for notice in notices
            )
        )

    def test_ocr_backlog_warning(self) -> None:
        db_path = self._db_path("operational_ocr_backlog.db")
        init_db(db_path)
        for index in range(1, 8):
            upsert_ocr_queue_item(
                document_url=f"https://example.com/ocr-{index}.pdf",
                source_name="Нормативные акты Краснодарского края",
                title=f"Скан {index}",
                priority="medium",
                reason="scan_candidate_pdf",
                db_path=db_path,
            )

        notices = collect_operational_notices(db_path=db_path)

        self.assertTrue(
            any(
                notice.severity == "info"
                and notice.message == "OCR queue: в очереди 7 документов на обработку"
                for notice in notices
            )
        )

    def test_no_notices_when_healthy(self) -> None:
        db_path = self._db_path("operational_healthy.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Свежая новость",
                url="https://www.zol.ru/n/fresh",
                action_level="watchlist",
                page_type="news_background",
                days_ago=0,
            ),
            db_path,
        )

        notices = collect_operational_notices(db_path=db_path)

        self.assertEqual(notices, [])

    def test_report_rendering_includes_operational_notices(self) -> None:
        db_path = self._db_path("operational_report_render.db")
        output_path = Path("data/test_artifacts/operational_report_render.md")
        if output_path.exists():
            output_path.unlink()
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Старая новость",
                url="https://www.zol.ru/n/old-report",
                action_level="watchlist",
                page_type="news_background",
                days_ago=5,
            ),
            db_path,
        )

        path = run_demo_report(db_path=db_path, output_path=str(output_path))
        markdown = path.read_text(encoding="utf-8")

        self.assertIn("## ⚠️ На что обратить внимание по системе", markdown)
        self.assertIn("ZOL.ru - зерновые новости: источник не обновлялся 5 дней", markdown)

    def test_telegram_digest_rendering_includes_operational_notices(self) -> None:
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
        )
        document.notified = True

        text = build_digest_message(
            [document],
            operational_notices=[
                OperationalNotice(
                    severity="warning",
                    message="OCR queue: в очереди 7 документов на обработку",
                )
            ],
        )

        self.assertIn("⚠️ На что обратить внимание по системе", text)
        self.assertIn("OCR queue: в очереди 7 документов на обработку", text)

    def test_report_and_status_include_notices_but_today_urgent_watchlist_do_not(self) -> None:
        db_path = self._db_path("operational_command_visibility.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Пошлина на экспорт пшеницы останется нулевой",
                url="https://www.zol.ru/n/41337",
                action_level="watchlist",
                page_type="news_background",
            ),
            db_path,
        )
        for index in range(1, 8):
            upsert_ocr_queue_item(
                document_url=f"https://example.com/backlog-{index}.pdf",
                source_name="Нормативные акты Краснодарского края",
                title=f"Backlog {index}",
                priority="medium",
                reason="scan_candidate_pdf",
                db_path=db_path,
            )

        fresh_event = {"event_name": "collect", "updated_at": datetime.now(timezone.utc), "details": "saved=1"}
        with patch("app.notify.telegram.get_runtime_event", return_value=fresh_event):
            report_text = build_command_response("/report", db_path=db_path)
            status_text = build_command_response("/status", db_path=db_path)

        today_text = build_command_response("/today", db_path=db_path)
        urgent_text = build_command_response("/urgent", db_path=db_path)
        watchlist_text = build_command_response("/watchlist", db_path=db_path)

        self.assertIn("На что обратить внимание по системе", report_text)
        self.assertIn("OCR queue: в очереди 7 документов на обработку", report_text)
        self.assertIn("На что обратить внимание по системе", status_text)
        self.assertNotIn("На что обратить внимание по системе", today_text)
        self.assertNotIn("На что обратить внимание по системе", urgent_text)
        self.assertNotIn("На что обратить внимание по системе", watchlist_text)


if __name__ == "__main__":
    unittest.main()
