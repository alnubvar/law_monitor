from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.models import RawDocument
from app.reports.markdown_report import select_visible_report_documents
from app.visibility import (
    effective_user_action_level,
    should_show_document,
    visibility_bucket,
)


class VisibilityDecisionTest(unittest.TestCase):
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
        raw_text: str = "text",
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
            content_hash=f"visibility-{doc_id}",
            raw_text=raw_text,
            is_relevant=action_level != "irrelevant",
            relevance_reason="reason",
            importance="high" if action_level == "requires_attention" else "medium",
            action_level=action_level,
            page_type=page_type,
            summary=summary,
            impact="impact",
            topic="topic",
        )

    def test_same_document_uses_same_effective_visibility_across_surfaces(self) -> None:
        document = self._doc(
            doc_id=1,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Пошлина на экспорт пшеницы останется нулевой",
            url="https://www.zol.ru/n/41337",
            action_level="watchlist",
            page_type="news_background",
        )

        self.assertEqual(effective_user_action_level(document), "watchlist")
        self.assertEqual(visibility_bucket(document), "industry_background")
        self.assertTrue(should_show_document(document, surface="report", relevant_only=False))
        self.assertTrue(should_show_document(document, surface="telegram_digest", relevant_only=False))
        self.assertTrue(should_show_document(document, surface="telegram_list", relevant_only=False))

    def test_market_background_downgrade_stays_hidden_for_report_and_telegram(self) -> None:
        document = self._doc(
            doc_id=2,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Алжир проводит тендер по закупке пшеницы",
            url="https://www.zol.ru/n/market-algeria-tender",
            action_level="requires_attention",
            page_type="news_background",
        )
        document.business_signal = "Рыночный или отраслевой фон без прямого регуляторного сигнала."

        self.assertEqual(effective_user_action_level(document), "background")
        self.assertEqual(visibility_bucket(document), "industry_background")
        self.assertFalse(
            should_show_document(
                document,
                surface="report",
                relevant_only=False,
                action_levels=["requires_attention", "watchlist"],
            )
        )
        self.assertFalse(should_show_document(document, surface="telegram_digest", relevant_only=False))
        self.assertFalse(should_show_document(document, surface="telegram_list", relevant_only=False))

    def test_hidden_background_reference_filtering_is_unchanged(self) -> None:
        document = self._doc(
            doc_id=3,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="Формы документов, связанных с противодействием коррупции",
            url="https://admkrai.krasnodar.ru/content/1270/",
            action_level="watchlist",
            page_type="reference_page",
        )

        self.assertFalse(should_show_document(document, surface="report", relevant_only=False))
        self.assertFalse(should_show_document(document, surface="telegram_digest", relevant_only=False))
        self.assertFalse(should_show_document(document, surface="telegram_list", relevant_only=False))

    def test_report_selection_uses_same_shared_visibility_layer(self) -> None:
        visible_document = self._doc(
            doc_id=4,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Производство сельхозпродукции в РФ выросло",
            url="https://www.zol.ru/n/rf",
            action_level="watchlist",
            page_type="news_background",
        )
        hidden_document = self._doc(
            doc_id=5,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Экспорт зерна в Казахстан изменился",
            url="https://www.zol.ru/n/kz",
            action_level="watchlist",
            page_type="news_background",
        )
        visible = select_visible_report_documents(
            [visible_document, hidden_document],
            relevant_only=False,
            action_levels=["requires_attention", "watchlist"],
            include_market_background=False,
        )

        self.assertEqual([document.id for document in visible], [4])

    def test_news_watchlist_noise_is_downgraded_to_background(self) -> None:
        document = self._doc(
            doc_id=6,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Рейтинг экспортных отгрузок зерна и фрахта за неделю",
            url="https://www.zol.ru/n/freight-rating",
            action_level="watchlist",
            page_type="news_background",
            summary="Обзор рынка зерна, фрахта и экспортных отгрузок без решений правительства.",
        )
        document.business_signal = "Новостной предвестник возможных изменений господдержки, экспорта или регулирования."
        document.raw_text = (
            "Еженедельный обзор рынка зерна, ставки фрахта, рейтинг экспортных отгрузок и оценки аналитиков."
        )

        self.assertEqual(effective_user_action_level(document), "background")
        self.assertFalse(
            should_show_document(
                document,
                surface="report",
                relevant_only=False,
                action_levels=["requires_attention", "watchlist"],
            )
        )
        self.assertFalse(should_show_document(document, surface="telegram_digest", relevant_only=False))
        self.assertFalse(should_show_document(document, surface="telegram_list", relevant_only=False))

    def test_news_watchlist_with_regulatory_signal_stays_visible(self) -> None:
        document = self._doc(
            doc_id=7,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Правительство расширило программу господдержки экспортеров АПК",
            url="https://www.zol.ru/n/export-support",
            action_level="watchlist",
            page_type="news_background",
            summary="Изменены параметры программы поддержки и экспортного финансирования.",
        )
        document.business_signal = "Новостной предвестник возможных изменений господдержки, экспорта или регулирования."
        document.raw_text = "Правительство утвердило изменения программы финансирования и меры поддержки экспорта АПК."

        self.assertEqual(effective_user_action_level(document), "watchlist")
        self.assertTrue(should_show_document(document, surface="report", relevant_only=False))
        self.assertTrue(should_show_document(document, surface="telegram_digest", relevant_only=False))
        self.assertTrue(should_show_document(document, surface="telegram_list", relevant_only=False))

    def test_generic_export_price_news_is_downgraded_to_background(self) -> None:
        document = self._doc(
            doc_id=73,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Экспорт зерна вырос на фоне мировых цен",
            url="https://www.zol.ru/n/export-prices",
            action_level="watchlist",
            page_type="news_background",
            summary="Обзор экспортных цен и рыночной конъюнктуры.",
        )
        document.business_signal = "Новостной предвестник возможных изменений господдержки, экспорта или регулирования."
        document.raw_text = "Аналитики обсуждают мировые цены, статистику поставок и динамику экспорта зерна."

        self.assertEqual(effective_user_action_level(document), "background")
        self.assertFalse(
            should_show_document(
                document,
                surface="report",
                relevant_only=False,
                action_levels=["requires_attention", "watchlist"],
            )
        )

    def test_generic_government_meeting_without_agro_signal_is_downgraded(self) -> None:
        document = self._doc(
            doc_id=74,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="В правительстве обсудили развитие транспортной инфраструктуры",
            url="https://www.zol.ru/n/infra-meeting",
            action_level="watchlist",
            page_type="news_background",
            summary="Совещание по дорогам, мостам и логистике.",
        )
        document.business_signal = "Новостной предвестник возможных изменений господдержки, экспорта или регулирования."
        document.raw_text = "На совещании обсуждались дороги, мосты, пассажирские перевозки и реконструкция узлов."

        self.assertEqual(effective_user_action_level(document), "background")
        self.assertFalse(
            should_show_document(
                document,
                surface="report",
                relevant_only=False,
                action_levels=["requires_attention", "watchlist"],
            )
        )

    def test_infrastructure_news_for_target_region_without_agro_relevance_is_downgraded(self) -> None:
        document = self._doc(
            doc_id=75,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="В Краснодарском крае реконструируют пассажирский железнодорожный мост",
            url="https://www.zol.ru/n/krasnodar-bridge",
            action_level="watchlist",
            page_type="news_background",
            summary="Инфраструктурный проект по пассажирскому сообщению и мостовому переходу.",
        )
        document.business_signal = "Новостной предвестник возможных изменений господдержки, экспорта или регулирования."
        document.raw_text = "Проект посвящен пассажирскому сообщению, мосту и реконструкции транспортной инфраструктуры."

        self.assertEqual(effective_user_action_level(document), "background")
        self.assertFalse(
            should_show_document(
                document,
                surface="report",
                relevant_only=False,
                action_levels=["requires_attention", "watchlist"],
            )
        )

    def test_target_region_agriculture_regulation_remains_visible_watchlist(self) -> None:
        document = self._doc(
            doc_id=76,
            source_name="Правительство РФ - новости",
            region="federal",
            title="В Ростовской области обсудили новые меры господдержки АПК",
            url="https://government.ru/news/rostov-apk-support",
            action_level="watchlist",
            page_type="news_background",
            summary="Речь идет о мерах поддержки, субсидиях и условиях для сельского хозяйства региона.",
        )
        document.business_signal = "Новостной предвестник возможных изменений господдержки, экспорта или регулирования."
        document.raw_text = "Правительство и Минсельхоз обсуждают субсидии, господдержку АПК и параметры региональной программы."

        self.assertEqual(effective_user_action_level(document), "watchlist")
        self.assertTrue(should_show_document(document, surface="report", relevant_only=False))

    def test_foreign_quota_news_is_not_user_facing_requires_attention(self) -> None:
        document = self._doc(
            doc_id=71,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Квота на импорт кукурузы в Турцию выбрана на 20%",
            url="https://www.zol.ru/n/turkey-corn-import",
            action_level="requires_attention",
            page_type="news_background",
            summary="Турецкая импортная квота по кукурузе выбрана на 20%.",
        )
        document.business_signal = "Новостной предвестник возможных изменений квот и внешней торговли."
        document.raw_text = "Турция сообщила, что квота на импорт кукурузы выбрана на 20 процентов."

        self.assertEqual(effective_user_action_level(document), "background")
        self.assertFalse(
            should_show_document(
                document,
                surface="report",
                relevant_only=False,
                action_levels=["requires_attention", "watchlist"],
            )
        )
        self.assertFalse(should_show_document(document, surface="telegram_digest", relevant_only=False))
        self.assertFalse(should_show_document(document, surface="telegram_list", relevant_only=False))

    def test_russian_export_quota_news_remains_requires_attention_when_actionable(self) -> None:
        document = self._doc(
            doc_id=72,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Правительство РФ скорректировало экспортную квоту на зерно",
            url="https://www.zol.ru/n/russian-export-quota",
            action_level="requires_attention",
            page_type="news_background",
            summary="Изменены параметры экспортной квоты для российских поставок.",
        )
        document.business_signal = "Новостной предвестник изменения экспортных ограничений и регулирования."
        document.raw_text = "Правительство РФ изменило экспортную квоту на зерно для российских компаний."

        self.assertEqual(effective_user_action_level(document), "requires_attention")
        self.assertTrue(should_show_document(document, surface="report", relevant_only=False))

    def test_gisp_support_measure_is_unaffected_by_news_noise_guard(self) -> None:
        document = self._doc(
            doc_id=8,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="watchlist",
            page_type="measure_card",
            summary="Активная мера поддержки без разового дедлайна.",
        )
        document.business_signal = "Активная федеральная мера поддержки, действует на регулярной основе"
        document.raw_text = "Мера поддержки действует на регулярной основе."

        self.assertEqual(effective_user_action_level(document), "watchlist")

    def test_regional_npa_is_unaffected_by_news_noise_guard(self) -> None:
        document = self._doc(
            doc_id=9,
            source_name="Право Ставропольского края",
            region="stavropol",
            title="Постановление о внесении изменений в порядок предоставления субсидий",
            url="https://pravo.stavregion.ru/document/98765",
            action_level="watchlist",
            page_type="new_rule",
            summary="Изменения порядка предоставления субсидий.",
        )
        document.business_signal = "Региональный НПА по профильной теме: оставить в наблюдении."

        self.assertEqual(effective_user_action_level(document), "watchlist")

    def test_weak_ocr_placeholder_is_capped_to_watchlist(self) -> None:
        document = self._doc(
            doc_id=10,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="document 'wgketjm9pqmq00n60yoyq8c0z2u13z38_cfa07cc203.pdf' requires OCR extraction",
            url="https://admkrai.krasnodar.ru/upload/iblock/d69/wgketjm9pqmq00n60yoyq8c0z2u13z38.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            raw_text="Распознанный текст отсутствует.",
        )

        self.assertEqual(effective_user_action_level(document), "watchlist")
        self.assertFalse(
            should_show_document(
                document,
                surface="report",
                relevant_only=False,
                action_levels=["requires_attention"],
            )
        )
        self.assertTrue(should_show_document(document, surface="report", relevant_only=False))

    def test_fallback_titled_weak_ocr_placeholder_is_also_capped_to_watchlist(self) -> None:
        document = self._doc(
            doc_id=12,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="НПА Краснодарского края: документ после OCR",
            url="https://admkrai.krasnodar.ru/upload/iblock/d69/fallback-ocr.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            raw_text=(
                "Документ после OCR требует ручной проверки. Распознанный текст частично отсутствует, "
                "структура фрагментарна и не позволяет уверенно выделить условия меры."
            ),
        )

        self.assertEqual(effective_user_action_level(document), "watchlist")
        self.assertFalse(
            should_show_document(
                document,
                surface="report",
                relevant_only=False,
                action_levels=["requires_attention"],
            )
        )

    def test_real_ocr_document_with_meaningful_text_remains_requires_attention(self) -> None:
        document = self._doc(
            doc_id=11,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="document 'meaningful.pdf' requires OCR extraction",
            url="https://admkrai.krasnodar.ru/upload/iblock/9a3/meaningful.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            raw_text=(
                "Настоящим постановлением утвержден порядок предоставления субсидий для АПК, "
                "определены сроки отбора заявок и условия льготного кредитования получателей."
            ),
        )

        self.assertEqual(effective_user_action_level(document), "requires_attention")
        self.assertTrue(
            should_show_document(
                document,
                surface="report",
                relevant_only=False,
                action_levels=["requires_attention"],
            )
        )


