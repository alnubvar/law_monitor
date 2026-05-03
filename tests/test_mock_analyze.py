from __future__ import annotations

import unittest

from app.llm.mock_client import MockLLMClient


class MockAnalyzeSmokeTest(unittest.TestCase):
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
        self.assertNotEqual(result.action_level, "requires_attention")
        self.assertEqual(result.business_signal, "Неактивная мера поддержки: оставить в справочном блоке")

    def test_gisp_active_regular_measure_card_becomes_requires_attention(self) -> None:
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
            "Прием заявок открыт до 15 мая 2026 года. НПА 338а.",
            source_name="Минсельхоз Ставропольского края - господдержка",
            url="https://mshsk.ru/subsidy-open/",
            level="support_measures",
        )

        self.assertEqual(result.application_status, "open")
        self.assertIsNotNone(result.deadline_text)
        self.assertIn("до 15 мая 2026 года", result.deadline_text or "")
        self.assertEqual(result.npa_number, "НПА 338а")
        self.assertIsNone(result.terms_text)

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


if __name__ == "__main__":
    unittest.main()
