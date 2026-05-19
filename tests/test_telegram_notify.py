from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import requests

from app.models import RawDocument
from app.notify import telegram
from app.storage import (
    init_db,
    list_active_tracking_items,
    save_document,
    upsert_ocr_queue_item,
)


class TelegramNotifySmokeTest(unittest.TestCase):
    def _db_path(self, name: str) -> Path:
        path = Path("data/test_artifacts") / name
        if path.exists():
            path.unlink()
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
        summary: str = "summary",
        days_ago: int = 0,
    ) -> RawDocument:
        now = datetime.now(timezone.utc) - timedelta(days=days_ago)
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
            content_hash=f"telegram-command-{doc_id}",
            raw_text="text",
            is_relevant=action_level != "irrelevant",
            relevance_reason="reason",
            importance="high" if action_level == "requires_attention" else "medium",
            action_level=action_level,
            page_type=page_type,
            summary=summary,
            impact="impact",
            topic="topic",
            status="analyzed",
        )

    def test_send_message_uses_proxy_when_configured(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None

        with patch.multiple(
            telegram.config,
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat-id",
            TELEGRAM_PROXY_URL="socks5://user:pass@127.0.0.1:1080",
            TELEGRAM_PROXY_ENABLED=True,
            TELEGRAM_API_TIMEOUT=30,
        ):
            with patch("app.notify.telegram.requests.post", return_value=response) as post:
                sent = telegram.send_message("hello")

        self.assertTrue(sent)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(
            post.call_args.kwargs["proxies"],
            {
                "http": "socks5://user:pass@127.0.0.1:1080",
                "https": "socks5://user:pass@127.0.0.1:1080",
            },
        )
        self.assertEqual(post.call_args.kwargs["timeout"], 30)

    def test_send_message_sends_directly_without_proxy(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None

        with patch.multiple(
            telegram.config,
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat-id",
            TELEGRAM_PROXY_URL="",
            TELEGRAM_PROXY_ENABLED=False,
            TELEGRAM_API_TIMEOUT=15,
        ):
            with patch("app.notify.telegram.requests.post", return_value=response) as post:
                sent = telegram.send_message("hello")

        self.assertTrue(sent)
        self.assertIsNone(post.call_args.kwargs["proxies"])
        self.assertEqual(post.call_args.kwargs["timeout"], 15)

    def test_send_message_returns_false_after_retries(self) -> None:
        with patch.multiple(
            telegram.config,
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat-id",
            TELEGRAM_PROXY_URL="",
            TELEGRAM_PROXY_ENABLED=False,
            TELEGRAM_API_TIMEOUT=10,
        ):
            with patch(
                "app.notify.telegram.requests.post",
                side_effect=requests.exceptions.ConnectTimeout(),
            ) as post:
                with patch("app.notify.telegram.time.sleep") as sleep:
                    sent = telegram.send_message("hello")

        self.assertFalse(sent)
        self.assertEqual(post.call_count, telegram.TELEGRAM_SEND_ATTEMPTS)
        self.assertEqual(sleep.call_count, telegram.TELEGRAM_SEND_ATTEMPTS - 1)

    def test_daily_report_digest_sends_empty_state_and_attachment(self) -> None:
        report_path = Path("data/test_artifacts/daily_report.md")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("# report", encoding="utf-8")

        with patch("app.notify.telegram.collect_operational_notices", return_value=[]):
            with patch("app.notify.telegram.send_message", return_value=True) as send_message:
                with patch("app.notify.telegram.send_document", return_value=True) as send_document:
                    sent = telegram.send_daily_report_digest([], report_path=report_path)

        self.assertTrue(sent)
        message_text = send_message.call_args.args[0]
        self.assertIn("Срочных изменений не найдено, источники проверены.", message_text)
        self.assertIn("Полная версия отчета — во вложении.", message_text)
        self.assertNotIn("сервер", message_text.lower())
        self.assertNotIn("data/test_artifacts", message_text)
        send_document.assert_called_once_with(report_path)

    def test_daily_report_digest_sends_visible_docs_as_daily_not_hourly(self) -> None:
        document = self._doc(
            doc_id=900,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://example.com/daily-visible",
            action_level="requires_attention",
            page_type="measure_card",
        )

        with patch("app.notify.telegram.collect_operational_notices", return_value=[]):
            with patch("app.notify.telegram.send_message", return_value=True) as send_message:
                with patch("app.notify.telegram.send_document", return_value=True):
                    sent = telegram.send_daily_report_digest([document], report_path=None)

        self.assertTrue(sent)
        message_text = send_message.call_args.args[0]
        self.assertIn("Ежедневная GR-сводка", message_text)
        self.assertNotIn("Новые документы, требующие внимания", message_text)

    def test_help_command_lists_supported_commands(self) -> None:
        text = telegram.build_command_response("/help")

        self.assertIn("🚨 Срочное — документы, требующие внимания", text)
        self.assertIn("📄 Отчёт — ежедневная сводка и новые сигналы", text)
        self.assertIn("🔎 Поиск — поиск по документам и мерам поддержки", text)
        self.assertIn("🔄 Обновить — запустить проверку новых данных", text)
        self.assertNotIn("/status", text)
        self.assertNotIn("/today", text)
        self.assertNotIn("/urgent", text)
        self.assertNotIn("/watchlist", text)
        self.assertNotIn("/report", text)
        self.assertNotIn("/sources", text)
        self.assertNotIn("/refresh", text)
        self.assertNotIn("/ocr", text)
        self.assertNotIn("/track", text)
        self.assertNotIn("/untrack", text)
        self.assertNotIn("/tracked", text)

    def test_ocr_command_shows_pending_and_top_documents(self) -> None:
        db_path = self._db_path("telegram_ocr_queue.db")
        init_db(db_path)
        upsert_ocr_queue_item(
            document_url="https://example.com/ocr-1.pdf",
            source_name="Нормативные акты Краснодарского края",
            title="Скан приказа 1",
            priority="high",
            reason="scan_candidate_pdf",
            db_path=db_path,
        )
        upsert_ocr_queue_item(
            document_url="https://example.com/ocr-2.pdf",
            source_name="Право Ростовской области",
            title="Скан приказа 2",
            priority="medium",
            reason="scan_candidate_pdf",
            db_path=db_path,
        )

        text = telegram.build_command_response("/ocr", db_path=db_path)

        self.assertIn("OCR triage queue", text)
        self.assertIn("Pending: 2", text)
        self.assertIn("High priority pending: 1", text)
        self.assertIn("🔴 Скан приказа 1", text)
        self.assertIn("example.com/ocr-1.pdf", text)

    def test_urgent_command_returns_requires_attention_documents(self) -> None:
        db_path = self._db_path("telegram_urgent.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="ГИСП - меры поддержки АПК",
                region="federal",
                title="Льготное кредитование АПК",
                url="https://gisp.gov.ru/nmp/measure/9564204",
                action_level="requires_attention",
                page_type="measure_card",
            ),
            db_path,
        )

        text = telegram.build_command_response("/urgent", db_path=db_path)

        self.assertIn("Требует внимания GR", text)
        self.assertIn("Найдено документов: 1", text)
        self.assertIn("Уровень: требует внимания", text)
        self.assertIn("Льготное кредитование АПК", text)
        self.assertIn("Для общей сводки используйте 📄 Отчёт.", text)

    def test_status_counts_follow_user_facing_visibility_and_match_urgent_after_downgrade_and_dedup(self) -> None:
        db_path = self._db_path("telegram_status_user_facing_counts.db")
        init_db(db_path)
        russian_urgent = self._doc(
            doc_id=10,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
        )
        foreign_noise = self._doc(
            doc_id=11,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Квота на импорт кукурузы в Турцию выбрана на 20%",
            url="https://www.zol.ru/n/turkey-corn-import",
            action_level="requires_attention",
            page_type="news_background",
            summary="Турецкая импортная квота по кукурузе выбрана на 20%.",
        )
        foreign_noise.business_signal = "Новостной предвестник возможных изменений квот и внешней торговли."
        foreign_noise.raw_text = "Турция сообщила, что квота на импорт кукурузы выбрана на 20 процентов."
        gov_news = self._doc(
            doc_id=12,
            source_name="Правительство РФ - новости",
            region="federal",
            title="Изменения в господдержке экспорта АПК",
            url="http://government.ru/news/58669/",
            action_level="requires_attention",
            page_type="new_rule",
        )
        gov_docs = self._doc(
            doc_id=13,
            source_name="Правительство РФ - документы",
            region="federal",
            title="Изменения в господдержке экспорта АПК",
            url="http://government.ru/docs/58669/",
            action_level="requires_attention",
            page_type="new_rule",
        )
        for document in (russian_urgent, foreign_noise, gov_news, gov_docs):
            save_document(document, db_path)

        status_text = telegram.build_command_response("/status", db_path=db_path)
        urgent_text = telegram.build_command_response("/urgent", db_path=db_path)

        self.assertIn("требует внимания — 2", status_text)
        self.assertIn("Найдено документов: 2", urgent_text)
        self.assertNotIn("требует внимания — 4", status_text)

    def test_today_command_returns_visible_today_documents(self) -> None:
        db_path = self._db_path("telegram_today.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Пошлина на экспорт пшеницы останется нулевой",
                url="https://www.zol.ru/n/41337",
                action_level="watchlist",
                page_type="news_background",
            ),
            db_path,
        )
        save_document(
            self._doc(
                doc_id=2,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Старый документ",
                url="https://www.zol.ru/n/old",
                action_level="watchlist",
                page_type="news_background",
                days_ago=3,
            ),
            db_path,
        )

        text = telegram.build_command_response("/today", db_path=db_path)

        self.assertIn("Новые сигналы сегодня", text)
        self.assertIn("Новых срочных документов сегодня нет", text)
        self.assertIn("Отраслевые сигналы", text)
        self.assertIn("Пошлина на экспорт пшеницы останется нулевой", text)
        self.assertNotIn("Старый документ", text)

    def test_today_command_skips_noisy_market_watchlist(self) -> None:
        db_path = self._db_path("telegram_today_noise.db")
        init_db(db_path)
        noisy = self._doc(
            doc_id=1,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Фрахт и экспортные отгрузки зерна за неделю",
            url="https://www.zol.ru/n/freight-week",
            action_level="watchlist",
            page_type="news_background",
            summary="Общая рыночная аналитика без мер поддержки и без регуляторных изменений.",
        )
        noisy.business_signal = "Новостной предвестник возможных изменений господдержки, экспорта или регулирования."
        noisy.raw_text = "Фрахт, экспортные отгрузки и общая аналитика зернового рынка."
        save_document(noisy, db_path)

        text = telegram.build_command_response("/today", db_path=db_path)

        self.assertEqual(text, "Новых срочных документов сегодня нет.")

    def test_today_empty_state_includes_active_urgent_count_for_last_14_days(self) -> None:
        db_path = self._db_path("telegram_today_active_urgent_context.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=10,
                source_name="ГИСП - меры поддержки АПК",
                region="federal",
                title="Активный срочный документ АПК",
                url="https://gisp.gov.ru/nmp/measure/999",
                action_level="requires_attention",
                page_type="measure_card",
                days_ago=2,
            ),
            db_path,
        )

        text = telegram.build_command_response("/today", db_path=db_path)

        self.assertIn("Новых срочных документов сегодня нет.", text)
        self.assertIn("Активные срочные вопросы за последние 14 дней: 1. Откройте 🚨 Срочное.", text)

    def test_today_deduplicates_government_news_and_docs_pair(self) -> None:
        db_path = self._db_path("telegram_today_government_dedup.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=250,
                source_name="Правительство РФ - новости",
                region="federal",
                title="Изменения в господдержке экспорта АПК",
                url="http://government.ru/news/58669/",
                action_level="watchlist",
                page_type="news_background",
            ),
            db_path,
        )
        save_document(
            self._doc(
                doc_id=251,
                source_name="Правительство РФ - документы",
                region="federal",
                title="Изменения в господдержке экспорта АПК",
                url="http://government.ru/docs/58669/",
                action_level="watchlist",
                page_type="new_rule",
            ),
            db_path,
        )

        text = telegram.build_command_response("/today", db_path=db_path)

        self.assertEqual(text.count("Изменения в господдержке экспорта"), 1)
        self.assertIn("http://government.ru/docs/58669/", text)
        self.assertNotIn("http://government.ru/news/58669/", text)

    def test_watchlist_command_returns_watchlist_documents(self) -> None:
        db_path = self._db_path("telegram_watchlist.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Пошлина на экспорт пшеницы останется нулевой",
                url="https://www.zol.ru/n/41337",
                action_level="watchlist",
                page_type="news_background",
            ),
            db_path,
        )

        text = telegram.build_command_response("/watchlist", db_path=db_path)

        self.assertIn("Отраслевые сигналы", text)
        self.assertIn("Уровень: наблюдение", text)
        self.assertIn("Пошлина на экспорт пшеницы останется нулевой", text)

    def test_watchlist_command_hides_noisy_zol_market_news(self) -> None:
        db_path = self._db_path("telegram_watchlist_noise.db")
        init_db(db_path)
        noisy = self._doc(
            doc_id=1,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Рейтинг экспортных отгрузок и фрахта на зерновом рынке",
            url="https://www.zol.ru/n/freight-rating",
            action_level="watchlist",
            page_type="news_background",
            summary="Обзор рынка, фрахта и отгрузок без прямого регуляторного сигнала.",
        )
        noisy.business_signal = "Новостной предвестник возможных изменений господдержки, экспорта или регулирования."
        noisy.raw_text = "Еженедельный обзор рынка зерна, ставки фрахта и рейтинг экспортных отгрузок."
        save_document(noisy, db_path)

        text = telegram.build_command_response("/watchlist", db_path=db_path)

        self.assertIn("за 7 дней нет", text)
        self.assertNotIn("Рейтинг экспортных отгрузок", text)

    def test_urgent_hides_foreign_quota_news_without_russia_marker(self) -> None:
        db_path = self._db_path("telegram_urgent_foreign_quota.db")
        init_db(db_path)
        document = self._doc(
            doc_id=199,
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
        save_document(document, db_path)

        text = telegram.build_command_response("/urgent", db_path=db_path)

        self.assertIn("новых документов нет", text)
        self.assertNotIn("Турцию", text)

    def test_urgent_hides_sport_subsidy_but_keeps_agriculture_subsidy(self) -> None:
        db_path = self._db_path("telegram_urgent_ahstep_domain_gate.db")
        init_db(db_path)
        sport_subsidy = self._doc(
            doc_id=205,
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
            doc_id=206,
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
        save_document(sport_subsidy, db_path)
        save_document(agriculture_subsidy, db_path)

        text = telegram.build_command_response("/urgent", db_path=db_path)

        self.assertIn("Найдено документов: 1", text)
        self.assertIn("agro-subsidy.pdf", text)
        self.assertNotIn("sport-subsidy.pdf", text)
        self.assertNotIn("физической культуры", text)
        self.assertNotIn("спорта Краснодарского края", text)

    def test_urgent_regional_npa_uses_stronger_reason_wording(self) -> None:
        db_path = self._db_path("telegram_urgent_regional_reason.db")
        init_db(db_path)
        document = self._doc(
            doc_id=210,
            source_name="Право Ставропольского края",
            region="stavropol",
            title="О внесении изменений в порядок предоставления субсидий сельхозтоваропроизводителям",
            url="https://pravo.stavregion.ru/document/98765",
            action_level="requires_attention",
            page_type="new_rule",
            summary="Изменения порядка предоставления субсидий сельхозтоваропроизводителям.",
        )
        document.business_signal = "Региональный НПА по профильной теме: оставить в наблюдении."
        save_document(document, db_path)

        text = telegram.build_command_response("/urgent", db_path=db_path)

        self.assertIn("- Изменены субсидии в Ставропольском крае", text)
        self.assertIn("Почему важно: Изменены условия субсидирования", text)
        self.assertNotIn("оставить в наблюдении", text)

    def test_period_argument_overrides_default_for_urgent(self) -> None:
        db_path = self._db_path("telegram_urgent_period.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="ГИСП - меры поддержки АПК",
                region="federal",
                title="Новый документ АПК",
                url="https://example.com/new",
                action_level="requires_attention",
                page_type="measure_card",
                days_ago=3,
            ),
            db_path,
        )
        save_document(
            self._doc(
                doc_id=2,
                source_name="ГИСП - меры поддержки АПК",
                region="federal",
                title="Старый документ АПК",
                url="https://example.com/old",
                action_level="requires_attention",
                page_type="measure_card",
                days_ago=20,
            ),
            db_path,
        )

        text_default = telegram.build_command_response("/urgent", db_path=db_path, default_days=7)
        text_30 = telegram.build_command_response("/urgent 30", db_path=db_path, default_days=7)

        self.assertIn("за 7 дней", text_default)
        self.assertIn("Новый документ", text_default)
        self.assertNotIn("Старый документ", text_default)
        self.assertIn("за 30 дней", text_30)
        self.assertIn("Старый документ", text_30)

    def test_watchlist_deduplicates_government_news_and_docs_pair(self) -> None:
        db_path = self._db_path("telegram_watchlist_government_dedup.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=301,
                source_name="Правительство РФ - новости",
                region="federal",
                title="Изменения в господдержке экспорта АПК",
                url="http://government.ru/news/58669/",
                action_level="watchlist",
                page_type="news_background",
            ),
            db_path,
        )
        save_document(
            self._doc(
                doc_id=302,
                source_name="Правительство РФ - документы",
                region="federal",
                title="Изменения в господдержке экспорта АПК",
                url="http://government.ru/docs/58669/",
                action_level="watchlist",
                page_type="new_rule",
            ),
            db_path,
        )

        text = telegram.build_command_response("/watchlist", db_path=db_path)

        self.assertEqual(text.count("Изменения в господдержке экспорта"), 1)
        self.assertIn("http://government.ru/docs/58669/", text)
        self.assertNotIn("http://government.ru/news/58669/", text)

    def test_search_returns_top_results(self) -> None:
        db_path = self._db_path("telegram_search.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="Правительство РФ - документы",
                region="federal",
                title="Мера поддержки экспорта",
                url="https://example.com/a",
                action_level="watchlist",
                page_type="new_rule",
                days_ago=1,
            ).model_copy(update={"raw_text": "Экспорт и логистика"}),
            db_path,
        )
        save_document(
            self._doc(
                doc_id=2,
                source_name="Правительство РФ - новости",
                region="federal",
                title="Новости по экспорту",
                url="https://example.com/b",
                action_level="watchlist",
                page_type="news_background",
                days_ago=0,
            ).model_copy(update={"raw_text": "Экспорт растет"}),
            db_path,
        )

        text = telegram.build_command_response("/search экспорт", db_path=db_path)

        self.assertIn("Результаты поиска", text)
        self.assertIn("Новости по экспорту", text)
        self.assertIn("Мера поддержки экспорта", text)
        self.assertNotIn("Показать ещё (скоро)", text)
        self.assertIn("Показано 2 результатов", text)

    def test_search_prioritizes_requires_attention_over_background(self) -> None:
        db_path = self._db_path("telegram_search_priority.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="ZOL.ru - зерновые новости",
                region="federal",
                title="Льготное кредитование обзор рынка",
                url="https://www.zol.ru/n/1",
                action_level="background",
                page_type="news_background",
            ).model_copy(update={"raw_text": "льготное кредитование в отрасли"}),
            db_path,
        )
        save_document(
            self._doc(
                doc_id=2,
                source_name="ГИСП - меры поддержки АПК",
                region="federal",
                title="Льготное кредитование АПК",
                url="https://gisp.gov.ru/nmp/measure/9564204",
                action_level="requires_attention",
                page_type="measure_card",
            ).model_copy(update={"raw_text": "льготное кредитование"}),
            db_path,
        )
        save_document(
            self._doc(
                doc_id=3,
                source_name="Шумовой источник",
                region="federal",
                title="Льготное кредитование",
                url="https://example.com/irr",
                action_level="irrelevant",
                page_type="reference_page",
            ).model_copy(update={"raw_text": "льготное кредитование"}),
            db_path,
        )

        text = telegram.build_command_response("/search льготное кредитование", db_path=db_path)
        gisp_pos = text.find("Льготное кредитование АПК")
        bg_pos = text.find("Льготное кредитование обзор рынка")
        irr_pos = text.find("Уровень: скрыто")
        self.assertGreaterEqual(gisp_pos, 0)
        self.assertGreaterEqual(bg_pos, 0)
        self.assertLess(gisp_pos, bg_pos)
        self.assertEqual(irr_pos, -1)

    def test_status_command_contains_main_counters(self) -> None:
        db_path = self._db_path("telegram_status.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=1,
                source_name="ГИСП - меры поддержки АПК",
                region="federal",
                title="Льготное кредитование АПК",
                url="https://gisp.gov.ru/nmp/measure/9564204",
                action_level="requires_attention",
                page_type="measure_card",
            ),
            db_path,
        )

        fresh_event = {"event_name": "collect", "updated_at": datetime.now(timezone.utc), "details": "saved=1"}
        with patch("app.notify.telegram.get_runtime_event", return_value=fresh_event):
            text = telegram.build_command_response("/status", db_path=db_path)

        self.assertIn("Состояние AHSTEP GR Monitor", text)
        self.assertIn("Документов в базе: 1", text)
        self.assertIn("требует внимания", text)
        self.assertNotIn("RTZ", text)
        self.assertNotIn("зима", text)
        self.assertNotIn("scheduler-state", text)
        self.assertIn("Данные свежие", text)

    def test_sources_command_includes_enabled_sources(self) -> None:
        db_path = self._db_path("telegram_sources.db")
        init_db(db_path)
        audits = [
            {
                "source_name": "ZOL.ru - зерновые новости",
                "success_at": datetime.now(timezone.utc),
                "error_at": None,
                "error_message": None,
            }
        ]
        with patch("app.notify.telegram.list_latest_source_audit", return_value=audits):
            text = telegram.build_command_response("/sources", db_path=db_path)

        self.assertIn("Проверка источников (", text)
        self.assertIn("ZOL.ru - зерновые новости", text)
        self.assertIn("ГИСП - меры поддержки АПК", text)
        self.assertNotIn("RA=", text)
        self.assertNotIn("WL=", text)
        self.assertNotIn("BG=", text)
        self.assertNotIn("IRR=", text)
        self.assertIn("Последний успешный сбор", text)
        self.assertTrue(("новых публикаций не найдено" in text) or ("работает" in text))

    def test_send_command_response_uses_send_message(self) -> None:
        with patch("app.notify.telegram.send_message", return_value=True) as send_message:
            sent = telegram.send_command_response("/help")

        self.assertTrue(sent)
        self.assertEqual(send_message.call_count, 1)

    def test_empty_urgent_state_is_human_friendly(self) -> None:
        db_path = self._db_path("telegram_urgent_empty.db")
        init_db(db_path)

        text = telegram.build_command_response("/urgent", db_path=db_path)

        self.assertEqual(
            text,
            "🚨 Требует внимания GR: новых документов нет за 7 дней.\nДля общей сводки используйте 📄 Отчёт.",
        )

    def test_report_command_does_not_show_local_report_path(self) -> None:
        text = telegram.build_command_response("/report")

        self.assertIn("GR-сводка", text)
        self.assertIn("Включено в краткую сводку", text)
        self.assertIn("Требует реакции", text)
        self.assertNotIn("сервер", text.lower())
        self.assertNotIn("urgent:", text)
        self.assertNotIn("reports\\", text)
        self.assertNotIn("reports/", text)
        self.assertNotIn("#", text)

    def test_report_period_wording_for_one_day(self) -> None:
        text = telegram.build_command_response("/report 1")
        self.assertIn("Период: сегодня,", text)

    def test_report_today_empty_state_includes_active_urgent_count(self) -> None:
        db_path = self._db_path("telegram_report_today_active_urgent.db")
        init_db(db_path)
        save_document(
            self._doc(
                doc_id=20,
                source_name="ГИСП - меры поддержки АПК",
                region="federal",
                title="Активный срочный документ АПК",
                url="https://gisp.gov.ru/nmp/measure/1001",
                action_level="requires_attention",
                page_type="measure_card",
                days_ago=3,
            ),
            db_path,
        )

        text = telegram.build_command_response("/report today", db_path=db_path)

        self.assertIn("Период: сегодня,", text)
        self.assertIn("Сегодня новых срочных документов нет.", text)
        self.assertIn("Активные срочные вопросы за последние 14 дней: 1. Откройте 🚨 Срочное.", text)

    def test_watchlist_is_limited_for_user_and_has_tail_hint(self) -> None:
        db_path = self._db_path("telegram_watchlist_limit.db")
        init_db(db_path)
        for idx in range(1, 8):
            save_document(
                self._doc(
                    doc_id=idx,
                    source_name="ZOL.ru - зерновые новости",
                    region="federal",
                    title=f"Новость {idx}",
                    url=f"https://www.zol.ru/n/{idx}",
                    action_level="watchlist",
                    page_type="news_background",
                ),
                db_path,
            )

        text = telegram.build_command_response("/watchlist", db_path=db_path)

        self.assertIn("Показано 5 из 7. Для общей сводки нажмите 📄 Отчёт.", text)
        self.assertEqual(text.count("Уровень: наблюдение"), 5)

    def test_sources_does_not_use_old_noise_wording(self) -> None:
        db_path = self._db_path("telegram_sources_wording.db")
        init_db(db_path)

        text = telegram.build_command_response("/sources", db_path=db_path)

        self.assertNotIn("много шума", text)
        self.assertNotIn("⚠️", text)
        self.assertIn("новых публикаций не найдено", text)

    def test_status_ignores_far_future_published_at(self) -> None:
        db_path = self._db_path("telegram_status_future_date.db")
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Документ с ошибочной будущей датой",
            url="https://example.com/future",
            action_level="requires_attention",
            page_type="measure_card",
        )
        document.published_at = datetime.now(timezone.utc) + timedelta(days=365)
        save_document(document, db_path)

        text = telegram.build_command_response("/status", db_path=db_path)
        self.assertNotIn("2027-", text)

    def test_status_handles_missing_and_future_published_at_together(self) -> None:
        db_path = self._db_path("telegram_status_mixed_dates.db")
        init_db(db_path)
        doc_missing = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Документ без даты публикации",
            url="https://example.com/missing",
            action_level="requires_attention",
            page_type="measure_card",
        )
        doc_missing.published_at = None
        save_document(doc_missing, db_path)

        doc_future = self._doc(
            doc_id=2,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Документ с будущей датой",
            url="https://example.com/future2",
            action_level="requires_attention",
            page_type="measure_card",
        )
        doc_future.published_at = datetime.now(timezone.utc) + timedelta(days=400)
        save_document(doc_future, db_path)

        text = telegram.build_command_response("/status", db_path=db_path)
        self.assertIn("Состояние AHSTEP GR Monitor", text)

    def test_sources_hides_raw_exception_details(self) -> None:
        db_path = self._db_path("telegram_sources_errors.db")
        init_db(db_path)
        audits = [
            {
                "source_name": "ZOL.ru - зерновые новости",
                "success_at": None,
                "error_at": datetime.now(timezone.utc),
                "error_message": "HTTPSConnectionPool(host='x'): Max retries exceeded; 403 Client Error",
            }
        ]
        with patch("app.notify.telegram.list_latest_source_audit", return_value=audits):
            text = telegram.build_command_response("/sources", db_path=db_path)

        self.assertIn("временно недоступен", text)
        self.assertNotIn("HTTPSConnectionPool", text)
        self.assertNotIn("Max retries", text)
        self.assertNotIn("Client Error", text)

    def test_sources_does_not_mark_successful_source_unavailable_on_item_errors(self) -> None:
        db_path = self._db_path("telegram_sources_item_errors.db")
        init_db(db_path)
        audits = [
            {
                "source_name": "Минсельхоз Ростовской области - господдержка",
                "success_at": datetime.now(timezone.utc),
                "error_at": datetime.now(timezone.utc),
                "error_message": "Item processing errors: 1",
            }
        ]
        with patch("app.notify.telegram.list_latest_source_audit", return_value=audits):
            text = telegram.build_command_response("/sources", db_path=db_path)

        self.assertNotIn("Минсельхоз Ростовской области - господдержка — временно недоступен", text)
        self.assertIn("Минсельхоз Ростовской области - господдержка", text)

    def test_missing_published_date_is_hidden_from_user(self) -> None:
        db_path = self._db_path("telegram_missing_date.db")
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Документ без даты",
            url="https://example.com/no-date",
            action_level="requires_attention",
            page_type="measure_card",
        )
        document.published_at = None
        save_document(document, db_path)

        text = telegram.build_command_response("/urgent", db_path=db_path)

        self.assertNotIn("Дата: n/a", text)
        self.assertNotIn("n/a", text)

    def test_urgent_hides_news_market_background_false_attention(self) -> None:
        db_path = self._db_path("telegram_urgent_zol_background.db")
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="ZOL.ru - зерновые новости",
            region="federal",
            title="Алжир проводит тендер по закупке пшеницы",
            url="https://www.zol.ru/n/market-algeria-tender",
            action_level="requires_attention",
            page_type="news_background",
        )
        document.business_signal = "Рыночный или отраслевой фон без прямого регуляторного сигнала."
        save_document(document, db_path)

        text = telegram.build_command_response("/urgent", db_path=db_path)

        self.assertIn("новых документов нет", text)
        self.assertNotIn("Алжир проводит тендер", text)

    def test_telegram_lists_use_ocr_fallback_title(self) -> None:
        db_path = self._db_path("telegram_ocr_title_cleanup.db")
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="document 'wgketjm9pqmq00n60yoyq8c0z2u13z38_cfa07cc203.pdf' requires OCR extraction",
            url="https://admkrai.krasnodar.ru/upload/wgketjm9pqmq00n60yoyq8c0z2u13z38_cfa07cc203.pdf",
            action_level="requires_attention",
            page_type="new_rule",
        )
        document.raw_text = "Распознанный текст отсутствует."
        save_document(document, db_path)

        text = telegram.build_command_response("/urgent", db_path=db_path)

        self.assertIn("новых документов нет", text)
        self.assertNotIn("НПА Краснодарского края: документ после OCR", text)
        self.assertNotIn("requires OCR extraction", text)

    def test_urgent_hides_fallback_titled_weak_ocr_placeholder(self) -> None:
        db_path = self._db_path("telegram_fallback_titled_ocr_placeholder.db")
        init_db(db_path)
        document = self._doc(
            doc_id=2,
            source_name="Нормативные акты Краснодарского края",
            region="krasnodar",
            title="НПА Краснодарского края: документ после OCR",
            url="https://admkrai.krasnodar.ru/upload/iblock/d69/fallback-ocr.pdf",
            action_level="requires_attention",
            page_type="new_rule",
        )
        document.raw_text = (
            "Документ после OCR требует ручной проверки. Распознанный текст частично отсутствует, "
            "структура фрагментарна и не позволяет уверенно выделить условия меры."
        )
        save_document(document, db_path)

        text = telegram.build_command_response("/urgent", db_path=db_path)

        self.assertIn("новых документов нет", text)
        self.assertNotIn("НПА Краснодарского края: документ после OCR", text)

    def test_long_message_is_split_to_multiple_chunks(self) -> None:
        with patch.multiple(
            telegram.config,
            TELEGRAM_BOT_TOKEN="token",
            TELEGRAM_CHAT_ID="chat-id",
            TELEGRAM_PROXY_URL="",
            TELEGRAM_API_TIMEOUT=15,
        ):
            response = Mock()
            response.raise_for_status.return_value = None
            with patch("app.notify.telegram.requests.post", return_value=response) as post:
                sent = telegram.send_message("x" * 9000)

        self.assertTrue(sent)
        self.assertGreaterEqual(post.call_count, 3)

    def test_telegram_exception_message_redacts_tokenized_url(self) -> None:
        raw = "HTTPSConnectionPool('https://api.telegram.org/bot123:ABCDEF/sendMessage')"
        sanitized = telegram.sanitize_telegram_exception_message(RuntimeError(raw))
        self.assertIn("bot<redacted>/sendMessage", sanitized)
        self.assertNotIn("bot123:ABCDEF", sanitized)

    def test_track_existing_url_creates_item(self) -> None:
        db_path = self._db_path("telegram_track_create.db")
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
        )
        save_document(document, db_path)

        text = telegram.build_command_response(
            "/track https://gisp.gov.ru/nmp/measure/9564204",
            db_path=db_path,
            chat_id="123",
        )

        self.assertIn("добавлен", text.lower())
        active = list_active_tracking_items(chat_id="123", db_path=db_path)
        self.assertEqual(len(active), 1)

    def test_track_duplicate_is_handled(self) -> None:
        db_path = self._db_path("telegram_track_duplicate.db")
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
        )
        save_document(document, db_path)
        telegram.build_command_response(
            "/track https://gisp.gov.ru/nmp/measure/9564204",
            db_path=db_path,
            chat_id="123",
        )
        text = telegram.build_command_response(
            "/track https://gisp.gov.ru/nmp/measure/9564204",
            db_path=db_path,
            chat_id="123",
        )
        self.assertIn("уже", text.lower())

    def test_untrack_deactivates_item(self) -> None:
        db_path = self._db_path("telegram_untrack.db")
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
        )
        save_document(document, db_path)
        telegram.build_command_response(
            "/track https://gisp.gov.ru/nmp/measure/9564204",
            db_path=db_path,
            chat_id="123",
        )
        text = telegram.build_command_response(
            "/untrack https://gisp.gov.ru/nmp/measure/9564204",
            db_path=db_path,
            chat_id="123",
        )
        self.assertIn("убран", text.lower())
        active = list_active_tracking_items(chat_id="123", db_path=db_path)
        self.assertEqual(len(active), 0)

    def test_tracked_lists_active_items(self) -> None:
        db_path = self._db_path("telegram_tracked_list.db")
        init_db(db_path)
        document = self._doc(
            doc_id=1,
            source_name="ГИСП - меры поддержки АПК",
            region="federal",
            title="Льготное кредитование АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
            action_level="requires_attention",
            page_type="measure_card",
        )
        save_document(document, db_path)
        telegram.build_command_response(
            "/track https://gisp.gov.ru/nmp/measure/9564204",
            db_path=db_path,
            chat_id="123",
        )
        text = telegram.build_command_response("/tracked", db_path=db_path, chat_id="123")
        self.assertIn("Отслеживаемые документы", text)
        self.assertIn("Льготное кредитование АПК", text)


if __name__ == "__main__":
    unittest.main()
