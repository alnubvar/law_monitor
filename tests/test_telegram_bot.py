from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta, timezone

import requests

from app.models import RawDocument
from app.notify import telegram_bot
from app.storage import init_db, save_document


class TelegramBotTest(unittest.TestCase):
    def _offset_path(self, name: str) -> Path:
        path = Path("data/test_artifacts") / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        return path

    def _doc(self, *, url: str, days_ago: int = 0) -> RawDocument:
        now = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return RawDocument(
            id=1,
            source_name="ГИСП - меры поддержки АПК",
            source_url=url,
            level="federal",
            region="federal",
            title="Льготное кредитование АПК",
            url=url,
            published_at=now,
            collected_at=now,
            content_hash=f"hash-{url}",
            raw_text="text",
            is_relevant=True,
            relevance_reason="reason",
            importance="high",
            action_level="requires_attention",
            page_type="measure_card",
            summary="summary",
            impact="impact",
            topic="topic",
            status="analyzed",
        )

    def test_set_my_commands_payload_is_expected(self) -> None:
        payload = telegram_bot.build_set_my_commands_payload()

        self.assertEqual(
            payload,
            {
                "commands": [
                    {"command": "start", "description": "открыть меню"},
                    {"command": "help", "description": "помощь"},
                    {"command": "status", "description": "состояние системы"},
                    {"command": "today", "description": "сводка за сегодня"},
                    {"command": "urgent", "description": "требует внимания"},
                    {"command": "watchlist", "description": "наблюдение"},
                    {"command": "report", "description": "последний отчет"},
                    {"command": "sources", "description": "источники"},
                    {"command": "search", "description": "поиск по архиву"},
                    {"command": "refresh", "description": "обновить данные"},
                ]
            },
        )

    def test_reply_keyboard_payload_contains_gr_buttons(self) -> None:
        payload = telegram_bot.build_reply_keyboard_payload()

        keyboard = payload["keyboard"]
        self.assertEqual(keyboard[0][0]["text"], "📊 Статус")
        self.assertEqual(keyboard[0][1]["text"], "🚨 Срочное")
        self.assertEqual(keyboard[1][0]["text"], "📅 Сегодня")
        self.assertEqual(keyboard[1][1]["text"], "👀 Наблюдение")
        self.assertEqual(keyboard[2][0]["text"], "📄 Отчёт")
        self.assertEqual(keyboard[2][1]["text"], "🛰 Источники")
        self.assertEqual(keyboard[3][0]["text"], "🔎 Поиск")
        self.assertEqual(keyboard[4][0]["text"], "🔄 Обновить данные")
        self.assertEqual(keyboard[5][0]["text"], "ℹ️ Помощь")
        self.assertTrue(payload["resize_keyboard"])
        self.assertTrue(payload["is_persistent"])

    def test_dispatch_start_returns_welcome_text(self) -> None:
        result = telegram_bot.dispatch_input_text("/start")

        self.assertEqual(result.command, "/start")
        self.assertIn("AHSTEP GR Monitor запущен", result.response_text)

    def test_dispatch_known_command_uses_existing_formatter(self) -> None:
        with patch("app.notify.telegram_bot.build_command_response", return_value="ok") as build:
            result = telegram_bot.dispatch_input_text("/status")

        self.assertEqual(result.command, "/status")
        self.assertEqual(result.response_text, "ok")
        build.assert_called_once_with("/status", db_path=None, default_days=7)

    def test_button_text_maps_to_command(self) -> None:
        self.assertEqual(telegram_bot.normalize_incoming_command("📊 Статус"), "/status")
        self.assertEqual(telegram_bot.normalize_incoming_command("🚨 Срочное"), "/urgent")
        self.assertEqual(telegram_bot.normalize_incoming_command("🔄 Обновить данные"), "/refresh")
        self.assertEqual(telegram_bot.normalize_incoming_command("🔎 Поиск"), "/search")

        with patch("app.notify.telegram_bot.build_command_response", return_value="mapped"):
            result = telegram_bot.dispatch_input_text("📄 Отчёт")

        self.assertEqual(result.command, "/report")
        self.assertEqual(result.response_text, "mapped")

    def test_unknown_command_returns_help_hint(self) -> None:
        result = telegram_bot.dispatch_input_text("/unknown")

        self.assertIsNone(result.command)
        self.assertEqual(result.response_text, telegram_bot.UNKNOWN_COMMAND_MESSAGE)

    def test_ignore_unauthorized_chat_id(self) -> None:
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 999},
                "text": "/help",
            },
        }
        with patch.multiple(telegram_bot.config, TELEGRAM_CHAT_ID="123"):
            with patch("app.notify.telegram_bot._send_response") as send_response:
                telegram_bot._process_update(update, db_path=None, proxies=None)

        send_response.assert_not_called()

    def test_polling_updates_offset_after_processed_update(self) -> None:
        offset_path = self._offset_path("telegram_bot_offset.txt")

        with patch("app.notify.telegram_bot._offset_store_path", return_value=offset_path):
            with patch("app.notify.telegram_bot._is_bot_configured", return_value=True):
                with patch("app.notify.telegram_bot._build_proxies", return_value=None):
                    with patch("app.notify.telegram_bot._configure_bot_commands"):
                        with patch("app.notify.telegram_bot._bootstrap_offset", return_value=None):
                            with patch(
                                "app.notify.telegram_bot._get_updates",
                                return_value=[{"update_id": 10, "message": {}}],
                            ):
                                with patch("app.notify.telegram_bot._process_update"):
                                    telegram_bot.run_polling_listener(
                                        max_cycles=1,
                                        sleep_fn=lambda _: None,
                                    )

        self.assertEqual(telegram_bot.load_offset(offset_path), 11)

    def test_polling_handles_proxy_error_without_crash(self) -> None:
        sleep_mock = Mock()
        offset_path = self._offset_path("telegram_bot_proxy_error_offset.txt")

        with patch("app.notify.telegram_bot._offset_store_path", return_value=offset_path):
            with patch("app.notify.telegram_bot._is_bot_configured", return_value=True):
                with patch("app.notify.telegram_bot._build_proxies", return_value=None):
                    with patch("app.notify.telegram_bot._configure_bot_commands"):
                        with patch("app.notify.telegram_bot._bootstrap_offset", return_value=None):
                            with patch(
                                "app.notify.telegram_bot._get_updates",
                                side_effect=[requests.exceptions.ProxyError("proxy"), []],
                            ):
                                telegram_bot.run_polling_listener(
                                    max_cycles=2,
                                    sleep_fn=sleep_mock,
                                )

        self.assertGreaterEqual(sleep_mock.call_count, 1)

    def test_polling_handles_bootstrap_proxy_error_without_crash(self) -> None:
        offset_path = self._offset_path("telegram_bot_bootstrap_offset.txt")
        sleep_mock = Mock()

        with patch("app.notify.telegram_bot._offset_store_path", return_value=offset_path):
            with patch("app.notify.telegram_bot._is_bot_configured", return_value=True):
                with patch("app.notify.telegram_bot._build_proxies", return_value=None):
                    with patch("app.notify.telegram_bot._configure_bot_commands"):
                        with patch(
                            "app.notify.telegram_bot._bootstrap_offset",
                            side_effect=requests.exceptions.ProxyError("proxy"),
                        ):
                            with patch("app.notify.telegram_bot._get_updates", return_value=[]):
                                telegram_bot.run_polling_listener(
                                    max_cycles=1,
                                    sleep_fn=sleep_mock,
                                )

        self.assertGreaterEqual(sleep_mock.call_count, 1)

    def test_report_command_sends_document_attachment(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True, "result": {"message_id": 1}}
        report_path = self._offset_path("gr_monitoring_2026-05-05_7d.txt")
        report_path.write_text("report", encoding="utf-8")

        update = {"update_id": 1, "message": {"chat": {"id": 123}, "text": "/report"}}
        with patch.multiple(telegram_bot.config, TELEGRAM_CHAT_ID="123", TELEGRAM_BOT_TOKEN="token"):
            with patch("app.notify.telegram_bot._build_period_report_attachment", return_value=report_path):
                with patch("app.notify.telegram_bot.dispatch_input_text", return_value=telegram_bot.DispatchResult(command="/report", response_text="summary")):
                    with patch("app.notify.telegram_bot._send_response", return_value=True) as send_response:
                        with patch("app.notify.telegram_bot.requests.post", return_value=response) as post:
                            telegram_bot._process_update(update, db_path=None, proxies=None)

        self.assertGreaterEqual(send_response.call_count, 2)
        post.assert_called_once()
        self.assertIn("/sendDocument", post.call_args.args[0])
        sent_document = post.call_args.kwargs["files"]["document"]
        self.assertTrue(sent_document[0].endswith(".txt"))

    def test_report_command_fallback_when_file_missing(self) -> None:
        update = {"update_id": 1, "message": {"chat": {"id": 123}, "text": "/report"}}
        with patch.multiple(telegram_bot.config, TELEGRAM_CHAT_ID="123"):
            with patch("app.notify.telegram_bot._build_period_report_attachment", return_value=None):
                with patch("app.notify.telegram_bot.dispatch_input_text", return_value=telegram_bot.DispatchResult(command="/report", response_text="summary")):
                    with patch("app.notify.telegram_bot._send_response", return_value=True) as send_response:
                        telegram_bot._process_update(update, db_path=None, proxies=None)

        self.assertGreaterEqual(send_response.call_count, 2)
        self.assertIn(
            "Полный отчет временно недоступен, используйте краткую сводку выше",
            str(send_response.call_args_list[-1]),
        )

    def test_markdown_to_plain_text_removes_headings(self) -> None:
        markdown = "# Заголовок\n\n## Блок\n### Пункт\n- строка\n"
        plain = telegram_bot._markdown_to_plain_text(markdown)
        self.assertNotIn("#", plain)
        self.assertIn("Заголовок", plain)
        self.assertIn("Блок", plain)
        self.assertIn("Пункт", plain)

    def test_refresh_starts_pipeline_if_allowed(self) -> None:
        mock_lock = Mock()
        mock_lock.acquire.return_value = True
        with patch("app.notify.telegram_bot._refresh_lock", mock_lock):
            with patch("app.notify.telegram_bot.get_runtime_event", return_value=None):
                with patch("app.notify.telegram_bot.run_collect", return_value=3):
                    with patch("app.notify.telegram_bot.run_analyze", return_value=2):
                        with patch("app.notify.telegram_bot.run_digest"):
                            with patch("app.notify.telegram_bot.count_documents_by_action_level", side_effect=[1, 4]):
                                with patch("app.notify.telegram_bot.list_latest_source_audit", return_value=[]):
                                    with patch("app.notify.telegram_bot.mark_runtime_event"):
                                        text = telegram_bot._run_manual_refresh(db_path=None)
        self.assertIn("Обновление завершено", text)
        self.assertIn("Новых документов: 3", text)
        self.assertIn("Ошибки источников: 0", text)

    def test_refresh_blocked_if_called_too_often(self) -> None:
        recent = {"updated_at": datetime.now(timezone.utc) - timedelta(minutes=10)}
        mock_lock = Mock()
        mock_lock.acquire.return_value = True
        with patch("app.notify.telegram_bot._refresh_lock", mock_lock):
            with patch("app.notify.telegram_bot.get_runtime_event", return_value=recent):
                text = telegram_bot._run_manual_refresh(db_path=None)
        self.assertIn("Обновление запускалось недавно", text)

    def test_refresh_no_parallel_runs(self) -> None:
        mock_lock = Mock()
        mock_lock.acquire.return_value = False
        with patch("app.notify.telegram_bot._refresh_lock", mock_lock):
            text = telegram_bot._run_manual_refresh(db_path=None)
        self.assertIn("уже выполняется", text)

    def test_refresh_reports_problematic_sources_count(self) -> None:
        mock_lock = Mock()
        mock_lock.acquire.return_value = True
        audits = [
            {"source_name": "A", "error_message": None},
            {"source_name": "B", "error_message": "timeout"},
            {"source_name": "C", "error_message": "403"},
        ]
        with patch("app.notify.telegram_bot._refresh_lock", mock_lock):
            with patch("app.notify.telegram_bot.get_runtime_event", return_value=None):
                with patch("app.notify.telegram_bot.run_collect", return_value=1):
                    with patch("app.notify.telegram_bot.run_analyze", return_value=1):
                        with patch("app.notify.telegram_bot.run_digest"):
                            with patch("app.notify.telegram_bot.count_documents_by_action_level", side_effect=[1, 2]):
                                with patch("app.notify.telegram_bot.list_latest_source_audit", return_value=audits):
                                    with patch("app.notify.telegram_bot.mark_runtime_event"):
                                        text = telegram_bot._run_manual_refresh(db_path=None)
        self.assertIn("Проблемных источников: 2", text)

    def test_period_command_persists_and_reuses_default_days_per_chat(self) -> None:
        update_explicit = {"update_id": 1, "message": {"chat": {"id": 123}, "text": "/report 30"}}
        update_plain = {"update_id": 2, "message": {"chat": {"id": 123}, "text": "/report"}}
        db_path = self._offset_path("telegram_period_preferences.db")
        with patch.multiple(telegram_bot.config, TELEGRAM_CHAT_ID="123"):
            with patch("app.notify.telegram_bot._send_response", return_value=True):
                with patch("app.notify.telegram_bot._send_report_attachment", return_value=True):
                    with patch("app.notify.telegram_bot.build_command_response", return_value="ok") as build:
                        telegram_bot._process_update(update_explicit, db_path=str(db_path), proxies=None)
                        telegram_bot._process_update(update_plain, db_path=str(db_path), proxies=None)

        self.assertEqual(build.call_args_list[0].kwargs["default_days"], 30)
        self.assertEqual(build.call_args_list[1].kwargs["default_days"], 30)

    def test_report_command_sends_attachment_with_requested_period(self) -> None:
        update = {"update_id": 1, "message": {"chat": {"id": 123}, "text": "/report 30"}}
        with patch.multiple(telegram_bot.config, TELEGRAM_CHAT_ID="123"):
            with patch("app.notify.telegram_bot.dispatch_input_text", return_value=telegram_bot.DispatchResult(command="/report", response_text="summary")):
                with patch("app.notify.telegram_bot._send_response", return_value=True):
                    with patch("app.notify.telegram_bot._send_report_attachment", return_value=True) as send_attachment:
                        telegram_bot._process_update(update, db_path=None, proxies=None)
        self.assertEqual(send_attachment.call_args.kwargs["days"], 30)

    def test_report_30_attachment_contains_period_label(self) -> None:
        db_path = self._offset_path("telegram_report_30_attachment.db")
        init_db(db_path)
        save_document(self._doc(url="https://gisp.gov.ru/nmp/measure/9564204", days_ago=10), db_path)
        path = telegram_bot._build_period_report_attachment(days=30, db_path=str(db_path))
        self.assertIsNotNone(path)
        assert path is not None
        content = path.read_text(encoding="utf-8")
        self.assertIn("Период: Последние 30 дн.", content)
        path.unlink(missing_ok=True)

    def test_report_7_attachment_contains_period_label(self) -> None:
        db_path = self._offset_path("telegram_report_7_attachment.db")
        init_db(db_path)
        save_document(self._doc(url="https://gisp.gov.ru/nmp/measure/9564205", days_ago=1), db_path)
        path = telegram_bot._build_period_report_attachment(days=7, db_path=str(db_path))
        self.assertIsNotNone(path)
        assert path is not None
        content = path.read_text(encoding="utf-8")
        self.assertIn("Период: Последние 7 дн.", content)
        path.unlink(missing_ok=True)

    def test_send_response_logs_do_not_contain_tokenized_url(self) -> None:
        token = "123:ABCDEF"
        unsafe_url = f"https://api.telegram.org/bot{token}/sendMessage"
        update = {"update_id": 1, "message": {"chat": {"id": 123}, "text": "/status"}}
        request_exc = requests.exceptions.ConnectionError(unsafe_url)
        with patch.multiple(telegram_bot.config, TELEGRAM_CHAT_ID="123", TELEGRAM_BOT_TOKEN=token):
            with patch("app.notify.telegram_bot.dispatch_input_text", return_value=telegram_bot.DispatchResult(command="/status", response_text="ok")):
                with patch("app.notify.telegram_bot._call_telegram_api", side_effect=request_exc):
                    with self.assertLogs("app.notify.telegram_bot", level="WARNING") as logs:
                        telegram_bot._process_update(update, db_path=None, proxies=None)
        joined = "\n".join(logs.output)
        self.assertNotIn(token, joined)
        self.assertNotIn(unsafe_url, joined)


if __name__ == "__main__":
    unittest.main()
