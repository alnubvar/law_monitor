from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest import mock

from app.models import RawDocument
from app.notify.telegram_formatter import build_digest_message
from app.user_facing import compress_visible_title


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
        self.assertIn("источники:", text)
        self.assertIn("Льготное кредитование АПК", text)

    def test_daily_digest_source_heat_counts_only_visible_documents(self) -> None:
        urgent = self._doc(
            doc_id=11,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="Изменения по субсидиям сельхозтоваропроизводителям",
            url="https://admkrai.krasnodar.ru/upload/a.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Изменения порядка предоставления субсидий сельхозтоваропроизводителям.",
        )
        news = self._doc(
            doc_id=12,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://www.zol.ru/n/41378",
            action_level="watchlist",
            page_type="news_background",
            summary="Новостной сигнал.",
        )
        news.business_signal = "Новостной предвестник возможных изменений господдержки."
        hidden = self._doc(
            doc_id=13,
            source_name="Правительство РФ - новости",
            region="federal",
            title="Скрытый рыночный фон",
            url="http://government.ru/news/hidden/",
            action_level="background",
            page_type="news_background",
            summary="Фоновая новость.",
        )

        text = build_digest_message([urgent, news, hidden])

        self.assertIn("источники: admkrai.krasnodar.ru: 1; zol.ru: 1", text)
        self.assertNotIn("government.ru", text)

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

        with mock.patch("app.notify.telegram_formatter.list_document_enrichments", return_value={}):
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
        document.raw_text = "Распознанный текст отсутствует."

        text = build_digest_message([document])

        self.assertNotIn("🚨 Новые документы, требующие внимания", text)
        self.assertIn("⚖️ Региональные НПА (1)", text)
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

        self.assertIn("Что проверить: Оставить как отраслевой фон.", text)

    def test_formatter_uses_urgent_news_hint_without_background_wording(self) -> None:
        document = self._doc(
            doc_id=31,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Минсельхоз предложил новые условия льготного кредитования АПК",
            url="https://www.zol.ru/n/urgent-credit-news",
            action_level="requires_attention",
            page_type="news_background",
            summary="Новость о правилах господдержки.",
        )
        document.business_signal = "Есть признаки изменения условий льготного кредитования для АПК."

        text = build_digest_message([document])

        self.assertIn("Сигнал: Обновлены условия льготного кредитования", text)
        self.assertIn(
            "Что проверить: Проверить условия кредитования, сроки и применимость для АПК.",
            text,
        )
        self.assertNotIn("Что проверить: Оставить как отраслевой фон.", text)
        self.assertNotIn("Изменение экспортных пошлин", text)
        self.assertNotIn("Проверить влияние на экспорт и меры поддержки.", text)

    def test_formatter_credit_news_keeps_credit_hint_even_with_trade_words(self) -> None:
        document = self._doc(
            doc_id=311,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Минсельхоз предложил новые условия льготного кредитования АПК",
            url="https://www.zol.ru/n/formatter-credit-trade",
            action_level="requires_attention",
            page_type="news_background",
            summary="Новость об обновлении условий льготного кредитования.",
        )
        document.raw_text = (
            "Минсельхоз предложил обновить условия льготного кредитования АПК. "
            "В тексте также упоминаются экспортные пошлины на смежном рынке."
        )

        text = build_digest_message([document])

        self.assertIn("Сигнал: Обновлены условия льготного кредитования", text)
        self.assertIn(
            "Что проверить: Проверить условия кредитования, сроки и применимость для АПК.",
            text,
        )
        self.assertNotIn(
            "Что проверить: Проверить влияние на экспорт и контрагентов.",
            text,
        )

    def test_daily_digest_keeps_weak_ocr_placeholder_out_of_urgent_section(self) -> None:
        urgent = self._doc(
            doc_id=312,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Минсельхоз предложил новые условия льготного кредитования АПК",
            url="https://www.zol.ru/n/41378",
            action_level="requires_attention",
            page_type="news_background",
            summary="Новость о правилах господдержки.",
        )
        urgent.business_signal = "Есть признаки изменения условий льготного кредитования для АПК."
        urgent.notified = True

        weak_ocr = self._doc(
            doc_id=313,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="document 'weak-ocr.pdf' requires OCR extraction",
            url="https://admkrai.krasnodar.ru/upload/iblock/d69/weak-ocr.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="OCR placeholder документ.",
        )
        weak_ocr.raw_text = "Распознанный текст отсутствует."
        weak_ocr.notified = True

        text = build_digest_message([urgent, weak_ocr])

        urgent_block = text.split("🚨 Требует внимания (", 1)[1].split("⚖️ Региональные НПА", 1)[0]
        self.assertIn("Минсельхоз предложил новые условия льготного кредитования АПК", urgent_block)
        self.assertNotIn("НПА Краснодарского края: документ после OCR", urgent_block)
        self.assertIn("⚖️ Региональные НПА (1)", text)
        self.assertIn("НПА Краснодарского края: документ после OCR", text)

    def test_fertilizer_duty_item_uses_trade_action_not_credit_wording(self) -> None:
        document = self._doc(
            doc_id=32,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="США снизили импортную пошлину на фосфорные удобрения для «ФосАгро»",
            url="https://www.zol.ru/n/fertilizer-duty",
            action_level="requires_attention",
            page_type="news_background",
            summary="Изменение импортной пошлины на фосфорные удобрения.",
        )
        document.business_signal = "Есть признаки изменения торгового регулирования на рынке удобрений."
        document.raw_text = "США снизили импортную пошлину для российской компании «ФосАгро» на фосфорные удобрения."

        text = build_digest_message([document])

        self.assertIn(
            "Что проверить: Проверить влияние на экспорт и контрагентов.",
            text,
        )
        self.assertNotIn("Проверить условия кредитования, сроки и применимость для АПК.", text)

    def test_formatter_uses_specific_regional_npa_hint(self) -> None:
        document = self._doc(
            doc_id=30,
            source_name="Право Ставропольского края",
            region="stavropol",
            title="О внесении изменений в порядок предоставления субсидий сельхозтоваропроизводителям",
            url="https://pravo.stavregion.ru/document/98765",
            action_level="watchlist",
            page_type="new_rule",
        )
        document.notified = True

        text = build_digest_message([document])

        self.assertIn("- Изменены условия субсидирования", text)
        self.assertIn(
            "Что проверить: Проверить изменения порядка субсидирования и сроки вступления.",
            text,
        )

    def test_formatter_deduplicates_government_news_and_docs_pair(self) -> None:
        news_document = self._doc(
            doc_id=20,
            source_name="Правительство РФ - новости",
            region="federal",
            title="Изменения в господдержке экспорта АПК",
            url="http://government.ru/news/58669/",
            action_level="watchlist",
            page_type="news_background",
        )
        docs_document = self._doc(
            doc_id=21,
            source_name="Правительство РФ - документы",
            region="federal",
            title="Изменения в господдержке экспорта АПК",
            url="http://government.ru/docs/58669/",
            action_level="watchlist",
            page_type="new_rule",
        )
        news_document.notified = True
        docs_document.notified = True

        text = build_digest_message([news_document, docs_document])

        self.assertEqual(text.count("Изменения в господдержке экспорта"), 1)
        self.assertIn("http://government.ru/docs/58669/", text)
        self.assertNotIn("http://government.ru/news/58669/", text)

    def test_formatter_uses_enrichment_when_available(self) -> None:
        document = self._doc(
            doc_id=40,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Базовая summary.",
        )
        document.business_signal = "Базовый сигнал."

        with mock.patch(
            "app.notify.telegram_formatter.list_document_enrichments",
            return_value={
                document.url: {
                    "executive_summary": "Executive summary для Telegram.",
                    "business_impact": "Новая редакция меры меняет условия участия для заемщиков АПК.",
                    "recommended_action": "Проверить применимость обновленных условий и ответственного.",
                    "deadline_hint": "До 30 июня 2026 года.",
                    "confidence": 0.8,
                    "error": None,
                }
            },
        ):
            text = build_digest_message([document])

        self.assertIn("Executive summary для Telegram.", text)
        self.assertIn("Новая редакция меры меняет условия участия для заемщиков АПК.", text)
        self.assertIn("Проверить применимость меры, сроки и ответственного.", text)
        self.assertIn("До 30 июня 2026 года.", text)

    def test_formatter_strips_legacy_ai_prefixes_from_enrichment(self) -> None:
        document = self._doc(
            doc_id=44,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Базовая summary.",
        )

        with mock.patch(
            "app.notify.telegram_formatter.list_document_enrichments",
            return_value={
                document.url: {
                    "executive_summary": "AI-сводка: Executive summary для Telegram.",
                    "business_impact": "AI-оценка влияния: Изменения влияют на условия участия.",
                    "recommended_action": "AI-рекомендация: Проверить применимость обновленных условий.",
                    "deadline_hint": "До 30 июня 2026 года.",
                    "confidence": 0.8,
                    "error": None,
                }
            },
        ):
            text = build_digest_message([document])

        self.assertIn("Executive summary для Telegram.", text)
        self.assertNotIn("AI-сводка:", text)
        self.assertNotIn("AI-оценка влияния:", text)
        self.assertNotIn("AI-рекомендация:", text)

    def test_formatter_falls_back_without_enrichment(self) -> None:
        document = self._doc(
            doc_id=41,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Базовая summary.",
        )
        document.business_signal = "Базовый сигнал."

        with mock.patch("app.notify.telegram_formatter.list_document_enrichments", return_value={}):
            text = build_digest_message([document])

        self.assertIn("Сигнал: Базовый сигнал.", text)
        self.assertIn("Что проверить: Проверить применимость меры, сроки и ответственного.", text)
        self.assertNotIn("Executive summary для Telegram.", text)

    def test_formatter_ignores_errored_or_low_confidence_enrichment(self) -> None:
        document = self._doc(
            doc_id=42,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Базовая summary.",
        )
        document.business_signal = "Базовый сигнал."

        with mock.patch(
            "app.notify.telegram_formatter.list_document_enrichments",
            return_value={
                document.url: {
                    "executive_summary": "Не использовать",
                    "business_impact": "Не использовать",
                    "recommended_action": "Не использовать",
                    "confidence": 0.4,
                    "error": None,
                }
            },
        ):
            low_confidence_text = build_digest_message([document])

        with mock.patch(
            "app.notify.telegram_formatter.list_document_enrichments",
            return_value={
                document.url: {
                    "executive_summary": "Не использовать",
                    "business_impact": "Не использовать",
                    "recommended_action": "Не использовать",
                    "confidence": 0.9,
                    "error": "timeout",
                }
            },
        ):
            errored_text = build_digest_message([document])

        self.assertNotIn("Не использовать", low_confidence_text)
        self.assertIn("Сигнал: Базовый сигнал.", low_confidence_text)
        self.assertNotIn("Не использовать", errored_text)
        self.assertIn("Сигнал: Базовый сигнал.", errored_text)

    def test_formatter_clips_long_enrichment_safely(self) -> None:
        document = self._doc(
            doc_id=43,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Базовая summary.",
        )
        long_text = "Очень длинное executive пояснение " * 20

        with mock.patch(
            "app.notify.telegram_formatter.list_document_enrichments",
            return_value={
                document.url: {
                    "executive_summary": long_text,
                    "business_impact": long_text,
                    "recommended_action": long_text,
                    "deadline_hint": long_text,
                    "confidence": 0.8,
                    "error": None,
                }
            },
        ):
            text = build_digest_message([document])

        self.assertIn("...", text)
        self.assertNotIn(long_text.strip(), text)

    def test_generic_mock_enrichment_does_not_override_specific_digest_reason_and_action(self) -> None:
        document = self._doc(
            doc_id=45,
            source_name="Право Ставропольского края",
            region="stavropol",
            title="О внесении изменений в порядок предоставления субсидий сельхозтоваропроизводителям",
            url="https://pravo.stavregion.ru/document/45",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Базовая summary.",
        )

        with mock.patch(
            "app.notify.telegram_formatter.list_document_enrichments",
            return_value={
                document.url: {
                    "executive_summary": "Документ содержит изменения в порядке предоставления поддержки; требуется проверка условий и сроков.",
                    "business_impact": "Сигнал может повлиять на контекст господдержки и требует наблюдения со стороны GR.",
                    "recommended_action": "Оценить срочность сигнала и определить следующий GR-шаг.",
                    "confidence": 0.8,
                    "error": None,
                }
            },
        ):
            text = build_digest_message([document])

        self.assertIn("- Изменены условия субсидирования", text)
        self.assertIn("Кратко: Изменён порядок предоставления субсидий в Ставропольском крае.", text)
        self.assertIn("Что проверить: Проверить изменения порядка субсидирования и сроки вступления.", text)
        self.assertNotIn("Сигнал может повлиять на контекст господдержки", text)
        self.assertNotIn("Оценить срочность сигнала", text)

    def test_compress_visible_title_does_not_mutate_original_title(self) -> None:
        original_title = "О внесении изменений в порядок предоставления субсидий сельхозтоваропроизводителям"
        document = self._doc(
            doc_id=46,
            source_name="Право Ставропольского края",
            region="stavropol",
            title=original_title,
            url="https://pravo.stavregion.ru/document/46",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Изменения порядка предоставления субсидий.",
        )

        compressed = compress_visible_title(document)

        self.assertEqual(compressed, "Изменены условия субсидирования")
        self.assertEqual(document.title, original_title)

    def test_daily_digest_disambiguates_two_documents_with_same_compressed_title(self) -> None:
        doc1 = self._doc(
            doc_id=801,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="Об утверждении порядка предоставления субсидий на молочное скотоводство",
            url="https://admkrai.krasnodar.ru/upload/iblock/9a3/d1.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Изменены условия субсидирования молочного направления.",
        )
        doc2 = self._doc(
            doc_id=802,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="Об утверждении порядка предоставления субсидий на развитие растениеводства",
            url="https://admkrai.krasnodar.ru/upload/iblock/c24/d2.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Изменены условия субсидирования растениеводства.",
        )
        text = build_digest_message([doc1, doc2])
        # Both items must be present
        self.assertIn("iblock/9a3", text)
        self.assertIn("iblock/c24", text)
        # The base title appears (as a prefix of the disambiguated titles)
        self.assertIn("Утверждены условия субсидирования", text)
        # The two items must NOT share the exact same title line
        title_lines = [ln for ln in text.splitlines() if ln.startswith("- Утверждены условия субсидирования")]
        self.assertEqual(len(title_lines), 2)
        self.assertNotEqual(title_lines[0], title_lines[1])

    def test_daily_digest_no_suffix_when_only_one_document(self) -> None:
        doc = self._doc(
            doc_id=803,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="Об утверждении порядка предоставления субсидий на молочное скотоводство",
            url="https://admkrai.krasnodar.ru/upload/iblock/9a3/only.pdf",
            action_level="requires_attention",
            page_type="new_rule",
        )
        text = build_digest_message([doc])
        self.assertIn("Утверждены условия субсидирования", text)
        self.assertNotIn("(документ", text)
        self.assertNotIn("(№", text)


if __name__ == "__main__":
    unittest.main()
