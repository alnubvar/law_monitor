from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.models import RawDocument
from app.user_facing import (
    build_executive_action,
    build_executive_reason,
    is_meaningful_executive_highlight,
    select_executive_summary,
)


class UserFacingIntentCoherenceTest(unittest.TestCase):
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
        raw_text: str = "",
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
            content_hash=f"user-facing-{doc_id}",
            raw_text=raw_text,
            is_relevant=True,
            relevance_reason="reason",
            importance="high" if action_level == "requires_attention" else "medium",
            action_level=action_level,
            page_type=page_type,
            summary=summary,
            impact="impact",
            topic="topic",
        )

    def test_credit_signal_keeps_credit_reason_and_action_even_with_trade_words(self) -> None:
        document = self._doc(
            doc_id=1,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Минсельхоз предложил новые условия льготного кредитования АПК",
            url="https://www.zol.ru/n/mixed-credit-trade",
            action_level="requires_attention",
            page_type="news_background",
            summary="Обсуждаются льготные кредиты для АПК и рыночный контекст.",
            raw_text=(
                "Минсельхоз предложил обновить условия льготного кредитования АПК. "
                "В материале также упоминаются экспортные пошлины на смежном рынке."
            ),
        )

        reason = build_executive_reason(document)
        action = build_executive_action(document)

        self.assertEqual(reason, "Обновлены условия льготного кредитования")
        self.assertEqual(action, "Проверить условия кредитования, сроки и применимость для АПК.")

    def test_export_duty_signal_uses_trade_reason_and_action(self) -> None:
        document = self._doc(
            doc_id=2,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Пошлина на экспорт пшеницы из РФ останется нулевой",
            url="https://www.zol.ru/n/export-duty",
            action_level="requires_attention",
            page_type="news_background",
            summary="Экспортная пошлина для российского рынка.",
            raw_text="Обсуждаются экспортная пошлина и параметры внешнеторгового регулирования.",
        )

        reason = build_executive_reason(document)
        action = build_executive_action(document)

        self.assertEqual(reason, "Подготовлены экспортные ограничения")
        self.assertEqual(action, "Проверить влияние пошлины/торгового регулирования на рынок и контрагентов.")

    def test_market_observation_uses_passive_reason_and_action(self) -> None:
        document = self._doc(
            doc_id=3,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Обзор рынка зерна и фрахта",
            url="https://www.zol.ru/n/market-watch",
            action_level="watchlist",
            page_type="news_background",
            summary="Фоновое наблюдение по рынку.",
        )

        reason = build_executive_reason(document)
        action = build_executive_action(document)

        self.assertEqual(reason, "Рынок оставлен на наблюдении")
        self.assertEqual(action, "Оставить как отраслевой фон.")

    def test_background_news_section_stays_passive(self) -> None:
        document = self._doc(
            doc_id=4,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Рыночная аналитика без регуляторных решений",
            url="https://www.zol.ru/n/background",
            action_level="background",
            page_type="news_background",
            summary="Фоновая рыночная аналитика.",
        )

        reason = build_executive_reason(document, section="news_signals")
        action = build_executive_action(document, section="news_signals")

        self.assertEqual(reason, "Рынок оставлен на наблюдении")
        self.assertEqual(action, "Оставить как отраслевой фон.")

    def test_strategy_item_uses_strategy_reason_and_action(self) -> None:
        document = self._doc(
            doc_id=5,
            source_name="Правительство РФ - документы",
            region="federal",
            title="Правительство расширило программу господдержки экспортеров АПК",
            url="https://government.ru/docs/12345/",
            action_level="watchlist",
            page_type="new_rule",
            summary="Стратегический федеральный сигнал по поддержке АПК.",
            raw_text="Программа затрагивает господдержку и регулирование в АПК.",
        )

        reason = build_executive_reason(document, section="strategy_signals")
        action = build_executive_action(document, section="strategy_signals")

        self.assertEqual(reason, "Стратегический федеральный сигнал по господдержке или порядку регулирования.")
        self.assertEqual(action, "Оценить влияние на регулирование.")

    def test_mcx_official_support_news_uses_specific_watchlist_wording(self) -> None:
        document = self._doc(
            doc_id=51,
            source_name="Минсельхоз России - новости",
            region="federal",
            title="Правительство расширило меры господдержки производителей молока",
            url="https://mcx.gov.ru/press-service/news/pravitelstvo-rasshirilo-mery-gospodderzhki-proizvoditeley-moloka/",
            action_level="watchlist",
            page_type="news_background",
            summary="Официальная новость о расширении мер господдержки производителей молока.",
        )

        reason = build_executive_reason(document)
        action = build_executive_action(document)

        self.assertEqual(reason, "Изменены условия поддержки")
        self.assertEqual(action, "Проверить влияние на условия поддержки.")

    def test_mcx_official_legislative_news_uses_specific_watchlist_wording(self) -> None:
        document = self._doc(
            doc_id=52,
            source_name="Минсельхоз России - новости",
            region="federal",
            title="Совет Федерации одобрил ряд законопроектов в сфере АПК",
            url="https://mcx.gov.ru/press-service/news/sovet-federatsii-odobril-ryad-zakonoproektov-v-sfere-apk/",
            action_level="watchlist",
            page_type="news_background",
            summary="Официальная новость о прохождении законопроектов в сфере АПК.",
        )

        reason = build_executive_reason(document)
        action = build_executive_action(document)

        self.assertEqual(reason, "Законодательный сигнал по регулированию АПК")
        self.assertEqual(
            action,
            "Проверить, какие законопроекты одобрены и есть ли влияние на регулирование.",
        )

    def test_ocr_placeholder_uses_manual_review_reason_and_action(self) -> None:
        document = self._doc(
            doc_id=6,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="document 'placeholder.pdf' requires OCR extraction",
            url="https://admkrai.krasnodar.ru/upload/iblock/d69/placeholder.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="OCR placeholder.",
            raw_text="Распознанный текст отсутствует.",
        )

        reason = build_executive_reason(document)
        action = build_executive_action(document)

        self.assertEqual(reason, "Документ после OCR требует ручной проверки")
        self.assertEqual(action, "Дождаться повторной проверки OCR или сверить текст вручную.")

    def test_regional_subsidy_summary_prefers_title_aware_specific_text(self) -> None:
        document = self._doc(
            doc_id=7,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="О внесении изменений в порядок предоставления субсидий",
            url="https://admkrai.krasnodar.ru/upload/a.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Документ содержит изменения в порядке предоставления поддержки; требуется проверка условий и сроков.",
        )

        summary = select_executive_summary(document, fallback_text=document.summary)

        self.assertEqual(summary, "Изменён порядок предоставления субсидий в Краснодарском крае.")

    def test_credit_news_summary_prefers_specific_title(self) -> None:
        document = self._doc(
            doc_id=8,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Минсельхоз предложил новые условия льготного кредитования АПК",
            url="https://www.zol.ru/n/41378",
            action_level="requires_attention",
            page_type="news_background",
            summary="Документ содержит изменения в порядке предоставления поддержки; требуется проверка условий и сроков.",
        )

        summary = select_executive_summary(document, fallback_text=document.summary)

        self.assertEqual(summary, "Минсельхоз предложил обновить условия льготного кредитования АПК.")

    def test_ocr_placeholder_summary_keeps_safe_fallback_behavior(self) -> None:
        document = self._doc(
            doc_id=9,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="document 'placeholder.pdf' requires OCR extraction",
            url="https://admkrai.krasnodar.ru/upload/iblock/d69/placeholder.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Документ содержит изменения в порядке предоставления поддержки; требуется проверка условий и сроков.",
            raw_text="Распознанный текст отсутствует.",
        )

        summary = select_executive_summary(document, fallback_text=document.summary)

        self.assertEqual(summary, "Текст после OCR недостаточен для уверенного выделения условий документа.")

    def test_summary_length_stays_concise(self) -> None:
        document = self._doc(
            doc_id=10,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Минсельхоз предложил новые условия льготного кредитования АПК",
            url="https://www.zol.ru/n/41378",
            action_level="requires_attention",
            page_type="news_background",
            summary="Документ содержит изменения в порядке предоставления поддержки; требуется проверка условий и сроков.",
        )

        summary = select_executive_summary(document, fallback_text=document.summary)

        self.assertLessEqual(len(summary), 120)

    def test_weak_ocr_placeholder_is_not_meaningful_executive_highlight(self) -> None:
        document = self._doc(
            doc_id=11,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="document 'placeholder.pdf' requires OCR extraction",
            url="https://admkrai.krasnodar.ru/upload/iblock/d69/placeholder.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Документ содержит изменения в порядке предоставления поддержки; требуется проверка условий и сроков.",
            raw_text="Распознанный текст отсутствует.",
        )

        self.assertFalse(is_meaningful_executive_highlight(document))

    def test_real_credit_signal_is_meaningful_executive_highlight(self) -> None:
        document = self._doc(
            doc_id=12,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Минсельхоз предложил новые условия льготного кредитования АПК",
            url="https://www.zol.ru/n/41378",
            action_level="requires_attention",
            page_type="news_background",
            summary="Документ содержит изменения в порядке предоставления поддержки; требуется проверка условий и сроков.",
            raw_text="Минсельхоз предложил обновить условия льготного кредитования АПК.",
        )

        self.assertTrue(is_meaningful_executive_highlight(document))


if __name__ == "__main__":
    unittest.main()
