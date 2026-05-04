from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from app.extractors.date_extractor import (
    extract_published_at_from_link_tag,
    extract_published_at_from_html,
    infer_published_at,
    normalize_date_to_iso,
    parse_russian_date,
)


class DateExtractorSmokeTest(unittest.TestCase):
    def test_parse_russian_month_date(self) -> None:
        parsed = parse_russian_date("3 мая 2026")
        self.assertIsNotNone(parsed)
        self.assertEqual(normalize_date_to_iso(parsed), "2026-05-03")

    def test_parse_iso_date(self) -> None:
        parsed = parse_russian_date("2026-05-03")
        self.assertIsNotNone(parsed)
        self.assertEqual(normalize_date_to_iso(parsed), "2026-05-03")

    def test_extract_date_from_meta_or_time_tag(self) -> None:
        html = """
        <html>
          <head>
            <meta property="article:published_time" content="2026-05-03T10:15:00+03:00" />
          </head>
          <body>
            <time datetime="2026-05-03">3 мая 2026</time>
          </body>
        </html>
        """
        parsed = extract_published_at_from_html(
            html,
            "Правительство РФ - новости",
            "https://government.ru/news/12345/",
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(normalize_date_to_iso(parsed), "2026-05-03")

    def test_deadline_is_not_accepted_as_published_at(self) -> None:
        inferred = infer_published_at(
            title="Конкурсный отбор",
            raw_text="Прием заявок до 30.06.2026. Конкурсный отбор для участников.",
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/123",
        )
        self.assertIsNone(inferred)

    def test_terms_text_is_not_accepted_as_published_at(self) -> None:
        inferred = infer_published_at(
            title="Льготное кредитование АПК",
            raw_text="Срок кредита: До 12 месяцев. Сумма: до 1 млрд рублей.",
            source_name="ГИСП - меры поддержки АПК",
            url="https://gisp.gov.ru/nmp/measure/9564204",
        )
        self.assertIsNone(inferred)

    def test_news_like_raw_text_allows_leading_publication_date(self) -> None:
        inferred = infer_published_at(
            title="Мишустин рассказал о поддержке АПК Северного Кавказа",
            raw_text=(
                "Мишустин рассказал о поддержке АПК Северного Кавказа "
                "30 апреля 2026 16:00 Более 36 млрд рублей направили на поддержку АПК."
            ),
            source_name="ZOL.ru - зерновые новости",
            url="https://www.zol.ru/n/41330",
        )
        self.assertIsNotNone(inferred)
        self.assertEqual(normalize_date_to_iso(inferred), "2026-04-30")

    def test_krasnodar_link_publication_marker_extracts_published_at(self) -> None:
        soup = BeautifulSoup(
            """
            <article>
              <span>Дата публикации: 02.05.2026</span>
              <a href="/upload/order.pdf">О внесении изменений в приказ</a>
            </article>
            """,
            "html.parser",
        )
        link = soup.find("a")
        self.assertIsNotNone(link)

        parsed = extract_published_at_from_link_tag(
            link,  # type: ignore[arg-type]
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/upload/order.pdf",
        )

        self.assertEqual(normalize_date_to_iso(parsed), "2026-05-02")

    def test_krasnodar_link_npa_and_deadline_dates_are_not_published_at(self) -> None:
        soup = BeautifulSoup(
            """
            <article>
              <span>Приказ от 30.04.2026. Прием заявок до 20.05.2026.</span>
              <a href="/upload/order.pdf">О внесении изменений в приказ</a>
            </article>
            """,
            "html.parser",
        )
        link = soup.find("a")
        self.assertIsNotNone(link)

        parsed = extract_published_at_from_link_tag(
            link,  # type: ignore[arg-type]
            source_name="Нормативные акты Краснодарского края",
            url="https://admkrai.krasnodar.ru/upload/order.pdf",
        )

        self.assertIsNone(parsed)


if __name__ == "__main__":
    unittest.main()
