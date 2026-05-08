from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.models import ActionLevel, PageType, RawDocument
from app.reports.markdown_report import (
    build_report_view,
    classify_document_bucket,
    generate_markdown_report,
)
from app.storage import init_db, save_document_enrichment
from app.llm.enrichment import EnrichmentResult


class ReportGenerationSmokeTest(unittest.TestCase):
    def _db_path(self, name: str):
        from pathlib import Path

        path = Path("data/test_artifacts") / name
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
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

    def test_report_uses_enrichment_when_available(self) -> None:
        db_path = self._db_path("report_enrichment.db")
        init_db(db_path)
        document = self._doc(
            doc_id=500,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Базовая summary.",
        )
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult(
                executive_summary="Executive summary для руководителя.",
                business_impact="Новая редакция меры меняет условия участия для заемщиков АПК.",
                recommended_action="Проверить применимость обновленных условий и ответственного.",
                deadline_hint="До 30 июня 2026 года.",
                confidence=0.8,
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-07",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertIn("Executive summary для руководителя.", markdown)
        self.assertIn("Новая редакция меры меняет условия участия для заемщиков АПК.", markdown)
        self.assertIn("Проверить применимость меры, сроки и ответственного.", markdown)
        self.assertIn("До 30 июня 2026 года.", markdown)

    def test_report_strips_legacy_ai_prefixes_from_enrichment(self) -> None:
        db_path = self._db_path("report_enrichment_legacy_prefix.db")
        init_db(db_path)
        document = self._doc(
            doc_id=505,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Базовая summary.",
        )
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult(
                executive_summary="AI-сводка: Executive summary для руководителя.",
                business_impact="AI-оценка влияния: Новая редакция меры меняет условия участия для заемщиков АПК.",
                recommended_action="AI-рекомендация: Проверить применимость обновленных условий и ответственного.",
                deadline_hint="До 30 июня 2026 года.",
                confidence=0.8,
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-07",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertIn("Executive summary для руководителя.", markdown)
        self.assertNotIn("AI-сводка:", markdown)
        self.assertNotIn("AI-оценка влияния:", markdown)
        self.assertNotIn("AI-рекомендация:", markdown)

    def test_report_falls_back_when_enrichment_missing(self) -> None:
        document = self._doc(
            doc_id=501,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Базовая summary.",
        )

        with patch("app.reports.markdown_report.list_document_enrichments", return_value={}):
            markdown = generate_markdown_report(
                [document],
                report_date="2026-05-07",
                relevant_only=True,
                action_levels=["requires_attention", "watchlist"],
            )

        self.assertIn("Почему важно: impact", markdown)
        self.assertIn("Что проверить: Проверить применимость меры, сроки и ответственного.", markdown)

    def test_report_ignores_errored_or_low_confidence_enrichment(self) -> None:
        db_path = self._db_path("report_enrichment_low_conf.db")
        init_db(db_path)
        document = self._doc(
            doc_id=502,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Базовая summary.",
        )
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult(
                executive_summary="Не использовать",
                business_impact="Не использовать",
                recommended_action="Не использовать",
                confidence=0.4,
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-07",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertNotIn("Не использовать", markdown)
        self.assertIn("Почему важно: impact", markdown)

    def test_report_ignores_errored_enrichment(self) -> None:
        db_path = self._db_path("report_enrichment_error.db")
        init_db(db_path)
        document = self._doc(
            doc_id=504,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Базовая summary.",
        )
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult(
                executive_summary="Не использовать",
                business_impact="Не использовать",
                recommended_action="Не использовать",
                confidence=0.9,
                error="timeout",
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-07",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertNotIn("Не использовать", markdown)
        self.assertIn("Почему важно: impact", markdown)

    def test_report_clips_long_enrichment_safely(self) -> None:
        db_path = self._db_path("report_enrichment_clip.db")
        init_db(db_path)
        document = self._doc(
            doc_id=503,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Базовая summary.",
        )
        long_text = "Очень длинное executive пояснение " * 20
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult(
                executive_summary=long_text,
                business_impact=long_text,
                recommended_action=long_text,
                confidence=0.8,
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-07",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertIn("...", markdown)
        self.assertNotIn(long_text.strip(), markdown)

    def test_generic_mock_enrichment_does_not_override_specific_reason_and_action(self) -> None:
        db_path = self._db_path("report_enrichment_generic_preference.db")
        init_db(db_path)
        document = self._doc(
            doc_id=904,
            source_name="Право Ставропольского края",
            region="stavropol",
            title="О внесении изменений в порядок предоставления субсидий",
            url="https://pravo.stavregion.ru/document/904",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Базовая summary.",
        )
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult(
                executive_summary="Документ содержит изменения в порядке предоставления поддержки; требуется проверка условий и сроков.",
                business_impact="Сигнал может повлиять на контекст господдержки и требует наблюдения со стороны GR.",
                recommended_action="Оценить срочность сигнала и определить следующий GR-шаг.",
                confidence=0.8,
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-08",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertIn("Кратко: Документ содержит изменения в порядке предоставления поддержки", markdown)
        self.assertIn("### Изменены условия субсидирования", markdown)
        self.assertIn("Почему важно: Изменены условия субсидирования", markdown)
        self.assertIn("Проверить изменения порядка субсидирования и сроки вступления.", markdown)
        self.assertNotIn("Сигнал может повлиять на контекст господдержки", markdown)
        self.assertNotIn("Оценить срочность сигнала", markdown)

    def test_urgent_regional_npa_report_uses_stronger_reason_and_specific_hint(self) -> None:
        document = self._doc(
            doc_id=150,
            source_name="Право Ставропольского края",
            region="stavropol",
            title="О внесении изменений в порядок предоставления субсидий",
            url="https://pravo.stavregion.ru/document/98765",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Изменения порядка предоставления субсидий.",
        )
        document.business_signal = "Региональный НПА по профильной теме: оставить в наблюдении."

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-07",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("### Изменены условия субсидирования", markdown)
        self.assertIn("Почему важно: Изменены условия субсидирования", markdown)
        self.assertIn("Проверить изменения порядка субсидирования и сроки вступления.", markdown)
        self.assertNotIn("оставить в наблюдении", markdown)

    def test_urgent_news_report_does_not_use_background_hint(self) -> None:
        document = self._doc(
            doc_id=151,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Пошлина на экспорт пшеницы из РФ останется нулевой",
            url="https://www.zol.ru/n/rf-duty-1",
            action_level="requires_attention",
            page_type="news_background",
            summary="Экспортная новость с прямым GR-сигналом.",
        )
        document.business_signal = "Есть признаки изменения экспортных условий для российского рынка."

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-07",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("Проверить влияние пошлины/торгового регулирования на рынок и контрагентов.", markdown)
        self.assertNotIn("Оставить как отраслевой фон.", markdown)

    def test_credit_news_report_keeps_credit_action_even_with_trade_words(self) -> None:
        document = self._doc(
            doc_id=152,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Минсельхоз предложил новые условия льготного кредитования АПК",
            url="https://www.zol.ru/n/credit-with-trade-words",
            action_level="requires_attention",
            page_type="news_background",
            summary="Обновление условий льготного кредитования для АПК.",
        )
        document.raw_text = (
            "Минсельхоз предложил обновить условия льготного кредитования АПК. "
            "В тексте также упоминаются экспортные пошлины на смежном рынке."
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-08",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("Почему важно: Обновлены условия льготного кредитования", markdown)
        self.assertIn("Что проверить: Проверить условия кредитования, сроки и применимость для АПК.", markdown)
        self.assertNotIn("Проверить влияние пошлины/торгового регулирования на рынок и контрагентов.", markdown)

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
        self.assertIn("По источникам:", markdown)
        self.assertIn("## 📢 Меры и отборы", markdown)

    def test_report_summary_includes_source_heat_line_for_visible_documents_only(self) -> None:
        visible_one = self._doc(
            doc_id=801,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="Изменения по субсидиям",
            url="https://admkrai.krasnodar.ru/upload/a.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Изменения порядка предоставления субсидий.",
        )
        visible_two = self._doc(
            doc_id=802,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://www.zol.ru/n/41378",
            action_level="requires_attention",
            page_type="news_background",
            summary="Обновление условий льготного кредитования для АПК.",
        )
        hidden_background = self._doc(
            doc_id=803,
            source_name="Правительство РФ - новости",
            region="federal",
            title="Глобальный рыночный фон",
            url="http://government.ru/news/hidden/",
            action_level="background",
            page_type="news_background",
            summary="Фоновая новость без срочной реакции.",
        )

        markdown = generate_markdown_report(
            [visible_one, visible_two, hidden_background],
            report_date="2026-05-08",
            relevant_only=False,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("По источникам: admkrai.krasnodar.ru: 1; zol.ru: 1", markdown)
        self.assertNotIn("government.ru", markdown)

    def test_report_deduplicates_main_focus_headlines(self) -> None:
        documents = [
            self._doc(
                doc_id=910,
                source_name="Право Краснодарского края",
                region="krasnodar",
                title="О внесении изменений в порядок предоставления субсидий",
                url="https://example.test/subsidy-1",
                action_level="requires_attention",
                page_type="new_rule",
                summary="Изменения по субсидиям.",
            ),
            self._doc(
                doc_id=911,
                source_name="Право Ростовской области",
                region="rostov",
                title="О внесении изменений в порядок предоставления субсидий для АПК",
                url="https://example.test/subsidy-2",
                action_level="requires_attention",
                page_type="new_rule",
                summary="Изменения по субсидиям для АПК.",
            ),
            self._doc(
                doc_id=912,
                source_name="ГИСП - меры поддержки АПК",
                region="federal",
                title="Льготное кредитование АПК",
                url="https://example.test/credit",
                action_level="requires_attention",
                page_type="measure_card",
                summary="Активная мера поддержки.",
            ),
        ]

        markdown = generate_markdown_report(
            documents,
            report_date="2026-05-08",
            period_days=7,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("Главный акцент:", markdown)
        self.assertEqual(markdown.count("Изменены условия субсидирования"), 3)
        self.assertNotIn(
            "Главный акцент: Изменены условия субсидирования; Изменены условия субсидирования;",
            markdown,
        )

    def test_weak_strategy_items_are_hidden_from_executive_report(self) -> None:
        weak_strategy = self._doc(
            doc_id=920,
            source_name="Правительство РФ - документы",
            region="federal",
            title="Концепция развития пассажирского железнодорожного сообщения",
            url="https://example.test/railway-concept",
            action_level="watchlist",
            page_type="new_rule",
            summary="Пассажирская железнодорожная инфраструктура и мостовые объекты.",
        )
        strong_strategy = self._doc(
            doc_id=921,
            source_name="Правительство РФ - документы",
            region="federal",
            title="Изменения в господдержке экспорта продукции АПК",
            url="https://example.test/export-apk",
            action_level="watchlist",
            page_type="new_rule",
            summary="Экспорт АПК, субсидии и квоты для российских поставок.",
        )

        markdown = generate_markdown_report(
            [weak_strategy, strong_strategy],
            report_date="2026-05-08",
            period_days=7,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("Изменения в господдержке экспорта продукции АПК", markdown)
        self.assertNotIn("пассажирского железнодорожного сообщения", markdown)

    def test_markdown_strategy_section_hides_weak_strategy_items(self) -> None:
        documents = [
            self._doc(
                doc_id=930,
                source_name="Правительство РФ - новости",
                region="federal",
                title="Правительство направит опережающее финансирование на реконструкцию моста в Калининградской области",
                url="https://example.test/bridge",
                action_level="watchlist",
                page_type="new_rule",
                summary="Реконструкция моста и транспортной инфраструктуры региона.",
            ),
            self._doc(
                doc_id=931,
                source_name="Правительство РФ - документы",
                region="federal",
                title="Правительство утвердило Концепцию развития перевозок пассажиров железнодорожным транспортом",
                url="https://example.test/passenger-rail",
                action_level="watchlist",
                page_type="new_rule",
                summary="Концепция пассажирских железнодорожных перевозок.",
            ),
            self._doc(
                doc_id=932,
                source_name="Правительство РФ - новости",
                region="federal",
                title="Александр Новак провёл совещание по ситуации в экономике",
                url="https://example.test/economy-meeting",
                action_level="watchlist",
                page_type="news_background",
                summary="Совещание по макроэкономическим показателям.",
            ),
            self._doc(
                doc_id=933,
                source_name="Правительство РФ - новости",
                region="federal",
                title="Дмитрий Чернышенко: Для участия в программе «Земский учитель» подано уже более 8 тыс. заявок",
                url="https://example.test/teacher",
                action_level="watchlist",
                page_type="news_background",
                summary="Итоги заявочной кампании образовательной программы.",
            ),
        ]
        documents[0].business_signal = "Стратегический федеральный сигнал по господдержке или порядку регулирования."
        documents[0].impact = "Документ стоит держать на наблюдении: тема может затронуть АПК."
        documents[1].business_signal = "Стратегический федеральный сигнал по господдержке или порядку регулирования."
        documents[1].impact = "Документ стоит держать на наблюдении: тема может затронуть АПК."
        documents[2].business_signal = "Стратегический федеральный сигнал по господдержке или порядку регулирования."
        documents[2].impact = "Документ стоит держать на наблюдении: тема может затронуть АПК."
        documents[3].business_signal = "Стратегический федеральный сигнал по господдержке или порядку регулирования."
        documents[3].impact = "Документ стоит держать на наблюдении: тема может затронуть АПК."

        markdown = generate_markdown_report(
            documents,
            report_date="2026-05-08",
            period_days=7,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("## 🏛 Стратегические сигналы", markdown)
        self.assertIn("Новых стратегических сигналов не найдено.", markdown)
        self.assertNotIn("реконструкцию моста", markdown)
        self.assertNotIn("пассажиров железнодорожным транспортом", markdown)
        self.assertNotIn("ситуации в экономике", markdown)
        self.assertNotIn("Земский учитель", markdown)

    def test_markdown_strategy_section_hides_budget_credit_and_naukograd_noise(self) -> None:
        documents = [
            self._doc(
                doc_id=935,
                source_name="Правительство РФ - новости",
                region="federal",
                title="Правительство списало часть задолженности по бюджетным кредитам ещё 21 региону",
                url="https://example.test/budget-credit-regions",
                action_level="watchlist",
                page_type="new_rule",
                summary="Решение по бюджетным кредитам регионов без профильного отраслевого контекста.",
            ),
            self._doc(
                doc_id=936,
                source_name="Правительство РФ - новости",
                region="federal",
                title="Правительство направит финансирование на комплексное развитие наукоградов",
                url="https://example.test/naukograds",
                action_level="watchlist",
                page_type="new_rule",
                summary="Финансирование наукоградов и городской инфраструктуры.",
            ),
        ]
        documents[0].business_signal = "Стратегический федеральный сигнал по господдержке или порядку регулирования."
        documents[1].business_signal = "Стратегический федеральный сигнал по господдержке или порядку регулирования."

        markdown = generate_markdown_report(
            documents,
            report_date="2026-05-08",
            period_days=7,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("Новых стратегических сигналов не найдено.", markdown)
        self.assertNotIn("бюджетным кредитам", markdown)
        self.assertNotIn("развитие наукоградов", markdown)

    def test_markdown_strategy_section_keeps_relevant_apk_strategy_item(self) -> None:
        document = self._doc(
            doc_id=934,
            source_name="Правительство РФ - документы",
            region="federal",
            title="Правительство расширило программу господдержки экспортеров АПК",
            url="https://example.test/apk-export-support",
            action_level="watchlist",
            page_type="new_rule",
            summary="Поддержка экспорта АПК, субсидии и параметры программы финансирования.",
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-08",
            period_days=7,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("## 🏛 Стратегические сигналы", markdown)
        self.assertIn("Правительство расширило программу господдержки экспортеров АПК", markdown)

    def test_markdown_strategy_section_hides_novak_macro_meeting_with_food_in_raw_text(self) -> None:
        document = self._doc(
            doc_id=937,
            source_name="Правительство РФ - новости",
            region="federal",
            title="Александр Новак провёл совещание по ситуации в экономике",
            url="https://example.test/novak-economy",
            action_level="watchlist",
            page_type="new_rule",
            summary="Совещание по макроэкономическим показателям и инфляции.",
        )
        document.raw_text = (
            "Обсуждались макроэкономические показатели, инфляция и цены, включая продовольственные товары, "
            "а также вопросы экспорта и тарифного регулирования."
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-08",
            period_days=7,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("Новых стратегических сигналов не найдено.", markdown)
        self.assertNotIn("Александр Новак провёл совещание по ситуации в экономике", markdown)

    def test_report_header_uses_explicit_period_label_for_seven_days(self) -> None:
        document = self._doc(
            doc_id=901,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/901",
            action_level="requires_attention",
            page_type="measure_card",
            summary="summary",
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-08",
            period_days=7,
            period_label="последние 7 дней",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("Период: последние 7 дней", markdown)
        self.assertNotIn("Последние 7 дн.", markdown)

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
            title="Правительство РФ одобрило изменения в господдержке экспорта АПК",
            url="http://government.ru/news/58669/",
            action_level="watchlist",
            page_type="news_background",
            summary="Короткая новостная версия.",
        )
        docs_document = self._doc(
            doc_id=22,
            source_name="Правительство РФ - документы",
            region="federal",
            title="Правительство РФ одобрило изменения в господдержке экспорта АПК",
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

        with patch("app.reports.markdown_report.list_document_enrichments", return_value={}):
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
            with patch("app.notify.telegram_formatter.list_document_enrichments", return_value={}):
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
        document.raw_text = "Распознанный текст отсутствует."

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-05",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("НПА Краснодарского края: документ после OCR", markdown)
        self.assertNotIn("requires OCR extraction", markdown)

    def test_weak_ocr_placeholder_is_removed_from_urgent_report_section(self) -> None:
        document = self._doc(
            doc_id=2,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="document 'weak-ocr.pdf' requires OCR extraction",
            url="https://admkrai.krasnodar.ru/upload/iblock/d69/weak-ocr.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="OCR placeholder документ.",
        )
        document.raw_text = "Распознанный текст отсутствует."

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-08",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("## 🚨 Требует внимания", markdown)
        self.assertIn("Новых пунктов, требующих внимания, не найдено.", markdown)
        self.assertIn("## ⚖️ Региональные изменения", markdown)
        self.assertIn("НПА Краснодарского края: документ после OCR", markdown)
        urgent_block = markdown.split("## 🚨 Требует внимания", 1)[1].split("## 📢 Меры и отборы", 1)[0]
        self.assertNotIn("НПА Краснодарского края: документ после OCR", urgent_block)

    def test_fallback_titled_weak_ocr_placeholder_is_not_rendered_in_urgent_section(self) -> None:
        document = self._doc(
            doc_id=3,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="НПА Краснодарского края: документ после OCR",
            url="https://admkrai.krasnodar.ru/upload/iblock/d69/fallback-ocr.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="OCR placeholder документ.",
        )
        document.raw_text = (
            "Документ после OCR требует ручной проверки. Распознанный текст частично отсутствует, "
            "структура фрагментарна и не позволяет уверенно выделить условия меры."
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-08",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        urgent_block = markdown.split("## 🚨 Требует внимания", 1)[1].split("## 📢 Меры и отборы", 1)[0]
        regional_block = markdown.split("## ⚖️ Региональные изменения", 1)[1].split("## 🏛 Стратегические сигналы", 1)[0]
        self.assertNotIn("НПА Краснодарского края: документ после OCR", urgent_block)
        self.assertIn("НПА Краснодарского края: документ после OCR", regional_block)

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

        self.assertIn("Проверить сроки подачи и ответственного.", markdown)
        self.assertIn("Проверить изменения порядка субсидирования и сроки вступления.", markdown)
        self.assertIn("Оставить как отраслевой фон.", markdown)

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

        self.assertIn("### Изменены условия субсидирования", markdown)
        self.assertIn("https://pravo.donland.ru/doc/view/id/very-long-title", markdown)
        self.assertNotIn(long_title, markdown)

    def test_report_compression_does_not_mutate_original_title(self) -> None:
        original_title = "О внесении изменений в порядок предоставления субсидий сельхозтоваропроизводителям"
        document = self._doc(
            doc_id=1300,
            source_name="Право Ростовской области",
            region="rostov",
            title=original_title,
            url="https://pravo.donland.ru/doc/view/id/1300",
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

        self.assertIn("### Изменены условия субсидирования", markdown)
        self.assertEqual(document.title, original_title)


class StrategyNoiseMdRegressionTest(unittest.TestCase):
    """Markdown-level regression: strategy items without agro context must not leak."""

    def _strategy_doc(
        self,
        *,
        doc_id: int,
        title: str,
        summary: str = "",
        raw_text: str = "",
        page_type: PageType = "new_rule",
        action_level: ActionLevel = "watchlist",
    ) -> RawDocument:
        now = datetime.now(timezone.utc)
        return RawDocument(
            id=doc_id,
            source_name="Правительство РФ - документы",
            source_url="https://government.ru/docs/",
            level="federal",
            region="federal",
            title=title,
            url=f"https://government.ru/docs/{doc_id}/",
            published_at=now,
            collected_at=now,
            content_hash=f"md-strategy-{doc_id}",
            raw_text=raw_text,
            is_relevant=True,
            relevance_reason="reason",
            importance="medium",
            action_level=action_level,
            page_type=page_type,
            summary=summary,
            impact="impact",
            topic="topic",
        )

    def _generate(self, documents: list) -> str:
        return generate_markdown_report(
            documents,
            report_date="2026-05-08",
            period_days=7,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

    def test_passenger_rail_with_tariff_in_raw_text_absent_from_strategy_section(self) -> None:
        document = self._strategy_doc(
            doc_id=500,
            title="Правительство утвердило Концепцию развития перевозок пассажиров железнодорожным транспортом",
            summary="Концепция пассажирских железнодорожных перевозок в пригородном сообщении.",
            raw_text=(
                "Концепция предусматривает развитие тарифной политики пригородных перевозок, "
                "обновление подвижного состава и поэтапное финансирование инфраструктуры."
            ),
        )
        markdown = self._generate([document])
        self.assertIn("Новых стратегических сигналов не найдено.", markdown)
        self.assertNotIn("пассажиров железнодорожным", markdown)

    def test_regional_budget_credits_with_finance_in_raw_text_absent_from_strategy_section(self) -> None:
        document = self._strategy_doc(
            doc_id=501,
            title="Правительство списало задолженность по бюджетным кредитам ещё 21 региону",
            summary="Решение по бюджетным кредитам регионов.",
            raw_text=(
                "Решение принято в рамках реструктуризации бюджетной задолженности. "
                "Финансирование направлено на погашение долговых обязательств субъектов."
            ),
        )
        markdown = self._generate([document])
        self.assertIn("Новых стратегических сигналов не найдено.", markdown)
        self.assertNotIn("бюджетным кредитам", markdown)

    def test_apk_export_support_with_selkhozprodukt_in_raw_text_visible_in_strategy_section(self) -> None:
        document = self._strategy_doc(
            doc_id=502,
            title="Правительство расширило программу поддержки экспорта АПК",
            summary="Параметры программы финансирования экспорта сельхозпродукции.",
            raw_text=(
                "Программа охватывает сельхозпроизводителей зерновых и масличных культур. "
                "Субсидии на экспорт зерна и подсолнечника."
            ),
        )
        markdown = self._generate([document])
        self.assertIn("Правительство расширило программу поддержки экспорта АПК", markdown)
        self.assertNotIn("Новых стратегических сигналов не найдено.", markdown)

    def test_fertilizer_regulation_with_udobrenie_in_raw_text_visible_in_strategy_section(self) -> None:
        document = self._strategy_doc(
            doc_id=503,
            title="Правительство ввело квоты на экспорт азотных удобрений",
            summary="Квотирование экспорта удобрений.",
            raw_text=(
                "Введены квоты на вывоз азотных и сложных удобрений в целях насыщения внутреннего рынка АПК."
            ),
        )
        markdown = self._generate([document])
        self.assertIn("Правительство ввело квоты на экспорт азотных удобрений", markdown)


class TitleDisambiguationReportTest(unittest.TestCase):
    """Regression tests: title collision in markdown report must produce distinct headings."""

    def _doc(
        self,
        *,
        doc_id: int,
        title: str,
        summary: str = "",
        url: str = "https://example.com/doc",
        source_name: str = "Нормативные акты Краснодарского края",
        region: str = "krasnodar",
        action_level: ActionLevel = "requires_attention",
        page_type: PageType = "new_rule",
        npa_number: str | None = None,
    ) -> RawDocument:
        now = datetime.now(timezone.utc)
        doc = RawDocument(
            id=doc_id,
            source_name=source_name,
            source_url=url,
            level="regional",
            region=region,
            title=title,
            url=url,
            published_at=now,
            collected_at=now,
            content_hash=f"dedup-{doc_id}",
            raw_text="",
            is_relevant=True,
            relevance_reason="reason",
            importance="high" if action_level == "requires_attention" else "medium",
            action_level=action_level,
            page_type=page_type,
            summary=summary or "Изменены условия субсидирования.",
            impact="impact",
            topic="topic",
        )
        if npa_number is not None:
            doc.npa_number = npa_number
        return doc

    def _generate(self, docs: list[RawDocument]) -> str:
        return generate_markdown_report(
            docs,
            report_date="2026-05-08",
            period_days=7,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

    def test_two_documents_colliding_on_compressed_title_render_with_distinct_headings(self) -> None:
        doc1 = self._doc(
            doc_id=601,
            title="Об утверждении порядка предоставления субсидий на молочное скотоводство",
            url="https://admkrai.krasnodar.ru/upload/iblock/9a3/doc1.pdf",
        )
        doc2 = self._doc(
            doc_id=602,
            title="Об утверждении порядка предоставления субсидий на развитие растениеводства",
            url="https://admkrai.krasnodar.ru/upload/iblock/c24/doc2.pdf",
        )
        markdown = self._generate([doc1, doc2])
        self.assertIn("Утверждены условия субсидирования", markdown)
        # Both items must be present, but the heading must NOT appear twice identically
        self.assertNotEqual(markdown.count("### Утверждены условия субсидирования\n"), 2)
        # Both URLs must be present (both items rendered)
        self.assertIn("iblock/9a3", markdown)
        self.assertIn("iblock/c24", markdown)

    def test_npa_number_field_produces_npa_suffix(self) -> None:
        # Embed NPA numbers in titles so _numeric_tokens differ → not grouped by select_best_report_documents.
        # Both still compress to "Утверждены условия субсидирования" via _approval_headline.
        doc1 = self._doc(
            doc_id=610,
            title="Постановление №214 об утверждении порядка предоставления субсидий на молочное скотоводство",
            url="https://admkrai.krasnodar.ru/upload/iblock/9a3/d1.pdf",
            npa_number="214",
        )
        doc2 = self._doc(
            doc_id=611,
            title="Постановление №318 об утверждении порядка предоставления субсидий на молочное скотоводство",
            url="https://admkrai.krasnodar.ru/upload/iblock/c24/d2.pdf",
            npa_number="318",
        )
        markdown = self._generate([doc1, doc2])
        self.assertIn("(№214)", markdown)
        self.assertIn("(№318)", markdown)

    def test_iblock_url_fragment_used_when_no_npa_and_no_date_in_title(self) -> None:
        # Test disambiguate_visible_titles directly: identical OCR placeholder titles get
        # grouped/merged by select_best_report_documents, so we bypass the pipeline here.
        from app.user_facing import disambiguate_visible_titles

        ocr_title = "Document 'abc.pdf' requires ocr extraction"
        doc1 = self._doc(
            doc_id=620,
            title=ocr_title,
            url="https://admkrai.krasnodar.ru/upload/iblock/9a3/abc.pdf",
        )
        doc2 = self._doc(
            doc_id=621,
            title=ocr_title,
            url="https://admkrai.krasnodar.ru/upload/iblock/c24/xyz.pdf",
        )
        title_map = disambiguate_visible_titles([doc1, doc2], max_chars=90)
        self.assertIsNotNone(doc1.id)
        self.assertIsNotNone(doc2.id)
        self.assertIn("документ 9a3", title_map[doc1.id])  # type: ignore[index]
        self.assertIn("документ c24", title_map[doc2.id])  # type: ignore[index]

    def test_unique_title_has_no_suffix_appended(self) -> None:
        doc = self._doc(
            doc_id=630,
            title="Об утверждении порядка предоставления субсидий на молочное скотоводство",
            url="https://admkrai.krasnodar.ru/upload/iblock/9a3/only.pdf",
        )
        markdown = self._generate([doc])
        self.assertIn("Утверждены условия субсидирования", markdown)
        self.assertNotIn("(документ", markdown)
        self.assertNotIn("(№", markdown)

    def test_original_document_title_is_not_mutated(self) -> None:
        original_title = "Об утверждении порядка предоставления субсидий на молочное скотоводство"
        doc1 = self._doc(
            doc_id=640,
            title=original_title,
            url="https://admkrai.krasnodar.ru/upload/iblock/9a3/d1.pdf",
        )
        doc2 = self._doc(
            doc_id=641,
            title=original_title,
            url="https://admkrai.krasnodar.ru/upload/iblock/c24/d2.pdf",
        )
        self._generate([doc1, doc2])
        self.assertEqual(doc1.title, original_title)
        self.assertEqual(doc2.title, original_title)

    def test_title_suffix_stays_within_reasonable_length(self) -> None:
        doc1 = self._doc(
            doc_id=650,
            title="Об утверждении порядка предоставления субсидий",
            url="https://admkrai.krasnodar.ru/upload/iblock/9a3/d1.pdf",
            npa_number="214",
        )
        doc2 = self._doc(
            doc_id=651,
            title="Об утверждении порядка предоставления субсидий",
            url="https://admkrai.krasnodar.ru/upload/iblock/c24/d2.pdf",
            npa_number="318",
        )
        markdown = self._generate([doc1, doc2])
        for line in markdown.splitlines():
            if line.startswith("### "):
                self.assertLessEqual(len(line) - 4, 95)  # heading text ≤ 95 chars


if __name__ == "__main__":
    unittest.main()
