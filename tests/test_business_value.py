from __future__ import annotations

import unittest

from app.llm.facts_extractor import extract_document_facts
from app.llm.mock_client import MockLLMClient


class BusinessValueSmokeTest(unittest.TestCase):
    def test_open_deadline_prefers_submission_date_over_publication_date(self) -> None:
        facts = extract_document_facts(
            "Объявлен конкурсный отбор заявок",
            (
                "Постановление от 30.04.2026 утвердило порядок предоставления субсидии. "
                "Прием заявок открыт до 15 мая 2099 года."
            ),
        )

        self.assertEqual(facts.application_status, "open")
        self.assertIsNotNone(facts.deadline_text)
        self.assertIn("до 15 мая 2099 года", facts.deadline_text or "")
        self.assertNotIn("30.04.2026", facts.deadline_text or "")

    def test_inactive_measure_with_deadline_does_not_become_open(self) -> None:
        facts = extract_document_facts(
            "Субсидии на возмещение затрат",
            "Не активная мера поддержки. Прием заявок до 15 мая 2099 года.",
        )

        self.assertEqual(facts.support_status, "inactive")
        self.assertEqual(facts.application_status, "unknown")
        self.assertIn("до 15 мая 2099 года", facts.deadline_text or "")

    def test_multiple_dates_choose_real_deadline(self) -> None:
        facts = extract_document_facts(
            "Отбор заявок на субсидии",
            (
                "Приказ от 30.04.2026 устанавливает порядок отбора. "
                "Заявки принимаются с 1 мая 2099 года по 20 мая 2099 года."
            ),
        )

        self.assertEqual(facts.application_status, "open")
        self.assertIn("по 20 мая 2099 года", facts.deadline_text or "")
        self.assertNotIn("30.04.2026", facts.deadline_text or "")

    def test_closed_marker_overrides_future_deadline(self) -> None:
        facts = extract_document_facts(
            "Конкурсный отбор заявок",
            "Прием заявок до 15 мая 2099 года. Прием окончен, новые заявки не принимаются.",
        )

        self.assertEqual(facts.application_status, "closed")
        self.assertIn("до 15 мая 2099 года", facts.deadline_text or "")

    def test_open_application_impact_mentions_deadline_and_action(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Объявлен конкурсный отбор заявок на субсидии для АПК",
            "Прием заявок открыт до 15 мая 2099 года. Мера поддержки для сельхозтоваропроизводителей.",
            source_name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/subsidy-open-2099/",
            level="support_measures",
            region="stavropol",
        )

        self.assertEqual(result.application_status, "open")
        self.assertIn("15 мая 2099 года", result.impact)
        self.assertIn("проверить окно участия", result.impact)

    def test_regular_measure_impact_mentions_participation_option(self) -> None:
        client = MockLLMClient(["льготное кредитование АПК"])

        result = client.analyze_document(
            "Льготное кредитование АПК",
            (
                "Активная мера поддержки. На регулярной основе. "
                "Льготное кредитование АПК для сельхозтоваропроизводителей."
            ),
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            level="support_measures",
            region="federal",
        )

        self.assertEqual(result.application_status, "regular")
        self.assertIn("регулярной основе", result.impact)
        self.assertIn("проработки участия", result.impact)

    def test_inactive_measure_impact_is_reference_like(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Субсидии на возмещение затрат",
            "Не активная мера поддержки. Прием заявок до 15 мая 2099 года.",
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/8130026",
            level="support_measures",
            region="federal",
        )

        self.assertEqual(result.support_status, "inactive")
        self.assertIn("справочный характер", result.impact)
        self.assertIn("неактивна", result.impact)


if __name__ == "__main__":
    unittest.main()
