from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.models import RawDocument
from app.reports.markdown_report import (
    build_report_view,
    classify_document_bucket,
    generate_markdown_report,
)


class ReportGenerationSmokeTest(unittest.TestCase):
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
        summary: str,
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
            content_hash=f"hash-{doc_id}",
            raw_text="text",
            is_relevant=True,
            relevance_reason="reason",
            importance="medium" if action_level != "requires_attention" else "high",
            action_level=action_level,
            page_type=page_type,
            summary=summary,
            impact="impact",
            topic="topic",
            collected_at=now,
        )

    def test_anti_corruption_forms_hidden(self) -> None:
        document = self._doc(
            doc_id=1,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="Формы документов, связанных с противодействием коррупции",
            url="https://admkrai.krasnodar.ru/content/1270/",
            action_level="watchlist",
            page_type="reference_page",
            summary="Формы документов по противодействию коррупции.",
        )
        report_view = build_report_view(
            [document],
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )
        self.assertEqual(report_view.total_visible, 0)

    def test_background_limited_to_five_by_default(self) -> None:
        documents = [
            self._doc(
                doc_id=index,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title=f"Производство сельхозпродукции в РФ {index}",
                url=f"https://www.zol.ru/n/{index}",
                action_level="watchlist",
                page_type="news_background",
                summary="Федеральный фон по АПК.",
            )
            for index in range(1, 8)
        ]
        report_view = build_report_view(
            documents,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )
        self.assertEqual(len(report_view.shown_buckets["industry_background"]), 5)
        self.assertEqual(report_view.hidden_background_overflow_count, 2)

    def test_full_background_appears_only_with_flag(self) -> None:
        documents = [
            self._doc(
                doc_id=index,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title=f"В Липецкой области яровой сев {index}",
                url=f"https://www.zol.ru/n/lipetsk-{index}",
                action_level="watchlist",
                page_type="news_background",
                summary="Фон вне целевой географии.",
            )
            for index in range(1, 8)
        ]
        limited_view = build_report_view(
            documents,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )
        full_view = build_report_view(
            documents,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            include_full_background=True,
        )
        self.assertEqual(len(limited_view.shown_buckets["non_target_background"]), 5)
        self.assertEqual(len(full_view.shown_buckets["non_target_background"]), 7)

    def test_bucket_rules_for_target_and_non_target_background(self) -> None:
        target_document = self._doc(
            doc_id=1,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Экспорт агропродукции в Китай через порты Краснодарского края вырос",
            url="https://www.zol.ru/n/krasnodar",
            action_level="watchlist",
            page_type="news_background",
            summary="Новость по Краснодарскому краю.",
        )
        non_target_document = self._doc(
            doc_id=2,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="В Липецкой области яровой сев ведут 15 округов",
            url="https://www.zol.ru/n/lipetsk",
            action_level="watchlist",
            page_type="news_background",
            summary="Новость по Липецкой области.",
        )
        self.assertEqual(classify_document_bucket(target_document), "target_watchlist")
        self.assertEqual(classify_document_bucket(non_target_document), "non_target_background")

    def test_report_uses_short_summary(self) -> None:
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary=(
                "Льготное кредитование АПК направлено на развитие российских "
                "сельхозтоваропроизводителей и переработчиков продукции АПК. "
                "Дополнительные длинные детали для проверки сокращения summary в отчете."
            ),
        )
        markdown = generate_markdown_report(
            [document],
            report_date="2026-04-30",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )
        self.assertIn("Льготное кредитование АПК направлено", markdown)
        self.assertNotIn("Дополнительные длинные детали для проверки сокращения summary в отчете.", markdown)

    def test_global_background_is_hidden_from_telegram_digest(self) -> None:
        from app.notify import telegram

        documents = [
            self._doc(
                doc_id=1,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Производство сельхозпродукции в РФ выросло",
                url="https://www.zol.ru/n/rf",
                action_level="watchlist",
                page_type="news_background",
                summary="Федеральный отраслевой фон по АПК.",
            ),
            self._doc(
                doc_id=2,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Экспорт зерна в Казахстан изменился",
                url="https://www.zol.ru/n/kz",
                action_level="watchlist",
                page_type="news_background",
                summary="Глобальный / рыночный фон.",
            ),
        ]

        captured: list[str] = []

        def _capture(text: str) -> bool:
            captured.append(text)
            return True

        with unittest.mock.patch("app.notify.telegram.send_message", side_effect=_capture):
            sent = telegram.send_digest(documents)

        self.assertTrue(sent)
        self.assertEqual(len(captured), 1)
        self.assertIn("Производство сельхозпродукции в РФ выросло", captured[0])
        self.assertNotIn("Казахстан", captured[0])

    def test_telegram_watchlist_is_limited_to_five(self) -> None:
        from app.notify import telegram

        documents = [
            self._doc(
                doc_id=index,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title=f"Производство сельхозпродукции в РФ {index}",
                url=f"https://www.zol.ru/n/rf-{index}",
                action_level="watchlist",
                page_type="news_background",
                summary="Федеральный отраслевой фон.",
            )
            for index in range(1, 8)
        ]

        captured: list[str] = []

        def _capture(text: str) -> bool:
            captured.append(text)
            return True

        with unittest.mock.patch("app.notify.telegram.send_message", side_effect=_capture):
            sent = telegram.send_digest(documents)

        self.assertTrue(sent)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].count("- Производство сельхозпродукции в РФ"), 5)

    def test_telegram_skips_inactive_and_reference_pages(self) -> None:
        from app.notify import telegram

        inactive_document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготный лизинг",
            url="https://gisp.gov.ru/nmp/measure/12447840",
            action_level="watchlist",
            page_type="new_rule",
            summary="Неактивная мера.",
        )
        inactive_document.support_status = "inactive"

        reference_document = self._doc(
            doc_id=2,
            source_name="Минсельхоз Ставропольского края - господдержка",
            region="stavropol",
            title="Анкета получателя мер государственной поддержки",
            url="https://mshsk.ru/anketa.docx",
            action_level="watchlist",
            page_type="reference_page",
            summary="Справочная анкета.",
        )

        captured: list[str] = []

        def _capture(text: str) -> bool:
            captured.append(text)
            return True

        with unittest.mock.patch("app.notify.telegram.send_message", side_effect=_capture):
            sent = telegram.send_digest([inactive_document, reference_document])

        self.assertTrue(sent)
        self.assertEqual(captured, ["Новых документов для уведомления не найдено."])

    def test_report_shows_business_facts_for_support_docs(self) -> None:
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Льготное кредитование АПК.",
        )
        document.support_status = "active"
        document.application_status = "regular"
        document.npa_number = "22-68850-00258-Р"
        document.deadline_text = "Прием заявок до 30.06.2026."
        document.terms_text = "Срок кредита: До 12 месяцев."
        document.business_signal = "Активная федеральная мера поддержки, действует на регулярной основе"

        markdown = generate_markdown_report(
            [document],
            report_date="2026-04-30",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("Статус меры: active", markdown)
        self.assertIn("Режим: regular", markdown)
        self.assertIn("НПА: 22-68850-00258-Р", markdown)
        self.assertIn("Сигнал: Активная федеральная мера поддержки", markdown)
        self.assertIn("Дедлайн/срок подачи: Прием заявок до 30.06.2026.", markdown)
        self.assertIn("Условия/срок действия: Срок кредита: До 12 месяцев.", markdown)

    def test_inactive_measure_does_not_show_deadline_as_current_in_report(self) -> None:
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Субсидии на возмещение затрат",
            url="https://gisp.gov.ru/nmp/measure/8130026",
            action_level="watchlist",
            page_type="new_rule",
            summary="Неактивная мера поддержки.",
        )
        document.support_status = "inactive"
        document.application_status = "open"
        document.deadline_text = "Прием заявок до 30.06.2026."
        document.risk_notes = "Срок найден в описании неактивной меры; не является текущим окном подачи."

        markdown = generate_markdown_report(
            [document],
            report_date="2026-04-30",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertNotIn("Дедлайн/срок подачи: Прием заявок до 30.06.2026.", markdown)
        self.assertIn("Примечание: Срок найден в описании неактивной меры", markdown)

    def test_telegram_digest_includes_business_facts_for_requires_attention(self) -> None:
        from app.notify import telegram

        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Льготное кредитование АПК.",
        )
        document.support_status = "active"
        document.application_status = "regular"
        document.deadline_text = None
        document.terms_text = "Срок кредита: До 12 месяцев."
        document.business_signal = "Активная федеральная мера поддержки, действует на регулярной основе"

        captured: list[str] = []

        def _capture(text: str) -> bool:
            captured.append(text)
            return True

        with unittest.mock.patch("app.notify.telegram.send_message", side_effect=_capture):
            sent = telegram.send_digest([document])

        self.assertTrue(sent)
        self.assertEqual(len(captured), 1)
        self.assertIn("статус: active", captured[0])
        self.assertIn("режим: regular", captured[0])
        self.assertIn("Сигнал: Активная федеральная мера поддержки", captured[0])
        self.assertNotIn("Срок кредита", captured[0])


if __name__ == "__main__":
    unittest.main()
