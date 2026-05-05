from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import requests

from app.models import RawDocument
from app.notify import telegram
from app.storage import init_db, save_document


class TelegramNotifySmokeTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path("data/test_artifacts") / name
        if path.exists():
            path.unlink()
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
        summary: str = "summary",
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
            content_hash=f"telegram-command-{doc_id}",
            raw_text="text",
            is_relevant=action_level != "irrelevant",
            relevance_reason="reason",
            importance="high" if action_level == "requires_attention" else "medium",
            action_level=action_level,
            page_type=page_type,
            summary=summary,
            impact="impact",
            topic="topic",
            status="analyzed",
        )

    def test_send_message_uses_proxy_when_configured(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None

        with patch.multiple(
            telegram.config,
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat-id",
            TELEGRAM_PROXY_URL="socks5://user:pass@127.0.0.1:1080",
            TELEGRAM_PROXY_ENABLED=True,
            TELEGRAM_API_TIMEOUT=30,
        ):
            with patch("app.notify.telegram.requests.post", return_value=response) as post:
                sent = telegram.send_message("hello")

        self.assertTrue(sent)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(
            post.call_args.kwargs["proxies"],
            {
                "http": "socks5://user:pass@127.0.0.1:1080",
                "https": "socks5://user:pass@127.0.0.1:1080",
            },
        )
        self.assertEqual(post.call_args.kwargs["timeout"], 30)

    def test_send_message_sends_directly_without_proxy(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None

        with patch.multiple(
            telegram.config,
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat-id",
            TELEGRAM_PROXY_URL="",
            TELEGRAM_PROXY_ENABLED=False,
            TELEGRAM_API_TIMEOUT=15,
        ):
            with patch("app.notify.telegram.requests.post", return_value=response) as post:
                sent = telegram.send_message("hello")

        self.assertTrue(sent)
        self.assertIsNone(post.call_args.kwargs["proxies"])
        self.assertEqual(post.call_args.kwargs["timeout"], 15)

    def test_send_message_returns_false_after_retries(self) -> None:
        with patch.multiple(
            telegram.config,
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat-id",
            TELEGRAM_PROXY_URL="",
            TELEGRAM_PROXY_ENABLED=False,
            TELEGRAM_API_TIMEOUT=10,
        ):
            with patch(
                "app.notify.telegram.requests.post",
                side_effect=requests.exceptions.ConnectTimeout(),
            ) as post:
                with patch("app.notify.telegram.time.sleep") as sleep:
                    sent = telegram.send_message("hello")

        self.assertFalse(sent)
        self.assertEqual(post.call_count, telegram.TELEGRAM_SEND_ATTEMPTS)
        self.assertEqual(sleep.call_count, telegram.TELEGRAM_SEND_ATTEMPTS - 1)

    def test_help_command_lists_supported_commands(self) -> None:
        text = telegram.build_command_response("/help")

        self.assertIn("/status", text)
        self.assertIn("/today", text)
        self.assertIn("/urgent", text)
        self.assertIn("/watchlist", text)
        self.assertIn("/report", text)
        self.assertIn("/sources", text)

    def test_urgent_command_returns_requires_attention_documents(self) -> None:
        db_path = self._db_path("telegram_urgent.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="ГИСП - меры поддержки АПК",
                region="federal",
                title="Льготное кредитование АПК",
                url="https://gisp.gov.ru/nmp/measure/9564204",
                action_level="requires_attention",
                page_type="measure_card",
            ),
            db_path,
        )

        text = telegram.build_command_response("/urgent", db_path=db_path)

        self.assertIn("Требует внимания GR", text)
        self.assertIn("Найдено документов: 1", text)
        self.assertIn("Уровень: требует внимания", text)
        self.assertIn("Льготное кредитование АПК", text)

    def test_today_command_returns_visible_today_documents(self) -> None:
        db_path = self._db_path("telegram_today.db")
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
        save_document(
            self._doc(
                doc_id=2,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Старый документ",
                url="https://www.zol.ru/n/old",
                action_level="watchlist",
                page_type="news_background",
                days_ago=3,
            ),
            db_path,
        )

        text = telegram.build_command_response("/today", db_path=db_path)

        self.assertIn("Сегодня", text)
        self.assertIn("Сегодня новых срочных документов нет", text)
        self.assertIn("Отраслевые сигналы", text)
        self.assertIn("Пошлина на экспорт пшеницы останется нулевой", text)
        self.assertNotIn("Старый документ", text)

    def test_watchlist_command_returns_watchlist_documents(self) -> None:
        db_path = self._db_path("telegram_watchlist.db")
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

        text = telegram.build_command_response("/watchlist", db_path=db_path)

        self.assertIn("Документы на наблюдении", text)
        self.assertIn("Уровень: наблюдение", text)
        self.assertIn("Пошлина на экспорт пшеницы останется нулевой", text)

    def test_status_command_contains_main_counters(self) -> None:
        db_path = self._db_path("telegram_status.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="ГИСП - меры поддержки АПК",
                region="federal",
                title="Льготное кредитование АПК",
                url="https://gisp.gov.ru/nmp/measure/9564204",
                action_level="requires_attention",
                page_type="measure_card",
            ),
            db_path,
        )

        text = telegram.build_command_response("/status", db_path=db_path)

        self.assertIn("Статус AHSTEP GR Monitor", text)
        self.assertIn("Документов в базе: 1", text)
        self.assertIn("требует внимания", text)
        self.assertNotIn("RTZ", text)
        self.assertNotIn("зима", text)
        self.assertNotIn("scheduler-state", text)

    def test_sources_command_includes_enabled_sources(self) -> None:
        db_path = self._db_path("telegram_sources.db")
        init_db(db_path)

        text = telegram.build_command_response("/sources", db_path=db_path)

        self.assertIn("Источники (", text)
        self.assertIn("ZOL.ru - зерновые новости", text)
        self.assertIn("ГИСП - меры поддержки АПК", text)
        self.assertNotIn("RA=", text)
        self.assertNotIn("WL=", text)
        self.assertNotIn("BG=", text)
        self.assertNotIn("IRR=", text)

    def test_send_command_response_uses_send_message(self) -> None:
        with patch("app.notify.telegram.send_message", return_value=True) as send_message:
            sent = telegram.send_command_response("/help")

        self.assertTrue(sent)
        self.assertEqual(send_message.call_count, 1)

    def test_empty_urgent_state_is_human_friendly(self) -> None:
        db_path = self._db_path("telegram_urgent_empty.db")
        init_db(db_path)

        text = telegram.build_command_response("/urgent", db_path=db_path)

        self.assertEqual(text, "🚨 Требует внимания GR: новых документов нет.")

    def test_report_command_does_not_show_local_report_path(self) -> None:
        text = telegram.build_command_response("/report")

        self.assertIn("GR-дайджест", text)
        self.assertNotIn("сервер", text.lower())
        self.assertNotIn("urgent:", text)
        self.assertNotIn("reports\\", text)
        self.assertNotIn("reports/", text)

    def test_watchlist_is_limited_for_user_and_has_tail_hint(self) -> None:
        db_path = self._db_path("telegram_watchlist_limit.db")
        init_db(db_path)
        for idx in range(1, 8):
            save_document(
                self._doc(
                    doc_id=idx,
                    source_name="ZOL.ru - зерновые новости",
                    region="federal",
                    title=f"Новость {idx}",
                    url=f"https://www.zol.ru/n/{idx}",
                    action_level="watchlist",
                    page_type="news_background",
                ),
                db_path,
            )

        text = telegram.build_command_response("/watchlist", db_path=db_path)

        self.assertIn("Показано 5 из 7. Для общей сводки нажмите 📄 Отчёт.", text)
        self.assertEqual(text.count("Уровень: наблюдение"), 5)

    def test_sources_does_not_use_old_noise_wording(self) -> None:
        db_path = self._db_path("telegram_sources_wording.db")
        init_db(db_path)

        text = telegram.build_command_response("/sources", db_path=db_path)

        self.assertNotIn("много шума", text)

    def test_missing_published_date_is_hidden_from_user(self) -> None:
        db_path = self._db_path("telegram_missing_date.db")
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Документ без даты",
            url="https://example.com/no-date",
            action_level="requires_attention",
            page_type="measure_card",
        )
        document.published_at = None
        save_document(document, db_path)

        text = telegram.build_command_response("/urgent", db_path=db_path)

        self.assertNotIn("Дата: n/a", text)
        self.assertNotIn("n/a", text)

    def test_long_message_is_split_to_multiple_chunks(self) -> None:
        with patch.multiple(
            telegram.config,
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat-id",
            TELEGRAM_PROXY_URL="",
            TELEGRAM_API_TIMEOUT=15,
        ):
            response = Mock()
            response.raise_for_status.return_value = None
            with patch("app.notify.telegram.requests.post", return_value=response) as post:
                sent = telegram.send_message("x" * 9000)

        self.assertTrue(sent)
        self.assertGreaterEqual(post.call_count, 3)


if __name__ == "__main__":
    unittest.main()
