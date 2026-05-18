from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.llm.facts_extractor import DocumentFacts
from app.llm.mock_client import MockLLMClient
from app.rules.business_signal_rules import detect_action_level
from app.sources.promote_budget_source import _build_raw_text


class MockAnalyzeSmokeTest(unittest.TestCase):
    def _regulation_public_discussion_text(self, deadline: datetime, *, agro: bool = True) -> str:
        target_text = (
            "Цели проекта: изменение порядка предоставления субсидий сельскохозяйственным "
            "товаропроизводителям в сфере племенного животноводства."
            if agro
            else "Цели проекта: изменение правил ведения реестра туристских маршрутов."
        )
        return (
            "Проект НПА: Об утверждении требований. "
            "Статус: Идет обсуждение. "
            "Процедура: Оценка регулирующего воздействия. "
            "Начало обсуждения: 2026-05-12T11:53:57.098Z. "
            f"Конец обсуждения: {deadline.strftime('%Y-%m-%d')}T11:53:57.098Z. "
            f"{target_text}"
        )

    def test_marks_action_document_as_requires_attention(self) -> None:
        client = MockLLMClient(
            [
                "государственная поддержка АПК",
                "сельское хозяйство",
            ]
        )

        result = client.analyze_document(
            "Субсидия на развитие сельского хозяйства",
            "Субсидия на развитие сельского хозяйства предоставляется в рамках государственной поддержки АПК.",
        )

        self.assertTrue(result.is_relevant)
        self.assertEqual(result.action_level, "requires_attention")
        self.assertEqual(result.page_type, "measure_card")
        self.assertEqual(result.importance, "high")
        self.assertIsNotNone(result.summary)

    def test_marks_general_sector_page_as_watchlist_or_background(self) -> None:
        client = MockLLMClient(["сельское хозяйство", "зерно"])

        result = client.analyze_document(
            "Обзор рынка зерна",
            "Сельское хозяйство и рынок зерна остаются под влиянием погоды.",
        )

        self.assertIn(result.action_level, {"watchlist", "background"})
        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertEqual(result.page_type, "news_background")

    def test_zol_navigation_keywords_do_not_trigger_requires_attention(self) -> None:
        client = MockLLMClient(["государственная поддержка АПК", "сельское хозяйство"])

        noisy_text = (
            "Новости Цены Зерновой еженедельник Новости законодательства Подписка "
            "Каталог предприятий АПК Реклама на сайте "
            "Обзор рынка зерна и сельское хозяйство региона без мер поддержки."
        )
        result = client.analyze_document(
            "Обзор рынка зерна",
            noisy_text,
            source_name="ZOL.ru - зерновые новости",
            url="https://www.zol.ru/n/12345",
        )

        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertIn(result.action_level, {"watchlist", "background", "irrelevant"})
        self.assertEqual(result.page_type, "navigation")

    def test_real_subsidy_title_becomes_requires_attention(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство", "государственная поддержка АПК"])

        result = client.analyze_document(
            "Объявлен конкурсный отбор заявок на субсидии для АПК",
            "Прием заявок открыт до 15 мая. Мера поддержки касается сельхозпроизводителей.",
        )

        self.assertEqual(result.action_level, "requires_attention")
        self.assertEqual(result.page_type, "selection_announcement")

    def test_government_navigation_page_becomes_irrelevant(self) -> None:
        client = MockLLMClient(["сельское хозяйство", "государственная поддержка АПК"])

        result = client.analyze_document(
            "Карта сайта",
            "Правительство России Работа Правительства Сельское хозяйство Следующая новость Предыдущая новость",
            source_name="Правительство РФ - документы",
            url="http://government.ru/sitemap/",
        )

        self.assertEqual(result.action_level, "irrelevant")
        self.assertEqual(result.page_type, "navigation")

    def test_government_rss_page_does_not_become_visible_signal(self) -> None:
        client = MockLLMClient(["сельское хозяйство", "государственная поддержка АПК"])

        result = client.analyze_document(
            "о субсидировании кредита для реализации инвестпроекта",
            "о субсидировании кредита для реализации крупного инвестпроекта в области нефтехимии",
            source_name="Правительство РФ - новости",
            url="http://government.ru/news/rss/",
        )

        self.assertEqual(result.page_type, "navigation")
        self.assertIn(result.action_level, {"background", "irrelevant"})

    def test_government_classifier_page_is_navigation_not_new_rule(self) -> None:
        client = MockLLMClient(["сельское хозяйство", "государственная поддержка АПК"])

        result = client.analyze_document(
            "Экономические отношения с зарубежными странами",
            "Экономические отношения с зарубежными странами. Классификатор материалов.",
            source_name="Правительство РФ - новости",
            url="http://government.ru/rugovclassifier/21/",
        )

        self.assertEqual(result.page_type, "navigation")
        self.assertIn(result.action_level, {"background", "irrelevant"})

    def test_government_search_archive_page_is_navigation(self) -> None:
        client = MockLLMClient(["сельское хозяйство", "государственная поддержка АПК"])

        result = client.analyze_document(
            "Документы",
            "Новости за выбранную дату и список документов.",
            source_name="Правительство РФ - документы",
            url="http://government.ru/docs/?dt.since=29.04.2026&dt.till=29.04.2026",
        )

        self.assertEqual(result.page_type, "navigation")
        self.assertIn(result.action_level, {"background", "irrelevant"})

    def test_agriculture_only_text_is_watchlist_not_requires_attention(self) -> None:
        client = MockLLMClient(["сельское хозяйство", "экспорт АПК"])

        result = client.analyze_document(
            "Посевная кампания в регионе",
            "Сельское хозяйство, посевная и урожай остаются ключевыми темами недели.",
        )

        self.assertEqual(result.action_level, "watchlist")
        self.assertEqual(result.page_type, "news_background")
        self.assertNotEqual(result.action_level, "requires_attention")

    def test_registry_page_is_capped_at_watchlist(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Реестр сельскохозяйственных товаропроизводителей",
            "Распоряжение о реестре и субсидиях для сельхозтоваропроизводителей.",
        )

        self.assertEqual(result.page_type, "registry")
        self.assertEqual(result.action_level, "watchlist")

    def test_reference_page_is_capped_at_watchlist(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Сроки предоставления государственных услуг",
            "Сроки предоставления государственных услуг и приема документов на субсидии.",
        )

        self.assertEqual(result.page_type, "reference_page")
        self.assertEqual(result.action_level, "watchlist")

    def test_press_center_is_not_requires_attention(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Пресс-центр",
            "Раздел новостей и публикаций о сельском хозяйстве.",
        )

        self.assertEqual(result.page_type, "section_page")
        self.assertNotEqual(result.action_level, "requires_attention")

    def test_year_title_becomes_year_archive(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "2022",
            "Архив документов по субсидиям и мерам поддержки за 2022 год.",
        )

        self.assertEqual(result.page_type, "year_archive")
        self.assertNotEqual(result.action_level, "requires_attention")

    def test_projects_documents_is_not_requires_attention(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Проекты документов",
            "Раздел с проектами документов и обсуждениями.",
        )

        self.assertEqual(result.page_type, "section_page")
        self.assertNotEqual(result.action_level, "requires_attention")

    def test_real_subsidy_application_title_is_requires_attention(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Прием заявок на субсидии для сельхозтоваропроизводителей",
            "Прием заявок на субсидии открыт до 15 мая.",
        )

        self.assertEqual(result.page_type, "selection_announcement")
        self.assertEqual(result.action_level, "requires_attention")

    def test_legacy_mcx_credit_subsidy_page_is_background(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство", "государственная поддержка АПК"])

        result = client.analyze_document(
            "Субсидия на возмещение части процентной ставки по инвестиционным кредитам, взятым до 1 января 2017 года",
            "Минсельхоз России. Мера господдержки АПК: субсидия на возмещение части процентной ставки.",
            source_name="Минсельхоз России - меры господдержки",
            url="https://mcx.gov.ru/activity/state-support/measures/subsidy-credit-2017/",
        )

        self.assertEqual(result.action_level, "background")

    def test_rule_change_title_is_requires_attention(self) -> None:
        client = MockLLMClient(["государственная поддержка АПК"])

        result = client.analyze_document(
            "Постановление от 30.04.2026: внесены изменения в порядок предоставления субсидий",
            "Постановление от 30.04.2026, которым внесены изменения в порядок предоставления субсидий.",
        )

        self.assertEqual(result.page_type, "new_rule")
        self.assertEqual(result.action_level, "requires_attention")

    def test_polls_page_becomes_section_or_irrelevant(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Опросы",
            "Раздел находится в стадии наполнения.",
            source_name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/polls/",
        )

        self.assertIn(result.page_type, {"section_page", "navigation"})
        self.assertNotEqual(result.action_level, "requires_attention")

    def test_gisp_measure_card_stays_visible_type(self) -> None:
        client = MockLLMClient(["льготное кредитование АПК"])

        result = client.analyze_document(
            "Льготное кредитование АПК",
            "Льготное кредитование АПК. Общая информация. Требования. Порядок получения меры.",
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
        )

        self.assertEqual(result.page_type, "measure_card")
        self.assertIn(result.action_level, {"requires_attention", "watchlist"})

    def test_reference_form_title_becomes_reference_page(self) -> None:
        client = MockLLMClient(["государственная поддержка АПК"])

        result = client.analyze_document(
            "Анкета получателя мер государственной поддержки",
            "Анкета для физических и юридических лиц, получающих меры государственной поддержки.",
            source_name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/anketa.docx",
        )

        self.assertEqual(result.page_type, "reference_page")
        self.assertIn(result.action_level, {"watchlist", "background"})

    def test_forum_title_is_not_mistaken_for_reference_page(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Михаил Мишустин осмотрел экспозицию инвестиционного форума",
            "На форуме среди прочего упоминалось сельское хозяйство.",
            source_name="Правительство РФ - новости",
            url="http://government.ru/news/58630/",
        )

        self.assertNotEqual(result.page_type, "reference_page")

    def test_gisp_summary_removes_ui_noise_and_is_capped(self) -> None:
        client = MockLLMClient(["льготное кредитование АПК"])

        result = client.analyze_document(
            "Льготное кредитование АПК",
            (
                "Главная Навигатор мер поддержки Сравнить 0 НПА 22-68850-00258-Р "
                "Администратор меры поддержки МИНСЕЛЬХОЗ РОССИИ Общая информация "
                "Требования Скачать условия "
                "Льготное кредитование АПК направлено на развитие российских сельхозтоваропроизводителей."
            ),
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
        )

        self.assertLessEqual(len(result.summary), 300)
        self.assertNotIn("Главная", result.summary)
        self.assertNotIn("Навигатор мер поддержки", result.summary)
        self.assertIn("Льготное кредитование АПК", result.summary)

    def test_gisp_summary_removes_ui_ids_and_duplicate_title(self) -> None:
        client = MockLLMClient(["транспортировка товаров апк"])

        result = client.analyze_document(
            "Господдержка. Транспортировка товаров АПК",
            (
                "Господдержка. Транспортировка товаров АПК .1104) "
                "Господдержка. Транспортировка товаров АПК Конкурсное событие "
                "Общая информация Требования Необходимые документы "
                "На регулярной основе Администратор меры поддержки АО РЭЦ"
            ),
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/9512857",
            level="support_measures",
        )

        self.assertNotIn("1104)", result.summary)
        self.assertNotIn("Конкурсное событие", result.summary)
        self.assertNotIn("Общая информация", result.summary)
        self.assertEqual(
            result.summary.count("Господдержка. Транспортировка товаров АПК"),
            1,
        )

    def test_generic_subsidies_title_is_not_requires_attention(self) -> None:
        client = MockLLMClient(["государственная поддержка АПК"])

        result = client.analyze_document(
            "Субсидии",
            "Раздел о мерах государственной поддержки в АПК без объявления отбора и без срока подачи заявок.",
            source_name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/subsidii/",
        )

        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertIn(result.action_level, {"watchlist", "background"})

    def test_gisp_inactive_measure_card_is_not_requires_attention(self) -> None:
        client = MockLLMClient(["льготное кредитование АПК"])

        result = client.analyze_document(
            "Льготное кредитование АПК",
            "Не активная мера поддержки. НПА 1413. Льготное кредитование АПК.",
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            level="support_measures",
        )

        self.assertEqual(result.support_status, "inactive")
        self.assertFalse(result.is_active)
        self.assertEqual(result.action_level, "background")
        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertEqual(result.business_signal, "Неактивная мера поддержки: оставить в справочном блоке")

    def test_gisp_active_regular_measure_card_without_deadline_becomes_watchlist(self) -> None:
        client = MockLLMClient(["льготное кредитование АПК"])

        result = client.analyze_document(
            "Льготное кредитование АПК",
            (
                "Активная мера поддержки. На регулярной основе. "
                "НПА 22-68850-00258-Р. Льготное кредитование АПК."
            ),
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            level="support_measures",
        )

        self.assertEqual(result.support_status, "active")
        self.assertTrue(result.is_active)
        self.assertTrue(result.is_continuous)
        self.assertEqual(result.application_status, "regular")
        self.assertIsNone(result.deadline_text)
        self.assertEqual(result.action_level, "watchlist")

    def test_gisp_transportirovka_active_regular_no_deadline_becomes_watchlist(self) -> None:
        client = MockLLMClient(["господдержка транспортировка товаров АПК"])

        result = client.analyze_document(
            "Господдержка. Транспортировка товаров АПК",
            (
                "Активная мера поддержки. На регулярной основе. "
                "Компенсация части затрат на транспортировку товаров АПК."
            ),
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/9582761",
            level="support_measures",
        )

        self.assertEqual(result.support_status, "active")
        self.assertEqual(result.application_status, "regular")
        self.assertIsNone(result.deadline_text)
        self.assertEqual(result.action_level, "watchlist")

    def test_gisp_active_regular_with_deadline_stays_requires_attention(self) -> None:
        client = MockLLMClient(["льготное кредитование АПК"])

        result = client.analyze_document(
            "Льготное кредитование АПК",
            (
                "Активная мера поддержки. На регулярной основе. "
                "Прием заявок до 30 июня 2026 года. Льготное кредитование АПК."
            ),
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            level="support_measures",
        )

        self.assertEqual(result.support_status, "active")
        # Extractor sees "Прием заявок до..." and sets application_status=open,
        # which is handled by the open branch → requires_attention regardless.
        self.assertIn(result.application_status, ("open", "regular"))
        self.assertIsNotNone(result.deadline_text)
        self.assertEqual(result.action_level, "requires_attention")

    def test_gisp_measure_url_stays_measure_card_even_with_rule_words(self) -> None:
        client = MockLLMClient(["льготное кредитование АПК"])

        result = client.analyze_document(
            "Льготное кредитование АПК",
            (
                "Активная мера поддержки. На регулярной основе. "
                "Приказ от 30.04.2026 упоминается в описании НПА. "
                "Льготное кредитование АПК."
            ),
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            level="support_measures",
            region="federal",
        )

        self.assertEqual(result.page_type, "measure_card")

    def test_veb_guarantee_active_regular_without_open_becomes_watchlist(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Гарантия ВЭБ.РФ",
            "Активная мера поддержки. На регулярной основе. Сельское хозяйство (агропромышленный комплекс).",
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/12446928",
            level="support_measures",
            region="federal",
        )

        self.assertEqual(result.action_level, "watchlist")
        self.assertEqual(
            result.business_signal,
            "Постоянная федеральная мера поддержки; срочное окно подачи не выявлено.",
        )

    def test_open_application_extracts_deadline_and_open_status(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Объявлен конкурсный отбор заявок на субсидии для АПК",
            "Прием заявок открыт до 15 мая 2099 года. НПА 338а.",
            source_name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/subsidy-open/",
            level="support_measures",
        )

        self.assertEqual(result.application_status, "open")
        self.assertIsNotNone(result.deadline_text)
        self.assertIn("до 15 мая 2099 года", result.deadline_text or "")
        self.assertEqual(result.npa_number, "НПА 338а")
        self.assertIsNone(result.terms_text)

    def test_gisp_open_deadline_measure_stays_requires_attention(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Объявление об отборе на предоставление субсидии сельхозтоваропроизводителям",
            (
                "Активная мера поддержки. Объявлен отбор. "
                "Прием заявок открыт до 20 июня 2026 года. Мера действует для АПК."
            ),
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/9999999",
            level="support_measures",
            region="federal",
        )

        self.assertEqual(result.page_type, "measure_card")
        self.assertEqual(result.application_status, "open")
        self.assertIsNotNone(result.deadline_text)
        self.assertEqual(result.action_level, "requires_attention")

    def test_closed_application_is_not_requires_attention(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Объявлен конкурсный отбор заявок на субсидии для АПК",
            "Прием завершен. Отбор завершен, новые заявки не принимаются.",
            source_name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/subsidy-closed/",
            level="support_measures",
        )

        self.assertEqual(result.application_status, "closed")
        self.assertNotEqual(result.action_level, "requires_attention")

    def test_closed_application_with_deadline_text_is_not_requires_attention(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Объявлен конкурсный отбор заявок на субсидии для АПК",
            "Прием заявок до 30.06.2026. Далее прием завершен, отбор завершен.",
            source_name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/subsidy-closed-deadline/",
            level="support_measures",
            region="stavropol",
        )

        self.assertEqual(result.application_status, "closed")
        self.assertIsNotNone(result.deadline_text)
        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertIn("не является текущим окном подачи", result.risk_notes or "")

    def test_promote_selection_view_url_becomes_selection_announcement_and_requires_attention(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])
        deadline = datetime.now(timezone.utc) + timedelta(days=1)
        raw_text = _build_raw_text(
            {
                "title": "Грант Агростартап",
                "shortName": "Агростартап",
                "pppItemName": "Министерство сельского хозяйства Российской Федерации",
                "startDate": "2026-05-12T10:00:00Z",
                "endDate": deadline.strftime("%Y-%m-%dT10:00:00Z"),
                "maxAmountForPersonInfo": "13 682 538,80 ₽",
                "isActive": True,
                "selectionAcceptingApplicationInfo": {
                    "acceptingApplicationsInfo": "меньше 1 дня",
                    "countDaysEndDate": 0.5,
                },
                "activityId": "activity-1",
                "competitionId": "competition-1",
                "id": "card-1",
            }
        )

        result = client.analyze_document(
            "Грант Агростартап",
            raw_text,
            source_name="promote.budget.gov.ru / Минфин - отборы и меры поддержки",
            url="https://promote.budget.gov.ru/public/minfin/selection/view/competition-1?showBackButton=true&competitionType=0&tab=1",
            level="support_measures",
            region="federal",
        )

        self.assertEqual(result.page_type, "selection_announcement")
        self.assertEqual(result.support_status, "active")
        self.assertEqual(result.application_status, "open")
        self.assertIsNotNone(result.deadline_text)
        self.assertEqual(result.action_level, "requires_attention")

    def test_promote_open_selection_with_far_deadline_becomes_watchlist(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])
        deadline = datetime.now(timezone.utc) + timedelta(days=5)
        raw_text = _build_raw_text(
            {
                "title": "Грант Агростартап",
                "shortName": "Агростартап",
                "pppItemName": "Министерство сельского хозяйства Российской Федерации",
                "startDate": "2026-05-12T10:00:00Z",
                "endDate": deadline.strftime("%Y-%m-%dT10:00:00Z"),
                "maxAmountForPersonInfo": "13 682 538,80 ₽",
                "isActive": True,
                "selectionAcceptingApplicationInfo": {
                    "acceptingApplicationsInfo": "5 дней",
                    "countDaysEndDate": 5,
                },
                "activityId": "activity-3",
                "competitionId": "competition-3",
                "id": "card-3",
            }
        )

        result = client.analyze_document(
            "Грант Агростартап",
            raw_text,
            source_name="promote.budget.gov.ru / Минфин - отборы и меры поддержки",
            url="https://promote.budget.gov.ru/public/minfin/selection/view/competition-3?showBackButton=true&competitionType=0&tab=1",
            level="support_measures",
            region="federal",
        )

        self.assertEqual(result.page_type, "selection_announcement")
        self.assertEqual(result.support_status, "active")
        self.assertEqual(result.application_status, "open")
        self.assertIsNotNone(result.deadline_text)
        self.assertEqual(result.action_level, "watchlist")

    def test_promote_expired_selection_downgrades_from_urgent(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])
        deadline = datetime.now(timezone.utc) - timedelta(days=2)
        raw_text = _build_raw_text(
            {
                "title": "Грант на развитие животноводства",
                "shortName": "Животноводство",
                "pppItemName": "Министерство сельского хозяйства Российской Федерации",
                "startDate": "2026-05-10T10:00:00Z",
                "endDate": deadline.strftime("%Y-%m-%dT10:00:00Z"),
                "maxAmountForPersonInfo": "20 000 000,00 ₽",
                "isActive": True,
                "selectionAcceptingApplicationInfo": {
                    "acceptingApplicationsInfo": "0 дней",
                    "countDaysEndDate": 0,
                },
                "activityId": "activity-2",
                "competitionId": "competition-2",
                "id": "card-2",
            }
        )

        result = client.analyze_document(
            "Грант на развитие животноводства",
            raw_text,
            source_name="promote.budget.gov.ru / Минфин - отборы и меры поддержки",
            url="https://promote.budget.gov.ru/public/minfin/selection/view/competition-2?showBackButton=true&competitionType=0&tab=1",
            level="support_measures",
            region="federal",
        )

        self.assertEqual(result.page_type, "selection_announcement")
        self.assertEqual(result.support_status, "active")
        self.assertEqual(result.application_status, "closed")
        self.assertIsNotNone(result.deadline_text)
        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertIn(result.action_level, {"watchlist", "background"})

    def test_results_protocol_never_requires_attention_even_with_subsidy_words(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Протокол рассмотрения заявок на субсидии",
            "Протокол отбора, субсидии, сельское хозяйство, результаты конкурса.",
            source_name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/protocol/",
            level="regional",
        )

        self.assertEqual(result.page_type, "results_protocol")
        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertEqual(result.business_signal, "Результаты/протокол отбора: не требует срочной реакции")

    def test_npa_number_extracted_from_sample_text(self) -> None:
        client = MockLLMClient(["льготное кредитование АПК"])

        result = client.analyze_document(
            "Льготное кредитование АПК",
            "Активная мера поддержки. На регулярной основе. 22-68850-00258-Р. бывш. 1528.",
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            level="support_measures",
        )

        self.assertEqual(result.npa_number, "22-68850-00258-Р")

    def test_terms_text_does_not_become_deadline_text(self) -> None:
        client = MockLLMClient(["льготное кредитование АПК"])

        result = client.analyze_document(
            "Льготное кредитование АПК",
            "Активная мера поддержки. На регулярной основе. Срок кредита: До 12 месяцев.",
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            level="support_measures",
            region="federal",
        )

        self.assertIsNone(result.deadline_text)
        self.assertIsNotNone(result.terms_text)
        self.assertIn("Срок кредита: До 12 месяцев", result.terms_text or "")

    def test_deadline_text_extracts_real_application_deadline(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Объявлен конкурсный отбор заявок на субсидии для АПК",
            "Прием заявок до 30.06.2026. Документы принимаются в электронном виде.",
            source_name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/subsidy-0630/",
            level="support_measures",
            region="stavropol",
        )

        self.assertEqual(result.application_status, "open")
        self.assertIn("Прием заявок", result.deadline_text or "")
        self.assertIn("30.06.2026", result.deadline_text or "")

    def test_negated_without_changes_subsidy_rule_is_not_requires_attention(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Порядок предоставления субсидий не изменился",
            (
                "Порядок предоставления субсидий не изменился. "
                "Действует прежняя редакция без изменений и без новых условий."
            ),
            source_name="Право Ставропольского края",
            url="https://pravo.stavregion.ru/doc/no-change-subsidy",
            level="regional",
            region="stavropol",
        )

        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertIn(result.action_level, {"watchlist", "background"})

    def test_negated_without_extension_deadline_is_not_requires_attention(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Сообщение об отборе на субсидии",
            (
                "Срок приема заявок не продлевался. "
                "Прием завершен, новых сроков подачи заявок не объявлено."
            ),
            source_name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/subsidy-no-extension/",
            level="support_measures",
            region="stavropol",
        )

        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertIn(result.action_level, {"watchlist", "background"})

    def test_negated_without_new_terms_is_not_requires_attention(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Обновление информации о субсидиях",
            (
                "Без новых условий предоставления субсидий. "
                "Изменение порядка не предусмотрено, действуют прежние требования."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/subsidy-no-new-terms/",
            level="regional",
            region="krasnodar",
        )

        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertIn(result.action_level, {"watchlist", "background"})

    def test_negated_without_deadline_text_is_not_requires_attention(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Разъяснение по субсидии",
            (
                "Без срока подачи заявок. "
                "Материал носит справочный характер, новых окон приема не открыто."
            ),
            source_name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/subsidy-no-deadline/",
            level="regional",
            region="rostov",
        )

        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertIn(result.action_level, {"watchlist", "background"})

    def test_real_subsidy_rule_change_still_requires_attention(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Утверждены изменения порядка предоставления субсидий сельхозтоваропроизводителям",
            (
                "Внесены изменения в порядок предоставления субсидий. "
                "Прием заявок открыт до 30 июня 2026 года. Получатели — сельхозтоваропроизводители."
            ),
            source_name="Право Ставропольского края",
            url="https://pravo.stavregion.ru/doc/subsidy-change",
            level="regional",
            region="stavropol",
        )

        self.assertEqual(result.action_level, "requires_attention")
        self.assertEqual(result.application_status, "open")
        self.assertIsNotNone(result.deadline_text)

    def test_regional_sport_subsidy_pdf_is_not_requires_attention(self) -> None:
        client = MockLLMClient(["порядок предоставления", "субсидии"])

        result = client.analyze_document(
            "Постановление об утверждении порядка предоставления субсидий организациям физической культуры и спорта",
            (
                "Утвержден порядок предоставления субсидий организациям физической культуры "
                "и спорта Краснодарского края. Региональная программа развития спорта."
            ),
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/upload/iblock/sport-subsidy.pdf",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "new_rule")
        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertIn(result.action_level, {"background", "irrelevant"})

    def test_non_agro_subsidy_domains_do_not_pass_ahstep_gate(self) -> None:
        client = MockLLMClient(["порядок предоставления", "субсидии", "финансирование"])

        cases = [
            (
                "Постановление о порядке предоставления субсидий в сфере туризма",
                "Субсидии предоставляются туристическим организациям на развитие внутреннего туризма.",
            ),
            (
                "Постановление о субсидиях учреждениям культуры",
                "Утверждены условия финансирования театров, музеев и учреждений культуры.",
            ),
            (
                "Постановление о поддержке образовательных организаций и школ",
                "Утвержден порядок предоставления субсидий школам и организациям образования.",
            ),
        ]

        for title, raw_text in cases:
            with self.subTest(title=title):
                result = client.analyze_document(
                    title,
                    raw_text,
                    source_name="Право Ставропольского края",
                    url="https://pravo.stavregion.ru/document/non-agro",
                    level="regional",
                    region="stavropol",
                )

                self.assertNotEqual(result.action_level, "requires_attention")
                self.assertIn(result.action_level, {"background", "irrelevant"})

    def test_government_finance_education_health_decision_is_hidden_without_agro(self) -> None:
        client = MockLLMClient(["порядок предоставления", "финансирование", "субсидии"])

        result = client.analyze_document(
            "Постановление о финансировании школ и медицинских организаций",
            (
                "Правительство утвердило условия предоставления субсидий на развитие "
                "образования и здравоохранения. Документ определяет региональную программу."
            ),
            source_name="Правительство РФ - документы",
            url="http://government.ru/docs/health-education-finance/",
            level="federal",
            region="federal",
        )

        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertIn(result.action_level, {"background", "irrelevant"})

    def test_terrorism_prevention_program_is_hidden_even_with_government_program(self) -> None:
        client = MockLLMClient(["государственная программа", "финансирование"])

        result = client.analyze_document(
            "Постановление о государственной программе профилактики терроризма и безопасности населения",
            (
                "Утверждены условия финансирования мероприятий по профилактике терроризма, "
                "антитеррористической защищенности и безопасности населения."
            ),
            source_name="Право Ростовской области",
            url="https://pravo.donland.ru/doc/view/id/security-program/",
            level="regional",
            region="rostov",
        )

        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertIn(result.action_level, {"background", "irrelevant"})

    def test_ahstep_agriculture_domains_pass_relevance_gate(self) -> None:
        client = MockLLMClient(
            [
                "субсидии сельское хозяйство",
                "льготное кредитование АПК",
                "экспорт зерна",
                "удобрения",
            ]
        )

        cases = [
            (
                "Постановление о порядке предоставления субсидий сельхозтоваропроизводителям",
                "Прием заявок открыт до 30 июня 2026 года. Получатели — сельхозтоваропроизводители.",
                "Право Ростовской области",
                "https://pravo.donland.ru/doc/view/id/agro-subsidy/",
                {"requires_attention"},
            ),
            (
                "Льготное кредитование АПК",
                "Активная мера поддержки для сельхозтоваропроизводителей. На регулярной основе.",
                "ГИСП - меры поддержки АПК",
                "https://gisp.gov.ru/nmp/measure/agro-credit",
                {"requires_attention", "watchlist"},
            ),
            (
                "Субсидия на поддержку молочного животноводства",
                "Мера поддержки производителей молока и КРС. Прием заявок открыт до 20 июня 2026 года.",
                "Минсельхоз Ростовской области - господдержка",
                "https://mcx.donland.ru/presscenter/events/dairy-support/",
                {"requires_attention"},
            ),
            (
                "Объявление об отборе на поддержку растениеводства, зерна и элитного семеноводства",
                "Субсидии предоставляются на растениеводство, зерновые культуры и элитное семеноводство.",
                "Минсельхоз Ставропольского края - господдержка",
                "https://mshsk.ru/gospodderzhka/selection-seeds-2026.php",
                {"requires_attention", "watchlist"},
            ),
            (
                "Постановление о квоте на экспорт зерна",
                "Правительство скорректировало экспортную квоту на зерно и пшеницу.",
                "Правительство РФ - документы",
                "http://government.ru/docs/grain-export-quota/",
                {"watchlist"},
            ),
            (
                "Постановление о регулировании экспорта минеральных удобрений",
                "Введены правила экспорта удобрений для нужд АПК и сельхозпроизводителей.",
                "Правительство РФ - документы",
                "http://government.ru/docs/fertilizer-export/",
                {"watchlist"},
            ),
        ]

        for title, raw_text, source_name, url, expected_levels in cases:
            with self.subTest(title=title):
                result = client.analyze_document(
                    title,
                    raw_text,
                    source_name=source_name,
                    url=url,
                    level="support_measures" if "gisp.gov.ru" in url else "regional",
                    region="federal" if "government.ru" in url or "gisp.gov.ru" in url else "rostov",
                )

                self.assertIn(result.action_level, expected_levels)
                self.assertNotEqual(result.action_level, "irrelevant")

    def test_real_application_window_announcement_remains_requires_attention(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Объявлен отбор заявок на субсидии для АПК",
            (
                "Объявлен конкурсный отбор. "
                "Прием заявок открыт до 15 июля 2026 года."
            ),
            source_name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/subsidy-window-open/",
            level="support_measures",
            region="stavropol",
        )

        self.assertEqual(result.action_level, "requires_attention")
        self.assertEqual(result.application_status, "open")
        self.assertIn("15 июля 2026 года", result.deadline_text or "")

    def test_active_regular_without_deadline_or_open_is_watchlist(self) -> None:
        client = MockLLMClient(["государственная поддержка АПК"])

        result = client.analyze_document(
            "Поддержка сельхозпроизводителей",
            "Активная мера поддержки. На регулярной основе. Общая информация о мере поддержки.",
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/12447974",
            level="support_measures",
            region="federal",
        )

        self.assertEqual(result.support_status, "active")
        self.assertEqual(result.application_status, "regular")
        self.assertEqual(result.action_level, "watchlist")

    def test_non_target_active_regular_measure_is_not_requires_attention(self) -> None:
        client = MockLLMClient(["финансирование АПК"])

        result = client.analyze_document(
            'Льготные заёмное финансирование РФРП Ульяновской области по программе "Финансирование АПК".',
            "Активная мера поддержки. На регулярной основе. Региональная программа Ульяновской области.",
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/12446930",
            level="support_measures",
            region="federal",
        )

        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertEqual(result.action_level, "background")
        self.assertEqual(
            result.business_signal,
            "Активная мера поддержки вне целевой географии; оставлена для справки.",
        )

    def test_inactive_measure_with_deadline_text_is_not_requires_attention(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Субсидии на возмещение затрат",
            "Не активная мера поддержки. Прием заявок до 30.06.2026. Конкурсный отбор проводился ранее.",
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/8130026",
            level="support_measures",
            region="federal",
        )

        self.assertEqual(result.support_status, "inactive")
        self.assertIsNotNone(result.deadline_text)
        self.assertEqual(result.action_level, "background")
        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertIn("не является текущим окном подачи", result.risk_notes or "")

    def test_gisp_listing_title_becomes_reference_not_requires_attention(self) -> None:
        client = MockLLMClient(["государственная поддержка АПК"])

        result = client.analyze_document(
            "Субсидии",
            (
                "Главная Навигатор мер поддержки Сравнить Избранное Смотреть все "
                "Открытые данные Объявления Господдержка Гостехнадзор"
            ),
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/main/",
            level="support_measures",
            region="federal",
        )

        self.assertEqual(result.page_type, "reference_page")
        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertEqual(
            result.business_signal,
            "Страница похожа на раздел/листинг мер поддержки, не на конкретную карточку меры.",
        )

    def test_generic_regional_orders_page_is_not_requires_attention(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Приказы минсельхоза Краснодарского края",
            "Документы Приказы минсельхоза Краснодарского края Архив документов.",
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/prikazy-minselkhoza-krasnodarskogo-kraya",
            level="regional",
            region="krasnodar",
        )

        self.assertIn(result.page_type, {"reference_page", "registry", "section_page"})
        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertEqual(
            result.business_signal,
            "Общий раздел документов/приказов; прямой GR-сигнал не выявлен.",
        )

    def test_regional_subsidies_section_becomes_background_not_watchlist(self) -> None:
        client = MockLLMClient(["государственная поддержка АПК"])

        result = client.analyze_document(
            "Субсидии",
            "Главная Документы Субсидии Раздел с документами и архивом приказов.",
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/subsidii/",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "reference_page")
        self.assertIn(result.action_level, {"background", "irrelevant"})
        self.assertEqual(
            result.business_signal,
            "Общий раздел/список документов; прямой GR-сигнал не выявлен.",
        )

    def test_support_documents_2022_listing_becomes_background(self) -> None:
        client = MockLLMClient(["государственная поддержка АПК"])

        result = client.analyze_document(
            "Субсидирование и финансирование 2022",
            (
                "Главная Документы Субсидирование и финансирование 2022 "
                "Электронный бюджет Инструкция по заполнению отчета "
                "Виноградарство и виноделие Животноводство Инвестиционные кредиты "
                "Льготное кредитование Мелиорация Экспорт Перерабатывающая промышленность."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1/i2022",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "reference_page")
        self.assertIn(result.action_level, {"background", "irrelevant"})

    def test_support_documents_2024_listing_becomes_background(self) -> None:
        client = MockLLMClient(["государственная поддержка АПК"])

        result = client.analyze_document(
            "Субсидирование и финансирование 2024",
            (
                "Главная Документы Субсидирование и финансирование 2024 "
                "АКТУАЛЬНЫЕ ОТБОРЫ ЭЛЕКТРОННЫЙ БЮДЖЕТ "
                "ПОРТАЛ ПРЕДОСТАВЛЕНИЯ МЕР ФИНАНСОВОЙ ГОСУДАРСТВЕННОЙ ПОДДЕРЖКИ "
                "Инструкции по заполнению отчетов Виноградарство Животноводство "
                "Инвестиционные кредиты Льготное кредитование."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/subsidirovanie-i-finansirovanie1/i2024",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "reference_page")
        self.assertIn(result.action_level, {"background", "irrelevant"})

    def test_regional_vacancies_is_not_watchlist(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Вакансии",
            "Госслужба Вакансии Раздел с конкурсами на замещение должностей государственной службы.",
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/content/1254/",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "section_page")
        self.assertIn(result.action_level, {"irrelevant", "background"})

    def test_regional_urban_planning_is_not_watchlist(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Градостроительная деятельность",
            "Генеральные планы Калькулятор процедур Контактный центр по вопросам предоставления услуг в электронном виде.",
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/content/1260/",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "section_page")
        self.assertIn(result.action_level, {"irrelevant", "background"})

    def test_regional_contact_center_is_not_watchlist(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Контактный центр по вопросам предоставления услуг в электронном виде",
            "Контактный центр по вопросам предоставления услуг в электронном виде. Справочная информация для заявителей.",
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/content/1265/",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "section_page")
        self.assertIn(result.action_level, {"irrelevant", "background"})

    def test_regional_procurement_is_not_watchlist(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Закупки",
            "Закупки Международное сотрудничество Защита от ЧС Аналитика Биржевая торговля в АПК.",
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/activity/purchases",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "section_page")
        self.assertIn(result.action_level, {"irrelevant", "background"})

    def test_real_regional_subsidy_document_still_visible(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Объявлен отбор заявок на субсидии для АПК Краснодарского края",
            "Прием заявок до 20 мая 2026 года. Субсидия предоставляется сельхозтоваропроизводителям.",
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/subsidy-open-2026",
            level="regional",
            region="krasnodar",
        )

        self.assertNotEqual(result.page_type, "section_page")
        self.assertIn(result.action_level, {"watchlist", "requires_attention"})

    def test_privacy_policy_is_not_visible_watchlist(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Политика обработки персональных данных",
            "Политика обработки персональных данных и правила использования сайта министерства.",
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/department/politika-obrabotki-personalnykh-dannykh",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.action_level, "irrelevant")
        self.assertIn(result.page_type, {"section_page", "reference_page", "navigation"})

    def test_independent_expertise_generic_page_is_not_visible_watchlist(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Независимая экспертиза",
            "Независимая экспертиза. Нормативная база для награждения. Основные направления работы министерства.",
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/nezavisimaya-ekspertiza1",
            level="regional",
            region="krasnodar",
        )

        self.assertIn(result.action_level, {"background", "irrelevant"})
        self.assertIn(result.page_type, {"section_page", "reference_page", "navigation"})

    def test_cultural_heritage_authority_page_is_not_visible_watchlist(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Управление государственной охраны объектов культурного наследия",
            "Управление Государственная историко-культурная экспертиза Информация о проведенных проверках деятельности органов местного самоуправления.",
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/content/1280/",
            level="regional",
            region="krasnodar",
        )

        self.assertIn(result.action_level, {"background", "irrelevant"})
        self.assertIn(result.page_type, {"section_page", "reference_page", "navigation"})

    def test_priorities_generic_page_is_not_visible_watchlist(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Приоритеты",
            "Пресс-центр Край Губернатор Власть Деятельность Документы Визитка Значимая дата года.",
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/content/1202/",
            level="regional",
            region="krasnodar",
        )

        self.assertIn(result.action_level, {"background", "irrelevant"})
        self.assertIn(result.page_type, {"section_page", "reference_page", "navigation"})

    def test_government_real_sector_document_is_not_navigation(self) -> None:
        client = MockLLMClient(["государственная поддержка АПК"])

        result = client.analyze_document(
            "Постановление о субсидиях для организаций АПК",
            (
                "Правительство России Постановление от 30.04.2026 "
                "о субсидиях для организаций агропромышленного комплекса. "
                "Внесены изменения в порядок предоставления субсидий."
            ),
            source_name="Правительство РФ - документы",
            url="http://government.ru/docs/58699/",
            level="federal",
            region="federal",
        )

        self.assertNotEqual(result.page_type, "navigation")
        self.assertNotEqual(result.action_level, "irrelevant")

    def test_government_strategy_doc_with_apk_signal_is_not_irrelevant(self) -> None:
        client = MockLLMClient(["государственная поддержка АПК"])

        result = client.analyze_document(
            "Постановление о государственной поддержке АПК",
            "Проект постановления уточняет порядок предоставления субсидий сельскому хозяйству.",
            source_name="Правительство РФ - документы",
            url="http://government.ru/docs/60001/",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.action_level, "watchlist")
        self.assertNotEqual(result.page_type, "navigation")

    def test_regional_npa_public_consultation_listing_is_background(self) -> None:
        client = MockLLMClient(["публичные консультации", "государственная поддержка АПК"])

        result = client.analyze_document(
            "Сводный отчёт о результатах проведения публичных консультаций",
            "Публичные консультации по проекту порядка предоставления субсидий в АПК Краснодарского края.",
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/content/1397/",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "reference_page")
        self.assertEqual(result.action_level, "background")

    def test_cultural_heritage_npa_is_background_not_watchlist(self) -> None:
        client = MockLLMClient(["субсидии"])

        result = client.analyze_document(
            "Об установлении зон охраны объектов культурного наследия",
            (
                "ПРИКАЗ от 01.04.2026. "
                "Постановление об утверждении границ зон охраны объектов культурного наследия "
                "на территории Краснодарского края."
            ),
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/upload/iblock/heritage-zones.pdf",
            level="regional",
            region="krasnodar",
        )

        # Early guard fires before section_page/watchlist escalation via "приказ от".
        self.assertEqual(result.action_level, "background")

    def test_cultural_heritage_npa_with_rule_words_is_background(self) -> None:
        client = MockLLMClient(["государственная поддержка"])

        result = client.analyze_document(
            "Об утверждении границ территории объектов культурного наследия",
            (
                "ПРИКАЗ от 26.05.2026. "
                "Приказ об утверждении границ территории объектов культурного наследия, "
                "памятников истории и культуры краевого значения."
            ),
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/upload/iblock/heritage-boundaries.pdf",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.action_level, "background")

    def test_agricultural_regional_npa_remains_watchlist(self) -> None:
        client = MockLLMClient(["субсидии АПК"])

        result = client.analyze_document(
            "О внесении изменений в порядок предоставления субсидий сельхозтоваропроизводителям",
            (
                "Постановление о внесении изменений в порядок предоставления субсидий "
                "сельхозтоваропроизводителям Краснодарского края на развитие растениеводства."
            ),
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/upload/iblock/agro-subsidy-order.pdf",
            level="regional",
            region="krasnodar",
        )

        # "внесены изменения в порядок предоставления субсидий" → strong signal;
        # watchlist or requires_attention are both correct agro outcomes.
        self.assertIn(result.action_level, ("watchlist", "requires_attention"))

    def test_krasnodar_ministry_subsidy_pdf_unaffected_by_heritage_fix(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Приказ о предоставлении субсидий на развитие молочного скотоводства",
            (
                "Приказ Министерства сельского хозяйства Краснодарского края "
                "о предоставлении субсидий на развитие молочного скотоводства."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/upload/prikaz-molochnoe-2026.pdf",
            level="regional",
            region="krasnodar",
        )

        self.assertIn(result.action_level, ("watchlist", "requires_attention"))

    def test_admkrai_real_pdf_npa_is_not_unknown_page_type(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Просмотр",
            (
                "МИНИСТЕРСТВО СЕЛЬСКОГО ХОЗЯЙСТВА КРАСНОДАРСКОГО КРАЯ "
                "О внесении изменений в приказ министерства сельского хозяйства "
                "Краснодарского края о предоставлении субсидий."
            ),
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/upload/iblock/261/subsidy-order.pdf",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "new_rule")
        self.assertEqual(result.action_level, "requires_attention")

    def test_msh_krasnodar_subsidy_pdf_is_not_unknown_page_type(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Приказ о предоставлении субсидий сельхозтоваропроизводителям",
            "Приказ министерства сельского хозяйства Краснодарского края о предоставлении субсидий.",
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/upload/prikaz-subsidy-2026.pdf",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "new_rule")
        self.assertIn(result.action_level, {"watchlist", "requires_attention"})

    def test_mcx_donland_category_listing_is_background_reference_page(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Животноводство",
            (
                "Животноводство. С 01.01.2025 проведение отбора по субсидиям будет "
                "осуществляться в электронном виде. Доступные меры государственной поддержки."
            ),
            source_name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/activity/37370/",
            level="regional",
            region="rostov",
        )

        self.assertEqual(result.page_type, "reference_page")
        self.assertEqual(result.action_level, "background")

    def test_mcx_donland_documents_listing_is_background_reference_page(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Действующие документы",
            (
                "Действующие документы Приказ от 23.04.2026 № П-117 "
                "О внесении изменений в приказ министерства. "
                "Постановление Правительства Ростовской области от 09.04.2026 № 8."
            ),
            source_name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/documents/active/",
            level="regional",
            region="rostov",
        )

        self.assertEqual(result.page_type, "reference_page")
        self.assertEqual(result.action_level, "background")

    def test_mcx_donland_real_selection_announcement_stays_visible(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Объявление о проведении отбора на предоставление субсидии сельхозтоваропроизводителям",
            (
                "Объявление о проведении отбора на предоставление субсидии "
                "сельхозтоваропроизводителям. Прием заявок открыт до 20 мая 2026 года."
            ),
            source_name="Минсельхоз Ростовской области - господдержка",
            url="https://mcx.donland.ru/presscenter/events/72822/",
            level="regional",
            region="rostov",
        )

        self.assertEqual(result.page_type, "selection_announcement")
        self.assertEqual(result.action_level, "requires_attention")

    def test_pravo_donland_listing_is_background_reference_page(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "ОФИЦИАЛЬНОЕ ОПУБЛИКОВАНИЕ",
            (
                "Официальное опубликование правовых актов. Найдено документов: 51330. "
                "Документ за сегодня, за неделю, за месяц, все."
            ),
            source_name="Право Ростовской области",
            url="https://pravo.donland.ru/doc/list/clear/1/",
            level="regional",
            region="rostov",
        )

        self.assertEqual(result.page_type, "reference_page")
        self.assertEqual(result.action_level, "background")

    def test_pravo_donland_real_npa_is_new_rule_not_unknown(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Постановление Правительства Ростовской области от 29.04.2026 № 42 «О внесении изменений в порядок предоставления субсидий сельскохозяйственным товаропроизводителям»",
            (
                "Постановление Правительства Ростовской области от 29.04.2026 № 42. "
                "О внесении изменений в порядок предоставления субсидий "
                "сельскохозяйственным товаропроизводителям."
            ),
            source_name="Право Ростовской области",
            url="https://pravo.donland.ru/doc/view/id/Постановление_42_29042026_60001/",
            level="regional",
            region="rostov",
        )

        self.assertEqual(result.page_type, "new_rule")
        self.assertEqual(result.action_level, "requires_attention")

    def test_mshsk_listing_is_background_reference_page(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Субсидии",
            (
                "Субсидии. Объявления. Результаты. Электронный Бюджет. "
                "Грантовая поддержка Агростартап. Решения о порядке предоставления субсидии."
            ),
            source_name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/subsidii/",
            level="regional",
            region="stavropol",
        )

        self.assertEqual(result.page_type, "reference_page")
        self.assertEqual(result.action_level, "background")

    def test_mshsk_real_selection_announcement_stays_visible(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Объявление об отборе на возмещение части затрат, связанных с посадкой ягодных культур",
            (
                "Объявление об отборе на возмещение части затрат, связанных с посадкой ягодных культур. "
                "Прием заявок открыт до 20 февраля 2099 года."
            ),
            source_name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/gospodderzhka/selection-berry-2099.php",
            level="regional",
            region="stavropol",
        )

        self.assertEqual(result.page_type, "selection_announcement")
        self.assertEqual(result.action_level, "requires_attention")

    def test_pravo_stavregion_listing_is_background_reference_page(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Правовые акты",
            "Правовые акты. Поиск документов. Архив документов. Список документов и переход по страницам архива.",
            source_name="Право Ставропольского края",
            url="https://pravo.stavregion.ru/document/list/",
            level="regional",
            region="stavropol",
        )

        self.assertEqual(result.page_type, "reference_page")
        self.assertEqual(result.action_level, "background")

    def test_pravo_stavregion_real_npa_is_new_rule_not_unknown(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Постановление Правительства Ставропольского края от 29.04.2026 № 123-п «О внесении изменений в порядок предоставления субсидий сельскохозяйственным товаропроизводителям»",
            (
                "Постановление Правительства Ставропольского края от 29.04.2026 № 123-п. "
                "О внесении изменений в порядок предоставления субсидий "
                "сельскохозяйственным товаропроизводителям."
            ),
            source_name="Право Ставропольского края",
            url="https://pravo.stavregion.ru/document/98765",
            level="regional",
            region="stavropol",
        )

        self.assertEqual(result.page_type, "new_rule")
        self.assertEqual(result.action_level, "requires_attention")

    def test_regional_npa_archive_listing_is_background_or_irrelevant(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Архив нормативных актов",
            "Раздел нормативных правовых актов. Архив документов.",
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/content/1291/archive/",
            level="regional",
            region="krasnodar",
        )

        self.assertIn(result.action_level, {"background", "irrelevant"})

    def test_regional_non_agro_new_rule_stays_background(self) -> None:
        action_level = detect_action_level(
            "Приказ министерства транспорта Краснодарского края",
            "Документ о временных ограничениях движения транспортных средств.",
            (),
            page_type="new_rule",
            content_quality="full_text",
            is_service_page=False,
            source_role="regional_npa",
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/upload/test-transport.pdf",
            domain="admkrai.krasnodar.ru",
            level="regional",
            region="krasnodar",
            facts=DocumentFacts(),
            has_strategy_signal=False,
            has_support_document_signal=False,
            has_regional_npa_signal=False,
            has_news_signal_value=False,
        )
        self.assertIn(action_level, {"background", "irrelevant"})

    def test_generic_pdf_title_is_replaced_from_summary_signal(self) -> None:
        client = MockLLMClient(["государственная поддержка АПК"])

        result = client.analyze_document(
            "Просмотр",
            (
                "МИНИСТЕРСТВО СЕЛЬСКОГО ХОЗЯЙСТВА И ПЕРЕРАБАТЫВАЮЩЕЙ ПРОМЫШЛЕННОСТИ "
                "КРАСНОДАРСКОГО КРАЯ\n"
                "О ВНЕСЕНИИ ИЗМЕНЕНИЙ В ПРИКАЗ МИНИСТЕРСТВА СЕЛЬСКОГО ХОЗЯЙСТВА "
                "КРАСНОДАРСКОГО КРАЯ ОТ 10 ЯНВАРЯ 2026 Г. № 12\n"
                "Текст приказа."
            ),
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/upload/test.pdf",
            level="regional",
            region="krasnodar",
        )

        self.assertTrue((result.normalized_title or "").startswith("О внесении изменений"))
        self.assertNotEqual(result.normalized_title, "Просмотр")

    def test_zol_export_support_signal_is_requires_attention(self) -> None:
        client = MockLLMClient(["Поддержка экспорта АПК", "экспортная пошлина"])

        result = client.analyze_document(
            "Пошлина на экспорт пшеницы из РФ останется нулевой",
            "В правительстве подтвердили параметры экспортной пошлины и меры поддержки экспорта АПК.",
            source_name="ZOL.ru - зерновые новости",
            url="https://www.zol.ru/n/41337",
            level="news",
            region="federal",
        )

        self.assertEqual(result.action_level, "requires_attention")

    def test_zol_broad_export_story_with_secondary_quota_is_background(self) -> None:
        client = MockLLMClient(["экспорт зерна", "квота на экспорт"])

        result = client.analyze_document(
            "Причины рекордного экспорта зерна по железной дороге",
            (
                "Экспорт зерна побьет рекорды благодаря урожаю и тарифам. "
                "В тексте также упомянута дополнительная квота на экспорт и оценка экспертов рынка."
            ),
            source_name="ZOL.ru - зерновые новости",
            url="https://www.zol.ru/n/41332",
            level="news",
            region="federal",
        )

        self.assertEqual(result.action_level, "background")

    def test_zol_forecast_news_is_background(self) -> None:
        client = MockLLMClient(["экспорт зерна", "сельское хозяйство"])

        result = client.analyze_document(
            "Совэкон повысил прогноз экспорта пшеницы из России",
            "Аналитики повысили прогноз экспорта пшеницы и оценку рынка зерна без мер господдержки и решений правительства.",
            source_name="ZOL.ru - зерновые новости",
            url="https://www.zol.ru/n/41322",
            level="news",
            region="federal",
        )

        self.assertEqual(result.action_level, "background")

    def test_zol_forecast_with_secondary_quota_context_stays_background(self) -> None:
        client = MockLLMClient(["экспорт зерна", "сельское хозяйство"])

        result = client.analyze_document(
            "«Совэкон» повысил прогноз экспорта пшеницы из РФ",
            (
                "Консалтинговая компания повысила прогноз экспорта пшеницы. "
                "В тексте также упомянута квота Турции на импорт кукурузы и мировой рынок."
            ),
            source_name="ZOL.ru - зерновые новости",
            url="https://www.zol.ru/n/4133d",
            level="news",
            region="federal",
        )

        self.assertEqual(result.action_level, "background")

    def test_zol_fieldwork_regional_news_is_background(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Весенние посевные работы в Волгоградской области идут с опережением",
            "В регионе продолжаются полевые работы и сев ранних культур без решений по господдержке или регулированию.",
            source_name="ZOL.ru - зерновые новости",
            url="https://www.zol.ru/n/41332",
            level="news",
            region="federal",
        )

        self.assertEqual(result.action_level, "background")

    def test_zol_fieldwork_with_support_amounts_stays_background(self) -> None:
        client = MockLLMClient(["сельское хозяйство", "льготные кредиты в АПК"])

        result = client.analyze_document(
            "Весенние посевные работы в Волгоградской области — на особом контроле экспертной группы",
            (
                "В регионе идет посевная. "
                "На проведение работ аграрии получат господдержку и льготные кредиты, "
                "но новость описывает ход полевых работ и посевное окно."
            ),
            source_name="ZOL.ru - зерновые новости",
            url="https://www.zol.ru/n/4132e",
            level="news",
            region="federal",
        )

        self.assertEqual(result.action_level, "background")

    def test_zol_selkhoztehnika_subsidy_news_is_watchlist(self) -> None:
        client = MockLLMClient(["субсидии сельхозтехника"])

        result = client.analyze_document(
            "В ГД предложили создать госпрограмму субсидирования ремонта сельхозтехники",
            (
                "Депутаты Государственной думы предложили создать государственную программу "
                "субсидирования ремонта сельхозтехники для аграриев."
            ),
            source_name="ZOL.ru - зерновые новости",
            url="https://www.zol.ru/n/41000",
            level="news",
            region="federal",
        )

        self.assertEqual(result.action_level, "watchlist")

    def test_zol_government_support_signal_is_requires_attention(self) -> None:
        client = MockLLMClient(["господдержка АПК", "субсидии"])

        result = client.analyze_document(
            "Правительство расширило программу господдержки экспортеров АПК",
            "Правительство России утвердило изменения программы финансирования и субсидии для экспорта продукции АПК.",
            source_name="ZOL.ru - зерновые новости",
            url="https://www.zol.ru/n/4133d",
            level="news",
            region="federal",
        )

        self.assertEqual(result.action_level, "requires_attention")

    def test_regional_generic_agro_npa_stays_watchlist_not_requires_attention(self) -> None:
        client = MockLLMClient(["сельское хозяйство", "агропромышленный комплекс"])

        result = client.analyze_document(
            "Постановление Правительства Ростовской области о развитии агропромышленного комплекса",
            (
                "Постановление Правительства Ростовской области о развитии агропромышленного комплекса "
                "и мониторинге реализации отраслевой программы без изменения порядка предоставления субсидий "
                "и без сроков подачи заявок."
            ),
            source_name="Право Ростовской области",
            url="https://pravo.donland.ru/doc/view/id/program-agr-2026/",
            level="regional",
            region="rostov",
        )

        self.assertEqual(result.page_type, "new_rule")
        self.assertEqual(result.action_level, "watchlist")

    def test_non_agro_government_fuel_news_is_background(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "Правительство договорилось с нефтяниками по поставкам и ценам на топливо",
            "Правительство поручило заключить соглашения о стабилизации внутреннего рынка топлива.",
            source_name="ZOL.ru - зерновые новости",
            url="https://www.zol.ru/n/4133c",
            level="news",
            region="federal",
        )

        self.assertEqual(result.action_level, "background")

    def test_zol_generic_market_news_is_background(self) -> None:
        client = MockLLMClient(["сельское хозяйство"])

        result = client.analyze_document(
            "В Австралии ожидается снижение урожайности",
            "Рыночный обзор по зерну и погодным условиям без сигналов господдержки или регулирования.",
            source_name="ZOL.ru - зерновые новости",
            url="https://www.zol.ru/n/41399",
            level="news",
            region="federal",
        )

        self.assertEqual(result.action_level, "background")

    def test_mcx_official_support_expansion_news_is_watchlist(self) -> None:
        client = MockLLMClient(["господдержка АПК", "субсидии"])

        result = client.analyze_document(
            "Правительство расширило меры господдержки производителей молока",
            "Минсельхоз России. Новость АПК: Правительство расширило меры господдержки производителей молока.",
            source_name="Минсельхоз России - новости",
            url="https://mcx.gov.ru/press-service/news/pravitelstvo-rasshirilo-mery-gospodderzhki-proizvoditeley-moloka/",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.action_level, "watchlist")
        self.assertIn("Официальная новость Минсельхоза", result.business_signal or "")

    def test_mcx_official_apk_legislation_news_is_watchlist(self) -> None:
        client = MockLLMClient(["законопроект АПК"])

        result = client.analyze_document(
            "Совет Федерации одобрил ряд законопроектов в сфере АПК",
            "Минсельхоз России. Новость АПК: Совет Федерации одобрил ряд законопроектов в сфере АПК.",
            source_name="Минсельхоз России - новости",
            url="https://mcx.gov.ru/press-service/news/sovet-federatsii-odobril-ryad-zakonoproektov-v-sfere-apk/",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.action_level, "watchlist")

    def test_mcx_official_export_procedure_news_is_watchlist(self) -> None:
        client = MockLLMClient(["Поддержка экспорта АПК"])

        result = client.analyze_document(
            "Минсельхоз и ФТС продолжат совместную работу по упрощению процедур для экспортеров АПК",
            "Минсельхоз России. Новость АПК: Минсельхоз и ФТС продолжат совместную работу по упрощению процедур для экспортеров АПК.",
            source_name="Минсельхоз России - новости",
            url="https://mcx.gov.ru/press-service/news/minselkhoz-i-fts-prodolzhat-sovmestnuyu-rabotu-po-uproshcheniyu-protsedur-dlya-eksporterov-apk/",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.action_level, "watchlist")

    def test_mcx_official_digital_logistics_news_is_watchlist(self) -> None:
        client = MockLLMClient(["цифровая платформа АПК"])

        result = client.analyze_document(
            "Оксана Лут и Андрей Никитин обсудили развитие цифровых и логистических решений для АПК",
            "Минсельхоз России. Новость АПК: Оксана Лут и Андрей Никитин обсудили развитие цифровых и логистических решений для АПК.",
            source_name="Минсельхоз России - новости",
            url="https://mcx.gov.ru/press-service/news/oksana-lut-i-andrey-nikitin-obsudili-razvitie-tsifrovykh-i-logisticheskikh-resheniy-dlya-apk/",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.action_level, "watchlist")

    def test_mcx_official_ceremonial_news_stays_background(self) -> None:
        client = MockLLMClient(["АПК"])

        result = client.analyze_document(
            "Поздравление Оксаны Лут с Днем Победы",
            "Минсельхоз России. Новость АПК: Поздравление Оксаны Лут с Днем Победы.",
            source_name="Минсельхоз России - новости",
            url="https://mcx.gov.ru/press-service/news/pozdravlenie-oksany-lut-s-dnem-pobedy/",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.action_level, "background")

    def test_regulation_near_discussion_deadline_is_watchlist(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])
        deadline = datetime.now(timezone.utc) + timedelta(days=5)

        result = client.analyze_document(
            "Проект постановления о правилах поддержки сельхозтоваропроизводителей",
            self._regulation_public_discussion_text(deadline),
            source_name="Regulation.gov.ru",
            url="https://regulation.gov.ru/projects/167863",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.action_level, "watchlist")
        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertIn("Конец обсуждения", result.deadline_text or "")
        self.assertEqual(result.application_status, "open")
        self.assertIn("публичного обсуждения", result.impact)
        self.assertIn("публичном обсуждении", result.business_signal or "")

    def test_regulation_future_discussion_deadline_stays_watchlist(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])
        deadline = datetime.now(timezone.utc) + timedelta(days=30)

        result = client.analyze_document(
            "Проект постановления о правилах поддержки сельхозтоваропроизводителей",
            self._regulation_public_discussion_text(deadline),
            source_name="Regulation.gov.ru",
            url="https://regulation.gov.ru/projects/167864",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.action_level, "watchlist")
        self.assertIn("Конец обсуждения", result.deadline_text or "")

    def test_regulation_expired_discussion_deadline_does_not_escalate(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])
        deadline = datetime.now(timezone.utc) - timedelta(days=2)

        result = client.analyze_document(
            "Проект постановления о правилах поддержки сельхозтоваропроизводителей",
            self._regulation_public_discussion_text(deadline),
            source_name="Regulation.gov.ru",
            url="https://regulation.gov.ru/projects/167865",
            level="federal",
            region="federal",
        )

        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertIn(result.action_level, {"watchlist", "background"})
        self.assertIn("Конец обсуждения", result.deadline_text or "")

    def test_regulation_unrelated_discussion_item_stays_background(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])
        deadline = datetime.now(timezone.utc) + timedelta(days=5)

        result = client.analyze_document(
            "Проект приказа о туристских маршрутах",
            self._regulation_public_discussion_text(deadline, agro=False),
            source_name="Regulation.gov.ru",
            url="https://regulation.gov.ru/projects/167866",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.action_level, "background")

    def test_msh_krasnodar_potato_vegetable_subsidy_order_is_watchlist(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            '№167 от 30.04.2026 "О внесении изменений в приказ министерства сельского хозяйства и перерабатывающей промышленности Краснодарского края от 01 апреля 2026 г. № 114 «Об утверждении Порядка предоставления субсидий сельскохозяйственным товаропроизводителям на финансовое обеспечение части затрат на стимулирование увеличения производства картофеля и овощей»',
            (
                "МИНИСТЕРСТВО СЕЛЬСКОГО ХОЗЯЙСТВА И ПЕРЕРАБАТЫВАЮЩЕЙ ПРОМЫШЛЕННОСТИ "
                "КРАСНОДАРСКОГО КРАЯ. ПРИКАЗ. О внесении изменений в приказ от 01 апреля 2026 г. №114 "
                "«Об утверждении Порядка предоставления субсидий сельскохозяйственным товаропроизводителям "
                "на финансовое обеспечение части затрат на стимулирование увеличения производства картофеля и овощей»."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://npa.krasnodar.ru/rest/files/1233707",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "new_rule")
        self.assertEqual(result.action_level, "watchlist")

    def test_msh_krasnodar_kfh_grant_procedure_is_watchlist(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            '№ 161 от 28.04.2026 "Об утверждении Порядка предоставления грантов крестьянским (фермерским) хозяйствам на развитие фермерских хозяйств"',
            (
                "ПРИКАЗ. Об утверждении Порядка предоставления грантов крестьянским "
                "(фермерским) хозяйствам на развитие фермерских хозяйств. Гранты предоставляются "
                "в форме субсидий в рамках государственной программы развития сельского хозяйства."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://npa.krasnodar.ru/rest/files/1233654",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "new_rule")
        self.assertEqual(result.action_level, "watchlist")

    def test_msh_krasnodar_agrotourism_selection_working_group_is_watchlist(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            '№164 от 29.04.2026 "Об образовании рабочей группы по отбору проектов развития сельского туризма для участия в конкурсе на предоставление гранта «Агротуризм»"',
            (
                "ПРИКАЗ. Об образовании рабочей группы по отбору проектов развития сельского туризма "
                "для участия в конкурсе на предоставление гранта «Агротуризм». Утвердить положение "
                "о рабочей группе по отбору проектов."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://npa.krasnodar.ru/rest/files/1233677",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "new_rule")
        self.assertEqual(result.action_level, "watchlist")

    def test_msh_krasnodar_melioration_subsidy_order_is_watchlist(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            '№ 183 от 13.05.2026 "О внесении изменения в приказ министерства сельского хозяйства и перерабатывающей промышленности Краснодарского края от 19 марта 2018 г. № 70 «Об утверждении Порядка предоставления субсидий на реализацию проектов мелиорации»',
            (
                "ПРИКАЗ. О внесении изменения в приказ министерства сельского хозяйства "
                "Краснодарского края «Об утверждении Порядка предоставления субсидий на реализацию "
                "проектов мелиорации». Документ регулирует предоставление субсидий сельскому хозяйству."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://npa.krasnodar.ru/rest/files/1233833",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "new_rule")
        self.assertEqual(result.action_level, "watchlist")

    def test_msh_krasnodar_corruption_risk_order_stays_irrelevant(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            '№165 от 30.04.2026 "О внесении изменений в приказ министерства сельского хозяйства и перерабатывающей промышленности Краснодарского края от 01 апреля 2020 г. № 78 «О перечне должностей государственной гражданской службы Краснодарского края в министерстве сельского хозяйства и перерабатывающей промышленности Краснодарского края, замещение которых связано с коррупционными рисками»',
            (
                "ПРИКАЗ. О внесении изменений в перечень должностей государственной гражданской службы "
                "Краснодарского края, замещение которых связано с коррупционными рисками. "
                "Документ относится к противодействию коррупции и кадровой службе."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://npa.krasnodar.ru/rest/files/1233704",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.action_level, "irrelevant")
        self.assertIn("антикоррупционный", result.business_signal or "")

    def test_msh_krasnodar_parent_orders_listing_stays_background(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Приказы минсельхоза Краснодарского края",
            (
                "Приказы минсельхоза Краснодарского края. Все По названию За период с по. "
                "№167 О внесении изменений в порядок предоставления субсидий. "
                "№164 грант Агротуризм. Список документов и навигация раздела."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://msh.krasnodar.ru/documents/prikazy-minselkhoza-krasnodarskogo-kraya",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.page_type, "reference_page")
        self.assertEqual(result.action_level, "background")

    def test_msh_krasnodar_open_near_deadline_selection_is_requires_attention(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])
        deadline = datetime.now(timezone.utc) + timedelta(days=7)

        result = client.analyze_document(
            '№201 от 20.05.2026 "Об утверждении Порядка проведения конкурсного отбора на предоставление субсидий производителям зерновых культур"',
            (
                "МИНИСТЕРСТВО СЕЛЬСКОГО ХОЗЯЙСТВА И ПЕРЕРАБАТЫВАЮЩЕЙ ПРОМЫШЛЕННОСТИ "
                "КРАСНОДАРСКОГО КРАЯ. ПРИКАЗ. Объявлен конкурсный отбор на предоставление субсидий "
                "производителям зерновых культур. "
                f"Прием заявок до {deadline.strftime('%d.%m.%Y')}. "
                "Заявки подаются в министерство."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://npa.krasnodar.ru/rest/files/1234001",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.action_level, "requires_attention")

    def test_msh_krasnodar_open_far_deadline_selection_is_watchlist(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])
        deadline = datetime.now(timezone.utc) + timedelta(days=30)

        result = client.analyze_document(
            '№202 от 20.05.2026 "Об утверждении Порядка проведения конкурсного отбора на предоставление субсидий производителям молока"',
            (
                "ПРИКАЗ. Объявлен конкурсный отбор на предоставление субсидий производителям молока. "
                f"Прием заявок до {deadline.strftime('%d.%m.%Y')}. "
                "Гранты предоставляются в форме субсидий."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://npa.krasnodar.ru/rest/files/1234002",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.action_level, "watchlist")
        self.assertIn(deadline.strftime("%d.%m.%Y"), result.business_signal or "")

    def test_msh_krasnodar_open_no_deadline_is_watchlist(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            '№203 от 21.05.2026 "Об объявлении конкурсного отбора на предоставление грантов фермерским хозяйствам"',
            (
                "ПРИКАЗ. Объявлен конкурсный отбор на предоставление грантов крестьянским (фермерским) "
                "хозяйствам. Прием заявок открыт. Субсидии выплачиваются в рамках госпрограммы."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://npa.krasnodar.ru/rest/files/1234003",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.action_level, "watchlist")

    def test_msh_krasnodar_suspended_intake_is_watchlist(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            '№204 от 22.05.2026 "О приостановлении приема заявок на предоставление субсидий на развитие садоводства"',
            (
                "ПРИКАЗ. Прием заявок приостановлен в связи с исчерпанием лимитов бюджетных "
                "обязательств. Приказ об утверждении Порядка предоставления субсидий на развитие "
                "садоводства остается в силе."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://npa.krasnodar.ru/rest/files/1234004",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.action_level, "watchlist")
        self.assertEqual(result.application_status, "closed")
        self.assertIn("приостановлен", (result.business_signal or "").lower())

    def test_msh_krasnodar_horse_show_order_is_background(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            '№205 от 23.05.2026 "О проведении регионального конкурса по конному спорту среди сельскохозяйственных предприятий"',
            (
                "ПРИКАЗ. Провести региональный конкурс по конному спорту среди "
                "сельскохозяйственных предприятий Краснодарского края. Субсидии предусмотрены "
                "победителям. Дата проведения: июнь 2026 г."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://npa.krasnodar.ru/rest/files/1234005",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.action_level, "background")

    def test_msh_krasnodar_reopened_intake_near_deadline_is_requires_attention(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])
        deadline = datetime.now(timezone.utc) + timedelta(days=5)

        result = client.analyze_document(
            '№206 от 24.05.2026 "О возобновлении приема заявок на предоставление субсидий на компенсацию части затрат на приобретение семян"',
            (
                "ПРИКАЗ. Возобновлен прием заявок на предоставление субсидий на компенсацию "
                "части затрат на приобретение семян элитных сортов. "
                f"Прием заявок до {deadline.strftime('%d.%m.%Y')}. "
                "Гранты предоставляются в форме субсидий."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://npa.krasnodar.ru/rest/files/1234006",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.action_level, "requires_attention")
        self.assertEqual(result.application_status, "open")

    def test_msh_krasnodar_horse_farming_subsidy_is_not_suppressed(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            '№207 от 25.05.2026 "Об утверждении Порядка предоставления субсидий на поддержку развития коневодства и конного хозяйства"',
            (
                "ПРИКАЗ. Об утверждении Порядка предоставления субсидий сельскохозяйственным "
                "товаропроизводителям на поддержку развития коневодства и конного хозяйства "
                "Краснодарского края. Субсидии предоставляются в форме целевых выплат."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://npa.krasnodar.ru/rest/files/1234007",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.action_level, "watchlist")

    def test_msh_krasnodar_equestrian_sport_competition_stays_background(self) -> None:
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            '№208 от 26.05.2026 "О проведении краевого конкурса по конному спорту и выездке среди воспитанников аграрных колледжей"',
            (
                "ПРИКАЗ. Провести краевой конкурс по конному спорту среди воспитанников "
                "аграрных колледжей. Субсидии предусмотрены победителям. Июль 2026 г."
            ),
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            url="https://npa.krasnodar.ru/rest/files/1234008",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.action_level, "background")

    def test_regulation_gov_subsidy_title_not_blocked_by_domain_gate(self) -> None:
        """Fix 1: regulation.gov.ru + субсид in title must not return background."""
        client = MockLLMClient(["субсидии сельское хозяйство"])
        deadline = datetime.now(timezone.utc) + timedelta(days=5)

        result = client.analyze_document(
            "Об утверждении порядка предоставления субсидий производителям зерна",
            (
                "Проект НПА. Министерство: Минсельхоз России. "
                "Статус: Идет обсуждение. "
                f"Конец обсуждения: {deadline.strftime('%d.%m.%Y')}. "
                "Порядок предоставления субсидий."
            ),
            source_name="Regulation.gov.ru",
            url="https://regulation.gov.ru/projects/168100",
            level="federal",
            region="federal",
        )

        self.assertNotEqual(result.action_level, "background")

    def test_regulation_gov_procedure_deadline_bypass_near(self) -> None:
        """Fix 2: regulation.gov.ru + deadline near + subsidy procedure signal → watchlist."""
        client = MockLLMClient(["субсидии"])
        deadline = datetime.now(timezone.utc) + timedelta(days=6)

        result = client.analyze_document(
            "О порядке предоставления государственной поддержки предприятиям АПК",
            (
                "Проект НПА: О порядке предоставления государственной поддержки. "
                "Министерство: Минсельхоз России. "
                "Статус: Идет обсуждение. "
                f"Конец обсуждения: {deadline.strftime('%d.%m.%Y')}. "
                "Порядок предоставления субсидий сельхозтоваропроизводителям АПК."
            ),
            source_name="Regulation.gov.ru",
            url="https://regulation.gov.ru/projects/168101",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.action_level, "watchlist")
        self.assertNotEqual(result.action_level, "requires_attention")

    def test_regulation_gov_procedure_deadline_bypass_future(self) -> None:
        """Fix 2: regulation.gov.ru + deadline far + subsidy procedure signal → at most watchlist."""
        client = MockLLMClient(["субсидии"])
        deadline = datetime.now(timezone.utc) + timedelta(days=60)

        result = client.analyze_document(
            "О порядке предоставления субсидий на возмещение части затрат",
            (
                "Проект НПА. Министерство: Минсельхоз России. "
                f"Конец обсуждения: {deadline.strftime('%d.%m.%Y')}. "
                "Возмещение части затрат сельскохозяйственным товаропроизводителям."
            ),
            source_name="Regulation.gov.ru",
            url="https://regulation.gov.ru/projects/168102",
            level="federal",
            region="federal",
        )

        self.assertNotEqual(result.action_level, "background")
        self.assertNotEqual(result.action_level, "requires_attention")

    def test_mcx_docs_compensation_becomes_measure_card(self) -> None:
        """Fix 3: mcx.gov.ru /docs/ URL with compensation title → measure_card page_type."""
        client = MockLLMClient(["государственная поддержка АПК"])

        result = client.analyze_document(
            "Компенсация части затрат на приобретение семян сельскохозяйственных культур",
            "Минсельхоз России. Мера господдержки АПК. Компенсация части затрат на приобретение семян. Государственная поддержка агропромышленного комплекса.",
            source_name="Минсельхоз России - меры господдержки",
            url="https://mcx.gov.ru/docs/documents/9876/",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.page_type, "measure_card")

    def test_mcx_measures_subisidization_becomes_measure_card(self) -> None:
        """Fix 3: mcx.gov.ru /activity/state-support/measures/ URL with субсидирован → measure_card."""
        client = MockLLMClient(["государственная поддержка АПК"])

        result = client.analyze_document(
            "Субсидирование части затрат на транспортировку продукции АПК",
            "Минсельхоз России. Субсидирование части затрат на транспортировку продукции АПК. Государственная поддержка.",
            source_name="Минсельхоз России - меры господдержки",
            url="https://mcx.gov.ru/activity/state-support/measures/transportirovka-apk/",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.page_type, "measure_card")

    def test_legacy_mcx_credit_subsidy_still_background_after_fix3(self) -> None:
        """Fix 3 non-regression: subsidy-credit-2017 legacy measure must stay background."""
        client = MockLLMClient(["субсидии сельское хозяйство", "государственная поддержка АПК"])

        result = client.analyze_document(
            "Субсидия на возмещение части процентной ставки по инвестиционным кредитам, взятым до 1 января 2017 года",
            "Минсельхоз России. Мера господдержки АПК: субсидия на возмещение части процентной ставки.",
            source_name="Минсельхоз России - меры господдержки",
            url="https://mcx.gov.ru/activity/state-support/measures/subsidy-credit-2017/",
        )

        self.assertEqual(result.action_level, "background")

    def test_regional_npa_requires_attention_business_signal_not_watchlist_wording(self) -> None:
        """Fix 5: regional_npa with requires_attention must not say 'оставить в наблюдении'."""
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Постановление о внесении изменений в порядок предоставления субсидий сельхозтоваропроизводителям",
            "Постановление Правительства Краснодарского края. О внесении изменений в порядок предоставления субсидий сельскохозяйственным товаропроизводителям. Субсидии на поддержку агропромышленного комплекса.",
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/upload/iblock/abc/subsidy-amendment.pdf",
            level="regional",
            region="krasnodar",
        )

        self.assertEqual(result.action_level, "requires_attention")
        self.assertNotIn("оставить в наблюдении", result.business_signal or "")
        self.assertIn("Региональный НПА", result.business_signal or "")

    def test_regulation_gov_subsidy_decision_no_deadline_becomes_watchlist(self) -> None:
        """Task 1: 'Решение о порядке предоставления субсидии' on regulation.gov.ru
        with no deadline_text must become watchlist, not background."""
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Решение о порядке предоставления субсидии № 22-64470-00598-Р (версия 2)",
            "Решение о порядке предоставления субсидии. Документ регулирует распределение средств.",
            source_name="Regulation.gov.ru",
            url="https://regulation.gov.ru/projects/167907",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.action_level, "watchlist")
        self.assertNotEqual(result.action_level, "background")

    def test_regulation_gov_subsidy_procedure_title_only_becomes_watchlist(self) -> None:
        """Task 1: 'порядок предоставления субсид' in title, no deadline → watchlist."""
        client = MockLLMClient(["субсидии"])

        result = client.analyze_document(
            "Порядок предоставления субсидий на возмещение части затрат на производство",
            "Документ утверждает порядок предоставления субсидий. Текст документа.",
            source_name="Regulation.gov.ru",
            url="https://regulation.gov.ru/projects/167910",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.action_level, "watchlist")
        self.assertNotEqual(result.action_level, "background")

    def test_mcx_measures_subsidii_title_becomes_measure_card(self) -> None:
        """Task 2: mcx.gov.ru /measures/ URL with 'Субсидии' title → measure_card,
        ensuring already-collected docs are reclassified by analyze --force."""
        client = MockLLMClient(["государственная поддержка АПК"])

        result = client.analyze_document(
            "Субсидии производителям сельскохозяйственной техники",
            "Минсельхоз России. Мера господдержки: субсидии производителям сельскохозяйственной техники.",
            source_name="Минсельхоз России - меры господдержки",
            url="https://mcx.gov.ru/activity/state-support/measures/machinery-subsidy/",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.page_type, "measure_card")

    def test_regulation_gov_non_subsidy_title_without_deadline_stays_background(self) -> None:
        """Task 1 non-regression: regulation.gov.ru doc without subsidy procedure title
        and without deadline must still be blocked as background."""
        client = MockLLMClient(["субсидии"])

        result = client.analyze_document(
            "О формах документов, применяемых кредитными организациями при осуществлении кассовых операций",
            "Документ устанавливает формы для кредитных организаций.",
            source_name="Regulation.gov.ru",
            url="https://regulation.gov.ru/projects/167910",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.action_level, "background")

    def test_government_apk_decree_is_requires_attention(self) -> None:
        client = MockLLMClient(["государственная поддержка АПК", "субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Постановление от 15 мая 2026 года №789",
            (
                "Правительство Российской Федерации постановляет: утвердить изменения "
                "в государственную программу развития сельского хозяйства и регулирования "
                "рынков сельскохозяйственной продукции. Субсидии на развитие АПК."
            ),
            source_name="Правительство РФ - документы",
            url="http://government.ru/docs/58789/",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.page_type, "new_rule")
        self.assertEqual(result.action_level, "requires_attention")

    def test_government_export_decree_is_requires_attention(self) -> None:
        # government.ru titles are always descriptive — "сельскохозяйственной" in title
        # triggers has_watch_in_title, which is the correct escalation gate.
        client = MockLLMClient(["государственная поддержка АПК"])

        result = client.analyze_document(
            "Правительство ввело временное ограничение вывоза сельскохозяйственной продукции Распоряжение от 12 мая 2026 года №1200-р",
            (
                "Правительство Российской Федерации распоряжается: ввести временное "
                "ограничение вывоза сельскохозяйственной продукции — пшеницы и ячменя. "
                "Распоряжение от 12 мая 2026 вступает в силу с 1 июня."
            ),
            source_name="Правительство РФ - документы",
            url="http://government.ru/docs/58790/",
            level="federal",
            region="federal",
        )

        self.assertEqual(result.page_type, "new_rule")
        self.assertEqual(result.action_level, "requires_attention")

    def test_government_non_agro_decree_stays_watchlist(self) -> None:
        client = MockLLMClient([])

        result = client.analyze_document(
            "Постановление от 15 мая 2026 года №790",
            (
                "Правительство Российской Федерации постановляет: утвердить правила "
                "присвоения квалификационных категорий гидам-проводникам туристских "
                "маршрутов. Настоящее постановление вступает в силу с момента "
                "официального опубликования."
            ),
            source_name="Правительство РФ - документы",
            url="http://government.ru/docs/58791/",
            level="federal",
            region="federal",
        )

        self.assertNotEqual(result.action_level, "requires_attention")

    def test_government_ceremonial_decree_is_not_requires_attention(self) -> None:
        client = MockLLMClient([])

        result = client.analyze_document(
            "Распоряжение от 9 мая 2026 года №1100-р",
            (
                "Правительство Российской Федерации распоряжается: наградить "
                "сотрудников за вклад в развитие государственного управления. "
                "Список награждённых прилагается."
            ),
            source_name="Правительство РФ - документы",
            url="http://government.ru/docs/58792/",
            level="federal",
            region="federal",
        )

        self.assertNotEqual(result.action_level, "requires_attention")

    def test_government_news_fisheries_decree_is_not_requires_attention(self) -> None:
        # Regression: investment quota decree for fishing companies escalated to RA because
        # "рыбопереработка" matched WATCHLIST_MARKERS substring "переработ" in body text.
        # government.ru pages also embed ministry attribution ("Министерство сельского
        # хозяйства") which bypasses the AHSTEP domain gate. The body-level watchlist
        # fallback must not be the escalation gate for government.ru strategy sources.
        client = MockLLMClient([])

        result = client.analyze_document(
            (
                "Правительство увеличило размер инвестиционной квоты для рыбопромысловых "
                "компаний на Дальнем Востоке Постановление от 12 мая 2026 года №549"
            ),
            (
                "Правительство России. Рыболовство, аквакультура, рыбопереработка. "
                "Постановление от 12 мая 2026 года №549. "
                "В целях стимулирования строительства судов для Дальневосточного "
                "рыбохозяйственного бассейна увеличен размер инвестиционной квоты "
                "для рыбопромысловых компаний. "
                "Министерства и ведомства: Министерство сельского хозяйства Российской Федерации."
            ),
            source_name="Правительство РФ - новости",
            url="http://government.ru/news/58725/",
            level="federal",
            region="federal",
        )

        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertIn(result.action_level, {"background", "watchlist"})

    def test_expired_selection_no_longer_requires_attention(self) -> None:
        # Same Stavropol selection text as the live-deadline case above, but the
        # window already expired. It must drop out of requires_attention.
        client = MockLLMClient(["субсидии сельское хозяйство"])

        result = client.analyze_document(
            "Объявление об отборе на возмещение части затрат, связанных с посадкой ягодных культур",
            (
                "Объявление об отборе на возмещение части затрат, связанных с посадкой ягодных культур. "
                "Прием заявок открыт до 01.01.2020."
            ),
            source_name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/gospodderzhka/selection-berry-expired.php",
            level="regional",
            region="stavropol",
        )

        self.assertEqual(result.application_status, "closed")
        self.assertNotEqual(result.action_level, "requires_attention")

    def test_regulation_discussion_future_deadline_still_classified(self) -> None:
        # A regulation.gov.ru draft with a future discussion deadline must
        # remain visible on the strategy track. Expiry guards must not demote
        # alive drafts.
        client = MockLLMClient(["субсидии сельское хозяйство"])

        from datetime import datetime, timedelta, timezone

        future_deadline = datetime.now(timezone.utc) + timedelta(days=14)
        raw_text = self._regulation_public_discussion_text(future_deadline, agro=True)

        result = client.analyze_document(
            "Проект НПА: Об утверждении порядка предоставления субсидий",
            raw_text,
            source_name="Regulation.gov - проекты НПА",
            url="https://regulation.gov.ru/projects/View/156432",
            level="federal",
            region="federal",
        )

        self.assertIn(result.action_level, {"watchlist", "requires_attention"})
        self.assertNotEqual(result.application_status, "closed")


if __name__ == "__main__":
    unittest.main()
