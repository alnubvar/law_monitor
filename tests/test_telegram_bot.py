from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import requests

from app.notify import telegram_bot


class TelegramBotTest(unittest.TestCase):
    def _offset_path(self, name: str) -> Path:
        path = Path("data/test_artifacts") / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        return path

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
        self.assertEqual(keyboard[3][0]["text"], "ℹ️ Помощь")
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
        build.assert_called_once_with("/status", db_path=None)

    def test_button_text_maps_to_command(self) -> None:
        self.assertEqual(telegram_bot.normalize_incoming_command("📊 Статус"), "/status")
        self.assertEqual(telegram_bot.normalize_incoming_command("🚨 Срочное"), "/urgent")

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
        report_path = self._offset_path("gr_monitoring_2026-05-05.md")
        report_path.write_text("report", encoding="utf-8")

        update = {"update_id": 1, "message": {"chat": {"id": 123}, "text": "/report"}}
        with patch.multiple(telegram_bot.config, TELEGRAM_CHAT_ID="123", TELEGRAM_BOT_TOKEN="token"):
            with patch("app.notify.telegram_bot.get_latest_report_file_path", return_value=report_path):
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
            with patch("app.notify.telegram_bot.get_latest_report_file_path", return_value=None):
                with patch("app.notify.telegram_bot.dispatch_input_text", return_value=telegram_bot.DispatchResult(command="/report", response_text="summary")):
                    with patch("app.notify.telegram_bot._send_response", return_value=True) as send_response:
                        telegram_bot._process_update(update, db_path=None, proxies=None)

        self.assertGreaterEqual(send_response.call_count, 2)
        self.assertIn(
            "Полный отчет временно недоступен, используйте краткую сводку выше",
            str(send_response.call_args_list[-1]),
        )


if __name__ == "__main__":
    unittest.main()
