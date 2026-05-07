from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

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
            published_at=now,
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
                title=f"В Липецкой области изменили порядок экспортных субсидий {index}",
                url=f"https://www.zol.ru/n/lipetsk-{index}",
                action_level="watchlist",
                page_type="news_background",
                summary="Правительство региона обновило порядок предоставления субсидий экспортерам.",
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
        self.assertIn("Почему важно:", markdown)
        self.assertNotIn("Action level", markdown)

    def test_report_header_contains_key_counters(self) -> None:
        requires_attention_document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Активная федеральная мера поддержки.",
        )
        watchlist_document = self._doc(
            doc_id=2,
            source_name="Минсельхоз Ставропольского края - господдержка",
            region="stavropol",
            title="Анкета получателя мер государственной поддержки",
            url="https://mshsk.ru/anketa.docx",
            action_level="watchlist",
            page_type="reference_page",
            summary="Справочный документ.",
        )
        background_document = self._doc(
            doc_id=3,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Производство сельхозпродукции в РФ выросло",
            url="https://www.zol.ru/n/rf",
            action_level="background",
            page_type="news_background",
            summary="Федеральный фон по АПК.",
        )
        irrelevant_document = self._doc(
            doc_id=4,
            source_name="Правительство РФ - новости",
            region="federal",
            title="Навигационная страница",
            url="https://government.ru/navigation",
            action_level="irrelevant",
            page_type="navigation",
            summary="Служебная страница.",
        )

        markdown = generate_markdown_report(
            [
                requires_attention_document,
                watchlist_document,
                background_document,
                irrelevant_document,
            ],
            report_date="2026-04-30",
            period_days=7,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("## Сводка", markdown)
        self.assertIn("Проанализировано: 4", markdown)
        self.assertIn("Требует реакции: 1", markdown)
        self.assertIn("На наблюдении: 1", markdown)
        self.assertIn("Включено в сводку: 2", markdown)
        self.assertIn("Главный акцент: Льготное кредитование АПК", markdown)
        self.assertIn("## 📢 Меры и отборы", markdown)

    def test_report_does_not_fail_when_published_at_is_none(self) -> None:
        document = self._doc(
            doc_id=1,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Производство сельхозпродукции в РФ выросло",
            url="https://www.zol.ru/n/rf",
            action_level="watchlist",
            page_type="news_background",
            summary="Федеральный фон по АПК.",
        )
        document.published_at = None

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-03",
            period_days=7,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("Проанализировано: 1", markdown)

    def test_report_deduplicates_documents_with_same_url(self) -> None:
        older_document = self._doc(
            doc_id=1,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Пошлина на экспорт пшеницы останется нулевой",
            url="https://www.zol.ru/n/41337",
            action_level="watchlist",
            page_type="news_background",
            summary="Короткая версия.",
        )
        better_document = self._doc(
            doc_id=2,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Пошлина на экспорт пшеницы останется нулевой",
            url="https://www.zol.ru/n/41337",
            action_level="watchlist",
            page_type="news_background",
            summary="Более подробная версия с параметрами экспортной пошлины для отчета.",
        )
        better_document.raw_text = "Полный текст " * 120

        report_view = build_report_view(
            [older_document, better_document],
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        visible_documents = report_view.flatten()
        self.assertEqual(len(visible_documents), 1)
        self.assertEqual(visible_documents[0].id, 2)

    def test_report_selects_more_informative_version(self) -> None:
        partial_document = self._doc(
            doc_id=1,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="Просмотр",
            url="https://admkrai.krasnodar.ru/upload/subsidy.pdf",
            action_level="watchlist",
            page_type="new_rule",
            summary="Короткий OCR-фрагмент.",
        )
        full_document = self._doc(
            doc_id=2,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="О внесении изменений в порядок предоставления субсидий",
            url="https://admkrai.krasnodar.ru/upload/subsidy.pdf",
            action_level="watchlist",
            page_type="new_rule",
            summary="Полная версия документа с описанием изменений порядка предоставления субсидий.",
        )
        full_document.raw_text = "Полный текст постановления. " * 300
        full_document.deadline_text = "Срок подачи заявок до 30.06.2026."
        full_document.application_status = "open"

        report_view = build_report_view(
            [partial_document, full_document],
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        visible_documents = report_view.flatten()
        self.assertEqual(len(visible_documents), 1)
        self.assertEqual(
            visible_documents[0].title,
            "О внесении изменений в порядок предоставления субсидий",
        )
        self.assertEqual(visible_documents[0].deadline_text, "Срок подачи заявок до 30.06.2026.")

    def test_report_deduplicates_documents_with_same_title(self) -> None:
        short_document = self._doc(
            doc_id=1,
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            region="krasnodar",
            title="Объявлен отбор заявок на субсидии для АПК Краснодарского края",
            url="https://msh.krasnodar.ru/documents/subsidy-short",
            action_level="watchlist",
            page_type="selection_announcement",
            summary="Объявлен отбор заявок.",
        )
        detailed_document = self._doc(
            doc_id=2,
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            region="krasnodar",
            title="Объявлен отбор заявок на субсидии для АПК Краснодарского края",
            url="https://msh.krasnodar.ru/documents/subsidy-full",
            action_level="watchlist",
            page_type="selection_announcement",
            summary="Объявлен отбор заявок на субсидии с описанием участников, условий и порядка подачи.",
        )
        detailed_document.application_status = "open"
        detailed_document.deadline_text = "Прием заявок до 20 мая 2026 года."

        report_view = build_report_view(
            [short_document, detailed_document],
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        visible_documents = report_view.flatten()
        self.assertEqual(len(visible_documents), 1)
        self.assertEqual(visible_documents[0].url, "https://msh.krasnodar.ru/documents/subsidy-full")

    def test_report_deduplicates_government_news_and_docs_by_shared_id(self) -> None:
        news_document = self._doc(
            doc_id=21,
            source_name="Правительство РФ - новости",
            region="federal",
            title="Правительство РФ одобрило изменения в господдержке экспорта",
            url="http://government.ru/news/58669/",
            action_level="watchlist",
            page_type="news_background",
            summary="Короткая новостная версия.",
        )
        docs_document = self._doc(
            doc_id=22,
            source_name="Правительство РФ - документы",
            region="federal",
            title="Правительство РФ одобрило изменения в господдержке экспорта",
            url="http://government.ru/docs/58669/",
            action_level="watchlist",
            page_type="new_rule",
            summary="Более подробная документная версия.",
        )
        docs_document.raw_text = "Полный текст решения правительства. " * 100

        report_view = build_report_view(
            [news_document, docs_document],
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        visible_documents = report_view.flatten()
        self.assertEqual(len(visible_documents), 1)
        self.assertEqual(visible_documents[0].url, "http://government.ru/docs/58669/")

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

        with patch("app.notify.telegram.send_message", side_effect=_capture):
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

        with patch("app.notify.telegram.send_message", side_effect=_capture):
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

        with patch("app.notify.telegram.send_message", side_effect=_capture):
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

        self.assertIn("Почему важно:", markdown)
        self.assertIn("Что проверить:", markdown)
        self.assertIn("Источник:", markdown)
        self.assertIn("Активная федеральная мера поддержки", markdown)

    def test_report_uses_source_taxonomy_sections(self) -> None:
        documents = [
            self._doc(
                doc_id=1,
                source_name="Правительство РФ - документы",
                region="federal",
                title="Постановление о господдержке АПК",
                url="https://government.ru/docs/1",
                action_level="watchlist",
                page_type="new_rule",
                summary="Федеральный стратегический сигнал.",
            ),
            self._doc(
                doc_id=2,
                source_name="Нормативные акты Краснодарского края",
                region="krasnodar",
                title="Сводный отчёт о результатах проведения публичных консультаций",
                url="https://admkrai.krasnodar.ru/content/1397/",
                action_level="watchlist",
                page_type="news_background",
                summary="Региональный НПА.",
            ),
            self._doc(
                doc_id=3,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Пошлина на экспорт пшеницы останется нулевой",
                url="https://www.zol.ru/n/1",
                action_level="watchlist",
                page_type="news_background",
                summary="Новостной сигнал.",
            ),
        ]

        markdown = generate_markdown_report(
            documents,
            report_date="2026-05-03",
            period_days=7,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("## 🏛 Стратегические сигналы", markdown)
        self.assertIn("## ⚖️ Региональные изменения", markdown)
        self.assertIn("## 📰 Отраслевые сигналы", markdown)

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

        self.assertNotIn("Action level", markdown)
        self.assertIn("Почему важно:", markdown)

    def test_background_gisp_measures_are_hidden_from_visible_watchlist_report(self) -> None:
        inactive_document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Субсидии на возмещение затрат",
            url="https://gisp.gov.ru/nmp/measure/8130026",
            action_level="background",
            page_type="measure_card",
            summary="Неактивная мера поддержки.",
        )
        inactive_document.support_status = "inactive"
        inactive_document.business_signal = "Неактивная мера поддержки: оставить в справочном блоке"

        non_target_document = self._doc(
            doc_id=2,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title='Льготные заёмное финансирование РФРП Ульяновской области по программе "Финансирование АПК".',
            url="https://gisp.gov.ru/nmp/measure/12446930",
            action_level="background",
            page_type="measure_card",
            summary="Активная мера вне целевой географии.",
        )
        non_target_document.support_status = "active"
        non_target_document.application_status = "regular"
        non_target_document.business_signal = "Активная мера поддержки вне целевой географии; оставлена для справки."

        report_view = build_report_view(
            [inactive_document, non_target_document],
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertEqual(report_view.total_visible, 0)

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

        with patch("app.notify.telegram.send_message", side_effect=_capture):
            sent = telegram.send_digest([document])

        self.assertTrue(sent)
        self.assertEqual(len(captured), 1)
        self.assertIn("статус: активна", captured[0])
        self.assertIn("режим: регулярная мера", captured[0])
        self.assertIn("Сигнал: Активная федеральная мера поддержки", captured[0])
        self.assertNotIn("Срок кредита", captured[0])

    def test_report_is_human_readable_without_technical_words(self) -> None:
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
        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-05",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("Почему важно:", markdown)
        self.assertIn("## 🚨 Требует внимания", markdown)
        self.assertIn("## Итог", markdown)
        self.assertIn("Что проверить:", markdown)
        self.assertNotIn("Action level", markdown)
        self.assertNotIn("Тип страницы", markdown)

    def test_report_uses_safe_ocr_title(self) -> None:
        document = self._doc(
            doc_id=1,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="document 'wgketjm9pqmq00n60yoyq8c0z2u13z38_cfa07cc203.pdf' requires OCR extraction",
            url="https://admkrai.krasnodar.ru/upload/wgketjm9pqmq00n60yoyq8c0z2u13z38_cfa07cc203.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="OCR-текст регионального НПА.",
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-05",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("НПА Краснодарского края: документ после OCR", markdown)
        self.assertNotIn("requires OCR extraction", markdown)

    def test_news_market_background_is_not_reported_as_requires_attention(self) -> None:
        document = self._doc(
            doc_id=1,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Алжир проводит тендер по закупке пшеницы",
            url="https://www.zol.ru/n/market-algeria-tender",
            action_level="requires_attention",
            page_type="news_background",
            summary="Рыночная новость.",
        )
        document.business_signal = "Рыночный или отраслевой фон без прямого регуляторного сигнала."

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-05",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("Новых пунктов, требующих внимания, не найдено.", markdown)
        self.assertNotIn("### Алжир проводит тендер", markdown)

    def test_report_hides_noisy_watchlist_news_but_keeps_regulatory_news(self) -> None:
        noisy_document = self._doc(
            doc_id=1,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Рейтинг экспортных отгрузок и фрахта на зерновом рынке",
            url="https://www.zol.ru/n/freight-rating",
            action_level="watchlist",
            page_type="news_background",
            summary="Обзор рынка, фрахта и экспортных отгрузок.",
        )
        noisy_document.business_signal = "Новостной предвестник возможных изменений господдержки, экспорта или регулирования."
        noisy_document.raw_text = "Еженедельный обзор рынка зерна, ставки фрахта и оценки аналитиков."

        signal_document = self._doc(
            doc_id=2,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Правительство расширило программу господдержки экспортеров АПК",
            url="https://www.zol.ru/n/export-support",
            action_level="watchlist",
            page_type="news_background",
            summary="Изменены параметры программы поддержки и экспортного финансирования.",
        )
        signal_document.business_signal = "Новостной предвестник возможных изменений господдержки, экспорта или регулирования."
        signal_document.raw_text = "Правительство утвердило изменения программы финансирования и меры поддержки экспорта АПК."

        markdown = generate_markdown_report(
            [noisy_document, signal_document],
            report_date="2026-05-05",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertNotIn("Рейтинг экспортных отгрузок", markdown)
        self.assertIn("Правительство расширило программу господдержки экспортеров АПК", markdown)

    def test_report_uses_role_specific_action_hints(self) -> None:
        support_document = self._doc(
            doc_id=1,
            source_name="Минсельхоз Ростовской области - господдержка",
            region="rostov",
            title="Объявление о проведении отбора на предоставление субсидии",
            url="https://mcx.donland.ru/selection",
            action_level="requires_attention",
            page_type="selection_announcement",
            summary="Открыт прием заявок.",
        )
        support_document.application_status = "open"
        support_document.deadline_text = "Прием заявок до 20.05.2026."

        regional_npa = self._doc(
            doc_id=2,
            source_name="Право Ростовской области",
            region="rostov",
            title="Постановление о внесении изменений в порядок предоставления субсидий",
            url="https://pravo.donland.ru/doc/view/id/42",
            action_level="watchlist",
            page_type="new_rule",
            summary="Изменен порядок предоставления субсидий.",
        )

        news_document = self._doc(
            doc_id=3,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Отраслевая новость по рынку зерна",
            url="https://www.zol.ru/n/market-rf",
            action_level="watchlist",
            page_type="news_background",
            summary="Фоновая новость без срочной реакции.",
        )

        markdown = generate_markdown_report(
            [support_document, regional_npa, news_document],
            report_date="2026-05-05",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            include_market_background=True,
        )

        self.assertIn("Проверить сроки подачи и ответственного", markdown)
        self.assertIn("Проверить изменения порядка субсидирования", markdown)
        self.assertIn("Оставить как отраслевой фон, без срочной реакции.", markdown)

    def test_report_clips_long_titles_cleanly(self) -> None:
        long_title = (
            "Постановление Правительства Ростовской области о внесении изменений в порядок предоставления "
            "субсидий сельскохозяйственным товаропроизводителям и перерабатывающим предприятиям региона"
        )
        document = self._doc(
            doc_id=1,
            source_name="Право Ростовской области",
            region="rostov",
            title=long_title,
            url="https://pravo.donland.ru/doc/view/id/very-long-title",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Изменения порядка предоставления субсидий.",
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-05",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("### Постановление Правительства Ростовской области", markdown)
        self.assertIn("...", markdown)
        self.assertIn("https://pravo.donland.ru/doc/view/id/very-long-title", markdown)
        self.assertNotIn(long_title + "\n- Источник", markdown)


if __name__ == "__main__":
    unittest.main()
