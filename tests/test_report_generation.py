from __future__ import annotations

import unittest
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from unittest.mock import patch

from app.models import ActionLevel, PageType, RawDocument
from app.pipeline.diagnostics import format_requires_attention_visibility_diagnostics
from app.reports.markdown_report import (
    _sanitize_report_display_text,
    build_report_view,
    classify_document_bucket,
    generate_markdown_report,
)
from app.reports.docx_report import create_docx_from_markdown
from docx import Document as DocxDocument
from app.storage import init_db, save_document_enrichment
from app.llm.enrichment import DocumentCardFacts, EnrichmentResult
from app.text_utils import safe_truncate_text
from app.visibility import effective_user_action_level, should_show_document


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

    def test_non_target_regional_zol_news_stays_hidden_even_with_full_background(self) -> None:
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
        self.assertEqual(len(limited_view.shown_buckets["non_target_background"]), 0)
        self.assertEqual(len(full_view.shown_buckets["non_target_background"]), 0)

    def test_max_items_keeps_all_requires_attention_before_watchlist(self) -> None:
        urgent = [
            self._doc(
                doc_id=10 + index,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title=f"Пошлины на экспорт зерна изменены {index}",
                url=f"https://www.zol.ru/n/urgent-{index}",
                action_level="requires_attention",
                page_type="news_background",
                summary="Федеральный сигнал по экспортным пошлинам на зерно.",
            )
            for index in range(3)
        ]
        watchlist = [
            self._doc(
                doc_id=20 + index,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title=f"Отраслевой фон по зерновому рынку {index}",
                url=f"https://www.zol.ru/n/watch-{index}",
                action_level="watchlist",
                page_type="news_background",
                summary="Федеральный фон по АПК.",
            )
            for index in range(5)
        ]

        report_view = build_report_view(
            [*urgent, *watchlist],
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            max_items=2,
        )

        self.assertEqual(len(report_view.shown_buckets["requires_attention"]), 3)
        self.assertEqual(report_view.total_visible, 3)
        self.assertEqual(report_view.hidden_due_to_max_items_count, 5)
        self.assertEqual(report_view.hidden_watchlist_due_to_max_items_count, 5)

    def test_watchlist_cap_never_hides_requires_attention_overflow(self) -> None:
        urgent = [
            self._doc(
                doc_id=30 + index,
                source_name="Нормативные акты Краснодарского края",
                region="krasnodar",
                title=f"О внесении изменений в порядок предоставления субсидий в АПК {index}",
                url=f"https://admkrai.krasnodar.ru/upload/iblock/urgent-overflow-{index}.pdf",
                action_level="requires_attention",
                page_type="new_rule",
                summary="Изменены условия предоставления субсидии в АПК.",
            )
            for index in range(7)
        ]
        watchlist = [
            self._doc(
                doc_id=40 + index,
                source_name="Нормативные акты Краснодарского края",
                region="krasnodar",
                title=f"Профильный НПА Краснодарского края на наблюдении {index}",
                url=f"https://admkrai.krasnodar.ru/upload/iblock/watch-overflow-{index}.pdf",
                action_level="watchlist",
                page_type="new_rule",
                summary="Региональный НПА по профильной теме.",
            )
            for index in range(4)
        ]

        db_path = self._db_path("watchlist_cap_keeps_urgent.db")
        init_db(db_path)
        markdown = generate_markdown_report(
            [*urgent, *watchlist],
            report_date="2026-05-25",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            max_items=5,
            db_path=db_path,
        )

        for document in urgent:
            self.assertIn(document.url, markdown)
        shown_watchlist = sum(1 for document in watchlist if document.url in markdown)
        self.assertEqual(shown_watchlist, 0)

    def test_watchlist_cap_still_works_without_requires_attention(self) -> None:
        watchlist = [
            self._doc(
                doc_id=45 + index,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title=f"Отраслевой фон по экспорту зерна {index}",
                url=f"https://www.zol.ru/n/watch-only-{index}",
                action_level="watchlist",
                page_type="news_background",
                summary="Федеральный отраслевой фон по экспорту зерна.",
            )
            for index in range(5)
        ]

        markdown = generate_markdown_report(
            watchlist,
            report_date="2026-05-25",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            max_items=3,
        )

        rendered_item_count = sum(1 for line in markdown.splitlines() if line.startswith("### "))
        self.assertEqual(rendered_item_count, 3)
        self.assertIn("- Включено в сводку: 3", markdown)
        self.assertEqual(sum(1 for document in watchlist if document.url in markdown), 3)

    def test_report_logs_hidden_watchlist_candidates_when_max_items_caps_them(self) -> None:
        documents = [
            self._doc(
                doc_id=47 + index,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title=f"Отраслевой фон по экспортным рынкам {index}",
                url=f"https://www.zol.ru/n/log-hidden-watch-{index}",
                action_level="watchlist",
                page_type="news_background",
                summary="Федеральный отраслевой фон по экспорту зерна.",
            )
            for index in range(4)
        ]

        with self.assertLogs("app.reports.markdown_report", level="INFO") as logs:
            report_view = build_report_view(
                documents,
                relevant_only=True,
                action_levels=["requires_attention", "watchlist"],
                max_items=2,
            )

        self.assertEqual(report_view.hidden_watchlist_due_to_max_items_count, 2)
        self.assertIn("hid 2 watchlist candidate", "\n".join(logs.output))

    def test_included_count_matches_rendered_visible_items(self) -> None:
        documents = [
            self._doc(
                doc_id=50,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Пошлины на экспорт пшеницы из РФ останутся нулевыми",
                url="https://www.zol.ru/n/included-urgent",
                action_level="requires_attention",
                page_type="news_background",
                summary="Федеральный сигнал по экспортным пошлинам на зерно.",
            ),
            self._doc(
                doc_id=51,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Отраслевой фон по логистике экспорта зерна",
                url="https://www.zol.ru/n/included-watch",
                action_level="watchlist",
                page_type="news_background",
                summary="Федеральный отраслевой фон по экспорту зерна и логистике.",
            ),
            self._doc(
                doc_id=52,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Фоновая справка по рынку зерна",
                url="https://www.zol.ru/n/included-background",
                action_level="background",
                page_type="news_background",
                summary="Фоновая справка без действия.",
            ),
            self._doc(
                doc_id=53,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Нерелевантная отрасль без связи с АПК",
                url="https://www.zol.ru/n/included-irrelevant",
                action_level="irrelevant",
                page_type="news_background",
                summary="Нерелевантный фон.",
            ),
        ]

        db_path = self._db_path("included_count_matches.db")
        init_db(db_path)
        markdown = generate_markdown_report(
            documents,
            report_date="2026-05-25",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        rendered_item_count = sum(1 for line in markdown.splitlines() if line.startswith("### "))
        self.assertEqual(rendered_item_count, 2)
        self.assertIn("- Включено в сводку: 2", markdown)
        self.assertNotIn("https://www.zol.ru/n/included-background", markdown)
        self.assertNotIn("https://www.zol.ru/n/included-irrelevant", markdown)

    def test_requires_attention_visibility_diagnostics_explain_suppressed_items(self) -> None:
        visible = self._doc(
            doc_id=54,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Пошлины на экспорт пшеницы из РФ останутся нулевыми",
            url="https://www.zol.ru/n/diagnostic-visible-urgent",
            action_level="requires_attention",
            page_type="news_background",
            summary="Федеральный сигнал по экспортным пошлинам на зерно.",
        )
        suppressed = self._doc(
            doc_id=55,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="В Томской области изменили порядок субсидий АПК",
            url="https://www.zol.ru/n/diagnostic-suppressed-urgent",
            action_level="requires_attention",
            page_type="news_background",
            summary="Регион обновил порядок предоставления субсидий.",
        )

        output = format_requires_attention_visibility_diagnostics([visible, suppressed])

        self.assertIn("Requires_attention visibility audit:", output)
        self.assertIn("- raw_requires_attention: 2", output)
        self.assertIn("- rendered_requires_attention: 1", output)
        self.assertIn("- suppressed_requires_attention: 1", output)
        self.assertIn("requires_attention_before_watchlist_cap: ok", output)
        self.assertIn("included_count_check: ok", output)
        self.assertIn("diagnostic-suppressed-urgent", output)
        self.assertIn("visibility_bucket=non_target_background", output)

    def test_argentina_market_news_uses_external_market_applicability(self) -> None:
        db_path = self._db_path("argentina_market_context.db")
        init_db(db_path)
        document = self._doc(
            doc_id=52,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Аргентина снижает экспортную пошлину на пшеницу",
            url="https://www.zol.ru/n/argentina-duty-test",
            action_level="watchlist",
            page_type="news_background",
            summary="Аргентина снижает экспортную пошлину на пшеницу, что может повлиять на глобальные цены зерна.",
        )
        document.raw_text = (
            "Аргентина объявила о снижении экспортной пошлины на пшеницу. "
            "Это может усилить конкуренцию на экспортных рынках зерна."
        )
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult.from_facts(
                DocumentCardFacts(
                    document_type="новость",
                    region="РФ",
                    status="неизвестно",
                    short_summary="Аргентина снижает экспортную пошлину на пшеницу.",
                    why_matters="Может повлиять на мировые цены и конкуренцию на рынке пшеницы.",
                    what_to_check="Оценить влияние на экспортные цены.",
                    applicability_note="Косвенный рыночный контекст для экспортного направления AHSTEP.",
                    confidence="high",
                )
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-25",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertIn("Аргентина снижает экспортную пошлину на пшеницу", markdown)
        self.assertIn("География: Аргентина / мировой рынок", markdown)
        self.assertIn("Косвенный рыночный контекст", markdown)
        self.assertNotIn("Регион: РФ", markdown)

    def test_grain_forum_livestream_announcement_is_hidden_without_outcome(self) -> None:
        announcement = self._doc(
            doc_id=53,
            source_name="Минсельхоз России - новости",
            region="federal",
            title="Прямая трансляция пленарной сессии Всероссийского зернового форума",
            url="https://mcx.gov.ru/press-service/news/grain-forum-livestream-test/",
            action_level="watchlist",
            page_type="news_background",
            summary="Состоится прямая трансляция сессии по цепочкам поставок зерна и экспорту.",
        )
        announcement.raw_text = (
            "Прямая трансляция пленарной сессии Всероссийского зернового форума. "
            "Участники обсудят прогнозы сезона, экспорт зерна и логистику."
        )

        markdown = generate_markdown_report(
            [announcement],
            report_date="2026-05-25",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertNotIn("Прямая трансляция пленарной сессии", markdown)
        self.assertIn("Новых отраслевых сигналов не найдено.", markdown)

    def test_grain_forum_policy_outcome_remains_visible(self) -> None:
        outcome = self._doc(
            doc_id=54,
            source_name="Минсельхоз России - новости",
            region="federal",
            title="На зерновом форуме объявили новые правила экспортной логистики",
            url="https://mcx.gov.ru/press-service/news/grain-forum-outcome-test/",
            action_level="watchlist",
            page_type="news_background",
            summary="Минсельхоз объявил новые правила экспортной логистики зерна.",
        )
        outcome.raw_text = (
            "На зерновом форуме Минсельхоз объявил новые правила экспортной "
            "логистики зерна и порядок взаимодействия участников рынка."
        )

        markdown = generate_markdown_report(
            [outcome],
            report_date="2026-05-25",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("На зерновом форуме объявили новые правила экспортной логистики", markdown)

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
        self.assertEqual(
            classify_document_bucket(non_target_document), "non_target_background"
        )

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

    def test_docx_export_keeps_markdown_content_readable(self) -> None:
        output_path = self._db_path("report_export.docx")
        markdown = (
            "# Главный отчет\n\n"
            "## Раздел\n\n"
            "### Подраздел\n\n"
            "- Пункт для GR\n"
            "Источник: [пример](https://example.com/report)\n"
        )

        created = create_docx_from_markdown(markdown, output_path)

        self.assertTrue(created.exists())
        text = "\n".join(paragraph.text for paragraph in DocxDocument(created).paragraphs)
        self.assertIn("Главный отчет", text)
        self.assertIn("Раздел", text)
        self.assertIn("Подраздел", text)
        self.assertIn("Пункт для GR", text)
        self.assertIn("https://example.com/report", text)

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
                executive_summary="Краткая сводка для руководителя.",
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

        self.assertIn("Краткая сводка для руководителя.", markdown)
        self.assertIn(
            "Новая редакция меры меняет условия участия для заемщиков АПК.", markdown
        )
        self.assertIn(
            "Проверить применимость меры, окно подачи, критерии получателей и ответственного.",
            markdown,
        )
        # Truth-aware renderer reformats raw deadline text to executive wording.
        self.assertIn("Срок: до 30.06.2026", markdown)

    def test_report_uses_document_card_facts_when_available(self) -> None:
        db_path = self._db_path("report_document_card_facts.db")
        init_db(db_path)
        document = self._doc(
            doc_id=507,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Отбор на субсидию АПК",
            url="https://gisp.gov.ru/nmp/measure/card-facts",
            action_level="requires_attention",
            page_type="selection_announcement",
            summary="Базовая summary.",
        )
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult.from_facts(
                DocumentCardFacts(
                    document_type="отбор",
                    region="РФ",
                    authority="Минсельхоз России",
                    status="прием открыт",
                    deadline="2026-06-30",
                    support_type="субсидия",
                    target_recipients=["сельхозтоваропроизводители"],
                    what_changed="Открыт отбор на предоставление субсидии.",
                    why_matters="GR нужно проверить, подходит ли мера под контур AHSTEP.",
                    what_to_check="Проверить критерии получателя и срок подачи заявки.",
                    applicability_note="Применимость требует проверки критериев получателя.",
                    short_summary="Открыт отбор на субсидию для АПК. Нужно проверить условия участия.",
                    confidence="high",
                    source_quotes=["Открыт отбор", "сельхозтоваропроизводители"],
                )
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-19",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertIn("Суть документа:", markdown)
        self.assertIn("Открыт отбор на предоставление субсидии.", markdown)
        self.assertIn("Открыт отбор на субсидию для АПК. Нужно проверить условия участия.", markdown)
        self.assertIn("Почему важно: GR нужно проверить, подходит ли мера под контур AHSTEP.", markdown)
        self.assertIn("Кому может быть применимо:", markdown)
        self.assertIn("Регион: РФ", markdown)
        self.assertIn("Получатели: сельхозтоваропроизводители", markdown)
        self.assertIn("Тип поддержки: субсидия", markdown)
        self.assertIn("Что проверить: Проверить критерии получателя и срок подачи заявки.", markdown)
        self.assertIn("Сроки / даты: Статус: прием открыт; Срок подачи заявок: до 30.06.2026", markdown)

    def test_promote_budget_weak_fallback_card_uses_deterministic_facts(self) -> None:
        db_path = self._db_path("report_promote_budget_cleanup.db")
        init_db(db_path)
        document = self._doc(
            doc_id=508,
            source_name="promote.budget.gov.ru / Минфин - отборы и меры поддержки",
            region="krasnodar",
            title="Открыт прием заявок на возмещение части затрат на элитные семена АПК",
            url="https://promote.budget.gov.ru/public/minfin/selection/view/cleanup",
            action_level="requires_attention",
            page_type="selection_announcement",
            summary="Открыт прием заявок для сельхозтоваропроизводителей АПК.",
        )
        document.application_status = "open"
        document.deadline_text = "Прием заявок до 30.06.2099."
        document.raw_text = "Отбор на субсидии для сельхозтоваропроизводителей АПК."
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult.from_facts(
                DocumentCardFacts(
                    document_type="отбор",
                    region="krasnodar",
                    status="прием открыт",
                    deadline="2099-06-30",
                    support_type="субсидия",
                    target_recipients=["сельхозтоваропроизводители"],
                    what_changed=(
                        "По документу видно окно поддержки или отбора. Нужно уточнить "
                        "условия участия, круг получателей и рабочие сроки."
                    ),
                    why_matters=(
                        "Документ помогает понять, применима ли мера к контуру AHSTEP, "
                        "и нужна ли проверка eligibility."
                    ),
                    what_to_check=(
                        "Проверить критерии получателя; окно подачи или текущий статус отбора; "
                        "перечень документов."
                    ),
                    short_summary="Открыт прием заявок.",
                    confidence="high",
                )
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-21",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertNotIn("По документу видно окно поддержки или отбора", markdown)
        self.assertNotIn("Документ помогает понять, применима ли мера", markdown)
        self.assertNotIn("eligibility", markdown.lower())
        self.assertIn("элитное семеноводство", markdown)
        self.assertIn("Регион: Краснодарский край", markdown)
        self.assertIn("соответствие критериям", markdown)

    def test_document_card_summary_replaces_raw_city_header_garbage(self) -> None:
        db_path = self._db_path("report_raw_header_summary_cleanup.db")
        init_db(db_path)
        document = self._doc(
            doc_id=509,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="О внесении изменений в порядок предоставления субсидий в АПК",
            url="https://admkrai.krasnodar.ru/upload/iblock/header.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Краснодар Об образовании рабочей группы по документу.",
        )
        document.business_signal = "Изменены условия предоставления субсидий в АПК."
        document.raw_text = "Порядок предоставления субсидий сельхозтоваропроизводителям АПК."
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult.from_facts(
                DocumentCardFacts(
                    document_type="НПА",
                    region="krasnodar",
                    status="принято",
                    what_changed="Краснодар Об образовании рабочей группы по документу.",
                    why_matters="Постановление вносит отдел правового сопровождения.",
                    what_to_check="Проверить приложение и условия поддержки.",
                    short_summary="Ростов-на-Дону Об утверждении состава комиссии.",
                    confidence="high",
                )
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-21",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertIn(
            "Региональный акт затрагивает порядок предоставления поддержки",
            markdown,
        )
        self.assertNotIn("Краснодар Об образовании", markdown)
        self.assertNotIn("Ростов-на-Дону Об утверждении", markdown)
        self.assertNotIn("Постановление вносит отдел", markdown)

    def test_document_card_deduplicates_repeated_sentences(self) -> None:
        db_path = self._db_path("report_sentence_dedup.db")
        init_db(db_path)
        document = self._doc(
            doc_id=510,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Отбор на субсидию АПК",
            url="https://gisp.gov.ru/nmp/measure/dedup",
            action_level="requires_attention",
            page_type="selection_announcement",
            summary="Базовая summary.",
        )
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult.from_facts(
                DocumentCardFacts(
                    document_type="отбор",
                    status="прием открыт",
                    what_changed="Открыт отбор на субсидию. Открыт отбор на субсидию.",
                    why_matters="Нужно проверить условия участия. Нужно проверить условия участия.",
                    what_to_check="Проверить критерии получателя.",
                    short_summary="Открыт отбор на субсидию.",
                    confidence="high",
                )
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-21",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertNotIn(
            "Открыт отбор на субсидию. Открыт отбор на субсидию.",
            markdown,
        )
        self.assertNotIn(
            "Нужно проверить условия участия. Нужно проверить условия участия.",
            markdown,
        )

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
                executive_summary="AI-сводка: Краткая сводка для руководителя.",
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

        self.assertIn("Краткая сводка для руководителя.", markdown)
        self.assertNotIn("AI-сводка:", markdown)
        self.assertNotIn("AI-оценка влияния:", markdown)
        self.assertNotIn("AI-рекомендация:", markdown)

    def test_report_deadline_hint_strips_extraction_garbage(self) -> None:
        db_path = self._db_path("report_deadline_hint_garbage.db")
        init_db(db_path)
        document = self._doc(
            doc_id=506,
            source_name="Regulation.gov.ru - проекты НПА",
            region="federal",
            title="Об утверждении требований к видам племенных хозяйств",
            url="https://regulation.gov.ru/projects/167863",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Проект НПА на публичном обсуждении.",
        )
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult(
                executive_summary="Проект НПА на публичном обсуждении.",
                business_impact="Проект НПА на публичном обсуждении со сроком.",
                recommended_action="Проверить проект и подготовить позицию.",
                deadline_hint="Конец обсуждения: 26.05.2026 Проблема: замен",
                confidence=0.8,
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-13",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        # The renderer now emits a complete executive label without a
        # redundant "Срок:" prefix when the label already carries its own
        # ("Конец обсуждения: ...").
        self.assertIn("- Конец обсуждения: 26.05.2026", markdown)
        self.assertNotIn("Проблема:", markdown)
        self.assertNotIn("замен", markdown)

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

        with patch(
            "app.reports.markdown_report.list_document_enrichments", return_value={}
        ):
            markdown = generate_markdown_report(
                [document],
                report_date="2026-05-07",
                relevant_only=True,
                action_levels=["requires_attention", "watchlist"],
            )

        self.assertIn("Почему важно: impact", markdown)
        self.assertIn(
            "Что проверить: Проверить применимость меры, окно подачи, критерии получателей и ответственного.",
            markdown,
        )

    def test_report_without_enrichment_suppresses_raw_synthetic_summary(self) -> None:
        document = self._doc(
            doc_id=509,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Мера поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/raw-summary",
            action_level="requires_attention",
            page_type="measure_card",
            summary=(
                "title: Мера поддержки АПК shortName: Поддержка "
                "endDate: 2026-06-30 acceptingApplicationsInfo: прием идет"
            ),
        )

        with patch(
            "app.reports.markdown_report.list_document_enrichments", return_value={}
        ):
            markdown = generate_markdown_report(
                [document],
                report_date="2026-05-19",
                relevant_only=True,
                action_levels=["requires_attention", "watchlist"],
            )

        self.assertIn(
            "Кратко: Мера поддержки требует проверки применимости, условий участия и возможных сроков.",
            markdown,
        )
        self.assertNotIn("title:", markdown)
        self.assertNotIn("shortName:", markdown)
        self.assertNotIn("endDate:", markdown)
        self.assertNotIn("acceptingApplicationsInfo:", markdown)

    def test_failed_enrichment_row_does_not_leak_raw_fallback_summary(self) -> None:
        db_path = self._db_path("report_failed_enrichment_raw_fallback.db")
        init_db(db_path)
        document = self._doc(
            doc_id=510,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Мера поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/failed-raw",
            action_level="requires_attention",
            page_type="measure_card",
            summary=(
                "title: Мера поддержки АПК shortName: Поддержка "
                "endDate: 2026-06-30 acceptingApplicationsInfo: прием идет"
            ),
        )
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult.failed("invalid JSON"),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-19",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertIn(
            "Кратко: Мера поддержки требует проверки применимости, условий участия и возможных сроков.",
            markdown,
        )
        self.assertNotIn("invalid JSON", markdown)
        self.assertNotIn("title:", markdown)
        self.assertNotIn("shortName:", markdown)

    def test_deleted_enrichment_rows_restore_clean_deterministic_fallback(self) -> None:
        db_path = self._db_path("report_deleted_enrichment_clean_fallback.db")
        init_db(db_path)
        document = self._doc(
            doc_id=511,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Мера поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/deleted-raw",
            action_level="requires_attention",
            page_type="measure_card",
            summary=(
                "title: Мера поддержки АПК shortName: Поддержка "
                "endDate: 2026-06-30 acceptingApplicationsInfo: прием идет"
            ),
        )
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult.from_facts(
                DocumentCardFacts(
                    short_summary="Полезное краткое пояснение по мере поддержки.",
                    why_matters="GR нужно проверить возможность участия.",
                    what_to_check="Проверить критерии получателя.",
                    confidence="high",
                    source_quotes=["Мера поддержки"],
                )
            ),
            db_path=db_path,
        )
        with closing(sqlite3.connect(str(db_path))) as connection:
            connection.execute("DELETE FROM document_enrichments")
            connection.commit()

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-19",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertNotIn("Полезное краткое пояснение", markdown)
        self.assertIn(
            "Кратко: Мера поддержки требует проверки применимости, условий участия и возможных сроков.",
            markdown,
        )
        self.assertNotIn("title:", markdown)
        self.assertNotIn("shortName:", markdown)

    def test_llm_disabled_report_remains_clean_without_enrichment(self) -> None:
        document = self._doc(
            doc_id=512,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Мера поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/disabled-raw",
            action_level="requires_attention",
            page_type="measure_card",
            summary="title: Мера поддержки shortName: Поддержка",
        )

        with patch("app.config.LLM_DOCUMENT_ENRICHMENT_ENABLED", False):
            markdown = generate_markdown_report(
                [document],
                report_date="2026-05-19",
                relevant_only=True,
                action_levels=["requires_attention", "watchlist"],
            )

        self.assertIn("Кратко: Мера поддержки требует проверки", markdown)
        self.assertNotIn("title:", markdown)
        self.assertNotIn("shortName:", markdown)

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

    def test_invalid_document_card_json_does_not_break_report(self) -> None:
        db_path = self._db_path("report_invalid_facts_json.db")
        init_db(db_path)
        document = self._doc(
            doc_id=508,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/invalid-json",
            action_level="requires_attention",
            page_type="measure_card",
            summary="Базовая summary.",
        )
        with closing(sqlite3.connect(str(db_path))) as connection:
            connection.execute(
                """
                INSERT INTO document_enrichments(
                    document_id, document_url, provider, model, prompt_version, status,
                    facts_json, executive_summary, business_impact, recommended_action,
                    confidence, created_at, updated_at
                ) VALUES (?, ?, 'mock', 'mock-enrichment', 'gr_document_card_v1', 'success',
                          '{not-json', 'Legacy summary', 'Legacy impact', 'Legacy action',
                          0.9, '2026-05-19T00:00:00+00:00', '2026-05-19T00:00:00+00:00')
                """,
                (document.id, document.url),
            )
            connection.commit()

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-19",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertIn("Legacy summary", markdown)
        self.assertIn("Legacy impact", markdown)
        self.assertIn(
            "Проверить применимость меры, окно подачи, критерии получателей и ответственного.",
            markdown,
        )

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

        self.assertNotIn("...", markdown)
        self.assertNotIn(long_text.strip(), markdown)

    def test_generic_mock_enrichment_does_not_override_specific_reason_and_action(
        self,
    ) -> None:
        db_path = self._db_path("report_enrichment_generic_preference.db")
        init_db(db_path)
        document = self._doc(
            doc_id=904,
            source_name="Право Ставропольского края",
            region="stavropol",
            title="О внесении изменений в порядок предоставления субсидий сельхозтоваропроизводителям",
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

        self.assertIn(
            "Кратко: Изменён порядок предоставления субсидий в Ставропольском крае.",
            markdown,
        )
        # Title compresses to a region-aware headline now ("Изменены субсидии в
        # Ставропольском крае"); the legacy generic "Изменены условия
        # субсидирования" form is retained only as the reason wording.
        self.assertIn("### Изменены субсидии в Ставропольском крае", markdown)
        self.assertIn("Почему важно: Изменены условия субсидирования", markdown)
        self.assertIn(
            "Проверить изменения условий субсидирования и критерии отбора.", markdown
        )
        self.assertNotIn("Сигнал может повлиять на контекст господдержки", markdown)
        self.assertNotIn("Оценить срочность сигнала", markdown)

    def test_urgent_regional_npa_report_uses_stronger_reason_and_specific_hint(
        self,
    ) -> None:
        document = self._doc(
            doc_id=150,
            source_name="Право Ставропольского края",
            region="stavropol",
            title="О внесении изменений в порядок предоставления субсидий сельхозтоваропроизводителям",
            url="https://pravo.stavregion.ru/document/98765",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Изменения порядка предоставления субсидий.",
        )
        document.business_signal = (
            "Региональный НПА по профильной теме: оставить в наблюдении."
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-07",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("### Изменены субсидии в Ставропольском крае", markdown)
        self.assertIn("Почему важно: Изменены условия субсидирования", markdown)
        self.assertIn(
            "Проверить изменения условий субсидирования и критерии отбора.", markdown
        )
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
        document.business_signal = (
            "Есть признаки изменения экспортных условий для российского рынка."
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-07",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn(
            "Проверить влияние на экспортные контракты и логистику.",
            markdown,
        )
        self.assertNotIn("Оставить как отраслевой фон.", markdown)
        # The signal must be a trade reason, not a generic support change.
        self.assertNotIn("Изменены условия поддержки", markdown)

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

        self.assertIn(
            "Почему важно: Обновлены условия льготного кредитования", markdown
        )
        self.assertIn(
            "Что проверить: Проверить условия кредитования и применимость для АПК.",
            markdown,
        )
        self.assertNotIn(
            "Проверить влияние на экспортные контракты и логистику.",
            markdown,
        )

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
            title="Субсидии на возмещение части затрат за реализованные объемы куриных пищевых яиц",
            url="http://mshsk.ru/gospodderzhka/subsidies-for-reimbursement-egg.php",
            action_level="watchlist",
            page_type="measure_card",
            summary="Мера поддержки АПК без срочного действия.",
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

    def test_report_summary_includes_source_heat_line_for_visible_documents_only(
        self,
    ) -> None:
        visible_one = self._doc(
            doc_id=801,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="Изменения по субсидиям сельхозтоваропроизводителям",
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
        # Region-aware title compression now makes the two subsidy headlines
        # distinguishable ("Изменены субсидии для АПК в Ростовской области" vs
        # the Краснодарский крае form), so they no longer dedupe to a single
        # legacy "Изменены условия субсидирования" entry. Still no duplicate.
        self.assertIn("Изменены субсидии для АПК в Ростовской области", markdown)
        self.assertIn("Льготное кредитование АПК", markdown)
        self.assertNotIn(
            "Изменены условия субсидирования; Изменены условия субсидирования",
            markdown,
        )

    def test_regulation_gov_watchlist_document_is_visible_in_report(self) -> None:
        document = self._doc(
            doc_id=940,
            source_name="Regulation.gov.ru - проекты НПА",
            region="federal",
            title="Об утверждении Порядка предоставления субсидий сельхозтоваропроизводителям",
            url="https://regulation.gov.ru/projects/12345",
            action_level="watchlist",
            page_type="reference_page",
            summary="Проект нормативного акта о порядке предоставления субсидий.",
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-14",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("Утверждены условия субсидирования", markdown)
        self.assertIn("regulation.gov.ru", markdown)

    def test_krasnodar_support_order_duplicate_documents_are_collapsed_in_report(
        self,
    ) -> None:
        document_one = self._doc(
            doc_id=941,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title=(
                "Об утверждении Порядка предоставления субсидий на реализацию проектов "
                "мелиорации» В соответствии со статьей 78 Бюджетного кодекса Российской "
                "Федерации, постановлениями Правительства Российской Федерации"
            ),
            url="https://admkrai.krasnodar.ru/upload/iblock/d2d/or335aa8v3axt3qcujus7xh2qgwz3zwm.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Изменения порядка предоставления субсидий на проекты мелиорации.",
        )
        document_two = self._doc(
            doc_id=942,
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            region="krasnodar",
            title=(
                '№ 183 от 13.05.2026 "О внесении изменения в приказ министерства '
                "сельского хозяйства и перерабатывающей промышленности Краснодарского "
                "края от 19 марта 2018 г. № 70 «Об утверждении Порядка предоставления "
                "субсидий на реализацию проектов мелиорации»"
            ),
            url="https://npa.krasnodar.ru/rest/files/1233833",
            action_level="watchlist",
            page_type="new_rule",
            summary="Изменения порядка предоставления субсидий на проекты мелиорации.",
        )

        markdown = generate_markdown_report(
            [document_one, document_two],
            report_date="2026-05-14",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertEqual(
            markdown.count("### Субсидии на мелиорацию в Краснодарском крае"), 1
        )
        self.assertIn("admkrai.krasnodar.ru", markdown)
        self.assertNotIn("npa.krasnodar.ru/rest/files/1233833", markdown)

    def test_main_focus_excludes_weak_ocr_placeholder_and_keeps_meaningful_items(
        self,
    ) -> None:
        strong_regional = self._doc(
            doc_id=913,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="О внесении изменений в порядок предоставления субсидий сельхозтоваропроизводителям",
            url="https://admkrai.krasnodar.ru/upload/iblock/f90/a.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Документ содержит изменения в порядке предоставления поддержки; требуется проверка условий и сроков.",
        )
        weak_ocr = self._doc(
            doc_id=914,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="document 'weak-ocr.pdf' requires OCR extraction",
            url="https://admkrai.krasnodar.ru/upload/iblock/d69/weak-ocr.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="OCR placeholder документ.",
        )
        weak_ocr.raw_text = "Распознанный текст отсутствует."
        credit_news = self._doc(
            doc_id=915,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Минсельхоз предложил новые условия льготного кредитования АПК",
            url="https://www.zol.ru/n/41378",
            action_level="requires_attention",
            page_type="news_background",
            summary="Документ содержит изменения в порядке предоставления поддержки; требуется проверка условий и сроков.",
        )

        markdown = generate_markdown_report(
            [strong_regional, weak_ocr, credit_news],
            report_date="2026-05-08",
            period_days=7,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn(
            "Главный акцент: Изменены субсидии в Краснодарском крае; Минсельхоз предложил новые условия льготного кредитования АПК",
            markdown,
        )
        self.assertNotIn(
            "Главный акцент: НПА Краснодарского края: документ после OCR", markdown
        )
        self.assertNotIn("документ после OCR; Минсельхоз", markdown)

    def test_report_hides_non_agro_sport_subsidy_but_keeps_agriculture_subsidy(
        self,
    ) -> None:
        sport_subsidy = self._doc(
            doc_id=916,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="Постановление об утверждении порядка предоставления субсидий организациям физической культуры и спорта",
            url="https://admkrai.krasnodar.ru/upload/iblock/sport-subsidy.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Утверждены условия субсидирования физической культуры и спорта.",
        )
        sport_subsidy.raw_text = (
            "Утвержден порядок предоставления субсидий организациям физической культуры "
            "и спорта Краснодарского края."
        )
        agriculture_subsidy = self._doc(
            doc_id=917,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="Постановление об утверждении порядка предоставления субсидий сельхозтоваропроизводителям",
            url="https://admkrai.krasnodar.ru/upload/iblock/agro-subsidy.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Утверждены условия субсидирования сельхозтоваропроизводителей.",
        )
        agriculture_subsidy.raw_text = (
            "Утвержден порядок предоставления субсидий сельхозтоваропроизводителям "
            "Краснодарского края."
        )

        markdown = generate_markdown_report(
            [sport_subsidy, agriculture_subsidy],
            report_date="2026-05-08",
            period_days=7,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("agro-subsidy.pdf", markdown)
        self.assertIn("Главный акцент: Субсидии", markdown)
        self.assertNotIn("sport-subsidy.pdf", markdown)
        self.assertNotIn("физической культуры", markdown)
        self.assertNotIn("спорта Краснодарского края", markdown)

    def test_main_focus_falls_back_to_no_urgent_when_only_weak_placeholder_exists(
        self,
    ) -> None:
        weak_ocr = self._doc(
            doc_id=918,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="НПА Краснодарского края: документ после OCR",
            url="https://admkrai.krasnodar.ru/upload/iblock/d69/fallback-ocr.pdf",
            action_level="requires_attention",
            page_type="new_rule",
            summary="OCR placeholder документ.",
        )
        weak_ocr.raw_text = (
            "Документ после OCR требует ручной проверки. Распознанный текст частично отсутствует, "
            "структура фрагментарна и не позволяет уверенно выделить условия меры."
        )

        markdown = generate_markdown_report(
            [weak_ocr],
            report_date="2026-05-08",
            period_days=7,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn(
            "Главный акцент: Срочных поводов для GR-реакции не выявлено.", markdown
        )
        self.assertNotIn(
            "Главный акцент: НПА Краснодарского края: документ после OCR", markdown
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
        documents[0].business_signal = (
            "Стратегический федеральный сигнал по господдержке или порядку регулирования."
        )
        documents[0].impact = (
            "Документ стоит держать на наблюдении: тема может затронуть АПК."
        )
        documents[1].business_signal = (
            "Стратегический федеральный сигнал по господдержке или порядку регулирования."
        )
        documents[1].impact = (
            "Документ стоит держать на наблюдении: тема может затронуть АПК."
        )
        documents[2].business_signal = (
            "Стратегический федеральный сигнал по господдержке или порядку регулирования."
        )
        documents[2].impact = (
            "Документ стоит держать на наблюдении: тема может затронуть АПК."
        )
        documents[3].business_signal = (
            "Стратегический федеральный сигнал по господдержке или порядку регулирования."
        )
        documents[3].impact = (
            "Документ стоит держать на наблюдении: тема может затронуть АПК."
        )

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

    def test_markdown_strategy_section_hides_budget_credit_and_naukograd_noise(
        self,
    ) -> None:
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
        documents[0].business_signal = (
            "Стратегический федеральный сигнал по господдержке или порядку регулирования."
        )
        documents[1].business_signal = (
            "Стратегический федеральный сигнал по господдержке или порядку регулирования."
        )

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
        self.assertIn(
            "Правительство расширило программу господдержки экспортеров АПК", markdown
        )

    def test_markdown_strategy_section_hides_novak_macro_meeting_with_food_in_raw_text(
        self,
    ) -> None:
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
        self.assertNotIn(
            "Александр Новак провёл совещание по ситуации в экономике", markdown
        )

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
            title="О внесении изменений в порядок предоставления субсидий сельхозтоваропроизводителям",
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
            "О внесении изменений в порядок предоставления субсидий сельхозтоваропроизводителям",
        )
        self.assertEqual(
            visible_documents[0].deadline_text, "Срок подачи заявок до 30.06.2026."
        )

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
        self.assertEqual(
            visible_documents[0].url, "https://msh.krasnodar.ru/documents/subsidy-full"
        )

    def test_report_collapses_generic_promote_selection_duplicates(self) -> None:
        first = self._doc(
            doc_id=31,
            source_name="promote.budget.gov.ru / Минфин - отборы и меры поддержки",
            region="federal",
            title="Компенсация понесенных затрат сельхозорганизациям",
            url="https://promote.budget.gov.ru/public/minfin/selection/view/first",
            action_level="requires_attention",
            page_type="selection_announcement",
            summary="Открыт прием заявок на возмещение затрат.",
        )
        second = self._doc(
            doc_id=32,
            source_name="promote.budget.gov.ru / Минфин - отборы и меры поддержки",
            region="federal",
            title="Возмещение части затрат для организаций АПК",
            url="https://promote.budget.gov.ru/public/minfin/selection/view/second",
            action_level="requires_attention",
            page_type="selection_announcement",
            summary="Открыт прием заявок на возмещение затрат.",
        )
        for document in (first, second):
            document.application_status = "open"
            document.deadline_text = "Прием заявок до 20.05.2099."

        markdown = generate_markdown_report(
            [first, second],
            report_date="2026-05-20",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertEqual(markdown.count("### Открыт прием заявок на возмещение затрат"), 1)
        visible_urls = [url for url in (first.url, second.url) if url in markdown]
        self.assertEqual(len(visible_urls), 1)

    def test_report_limits_expired_selection_noise_in_measures_section(self) -> None:
        documents = [
            self._doc(
                doc_id=40 + index,
                source_name="promote.budget.gov.ru / Минфин - отборы и меры поддержки",
                region="federal",
                title=f"Отбор заявок на субсидии АПК {index}",
                url=f"https://promote.budget.gov.ru/public/minfin/selection/view/expired-{index}",
                action_level="watchlist",
                page_type="selection_announcement",
                summary="Прием заявок завершён.",
            )
            for index in range(3)
        ]
        for document in documents:
            document.application_status = "closed"
            document.deadline_text = "Прием заявок до 01.01.2020."

        markdown = generate_markdown_report(
            documents,
            report_date="2026-05-20",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertEqual(markdown.count("Срок истёк: 01.01.2020"), 1)
        visible_urls = [document.url for document in documents if document.url in markdown]
        self.assertEqual(len(visible_urls), 1)

    def test_report_ignores_weak_cached_document_card_text(self) -> None:
        db_path = self._db_path("report_weak_document_card_text.db")
        init_db(db_path)
        document = self._doc(
            doc_id=33,
            source_name="promote.budget.gov.ru / Минфин - отборы и меры поддержки",
            region="federal",
            title="Отбор на субсидию АПК",
            url="https://promote.budget.gov.ru/public/minfin/selection/view/weak-card",
            action_level="requires_attention",
            page_type="selection_announcement",
            summary="Открыт прием заявок.",
        )
        document.application_status = "open"
        document.deadline_text = "Прием заявок до 20.05.2099."
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult.from_facts(
                DocumentCardFacts(
                    document_type="отбор",
                    status="прием открыт",
                    deadline="2099-05-20",
                    why_matters="Открыт прием заявок",
                    what_to_check="Проверить применимость меры, сроки подачи и ответственного",
                    short_summary="Открыт прием заявок на субсидию.",
                    confidence="high",
                )
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-20",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertNotIn("- Почему важно: Открыт прием заявок", markdown)
        self.assertNotIn(
            "Проверить применимость меры, сроки подачи и ответственного",
            markdown,
        )
        self.assertIn("По теме субсидии может потребоваться решение о подаче", markdown)
        self.assertIn("По теме субсидии: проверить критерии получателя", markdown)

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
        document.business_signal = (
            "Активная федеральная мера поддержки, действует на регулярной основе"
        )

        with patch(
            "app.reports.markdown_report.list_document_enrichments", return_value={}
        ):
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
            title="Субсидии на возмещение затрат сельхозтоваропроизводителям",
            url="https://gisp.gov.ru/nmp/measure/8130026",
            action_level="watchlist",
            page_type="new_rule",
            summary="Неактивная мера поддержки.",
        )
        document.support_status = "inactive"
        document.application_status = "open"
        document.deadline_text = "Прием заявок до 30.06.2026."
        document.risk_notes = (
            "Срок найден в описании неактивной меры; не является текущим окном подачи."
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-04-30",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertNotIn("Action level", markdown)
        self.assertIn("Почему важно:", markdown)

    def test_background_gisp_measures_are_hidden_from_visible_watchlist_report(
        self,
    ) -> None:
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
        inactive_document.business_signal = (
            "Неактивная мера поддержки: оставить в справочном блоке"
        )

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
        non_target_document.business_signal = (
            "Активная мера поддержки вне целевой географии; оставлена для справки."
        )

        report_view = build_report_view(
            [inactive_document, non_target_document],
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertEqual(report_view.total_visible, 0)

    def test_report_suppresses_evergreen_support_references_but_keeps_fresh_regional_order(
        self,
    ) -> None:
        evergreen_measure = self._doc(
            doc_id=10,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="watchlist",
            page_type="measure_card",
            summary="Мера действует на регулярной основе.",
        )
        evergreen_measure.published_at = None
        evergreen_measure.support_status = "active"
        evergreen_measure.application_status = "regular"
        evergreen_measure.is_active = True

        fresh_order = self._doc(
            doc_id=11,
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            region="krasnodar",
            title="О внесении изменения в порядок предоставления субсидий на реализацию проектов мелиорации",
            url="https://npa.krasnodar.ru/rest/files/1233833",
            action_level="watchlist",
            page_type="new_rule",
            summary="Изменен порядок предоставления субсидий на проекты мелиорации.",
        )
        fresh_order.business_signal = (
            "Региональный приказ Минсельхоза Краснодарского края по субсидии, гранту "
            "или порядку поддержки; держать на наблюдении."
        )

        with patch("app.reports.markdown_report.list_document_enrichments", return_value={}):
            markdown = generate_markdown_report(
                [evergreen_measure, fresh_order],
                report_date="2026-05-13",
                relevant_only=True,
                action_levels=["requires_attention", "watchlist"],
            )

        self.assertIn("Изменены условия субсидирования", markdown)
        self.assertIn("https://npa.krasnodar.ru/rest/files/1233833", markdown)
        self.assertNotIn("Льготное кредитование АПК", markdown)

    def test_report_suppresses_stale_handbook_reference_pdf(self) -> None:
        handbook = self._doc(
            doc_id=13,
            source_name="Минсельхоз Ставропольского края - господдержка",
            region="stavropol",
            title="Справочник по мерам государственной поддержки",
            url="https://mshsk.ru/брошюра%202023.pdf",
            action_level="watchlist",
            page_type="reference_page",
            summary="Справочный материал по мерам поддержки.",
        )
        handbook.published_at = datetime(2025, 2, 14, tzinfo=timezone.utc)

        fresh_order = self._doc(
            doc_id=14,
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            region="krasnodar",
            title="О внесении изменения в порядок предоставления субсидий на реализацию проектов мелиорации",
            url="https://npa.krasnodar.ru/rest/files/1233833",
            action_level="watchlist",
            page_type="new_rule",
            summary="Изменен порядок предоставления субсидий на проекты мелиорации.",
        )
        fresh_order.business_signal = "Изменены условия поддержки."

        markdown = generate_markdown_report(
            [handbook, fresh_order],
            report_date="2026-05-14",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertNotIn("Справочник по мерам государственной поддержки", markdown)
        self.assertIn("https://npa.krasnodar.ru/rest/files/1233833", markdown)

    def test_report_sorts_measures_by_priority_even_when_under_limit(self) -> None:
        older_measure = self._doc(
            doc_id=15,
            source_name="Минсельхоз Ставропольского края - господдержка",
            region="stavropol",
            title="Субсидии на возмещение части затрат за реализованные объемы куриных пищевых яиц",
            url="http://mshsk.ru/gospodderzhka/subsidies-for-reimbursement-egg.php",
            action_level="watchlist",
            page_type="measure_card",
            summary="Мера поддержки АПК без срочного действия.",
        )
        older_measure.published_at = datetime(2025, 2, 14, tzinfo=timezone.utc)
        older_measure.raw_text = "Мера поддержки АПК для производителей яиц."

        fresh_selection = self._doc(
            doc_id=16,
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            region="krasnodar",
            title="Объявление о проведении отбора на предоставление субсидии сельхозтоваропроизводителям",
            url="https://npa.krasnodar.ru/rest/files/1233677",
            action_level="watchlist",
            page_type="selection_announcement",
            summary="Открыт прием заявок.",
        )
        fresh_selection.application_status = "open"
        fresh_selection.deadline_text = "Прием заявок до 20.05.2026."

        markdown = generate_markdown_report(
            [older_measure, fresh_selection],
            report_date="2026-05-14",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertLess(
            markdown.index("https://npa.krasnodar.ru/rest/files/1233677"),
            markdown.index(
                "http://mshsk.ru/gospodderzhka/subsidies-for-reimbursement-egg.php"
            ),
        )

    def test_report_keeps_support_reference_with_change_signal(self) -> None:
        changed_measure = self._doc(
            doc_id=12,
            source_name="Минсельхоз России - меры господдержки",
            region="federal",
            title="Субсидии производителям сельскохозяйственной техники",
            url="https://mcx.gov.ru/activity/state-support/measures/machinery-subsidy/",
            action_level="watchlist",
            page_type="new_rule",
            summary="Внесены изменения в правила предоставления субсидии.",
        )
        changed_measure.published_at = None
        changed_measure.business_signal = "Изменены условия поддержки"

        markdown = generate_markdown_report(
            [changed_measure],
            report_date="2026-05-13",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("Субсидии производителям сельскохозяйственной техники", markdown)

    def test_report_header_counts_rendered_items_after_evergreen_suppression(
        self,
    ) -> None:
        regulation_document = self._doc(
            doc_id=20,
            source_name="Regulation.gov.ru",
            region="federal",
            title="Об утверждении требований к видам племенных хозяйств",
            url="https://regulation.gov.ru/projects/167863",
            action_level="watchlist",
            page_type="new_rule",
            summary="Проект НПА по сельскому хозяйству.",
        )
        regulation_document.application_status = "open"
        regulation_document.deadline_text = "Конец обсуждения: 2026-05-26T11:53:57.098Z"

        evergreen_measure = self._doc(
            doc_id=21,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Гарантия ВЭБ.РФ",
            url="https://gisp.gov.ru/nmp/measure/12446928",
            action_level="watchlist",
            page_type="measure_card",
            summary="Постоянная федеральная мера поддержки.",
        )
        evergreen_measure.published_at = None
        evergreen_measure.application_status = "regular"
        evergreen_measure.support_status = "active"

        news_document = self._doc(
            doc_id=22,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="В ГД предложили создать госпрограмму субсидирования ремонта сельхозтехники",
            url="https://www.zol.ru/n/41438",
            action_level="watchlist",
            page_type="news_background",
            summary="Предложена программа поддержки ремонта сельхозтехники.",
        )

        markdown = generate_markdown_report(
            [regulation_document, evergreen_measure, news_document],
            report_date="2026-05-13",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        self.assertIn("- Включено в сводку: 2", markdown)
        self.assertIn("- Требует реакции: 0", markdown)
        self.assertIn("- На наблюдении: 2", markdown)
        self.assertNotIn("Гарантия ВЭБ.РФ", markdown)

    def test_regulation_public_discussion_uses_correct_report_wording(self) -> None:
        db_path = self._db_path("report_regulation_public_discussion.db")
        init_db(db_path)
        document = self._doc(
            doc_id=30,
            source_name="Regulation.gov.ru",
            region="federal",
            title="Об утверждении требований к видам племенных хозяйств",
            url="https://regulation.gov.ru/projects/167863",
            action_level="watchlist",
            page_type="new_rule",
            summary=(
                "Проект Статус: Идет обсуждение Процедура: Оценка регулирующего воздействия "
                "Начало обсуждения: 2026-05-12T11:53:57.098Z "
                "Конец обсуждения: 2026-05-26T11:53:57.098Z"
            ),
        )
        document.application_status = "open"
        document.deadline_text = "Конец обсуждения: 2026-05-26T11:53:57.098Z"
        document.business_signal = (
            "Проект НПА на публичном обсуждении со сроком; проверить влияние "
            "и необходимость GR-позиции."
        )
        document.raw_text = (
            "Проект касается требований к видам племенных хозяйств и "
            "сельскохозяйственных товаропроизводителей."
        )
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult.from_facts(
                DocumentCardFacts(
                    document_type="проект НПА",
                    region="РФ",
                    authority="Regulation.gov.ru",
                    status="проект обсуждается",
                    deadline="2026-05-26",
                    effective_date=None,
                    support_type="субсидия",
                    target_recipients=["сельхозтоваропроизводители"],
                    what_changed=(
                        "Проект касается требований к видам племенных хозяйств и "
                        "сельскохозяйственных товаропроизводителей."
                    ),
                    why_matters=(
                        "Проект НПА на публичном обсуждении. "
                        "Нужно проверить влияние на порядок поддержки и необходимость GR-позиции."
                    ),
                    what_to_check=(
                        "Проверить предмет проекта, затронутые требования к получателям и "
                        "необходимость подготовки позиции."
                    ),
                    applicability_note=(
                        "Регион: РФ. Применимость к AHSTEP требует проверки затронутых правил и статуса получателя."
                    ),
                    short_summary=(
                        "Проект НПА вынесен на публичное обсуждение. "
                        "До окончания обсуждения нужно понять, меняет ли он требования к профильным получателям."
                    ),
                    confidence="high",
                    source_quotes=["Конец обсуждения", "племенных хозяйств"],
                )
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-13",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertIn("Проект НПА на публичном обсуждении", markdown)
        self.assertIn("Проект НПА вынесен на публичное обсуждение.", markdown)
        self.assertIn("Что сделать до конца обсуждения:", markdown)
        self.assertIn("подготовить позицию до 26.05.2026", markdown)
        self.assertIn(
            "Сроки / даты: Статус: проект обсуждается; Публичное обсуждение: до 26.05.2026",
            markdown,
        )
        self.assertNotIn("Открыт прием заявок", markdown)
        self.assertNotIn("Проверить сроки подачи", markdown)

    def test_telegram_digest_includes_business_facts_for_requires_attention(
        self,
    ) -> None:
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
        document.business_signal = (
            "Активная федеральная мера поддержки, действует на регулярной основе"
        )

        captured: list[str] = []

        def _capture(text: str) -> bool:
            captured.append(text)
            return True

        with patch("app.notify.telegram.send_message", side_effect=_capture):
            with patch(
                "app.notify.telegram_formatter.list_document_enrichments",
                return_value={},
            ):
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
        urgent_block = markdown.split("## 🚨 Требует внимания", 1)[1].split(
            "## 📢 Меры и отборы", 1
        )[0]
        self.assertNotIn("НПА Краснодарского края: документ после OCR", urgent_block)

    def test_fallback_titled_weak_ocr_placeholder_is_not_rendered_in_urgent_section(
        self,
    ) -> None:
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

        urgent_block = markdown.split("## 🚨 Требует внимания", 1)[1].split(
            "## 📢 Меры и отборы", 1
        )[0]
        regional_block = markdown.split("## ⚖️ Региональные изменения", 1)[1].split(
            "## 🏛 Стратегические сигналы", 1
        )[0]
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
        document.business_signal = (
            "Рыночный или отраслевой фон без прямого регуляторного сигнала."
        )

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
        noisy_document.raw_text = (
            "Еженедельный обзор рынка зерна, ставки фрахта и оценки аналитиков."
        )

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
        self.assertIn(
            "Правительство расширило программу господдержки экспортеров АПК", markdown
        )

    def test_report_uses_role_specific_action_hints(self) -> None:
        support_document = self._doc(
            doc_id=1,
            source_name="Минсельхоз Ростовской области - господдержка",
            region="rostov",
            title="Объявление о проведении отбора на предоставление субсидии сельхозтоваропроизводителям",
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
            title="Постановление о внесении изменений в порядок предоставления субсидий сельхозтоваропроизводителям",
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

        frozen_today = date(2026, 5, 5)
        with patch("app.rules.deadline_truth.today_utc", return_value=frozen_today):
            with patch("app.reports.markdown_report.today_utc", return_value=frozen_today):
                markdown = generate_markdown_report(
                    [support_document, regional_npa, news_document],
                    report_date="2026-05-05",
                    relevant_only=True,
                    action_levels=["requires_attention", "watchlist"],
                    include_market_background=True,
                )

        self.assertIn("Проверить сроки подачи документов и готовность заявки.", markdown)
        self.assertIn(
            "Проверить изменения условий субсидирования и критерии отбора.", markdown
        )
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

        self.assertIn("### Изменены субсидии в Ростовской области", markdown)
        self.assertIn("https://pravo.donland.ru/doc/view/id/very-long-title", markdown)
        self.assertNotIn(long_title, markdown)

    def test_report_title_truncation_does_not_cut_mid_word(self) -> None:
        long_title = (
            "Минсельхоз рассматривает скидку на экспортную пошлину как инструмент "
            "стимулирования биржевой торговли зерном и расширения поставок"
        )
        document = self._doc(
            doc_id=1301,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title=long_title,
            url="https://www.zol.ru/n/title-clip",
            action_level="requires_attention",
            page_type="news_background",
            summary="Изменения экспортного регулирования зерна.",
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-22",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        title_line = next(line for line in markdown.splitlines() if line.startswith("### "))
        self.assertFalse(title_line.endswith("..."))
        self.assertNotIn("бир...", markdown)
        self.assertNotIn("экспортн...", markdown)
        self.assertIn("инструмент стимулирования", markdown)

    def test_report_summary_truncation_does_not_cut_mid_word(self) -> None:
        text = (
            "Минсельхоз рассматривает скидку на экспортную пошлину как инструмент "
            "стимулирования биржевой торговли зерном и расширения поставок."
        )
        clipped = _sanitize_report_display_text(text, max_chars=88)

        self.assertNotIn("бир...", clipped)
        self.assertNotIn("экспортн...", clipped)
        self.assertFalse(clipped.endswith("..."))

    def test_docx_export_does_not_contain_mid_word_report_ellipsis(self) -> None:
        output_path = self._db_path("report_no_midword_ellipsis.docx")
        long_title = (
            "Минсельхоз рассматривает скидку на экспортную пошлину как инструмент "
            "стимулирования биржевой торговли зерном и расширения поставок"
        )
        document = self._doc(
            doc_id=1302,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title=long_title,
            url="https://www.zol.ru/n/docx-title-clip",
            action_level="requires_attention",
            page_type="news_background",
            summary="Изменения экспортного регулирования зерна.",
        )
        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-22",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )

        created = create_docx_from_markdown(markdown, output_path)
        text = "\n".join(paragraph.text for paragraph in DocxDocument(created).paragraphs)

        self.assertNotIn("бир...", text)
        self.assertNotIn("экспортн...", text)
        self.assertNotIn("...", text)

    def test_report_strips_terminal_ellipsis_from_stale_enrichment(self) -> None:
        db_path = self._db_path("report_terminal_ellipsis.db")
        init_db(db_path)
        document = self._doc(
            doc_id=1303,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Сигнал по экспортной пошлине на зерно",
            url="https://www.zol.ru/n/stale-ellipsis",
            action_level="requires_attention",
            page_type="news_background",
            summary="Базовая summary.",
        )
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult(
                executive_summary="Минсельхоз рассматривает скидку для стимулирования биржевой торговли зерном...",
                business_impact="Мера может повлиять на экономику экспорта зерна...",
                recommended_action="Отследить публикацию официального НПА...",
                confidence=0.8,
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-22",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertNotIn("...", markdown)
        self.assertIn("биржевой торговли зерном", markdown)

    def test_preview_truncation_keeps_word_boundary_with_ellipsis(self) -> None:
        clipped = safe_truncate_text(
            "Минсельхоз рассматривает скидку на экспортную пошлину как инструмент стимулирования биржевой торговли",
            84,
        )

        self.assertTrue(clipped.endswith("..."))
        self.assertNotIn("бир...", clipped)
        self.assertNotIn("экспортн...", clipped)

    def test_rosstat_old_census_repeal_is_not_visible_digest_item(self) -> None:
        document = self._doc(
            doc_id=1304,
            source_name="Regulation.gov.ru",
            region="federal",
            title="О признании утратившими силу приказов Росстата по вопросам подготовки, проведения и подведения итогов Всероссийской сельскохозяйственной переписи 2016 года",
            url="https://regulation.gov.ru/projects/old-census",
            action_level="watchlist",
            page_type="new_rule",
            summary="Проект НПА об отмене устаревших приказов Росстата по сельскохозяйственной переписи 2016 года.",
        )
        document.raw_text = (
            "Проект приказа о признании утратившими силу актов по вопросам подготовки, "
            "проведения и подведения итогов Всероссийской сельскохозяйственной переписи 2016 года."
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

    def test_real_stats_reporting_change_remains_visible(self) -> None:
        document = self._doc(
            doc_id=1305,
            source_name="Regulation.gov.ru",
            region="federal",
            title="Росстат утвердил новые формы отчетности для сельхозтоваропроизводителей",
            url="https://regulation.gov.ru/projects/stats-reporting",
            action_level="watchlist",
            page_type="new_rule",
            summary="Проект меняет формы отчетности и сроки представления сведений для сельхозтоваропроизводителей.",
        )
        document.raw_text = (
            "Утверждаются новые формы отчетности, порядок представления сведений "
            "и сроки подачи данных сельхозтоваропроизводителями."
        )

        self.assertEqual(effective_user_action_level(document), "watchlist")
        self.assertTrue(
            should_show_document(
                document,
                surface="report",
                relevant_only=False,
                action_levels=["requires_attention", "watchlist"],
            )
        )

    def test_weak_bashgau_digital_selection_news_is_not_visible_digest_item(self) -> None:
        document = self._doc(
            doc_id=1306,
            source_name="Минсельхоз России - новости",
            region="federal",
            title="В Минсельхозе обсудили проекты по развитию цифровой селекции на базе Башкирского ГАУ",
            url="https://mcx.gov.ru/press-service/news/bashgau-digital-selection/",
            action_level="watchlist",
            page_type="news_background",
            summary="Минсельхоз поддерживает разработку цифровых инструментов селекции на базе БашГАУ.",
        )
        document.raw_text = (
            "В Минсельхозе обсудили проекты Башкирского государственного аграрного университета "
            "по цифровизации селекции в свиноводстве и растениеводстве."
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

    def test_federal_export_grain_signal_still_visible_after_technical_cleanup(self) -> None:
        document = self._doc(
            doc_id=1307,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Минсельхоз рассматривает скидку на экспортную пошлину для зерна",
            url="https://www.zol.ru/n/export-duty-visible",
            action_level="requires_attention",
            page_type="news_background",
            summary="Минсельхоз рассматривает скидку на экспортную пошлину для участников биржевых торгов зерном.",
        )
        document.raw_text = (
            "Минсельхоз России прорабатывает скидки на экспортную пошлину для зерна "
            "и стимулирование биржевых торгов."
        )

        self.assertEqual(effective_user_action_level(document), "requires_attention")
        self.assertTrue(
            should_show_document(
                document,
                surface="report",
                relevant_only=False,
                action_levels=["requires_attention", "watchlist"],
            )
        )

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

        self.assertIn("### Изменены субсидии в Ростовской области", markdown)
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

    def test_passenger_rail_with_tariff_in_raw_text_absent_from_strategy_section(
        self,
    ) -> None:
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

    def test_regional_budget_credits_with_finance_in_raw_text_absent_from_strategy_section(
        self,
    ) -> None:
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

    def test_apk_export_support_with_selkhozprodukt_in_raw_text_visible_in_strategy_section(
        self,
    ) -> None:
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
        self.assertIn(
            "Правительство расширило программу поддержки экспорта АПК", markdown
        )
        self.assertNotIn("Новых стратегических сигналов не найдено.", markdown)

    def test_fertilizer_regulation_with_udobrenie_in_raw_text_visible_in_strategy_section(
        self,
    ) -> None:
        document = self._strategy_doc(
            doc_id=503,
            title="Правительство ввело квоты на экспорт азотных удобрений",
            summary="Квотирование экспорта удобрений.",
            raw_text=(
                "Введены квоты на вывоз азотных и сложных удобрений в целях насыщения внутреннего рынка АПК."
            ),
        )
        markdown = self._generate([document])
        self.assertIn(
            "Правительство ввело квоты на экспорт азотных удобрений", markdown
        )


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

    def test_two_documents_colliding_on_compressed_title_render_with_distinct_headings(
        self,
    ) -> None:
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
        # After Fix 4 both get distinct topic-aware headlines, no collision
        self.assertIn("Субсидии", markdown)
        # Both URLs must be present (both items rendered)
        self.assertIn("iblock/9a3", markdown)
        self.assertIn("iblock/c24", markdown)

    def test_npa_number_field_produces_npa_suffix(self) -> None:
        # Embed NPA numbers in titles so _numeric_tokens differ → not grouped by select_best_report_documents.
        # Both compress to "Субсидии в животноводстве — Краснодарском крае" via _approval_headline (Fix 4).
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

    def test_identical_ocr_titles_do_not_leak_parser_url_tokens(self) -> None:
        # Two identical OCR placeholder titles share the URL fallback path.
        # Previously the helper emitted "документ 9a3" / "документ c24" tokens
        # which read like parser artifacts; under the cleaner UX rule the
        # disambiguator must NOT emit these.
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
        rendered = list(title_map.values())
        self.assertFalse(any("документ 9a3" in title for title in rendered))
        self.assertFalse(any("документ c24" in title for title in rendered))
        # Bare numeric suffix fallback must also stay out of the rendered title.
        self.assertFalse(any(title.endswith(" (1)") for title in rendered))
        self.assertFalse(any(title.endswith(" (2)") for title in rendered))

    def test_bad_lexical_suffixes_are_dropped_when_no_topical_match(self) -> None:
        # When neither a topical stem nor an NPA/date is available, the
        # disambiguator must keep titles clean rather than emit parser tokens
        # or bare Russian wordforms.
        from app.user_facing import disambiguate_visible_titles

        doc1 = self._doc(
            doc_id=622,
            title="Об утверждении порядка предоставления субсидий в агропромышленном комплексе",
            url="https://admkrai.krasnodar.ru/upload/iblock/9a3/abc.pdf",
        )
        doc2 = self._doc(
            doc_id=623,
            title=(
                "Об утверждении порядка предоставления субсидий в агропромышленном "
                "комплексе постановлениями Правительства Российской Федерации"
            ),
            url="https://admkrai.krasnodar.ru/upload/iblock/c24/xyz.pdf",
        )

        title_map = disambiguate_visible_titles([doc1, doc2], max_chars=90)
        rendered_titles = list(title_map.values())

        self.assertFalse(any("документ 9a3" in title for title in rendered_titles))
        self.assertFalse(any("документ c24" in title for title in rendered_titles))
        self.assertFalse(
            any("(агропромышленном)" in title for title in rendered_titles)
        )
        self.assertFalse(any("(мелиорации)" in title for title in rendered_titles))

    def test_unique_title_has_no_suffix_appended(self) -> None:
        doc = self._doc(
            doc_id=630,
            title="Об утверждении порядка предоставления субсидий на молочное скотоводство",
            url="https://admkrai.krasnodar.ru/upload/iblock/9a3/only.pdf",
        )
        markdown = self._generate([doc])
        self.assertIn("Субсидии на молочное животноводство", markdown)
        self.assertNotIn("(документ", markdown)
        self.assertNotIn("(№", markdown)

    def test_original_document_title_is_not_mutated(self) -> None:
        original_title = (
            "Об утверждении порядка предоставления субсидий на молочное скотоводство"
        )
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


class RequiresAttentionCapTest(unittest.TestCase):
    """Executive cap + overflow routing for the urgent block."""

    def _doc(
        self,
        *,
        doc_id: int,
        source_name: str,
        region: str,
        title: str,
        url: str,
        page_type: str,
        summary: str = "summary",
        application_status: str = "unknown",
        deadline_text: str | None = None,
        business_signal: str | None = None,
        action_level: str = "requires_attention",
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
            content_hash=f"cap-{doc_id}",
            raw_text="text",
            is_relevant=True,
            relevance_reason="reason",
            importance="high",
            action_level=action_level,
            page_type=page_type,
            summary=summary,
            impact="impact",
            topic="topic",
            collected_at=now,
            application_status=application_status,
            deadline_text=deadline_text,
            business_signal=business_signal,
        )

    def _build(self, documents):
        from app.reports.markdown_report import (
            _build_display_sections,
            REQUIRES_ATTENTION_DISPLAY_MAX,
        )

        return _build_display_sections(documents), REQUIRES_ATTENTION_DISPLAY_MAX

    def test_urgent_section_capped_to_executive_max(self) -> None:
        documents = [
            self._doc(
                doc_id=100 + i,
                source_name="Нормативные акты Краснодарского края",
                region="krasnodar",
                title=f"О внесении изменений в порядок предоставления субсидий в АПК №{i}",
                url=f"https://admkrai.krasnodar.ru/upload/iblock/cap{i}.pdf",
                page_type="new_rule",
                summary="Изменены условия субсидирования.",
                business_signal="Изменены условия предоставления субсидии в АПК.",
            )
            for i in range(8)
        ]
        sections, cap = self._build(documents)
        self.assertLessEqual(len(sections["requires_attention"]), cap)

    def test_overflow_items_remain_visible_elsewhere(self) -> None:
        # 8 urgent regional NPA items — 5 visible at the top, 3 overflow into
        # regional_npa section. None must disappear.
        documents = [
            self._doc(
                doc_id=200 + i,
                source_name="Нормативные акты Краснодарского края",
                region="krasnodar",
                title=f"О внесении изменений в порядок предоставления субсидий в АПК №{i}",
                url=f"https://admkrai.krasnodar.ru/upload/iblock/overflow{i}.pdf",
                page_type="new_rule",
                summary="Изменены условия субсидирования.",
                business_signal="Изменены условия предоставления субсидии в АПК.",
            )
            for i in range(8)
        ]
        sections, cap = self._build(documents)
        total = sum(len(sections[s]) for s in sections)
        self.assertEqual(total, len(documents))
        self.assertEqual(len(sections["requires_attention"]), cap)
        self.assertEqual(len(sections["regional_npa"]), len(documents) - cap)

    def test_overflow_routes_to_source_role_appropriate_section(self) -> None:
        # Mix of source roles: regional NPA, support docs, news. Overflow must
        # land in their natural sections, not collapse into one bucket.
        urgent_regional = [
            self._doc(
                doc_id=300 + i,
                source_name="Нормативные акты Краснодарского края",
                region="krasnodar",
                title=f"О внесении изменений в порядок субсидий в АПК №{i}",
                url=f"https://admkrai.krasnodar.ru/iblock/regional{i}.pdf",
                page_type="new_rule",
                summary="Изменены условия субсидирования.",
                business_signal="Изменены условия предоставления субсидии в АПК.",
            )
            for i in range(5)
        ]
        urgent_news = [
            self._doc(
                doc_id=310 + i,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title=f"Минсельхоз расширил поддержку программы АПК №{i}",
                url=f"https://www.zol.ru/n/news{i}",
                page_type="news_background",
                summary="Сигнал по господдержке АПК сельхозтоваропроизводителей.",
                business_signal="Сигнал по господдержке АПК.",
            )
            for i in range(3)
        ]
        documents = urgent_regional + urgent_news
        sections, cap = self._build(documents)
        self.assertEqual(len(sections["requires_attention"]), cap)
        # All 5 regional NPA acts outrank news items, so news overflows.
        regional_urls = {doc.url for doc in sections["requires_attention"]}
        self.assertEqual(
            len(regional_urls & {doc.url for doc in urgent_regional}),
            cap,
        )
        # Overflow news items must land in news_signals, not regional_npa.
        news_overflow = {doc.url for doc in sections["news_signals"]}
        self.assertEqual(
            news_overflow,
            {doc.url for doc in urgent_news},
        )

    def test_export_outranks_weak_announcement(self) -> None:
        export = self._doc(
            doc_id=400,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Пошлины на экспорт зерна АПК останутся нулевыми с 20 мая",
            url="https://www.zol.ru/n/export-duty",
            page_type="news_background",
            summary="Пошлина на экспорт пшеницы АПК будет нулевой.",
            business_signal="Изменение экспортных пошлин по продукции АПК.",
        )
        soft = self._doc(
            doc_id=401,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Стартовал прием заявок на участие в Агросмене-2026",
            url="https://www.zol.ru/n/event",
            page_type="news_background",
            summary="Образовательная программа АПК для аграриев и сельхозтоваропроизводителей.",
            application_status="open",
        )
        sections, _ = self._build([soft, export])
        urgent = sections["requires_attention"]
        # Both should still be in urgent (only 2 items, below cap), but the
        # export item must precede the soft announcement.
        urls = [doc.url for doc in urgent]
        self.assertEqual(urls.index(export.url), 0)
        self.assertLess(urls.index(export.url), urls.index(soft.url))

    def test_regulation_discussion_does_not_dominate_urgent_when_capping(self) -> None:
        # An open accepted act + 5 regulation discussions: when capping, the
        # accepted act keeps top and discussions overflow to strategy_signals.
        accepted_act = self._doc(
            doc_id=500,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="О внесении изменений в порядок предоставления субсидий в АПК",
            url="https://admkrai.krasnodar.ru/iblock/act.pdf",
            page_type="new_rule",
            summary="Изменены условия субсидирования в АПК.",
            business_signal="Изменены условия предоставления субсидий в АПК.",
        )
        discussions = [
            self._doc(
                doc_id=510 + i,
                source_name="Правительство РФ - документы",
                region="federal",
                title=f"Проект НПА по субсидиям АПК №{i}",
                url=f"https://government.ru/docs/discuss{i}",
                page_type="new_rule",
                summary="Проект НПА по поддержке сельского хозяйства вынесен на публичное обсуждение.",
                business_signal="Стратегический федеральный сигнал по АПК.",
            )
            for i in range(6)
        ]
        sections, cap = self._build([*discussions, accepted_act])
        urgent_urls = [doc.url for doc in sections["requires_attention"]]
        self.assertEqual(len(urgent_urls), cap)
        self.assertEqual(urgent_urls[0], accepted_act.url)
        # At least one discussion must overflow to strategy_signals; none must
        # disappear overall.
        strategy_overflow = [doc.url for doc in sections["strategy_signals"]]
        self.assertGreaterEqual(len(strategy_overflow), 1)
        all_kept = set(urgent_urls) | set(strategy_overflow)
        self.assertEqual(
            all_kept,
            {accepted_act.url} | {doc.url for doc in discussions},
        )

    def test_educational_event_does_not_outrank_operational_change(self) -> None:
        # The "Агросмена" event-style item must rank below subsidy changes.
        event = self._doc(
            doc_id=600,
            source_name="Минсельхоз России - новости",
            region="federal",
            title="Стартовал прием заявок на участие в Агросмене-2026",
            url="https://mcx.gov.ru/press-service/news/agrosmena/",
            page_type="news_background",
            summary="Образовательная программа АПК для молодых специалистов сельского хозяйства.",
            application_status="open",
        )
        subsidy_change = self._doc(
            doc_id=601,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="О внесении изменений в порядок предоставления субсидий в АПК",
            url="https://admkrai.krasnodar.ru/iblock/subsidy.pdf",
            page_type="new_rule",
            summary="Изменены условия субсидирования.",
            business_signal="Изменены условия предоставления субсидии в АПК.",
        )
        sections, _ = self._build([event, subsidy_change])
        urgent = sections["requires_attention"]
        urls = [doc.url for doc in urgent]
        self.assertLess(urls.index(subsidy_change.url), urls.index(event.url))

    def test_accepted_regional_act_stays_high_priority(self) -> None:
        act = self._doc(
            doc_id=700,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="О внесении изменений в порядок предоставления субсидий в АПК",
            url="https://admkrai.krasnodar.ru/iblock/highprio.pdf",
            page_type="new_rule",
            summary="Изменены условия субсидирования.",
            business_signal="Изменены условия предоставления субсидии в АПК.",
        )
        # Surrounded by weaker urgent items.
        filler = [
            self._doc(
                doc_id=710 + i,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title=f"Минсельхоз расширил программу АПК №{i}",
                url=f"https://www.zol.ru/n/filler{i}",
                page_type="news_background",
                summary="Сигнал по господдержке АПК сельхозтоваропроизводителей.",
            )
            for i in range(6)
        ]
        sections, cap = self._build([*filler, act])
        self.assertIn(act.url, {doc.url for doc in sections["requires_attention"]})
        self.assertEqual(len(sections["requires_attention"]), cap)

    def test_gr_topic_family_boosts_urgent_report_priority(self) -> None:
        published_at = datetime(2026, 5, 19, tzinfo=timezone.utc)
        generic_subsidy_act = self._doc(
            doc_id=901,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="О внесении изменений в порядок предоставления субсидий в АПК",
            url="https://admkrai.krasnodar.ru/iblock/generic-subsidy.pdf",
            page_type="new_rule",
            summary="Изменены условия субсидирования.",
            business_signal="Изменены условия предоставления субсидий в АПК.",
        )
        family_specific_act = self._doc(
            doc_id=902,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="Постановление №1528 о внесении изменений в порядок господдержки АПК",
            url="https://admkrai.krasnodar.ru/iblock/postanovlenie-1528.pdf",
            page_type="new_rule",
            summary="Изменения по Постановлению 1528 и субсидиям АПК.",
            business_signal="Сигнал по Постановлению №1528.",
        )
        generic_subsidy_act.published_at = published_at
        family_specific_act.published_at = published_at

        sections, _ = self._build([generic_subsidy_act, family_specific_act])
        urgent_urls = [doc.url for doc in sections["requires_attention"]]

        self.assertLess(
            urgent_urls.index(family_specific_act.url),
            urgent_urls.index(generic_subsidy_act.url),
        )

    def test_deterministic_ordering_is_stable_across_runs(self) -> None:
        documents = [
            self._doc(
                doc_id=800 + i,
                source_name="Нормативные акты Краснодарского края",
                region="krasnodar",
                title=f"О внесении изменений в порядок предоставления субсидий в АПК №{i}",
                url=f"https://admkrai.krasnodar.ru/iblock/stable{i}.pdf",
                page_type="new_rule",
                summary="Изменены условия субсидирования.",
                business_signal="Изменены условия предоставления субсидии в АПК.",
            )
            for i in range(7)
        ]
        sections_one, _ = self._build(list(documents))
        sections_two, _ = self._build(list(reversed(documents)))
        urgent_one = [doc.url for doc in sections_one["requires_attention"]]
        urgent_two = [doc.url for doc in sections_two["requires_attention"]]
        # Same priority + same published_at → tie-break by doc.id keeps order
        # identical regardless of input order.
        self.assertEqual(urgent_one, urgent_two)


class DeadlineTruthRenderingTest(unittest.TestCase):
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
        title: str,
        url: str,
        action_level: str,
        application_status: str,
        deadline_text: str | None,
        page_type: str = "selection_announcement",
    ) -> RawDocument:
        now = datetime.now(timezone.utc)
        return RawDocument(
            id=doc_id,
            source_name="Минсельхоз Ставропольского края - господдержка",
            source_url=url,
            level="regional",
            region="stavropol",
            title=title,
            url=url,
            published_at=now,
            content_hash=f"deadline-{doc_id}",
            raw_text="Объявление об отборе на субсидии для АПК сельхозтоваропроизводителям.",
            is_relevant=True,
            relevance_reason="reason",
            importance="high",
            action_level=action_level,
            page_type=page_type,
            summary="Объявление об отборе на субсидии АПК для сельхозтоваропроизводителей.",
            impact="impact",
            topic="topic",
            collected_at=now,
            application_status=application_status,
            deadline_text=deadline_text,
        )

    def test_expired_deadline_renders_as_сurok_istek(self) -> None:
        db_path = self._db_path("deadline_expired.db")
        init_db(db_path)
        document = self._doc(
            doc_id=900,
            title="Объявление об отборе",
            url="https://mshsk.ru/gospodderzhka/expired.php",
            action_level="watchlist",
            application_status="closed",
            deadline_text="Прием заявок открыт до 01.01.2020",
        )
        # Mirror enrichment that the renderer pulls from storage.
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult(
                executive_summary="Объявление об отборе.",
                business_impact="Конкурсный отбор.",
                recommended_action="Проверить применимость меры.",
                deadline_hint="Прием заявок открыт до 01.01.2020",
                confidence=0.8,
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-18",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertIn("Срок истёк: 01.01.2020", markdown)
        # The renderer must not regress to selection-open wording.
        self.assertNotIn("Открыт прием заявок", markdown)
        self.assertNotIn(
            "Проверить сроки и подачу: Прием заявок открыт до 01.01.2020",
            markdown,
        )

    def test_today_deadline_renders_as_сegodnya(self) -> None:
        db_path = self._db_path("deadline_today.db")
        init_db(db_path)
        from app.rules.deadline_truth import format_iso_date, today_utc

        today = today_utc()
        deadline_snippet = f"Прием заявок открыт до {format_iso_date(today)}"

        document = self._doc(
            doc_id=901,
            title="Объявление об отборе",
            url="https://mshsk.ru/gospodderzhka/today.php",
            action_level="requires_attention",
            application_status="open",
            deadline_text=deadline_snippet,
        )
        save_document_enrichment(
            document_id=document.id,
            document_url=document.url,
            provider="mock",
            model="mock-enrichment",
            enrichment=EnrichmentResult(
                executive_summary="Объявление об отборе.",
                business_impact="Конкурсный отбор.",
                recommended_action="Проверить применимость меры.",
                deadline_hint=deadline_snippet,
                confidence=0.8,
            ),
            db_path=db_path,
        )

        markdown = generate_markdown_report(
            [document],
            report_date="2026-05-18",
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
            db_path=db_path,
        )

        self.assertIn(f"Срок: сегодня ({format_iso_date(today)})", markdown)
        # Same-day deadline may still legitimately appear in requires_attention,
        # but must not be rendered with an "истёк" label.
        self.assertNotIn("Срок истёк", markdown)


if __name__ == "__main__":
    unittest.main()
