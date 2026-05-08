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
            raw_text="text",
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


if __name__ == "__main__":
    unittest.main()
