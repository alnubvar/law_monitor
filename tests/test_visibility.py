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


if __name__ == "__main__":
    unittest.main()