class StrategyRelevanceGateTest(unittest.TestCase):
    """Tests for _is_executive_strategy_relevant via should_show_document.

    The strategy relevance gate must require agro/food context — trade or finance
    terms alone (e.g. railway tariffs, regional budget credits) must not pass.
    """

    def _strategy_doc(
        self,
        *,
        doc_id: int,
        title: str,
        summary: str = "",
        raw_text: str = "",
        page_type: str = "new_rule",
        action_level: str = "watchlist",
        source_name: str = "Правительство РФ - документы",
        region: str = "federal",
    ) -> RawDocument:
        now = datetime.now(timezone.utc)
        return RawDocument(
            id=doc_id,
            source_name=source_name,
            source_url="https://government.ru/docs/",
            level="federal",
            region=region,
            title=title,
            url=f"https://government.ru/docs/{doc_id}/",
            published_at=now,
            collected_at=now,
            content_hash=f"strategy-gate-{doc_id}",
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

    # --- items that must be HIDDEN (no agro context) ---

    def test_passenger_rail_concept_with_tariff_in_raw_text_is_hidden(self) -> None:
        document = self._strategy_doc(
            doc_id=200,
            title="Правительство утвердило Концепцию развития перевозок пассажиров железнодорожным транспортом",
            summary="Концепция пассажирских железнодорожных перевозок в пригородном сообщении.",
            raw_text=(
                "Концепция предусматривает развитие тарифной политики на пригородные перевозки, "
                "обновление подвижного состава и финансирование инфраструктуры."
            ),
        )
        self.assertFalse(should_show_document(document, surface="report"))
        self.assertFalse(should_show_document(document, surface="telegram_digest"))

    def test_bridge_reconstruction_with_financing_in_raw_text_is_hidden(self) -> None:
        document = self._strategy_doc(
            doc_id=201,
            title="Правительство направит опережающее финансирование на реконструкцию моста в Калининградской области",
            summary="Реконструкция транспортной инфраструктуры, мостовой переход.",
            raw_text=(
                "Финансирование выделено на реконструкцию мостового перехода. "
                "Экспортный потенциал порта учитывается при планировании тарифов."
            ),
        )
        self.assertFalse(should_show_document(document, surface="report"))
        self.assertFalse(should_show_document(document, surface="telegram_digest"))

    def test_zemsky_teacher_program_is_hidden(self) -> None:
        document = self._strategy_doc(
            doc_id=202,
            title="Для участия в программе «Земский учитель» подано более 8 тыс. заявок",
            summary="Итоги заявочной кампании образовательной программы.",
            raw_text="Программа поддержки учителей в сельской местности. Финансирование — 1 млн руб.",
        )
        self.assertFalse(should_show_document(document, surface="report"))
        self.assertFalse(should_show_document(document, surface="telegram_digest"))

    def test_budget_credit_writeoff_for_regions_is_hidden(self) -> None:
        document = self._strategy_doc(
            doc_id=203,
            title="Правительство списало задолженность по бюджетным кредитам 21 региону",
            summary="Решение по бюджетным кредитам регионов.",
            raw_text="Решение принято в рамках реструктуризации бюджетной задолженности субъектов РФ.",
        )
        self.assertFalse(should_show_document(document, surface="report"))
        self.assertFalse(should_show_document(document, surface="telegram_digest"))

    def test_generic_economy_meeting_with_export_mention_is_hidden(self) -> None:
        document = self._strategy_doc(
            doc_id=204,
            title="Александр Новак провёл совещание по ситуации в экономике",
            summary="Совещание по макроэкономическим показателям.",
            raw_text=(
                "Обсуждались вопросы экспорта, импорта и тарифного регулирования в общеэкономическом контексте."
            ),
        )
        self.assertFalse(should_show_document(document, surface="report"))
        self.assertFalse(should_show_document(document, surface="telegram_digest"))

    def test_generic_economy_meeting_with_food_in_raw_text_but_not_in_title_summary_is_hidden(self) -> None:
        document = self._strategy_doc(
            doc_id=205,
            title="Александр Новак провёл совещание по ситуации в экономике",
            summary="Совещание по макроэкономическим показателям и инфляции.",
            raw_text=(
                "Обсуждались макроэкономические показатели, инфляция и цены, включая продовольственные товары, "
                "а также вопросы экспорта и тарифного регулирования."
            ),
        )
        self.assertFalse(should_show_document(document, surface="report"))
        self.assertFalse(should_show_document(document, surface="telegram_digest"))

    # --- items that must be VISIBLE (have agro context) ---

    def test_apk_export_support_program_is_visible(self) -> None:
        document = self._strategy_doc(
            doc_id=210,
            title="Правительство расширило программу господдержки экспортеров АПК",
            summary="Поддержка экспорта АПК, субсидии и параметры программы.",
            raw_text="Программа охватывает экспортёров сельхозпродукции, зерна и масличных культур.",
        )
        self.assertTrue(should_show_document(document, surface="report"))
        self.assertTrue(should_show_document(document, surface="telegram_digest"))

    def test_grain_export_quota_is_visible(self) -> None:
        document = self._strategy_doc(
            doc_id=211,
            title="Правительство скорректировало экспортную квоту на зерно",
            summary="Изменены параметры экспортной квоты для российских поставок зерна.",
            raw_text="Квота на экспорт зерна изменена с учётом балансовых показателей урожая пшеницы.",
        )
        self.assertTrue(should_show_document(document, surface="report"))

    def test_fertilizer_export_regulation_is_visible(self) -> None:
        document = self._strategy_doc(
            doc_id=212,
            title="Правительство ввело новые правила экспорта удобрений",
            summary="Регулирование экспорта азотных и фосфорных удобрений.",
            raw_text="Правила устанавливают квоты и пошлины на экспорт удобрений для нужд АПК.",
        )
        self.assertTrue(should_show_document(document, surface="report"))

    def test_selkhozproduct_subsidy_with_selhkhoz_term_is_visible(self) -> None:
        document = self._strategy_doc(
            doc_id=213,
            title="Минсельхоз расширил перечень субсидируемых направлений",
            summary="Расширение программы субсидирования сельхозпроизводителей.",
            raw_text="Субсидии распространены на сельхозпроизводителей зерновых и масличных культур.",
        )
        self.assertTrue(should_show_document(document, surface="report"))

    def test_dairy_support_measure_is_visible(self) -> None:
        document = self._strategy_doc(
            doc_id=214,
            title="Утверждены условия господдержки молочного скотоводства",
            summary="Субсидирование производителей молока.",
            raw_text="Новые условия предоставления субсидий на производство молока и молочной продукции.",
        )
        self.assertTrue(should_show_document(document, surface="report"))


if __name__ == "__main__":
    unittest.main()
