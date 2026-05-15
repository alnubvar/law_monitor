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
    mark_runtime_event,
    save_document,
    save_source_audit_record,
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
        stale_now = datetime.now(timezone.utc) - timedelta(days=5)
        save_source_audit_record(
            source_name="ZOL.ru - зерновые новости",
            source_url="https://www.zol.ru",
            enabled=True,
            attempted_at=stale_now,
            success_at=stale_now,
            error_at=None,
            error_message=None,
            fetched_count=1,
            saved_count=0,
            existing_count=1,
            duplicates_count=0,
            item_errors_count=0,
            db_path=db_path,
        )
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
            any(
                "ZOL.ru - зерновые новости: последнее успешное обновление было 5 дней назад"
                in notice.message
                for notice in notices
            )
        )

    def test_stale_support_source_detection(self) -> None:
        db_path = self._db_path("operational_stale_support.db")
        init_db(db_path)
        stale_now = datetime.now(timezone.utc) - timedelta(days=8)
        save_source_audit_record(
            source_name="Минсельхоз Ставропольского края - господдержка",
            source_url="https://mshsk.ru",
            enabled=True,
            attempted_at=stale_now,
            success_at=stale_now,
            error_at=None,
            error_message=None,
            fetched_count=1,
            saved_count=0,
            existing_count=1,
            duplicates_count=0,
            item_errors_count=0,
            db_path=db_path,
        )
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
                "Минсельхоз Ставропольского края - господдержка: последнее успешное обновление было 8 дней назад"
                in notice.message
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
                and "Источник временно недоступен: Право Ставропольского края" in notice.message
                for notice in notices
            )
        )

    def test_latest_failed_source_remains_visible_after_error_lookback(self) -> None:
        db_path = self._db_path("operational_failed_source_persists.db")
        init_db(db_path)
        success_at = datetime.now(timezone.utc) - timedelta(days=2)
        failed_at = success_at + timedelta(hours=1)
        save_source_audit_record(
            source_name="Минсельхоз России - меры господдержки",
            source_url="https://mcx.gov.ru/activity/state-support/measures/",
            enabled=True,
            attempted_at=success_at,
            success_at=success_at,
            error_at=None,
            error_message=None,
            fetched_count=20,
            saved_count=1,
            existing_count=19,
            duplicates_count=0,
            item_errors_count=0,
            db_path=db_path,
        )
        save_source_audit_record(
            source_name="Минсельхоз России - меры господдержки",
            source_url="https://mcx.gov.ru/activity/state-support/measures/",
            enabled=True,
            attempted_at=failed_at,
            success_at=None,
            error_at=failed_at,
            error_message="source connection error",
            fetched_count=0,
            saved_count=0,
            existing_count=0,
            duplicates_count=0,
            item_errors_count=0,
            db_path=db_path,
        )

        notices = collect_operational_notices(db_path=db_path)

        self.assertTrue(
            any(
                "Источник временно недоступен: Минсельхоз России - меры господдержки"
                in notice.message
                for notice in notices
            )
        )

    def test_item_processing_errors_do_not_mark_successful_source_unavailable(self) -> None:
        db_path = self._db_path("operational_item_errors_not_unavailable.db")
        init_db(db_path)
        now = datetime.now(timezone.utc)
        save_source_error(
            "Минсельхоз Ростовской области - господдержка",
            "https://mcx.donland.ru/activity/35217/",
            "Item processing errors: 1",
            db_path=db_path,
        )
        save_source_audit_record(
            source_name="Минсельхоз Ростовской области - господдержка",
            source_url="https://mcx.donland.ru/activity/35217/",
            enabled=True,
            attempted_at=now,
            success_at=now,
            error_at=now,
            error_message="Item processing errors: 1",
            fetched_count=30,
            saved_count=26,
            existing_count=3,
            duplicates_count=0,
            item_errors_count=1,
            db_path=db_path,
        )

        notices = collect_operational_notices(db_path=db_path)

        self.assertFalse(
            any(
                "Источник временно недоступен: Минсельхоз Ростовской области - господдержка"
                in notice.message
                for notice in notices
            )
        )

    def test_old_source_with_no_success_creates_no_success_warning(self) -> None:
        db_path = self._db_path("operational_no_success_source.db")
        init_db(db_path)
        attempted_at = datetime.now(timezone.utc) - timedelta(days=8)
        save_source_audit_record(
            source_name="Минсельхоз Ставропольского края - господдержка",
            source_url="https://mshsk.ru",
            enabled=True,
            attempted_at=attempted_at,
            success_at=None,
            error_at=attempted_at,
            error_message="source runtime error",
            fetched_count=0,
            saved_count=0,
            existing_count=0,
            duplicates_count=0,
            item_errors_count=0,
            db_path=db_path,
        )

        notices = collect_operational_notices(db_path=db_path)

        self.assertTrue(
            any(
                "Минсельхоз Ставропольского края - господдержка: успешный сбор не подтвержден 8 дней"
                in notice.message
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
        now = datetime.now(timezone.utc)
        save_source_audit_record(
            source_name="ZOL.ru - зерновые новости",
            source_url="https://www.zol.ru",
            enabled=True,
            attempted_at=now,
            success_at=now,
            error_at=None,
            error_message=None,
            fetched_count=1,
            saved_count=0,
            existing_count=1,
            duplicates_count=0,
            item_errors_count=0,
            db_path=db_path,
        )
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

    def test_fresh_success_without_new_saved_documents_does_not_create_stale_warning(self) -> None:
        db_path = self._db_path("operational_fresh_success_no_saved_docs.db")
        init_db(db_path)
        now = datetime.now(timezone.utc)
        save_source_audit_record(
            source_name="Regulation.gov.ru",
            source_url="https://regulation.gov.ru",
            enabled=True,
            attempted_at=now,
            success_at=now,
            error_at=None,
            error_message=None,
            fetched_count=12,
            saved_count=0,
            existing_count=12,
            duplicates_count=0,
            item_errors_count=0,
            db_path=db_path,
        )

        notices = collect_operational_notices(db_path=db_path)

        self.assertFalse(any("источник не обновлялся" in notice.message for notice in notices))
        self.assertFalse(any("нет успешного сбора" in notice.message for notice in notices))

    def test_report_rendering_includes_operational_notices(self) -> None:
        db_path = self._db_path("operational_report_render.db")
        output_path = Path("data/test_artifacts/operational_report_render.md")
        if output_path.exists():
            output_path.unlink()
        init_db(db_path)
        stale_now = datetime.now(timezone.utc) - timedelta(days=5)
        save_source_audit_record(
            source_name="ZOL.ru - зерновые новости",
            source_url="https://www.zol.ru",
            enabled=True,
            attempted_at=stale_now,
            success_at=stale_now,
            error_at=None,
            error_message=None,
            fetched_count=1,
            saved_count=0,
            existing_count=1,
            duplicates_count=0,
            item_errors_count=0,
            db_path=db_path,
        )
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
        self.assertIn(
            "ZOL.ru - зерновые новости: последнее успешное обновление было 5 дней назад",
            markdown,
        )

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

    def test_status_does_not_mark_fresh_when_source_success_is_stale(self) -> None:
        db_path = self._db_path("operational_status_stale_source_success.db")
        init_db(db_path)
        stale_success = datetime.now(timezone.utc) - timedelta(hours=30)
        save_source_audit_record(
            source_name="Regulation.gov.ru",
            source_url="https://regulation.gov.ru/",
            enabled=True,
            attempted_at=stale_success,
            success_at=stale_success,
            error_at=None,
            error_message=None,
            fetched_count=20,
            saved_count=0,
            existing_count=20,
            duplicates_count=0,
            item_errors_count=0,
            db_path=db_path,
        )
        mark_runtime_event(
            "report",
            details="fresh report",
            occurred_at=datetime.now(timezone.utc),
            db_path=db_path,
        )

        status_text = build_command_response("/status", db_path=db_path)

        self.assertIn("Данные могут быть не полностью свежими", status_text)
        self.assertNotIn("✅ Данные свежие", status_text)


if __name__ == "__main__":
    unittest.main()
