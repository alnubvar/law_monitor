from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.models import RawDocument
from app.pipeline.diagnostics import run_diagnostics
from app.pipeline.digest import run_demo_report
from app.storage import init_db, save_document


class DiagnosticsSmokeTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path(f"data/test_artifacts/{name}")
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _doc(
        self,
        *,
        doc_id: int,
        source_name: str,
        title: str,
        action_level: str,
        page_type: str,
        summary: str = "summary",
        raw_text: str = "raw text",
    ) -> RawDocument:
        return RawDocument(
            id=doc_id,
            source_name=source_name,
            source_url=f"https://example.com/{doc_id}",
            level="federal",
            region="federal",
            title=title,
            url=f"https://example.com/doc/{doc_id}",
            published_at=datetime.now(timezone.utc),
            collected_at=datetime.now(timezone.utc),
            content_hash=f"hash-{doc_id}",
            raw_text=raw_text,
            is_relevant=action_level != "irrelevant",
            relevance_reason="reason",
            importance="high" if action_level == "requires_attention" else "medium",
            action_level=action_level,
            page_type=page_type,
            summary=summary,
            impact="impact",
            topic="topic",
            support_status="active" if page_type == "measure_card" else "unknown",
            application_status="regular" if page_type == "measure_card" else "unknown",
            business_signal="signal",
            status="analyzed",
        )

    def test_diagnostics_does_not_fail_on_empty_database(self) -> None:
        db_path = self._db_path("diagnostics_empty.db")
        init_db(db_path)

        output = run_diagnostics(db_path=db_path)

        self.assertIn("Total documents: 0", output)
        self.assertIn("Published_at coverage: 0/0", output)
        self.assertIn("No documents found in the selected period.", output)

    def test_diagnostics_days_output_contains_period_and_filter(self) -> None:
        db_path = self._db_path("diagnostics_days.db")
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            title="Льготное кредитование АПК",
            action_level="requires_attention",
            page_type="measure_card",
        )
        document.published_at = None
        save_document(document, db_path)

        output = run_diagnostics(db_path=db_path, days=7)

        self.assertIn("Period: last 7 days", output)
        self.assertIn("Date filter: published_at with collected_at fallback", output)
        self.assertIn(
            "Warning: many documents have no published_at; --days uses published_at with collected_at fallback.",
            output,
        )

    def test_diagnostics_correctly_counts_action_levels(self) -> None:
        db_path = self._db_path("diagnostics_counts.db")
        init_db(db_path)
        first = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            title="Льготное кредитование АПК",
            action_level="requires_attention",
            page_type="measure_card",
        )
        second = self._doc(
            doc_id=2,
            source_name="ГИСП - меры поддержки АПК",
            title="Гарантия ВЭБ.РФ",
            action_level="watchlist",
            page_type="measure_card",
            summary="",
        )
        second.published_at = None
        third = self._doc(
            doc_id=3,
            source_name="ZOL.ru - зерновые новости",
            title="Навигационная страница",
            action_level="irrelevant",
            page_type="navigation",
            raw_text="",
        )

        for document in (first, second, third):
            save_document(document, db_path)

        output = run_diagnostics(db_path=db_path)

        self.assertIn("- requires_attention: 1", output)
        self.assertIn("- watchlist: 1", output)
        self.assertIn("- irrelevant: 1", output)
        self.assertIn("Published_at coverage: 2/3", output)
        self.assertIn("By source_role:", output)
        self.assertIn("- active_support_measures", output)
        self.assertIn("- news_signals", output)
        self.assertIn("- ГИСП - меры поддержки АПК [active_support_measures]", output)
        self.assertIn("total=2; RA=1; WL=1; BG=0; IRR=0", output)
        self.assertIn("missing published_at=1; coverage=1/2", output)
        self.assertIn("missing summary=1", output)
        self.assertIn("measure_card=2", output)
        self.assertIn("Low-signal sources:", output)

    def test_source_lines_are_multiline_and_not_glued(self) -> None:
        db_path = self._db_path("diagnostics_multiline.db")
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="Минсельхоз Краснодарского края - субсидирование и финансирование",
            title="Раздел субсидий",
            action_level="watchlist",
            page_type="reference_page",
        )
        save_document(document, db_path)

        output = run_diagnostics(db_path=db_path)

        self.assertIn("- Минсельхоз Краснодарского края - субсидирование и финансирование [support_documents]", output)
        self.assertIn("registry/results=", output)
        self.assertNotIn("missineference_page", output)
        self.assertNotIn("irrelev-", output)
        self.assertNotIn("registr-", output)

    def test_low_signal_score_does_not_count_plain_watchlist_as_noise(self) -> None:
        db_path = self._db_path("diagnostics_noise.db")
        init_db(db_path)
        watchlist_document = self._doc(
            doc_id=1,
            source_name="ZOL.ru - зерновые новости",
            title="Производство сельхозпродукции в РФ выросло",
            action_level="watchlist",
            page_type="news_background",
        )
        irrelevant_document = self._doc(
            doc_id=2,
            source_name="ZOL.ru - зерновые новости",
            title="Навигация",
            action_level="irrelevant",
            page_type="navigation",
        )
        save_document(watchlist_document, db_path)
        save_document(irrelevant_document, db_path)

        output = run_diagnostics(db_path=db_path)

        self.assertIn("ZOL.ru - зерновые новости: low_signal=1/2 (50%)", output)

    def test_demo_report_generation_does_not_require_telegram_or_env(self) -> None:
        db_path = self._db_path("demo_report.db")
        output_path = Path("data/test_artifacts/demo_report.md")
        if output_path.exists():
            output_path.unlink()
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            title="Льготное кредитование АПК",
            action_level="requires_attention",
            page_type="measure_card",
        )
        document.npa_number = "22-68850-00258-Р"
        document.terms_text = "Срок кредита: До 12 месяцев."
        document.business_signal = "Активная федеральная мера поддержки, действует на регулярной основе"
        save_document(document, db_path)

        path = run_demo_report(db_path=db_path, output_path=str(output_path))
        markdown = path.read_text(encoding="utf-8")

        self.assertTrue(path.exists())
        self.assertIn("# AHSTEP Demo Report", markdown)
        self.assertIn("Льготное кредитование АПК", markdown)
        self.assertIn("Почему важно:", markdown)
        self.assertIn("Активная федеральная мера поддержки", markdown)

    def test_diagnostics_includes_parser_quality_hints(self) -> None:
        db_path = self._db_path("diagnostics_hints.db")
        init_db(db_path)
        noisy = self._doc(
            doc_id=1,
            source_name="Нормативные акты Краснодарского края",
            title="Просмотр",
            action_level="irrelevant",
            page_type="unknown",
        )
        noisy.published_at = None
        save_document(noisy, db_path)

        output = run_diagnostics(db_path=db_path, days=7)

        self.assertIn("Parser quality hints:", output)
        self.assertIn("high missing published_at:", output)
        self.assertIn("high low_signal:", output)
        self.assertIn("many unknown page_type:", output)

    def test_diagnostics_groups_by_source_role(self) -> None:
        db_path = self._db_path("diagnostics_roles.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="Правительство РФ - документы",
                title="Постановление о господдержке АПК",
                action_level="watchlist",
                page_type="new_rule",
            ),
            db_path,
        )
        save_document(
            self._doc(
                doc_id=2,
                source_name="ZOL.ru - зерновые новости",
                title="Пошлина на экспорт пшеницы останется нулевой",
                action_level="watchlist",
                page_type="news_background",
            ),
            db_path,
        )

        output = run_diagnostics(db_path=db_path)

        self.assertIn("- strategy", output)
        self.assertIn("- news_signals", output)
        self.assertIn("coverage=1/1", output)


if __name__ == "__main__":
    unittest.main()
