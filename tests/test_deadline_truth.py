from __future__ import annotations

import unittest
from datetime import date

from app.rules.deadline_truth import (
    classify_deadline,
    is_deadline_alive,
    is_deadline_expired,
    is_deadline_today,
    parse_deadline_date,
)


class DeadlineTruthTest(unittest.TestCase):
    today = date(2026, 5, 18)

    def test_parse_до_date_in_dot_format(self) -> None:
        self.assertEqual(
            parse_deadline_date("Прием заявок открыт до 18.05.2026"),
            date(2026, 5, 18),
        )

    def test_parse_до_date_in_russian_words(self) -> None:
        self.assertEqual(
            parse_deadline_date("до 30 июня 2026 года"),
            date(2026, 6, 30),
        )

    def test_parse_range_uses_end_date(self) -> None:
        self.assertEqual(
            parse_deadline_date("с 01.05.2026 по 30.06.2026"),
            date(2026, 6, 30),
        )

    def test_parse_discussion_deadline(self) -> None:
        self.assertEqual(
            parse_deadline_date("Конец обсуждения: 22.05.2026"),
            date(2026, 5, 22),
        )

    def test_parse_returns_none_for_empty(self) -> None:
        self.assertIsNone(parse_deadline_date(""))
        self.assertIsNone(parse_deadline_date(None))
        self.assertIsNone(parse_deadline_date("no date here"))

    def test_classify_expired(self) -> None:
        self.assertEqual(
            classify_deadline("до 17.05.2026", today=self.today),
            "expired",
        )

    def test_classify_today(self) -> None:
        self.assertEqual(
            classify_deadline("до 18.05.2026", today=self.today),
            "today",
        )

    def test_classify_near(self) -> None:
        self.assertEqual(
            classify_deadline("до 22.05.2026", today=self.today),
            "near",
        )

    def test_classify_future(self) -> None:
        self.assertEqual(
            classify_deadline("до 18.06.2026", today=self.today),
            "future",
        )

    def test_classify_unknown_when_no_date(self) -> None:
        self.assertEqual(classify_deadline("любой текст", today=self.today), "unknown")

    def test_is_expired_helpers(self) -> None:
        self.assertTrue(is_deadline_expired("до 17.05.2026", today=self.today))
        self.assertFalse(is_deadline_expired("до 18.05.2026", today=self.today))
        self.assertFalse(is_deadline_expired("любой текст", today=self.today))

    def test_is_today_helpers(self) -> None:
        self.assertTrue(is_deadline_today("до 18.05.2026", today=self.today))
        self.assertFalse(is_deadline_today("до 19.05.2026", today=self.today))

    def test_is_alive_excludes_expired_and_unknown(self) -> None:
        self.assertTrue(is_deadline_alive("до 18.05.2026", today=self.today))
        self.assertTrue(is_deadline_alive("до 22.05.2026", today=self.today))
        self.assertFalse(is_deadline_alive("до 17.05.2026", today=self.today))
        # Unknown text must not be treated as alive — callers must decide
        # explicitly.
        self.assertFalse(is_deadline_alive("любой текст", today=self.today))


if __name__ == "__main__":
    unittest.main()
