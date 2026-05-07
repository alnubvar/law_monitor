from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest import mock

from app.models import RawDocument
from app.notify.telegram_formatter import build_digest_message


class TelegramFormatterTest(unittest.TestCase):
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
    ) -> RawDocument:
        now = datetime.now(timezone.utc)
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
            content_hash=f"formatter-hash-{doc_id}",
            raw_text="text",
            is_relevant=action_level != "irrelevant",
            relevance_reason="reason",
            importance="high" if action_level == "requires_attention" else "medium",
            action_level=action_level,
            page_type=page_type,
            summary=summary,
            impact="impact",
            topic="topic",
            notified=False,
        )

    def test_daily_digest_formatter_includes_requires_attention(self) -> None:
        urgent = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Льготное кредитование АПК. Очень длинный текст для проверки digest.",
        )
        urgent.support_status = "active"
        urgent.application_status = "regular"
        urgent.business_signal = "Активная федеральная мера поддержки, действует на регулярной основе"
        urgent.notified = True

        news = self._doc(
            doc_id=2,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Пошлина на экспорт пшеницы останется нулевой",
            url="https://www.zol.ru/n/1",
            action_level="watchlist",
            page_type="news_background",
            summary="Новостной сигнал.",
        )
        news.business_signal = "Новостной предвестник возможных изменений господдержки."

        text = build_digest_message([urgent, news])

        self.assertIn("🧾 Ежедневная GR-сводка", text)
        self.assertIn("🚨 Требует внимания (1)", text)
        self.assertIn("Требует реакции: 1", text)
        self.assertIn("Льготное кредитование АПК", text)

    def test_inactive_measures_are_not_shown_as_urgent(self) -> None:
        urgent = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготный лизинг",
            url="https://gisp.gov.ru/nmp/measure/12447840",
            action_level="requires_attention",
            page_type="measure_card",
        )
        urgent.support_status = "inactive"

        text = build_digest_message([urgent])

        self.assertEqual(text, "Новых документов для уведомления не найдено.")

    def test_block_limits_work(self) -> None:
        documents: list[RawDocument] = []
        for index in range(1, 8):
            item = self._doc(
                doc_id=index,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title=f"Производство сельхозпродукции в РФ {index}",
                url=f"https://www.zol.ru/n/{index}",
                action_level="watchlist",
                page_type="news_background",
            )
            item.business_signal = "Новостной предвестник."
            documents.append(item)

        text = build_digest_message(documents)

        self.assertEqual(text.count("- Производство сельхозпродукции в РФ"), 5)
        self.assertIn("... и еще 2.", text)

    def test_urls_and_status_and_business_signal_are_included(self) -> None:
        urgent = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Господдержка. Транспортировка товаров АПК",
            url="https://gisp.gov.ru/nmp/measure/9512857",
            action_level="requires_attention",
            page_type="measure_card",
        )
        urgent.support_status = "active"
        urgent.application_status = "regular"
        urgent.business_signal = "Активная федеральная мера поддержки, действует на регулярной основе"

        text = build_digest_message([urgent])

        self.assertIn("статус: активна", text)
        self.assertIn("режим: регулярная мера", text)
        self.assertIn("Сигнал: Активная федеральная мера поддержки", text)
        self.assertIn("Что проверить: Проверить применимость меры, сроки и ответственного.", text)
        self.assertIn("https://gisp.gov.ru/nmp/measure/9512857", text)

    def test_long_text_is_truncated(self) -> None:
        urgent = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Очень длинное summary " * 20,
        )
        urgent.business_signal = "Очень длинный business signal " * 20

        text = build_digest_message([urgent])

        self.assertIn("...", text)
        self.assertNotIn(("Очень длинное summary " * 20).strip(), text)

    def test_formatter_tests_do_not_call_telegram_send(self) -> None:
        urgent = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
        )

        with mock.patch("app.notify.telegram.send_message") as send_message_mock:
            build_digest_message([urgent])

        send_message_mock.assert_not_called()

    def test_hourly_alert_skips_news_market_background_false_attention(self) -> None:
        document = self._doc(
            doc_id=1,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Алжир проводит тендер по закупке пшеницы",
            url="https://www.zol.ru/n/market-algeria-tender",
            action_level="requires_attention",
            page_type="news_background",
        )
        document.business_signal = "Рыночный или отраслевой фон без прямого регуляторного сигнала."

        text = build_digest_message([document])

        self.assertEqual(text, "Новых документов для уведомления не найдено.")

    def test_formatter_uses_safe_ocr_title(self) -> None:
        document = self._doc(
            doc_id=1,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="document 'wgketjm9pqmq00n60yoyq8c0z2u13z38_cfa07cc203.pdf' requires OCR extraction",
            url="https://admkrai.krasnodar.ru/upload/wgketjm9pqmq00n60yoyq8c0z2u13z38_cfa07cc203.pdf",
            action_level="requires_attention",
            page_type="new_rule",
        )

        text = build_digest_message([document])

        self.assertIn("НПА Краснодарского края: документ после OCR", text)
        self.assertNotIn("requires OCR extraction", text)

    def test_formatter_uses_news_background_hint_without_urgent_tone(self) -> None:
        document = self._doc(
            doc_id=1,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Пошлина на экспорт пшеницы останется нулевой",
            url="https://www.zol.ru/n/1",
            action_level="watchlist",
            page_type="news_background",
        )
        document.business_signal = "Новостной предвестник возможных изменений господдержки."
        document.notified = True

        text = build_digest_message([document])

        self.assertIn("Что проверить: Оставить как отраслевой фон, без срочной реакции.", text)


if __name__ == "__main__":
    unittest.main()
