from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

from app.notify import telegram


class TelegramNotifySmokeTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
