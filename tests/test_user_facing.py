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
        self.assertEqual(action, "Проверить условия кредитования и применимость для АПК.")

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

        self.assertEqual(reason, "Изменение экспортных пошлин")
        self.assertEqual(action, "Проверить влияние на экспортные контракты и логистику.")

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
        self.assertEqual(action, "Оценить влияние на регулирование АПК.")

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
        self.assertEqual(action, "Проверить влияние на условия поддержки и регламент применения.")

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
            "Проверить, какие законопроекты одобрены, и оценить влияние на регулирование АПК.",
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

    def _selection_doc(
        self,
        *,
        doc_id: int,
        application_status: str,
        deadline_text: str | None,
    ) -> RawDocument:
        now = datetime.now(timezone.utc)
        return RawDocument(
            id=doc_id,
            source_name="Минсельхоз Ставропольского края - господдержка",
            source_url="https://mshsk.ru/gospodderzhka/selection.php",
            level="regional",
            region="stavropol",
            title="Объявление об отборе на возмещение части затрат",
            url="https://mshsk.ru/gospodderzhka/selection.php",
            published_at=now,
            collected_at=now,
            content_hash=f"selection-{doc_id}",
            raw_text="Объявлен отбор по господдержке.",
            is_relevant=True,
            relevance_reason="reason",
            importance="high",
            action_level="watchlist",
            page_type="selection_announcement",
            summary="Объявление об отборе.",
            impact="impact",
            topic="topic",
            application_status=application_status,
            deadline_text=deadline_text,
        )

    def test_expired_selection_says_window_closed_not_open(self) -> None:
        document = self._selection_doc(
            doc_id=200,
            application_status="closed",
            deadline_text="Прием заявок открыт до 01.01.2020",
        )

        reason = build_executive_reason(document)
        action = build_executive_action(document)

        # The user must never see "Открыт прием заявок" for a window that has
        # already closed, and must never be instructed to submit after expiry.
        self.assertEqual(reason, "Прием заявок завершён")
        self.assertEqual(action, "Срок истёк, документ — справочно.")

    def test_stale_open_status_with_expired_deadline_renders_as_closed(self) -> None:
        # Stored application_status may still say "open" if the document was
        # analyzed when the deadline was alive. The render-time deadline truth
        # check must demote the wording anyway.
        document = self._selection_doc(
            doc_id=201,
            application_status="open",
            deadline_text="Прием заявок открыт до 01.01.2020",
        )

        reason = build_executive_reason(document)
        action = build_executive_action(document)

        self.assertEqual(reason, "Прием заявок завершён")
        self.assertEqual(action, "Срок истёк, документ — справочно.")

    def test_alive_selection_still_uses_open_wording(self) -> None:
        document = self._selection_doc(
            doc_id=202,
            application_status="open",
            deadline_text="Прием заявок открыт до 15 мая 2099 года",
        )

        reason = build_executive_reason(document)
        action = build_executive_action(document)

        self.assertEqual(reason, "Открыт прием заявок")
        self.assertEqual(
            action,
            "Проверить сроки подачи документов и готовность заявки.",
        )

    # --- Intent taxonomy: trade vs support distinction --------------------
    def test_export_duty_with_support_words_in_signal_still_renders_as_trade(self) -> None:
        # Real-world regression: the document is a trade signal but its
        # business_signal / impact text contains "господдержки" + "изменений",
        # which previously caused the renderer to pick INTENT_SUPPORT_CHANGE.
        document = self._doc(
            doc_id=300,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Пошлины на экспорт зерна останутся нулевыми с 20 мая",
            url="https://www.zol.ru/n/414bd",
            action_level="requires_attention",
            page_type="news_background",
            summary=(
                "Пошлины на экспорт зерна останутся нулевыми с 20 мая. "
                "Пошлина на экспорт пшеницы из России будет нулевой."
            ),
            raw_text="Пошлины на экспорт пшеницы будут нулевыми с 20 мая по 26 мая.",
        )
        document.business_signal = (
            "Новостной предвестник возможных изменений господдержки, "
            "экспорта или регулирования."
        )
        document.impact = (
            "Материал требует реакции GR-команды: в тексте есть признаки "
            "мер поддержки, регуляторных изменений, сроков подачи."
        )

        reason = build_executive_reason(document)
        action = build_executive_action(document)

        self.assertEqual(reason, "Изменение экспортных пошлин")
        self.assertEqual(action, "Проверить влияние на экспортные контракты и логистику.")
        self.assertNotIn("поддерж", reason.lower())
        self.assertNotIn("поддерж", action.lower())

    def test_export_quota_signal_uses_quota_wording(self) -> None:
        document = self._doc(
            doc_id=301,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Правительство утвердило квоту на экспорт зерна",
            url="https://www.zol.ru/n/quota",
            action_level="requires_attention",
            page_type="news_background",
            summary="Изменение экспортной квоты по зерну.",
            raw_text="Экспортная квота на зерно установлена на следующий период.",
        )

        reason = build_executive_reason(document)
        action = build_executive_action(document)

        self.assertEqual(reason, "Изменение экспортных квот")
        self.assertEqual(action, "Проверить влияние на экспортные контракты и логистику.")

    def test_export_restriction_signal_uses_restriction_wording(self) -> None:
        document = self._doc(
            doc_id=302,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Введён запрет на вывоз серы за пределы ЕАЭС",
            url="https://www.zol.ru/n/sulfur-export",
            action_level="requires_attention",
            page_type="news_background",
            summary="Введён запрет на экспорт серы. Ограничения для производителей удобрений.",
            raw_text="Решение о запрете экспорта серы вступает в силу.",
        )

        reason = build_executive_reason(document)
        action = build_executive_action(document)

        self.assertEqual(reason, "Ограничения экспорта")
        self.assertEqual(
            action,
            "Проверить влияние ограничений на экспортные контракты и логистику.",
        )

    def test_export_logistics_signal_uses_logistics_wording(self) -> None:
        document = self._doc(
            doc_id=303,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="В Новороссийском зерновом терминале запустили новую логистику экспорта",
            url="https://www.zol.ru/n/logistics",
            action_level="requires_attention",
            page_type="news_background",
            summary="Изменение логистики экспортных поставок зерна через терминал.",
            raw_text="Новая схема логистики экспорта зерна через терминал.",
        )

        reason = build_executive_reason(document)
        action = build_executive_action(document)

        self.assertEqual(reason, "Изменение условий логистики экспорта")
        self.assertEqual(action, "Проверить влияние на логистику и условия поставок.")

    def test_pure_subsidy_item_still_classified_as_support_not_trade(self) -> None:
        # Defensive test: a subsidy doc with no trade markers must not flip
        # into the trade branch just because of the reorder.
        document = self._doc(
            doc_id=304,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Минсельхоз изменил условия субсидирования производителей молока",
            url="https://www.zol.ru/n/milk-subsidy",
            action_level="requires_attention",
            page_type="news_background",
            summary="Изменены условия субсидирования в молочном животноводстве.",
            raw_text="Внесены изменения в порядок предоставления субсидий молочного направления.",
        )

        reason = build_executive_reason(document)
        action = build_executive_action(document)

        self.assertEqual(reason, "Изменены условия поддержки")
        self.assertEqual(action, "Проверить влияние на условия поддержки и регламент применения.")
        self.assertNotIn("экспорт", reason.lower())

    def test_restriction_word_alone_without_export_context_is_not_trade(self) -> None:
        # "Ограничение приема заявок" must not be mistaken for trade
        # restriction wording. The reduced TRADE_REGULATION_RE plus the
        # export-context gate guard against this.
        document = self._doc(
            doc_id=305,
            source_name="Минсельхоз Ставропольского края - господдержка",
            region="stavropol",
            title="Об ограничении приема заявок на субсидии в текущем периоде",
            url="https://mshsk.ru/restriction-applications",
            action_level="watchlist",
            page_type="reference_page",
            summary="Об ограничении приема заявок на субсидии. Внесены изменения в порядок.",
            raw_text="Установлено ограничение приема заявок на субсидии.",
        )

        reason = build_executive_reason(document)

        self.assertNotIn("экспорт", reason.lower())
        self.assertNotIn("Ограничения экспорта", reason)


if __name__ == "__main__":
    unittest.main()
