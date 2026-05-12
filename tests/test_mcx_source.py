from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.models import SourceConfig
from app.sources.mcx_source import McxSource

_MEASURES_CONFIG = SourceConfig(
    name="Минсельхоз России - меры господдержки",
    url="https://mcx.gov.ru/activity/state-support/measures/",
    level="federal",
    region="federal",
    source_role="support_documents",
    parser="mcx",
    max_items=10,
    description="test",
)

_NEWS_CONFIG = SourceConfig(
    name="Минсельхоз России - новости",
    url="https://mcx.gov.ru/press-service/news/",
    level="federal",
    region="federal",
    source_role="news_signals",
    parser="mcx",
    max_items=10,
    description="test",
)

_MEASURES_HTML = """
<html><body>
<ul>
  <li class="b-siteNavListMobile-3lvl__item">
    <a class="b-siteNavListMobile-3lvl__link"
       href="/activity/state-support/measures/льготное-кредитование/">
      Льготное кредитование по СПК
    </a>
  </li>
  <li class="b-siteNavListMobile-3lvl__item">
    <a class="b-siteNavListMobile-3lvl__link"
       href="/activity/state-support/measures/obedinennaiya-subsidiya/">
      Объединённая субсидия
    </a>
  </li>
  <li class="b-siteNavListMobile-3lvl__item">
    <a class="b-siteNavListMobile-3lvl__link"
       href="/activity/state-support/measures/lgotnyy-lizing/">
      Льготный лизинг
    </a>
  </li>
</ul>
</body></html>
"""

_MEASURES_HTML_EMPTY_HREF = """
<html><body>
<ul>
  <li class="b-siteNavListMobile-3lvl__item">
    <a class="b-siteNavListMobile-3lvl__link" href="">Пустая ссылка</a>
  </li>
  <li class="b-siteNavListMobile-3lvl__item">
    <a class="b-siteNavListMobile-3lvl__link"
       href="/activity/state-support/measures/льготный-лизинг/">
      Льготный лизинг
    </a>
  </li>
</ul>
</body></html>
"""

_MEASURES_HTML_EMPTY_TITLE = """
<html><body>
<ul>
  <li class="b-siteNavListMobile-3lvl__item">
    <a class="b-siteNavListMobile-3lvl__link"
       href="/activity/state-support/measures/some-measure/">   </a>
  </li>
  <li class="b-siteNavListMobile-3lvl__item">
    <a class="b-siteNavListMobile-3lvl__link"
       href="/activity/state-support/measures/льготный-лизинг/">
      Льготный лизинг
    </a>
  </li>
</ul>
</body></html>
"""

_NEWS_HTML = """
<html><body>
<ul>
  <li class="newsList__item">
    12 мая 2026
    <a class="newsList__title"
       href="/press-service/news/obyem-realizatsii-moloka-vyros-117720/">
      Объём реализации молока в сельхозорганизациях вырос на 2,6%
    </a>
  </li>
  <li class="newsList__item">
    11 мая 2026
    <a class="newsList__title"
       href="/press-service/news/subsidii-na-apk-117719/">
      Субсидии на поддержку АПК увеличены
    </a>
  </li>
</ul>
</body></html>
"""

_NEWS_HTML_NO_DATE = """
<html><body>
<ul>
  <li class="newsList__item">
    <a class="newsList__title"
       href="/press-service/news/novost-bez-daty-117718/">
      Новость без даты
    </a>
  </li>
</ul>
</body></html>
"""

_NEWS_HTML_NO_TITLE_TAG = """
<html><body>
<ul>
  <li class="newsList__item">
    12 мая 2026
    <a href="/press-service/news/no-class-117717/">Нет класса newsList__title</a>
  </li>
  <li class="newsList__item">
    10 мая 2026
    <a class="newsList__title"
       href="/press-service/news/normalnaya-117716/">
      Нормальная новость
    </a>
  </li>
</ul>
</body></html>
"""


def _mock_response(html: str) -> MagicMock:
    mock = MagicMock()
    mock.text = html
    return mock


class McxSourceMeasuresTest(unittest.TestCase):

    def _source(self, max_items: int | None = 10) -> McxSource:
        config = SourceConfig(
            name=_MEASURES_CONFIG.name,
            url=_MEASURES_CONFIG.url,
            level=_MEASURES_CONFIG.level,
            region=_MEASURES_CONFIG.region,
            source_role=_MEASURES_CONFIG.source_role,
            parser="mcx",
            max_items=max_items,
            description="test",
        )
        return McxSource(config)

    def test_measures_items_extracted(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_MEASURES_HTML)):
            items = source.fetch_items()
        self.assertEqual(len(items), 3)

    def test_measures_titles_correct(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_MEASURES_HTML)):
            items = source.fetch_items()
        titles = [item.title for item in items]
        self.assertIn("Льготное кредитование по СПК", titles)
        self.assertIn("Объединённая субсидия", titles)
        self.assertIn("Льготный лизинг", titles)

    def test_measures_urls_absolute(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_MEASURES_HTML)):
            items = source.fetch_items()
        for item in items:
            self.assertTrue(item.url.startswith("https://mcx.gov.ru"), item.url)

    def test_measures_raw_text_populated(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_MEASURES_HTML)):
            items = source.fetch_items()
        for item in items:
            self.assertIsNotNone(item.raw_text)
            self.assertGreater(len(item.raw_text or ""), 0)

    def test_measures_raw_text_contains_label(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_MEASURES_HTML)):
            items = source.fetch_items()
        self.assertIn("Мера господдержки АПК", items[0].raw_text or "")

    def test_measures_raw_text_contains_title(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_MEASURES_HTML)):
            items = source.fetch_items()
        self.assertIn("Льготное кредитование по СПК", items[0].raw_text or "")

    def test_measures_raw_text_contains_url(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_MEASURES_HTML)):
            items = source.fetch_items()
        self.assertIn("Источник:", items[0].raw_text or "")
        self.assertIn("mcx.gov.ru", items[0].raw_text or "")

    def test_measures_no_published_at(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_MEASURES_HTML)):
            items = source.fetch_items()
        for item in items:
            self.assertIsNone(item.published_at)

    def test_measures_document_type_html(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_MEASURES_HTML)):
            items = source.fetch_items()
        for item in items:
            self.assertEqual(item.document_type, "html")

    def test_measures_max_items_respected(self) -> None:
        source = self._source(max_items=2)
        with patch.object(source, "get", return_value=_mock_response(_MEASURES_HTML)):
            items = source.fetch_items()
        self.assertEqual(len(items), 2)

    def test_measures_skips_empty_href(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_MEASURES_HTML_EMPTY_HREF)):
            items = source.fetch_items()
        self.assertEqual(len(items), 1)
        self.assertIn("Льготный лизинг", items[0].title)

    def test_measures_skips_empty_title(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_MEASURES_HTML_EMPTY_TITLE)):
            items = source.fetch_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Льготный лизинг")

    def test_measures_only_one_http_request(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_MEASURES_HTML)) as mock_get:
            source.fetch_items()
        self.assertEqual(mock_get.call_count, 1)

    def test_measures_source_metadata(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_MEASURES_HTML)):
            items = source.fetch_items()
        self.assertEqual(items[0].source_name, _MEASURES_CONFIG.name)
        self.assertEqual(items[0].level, "federal")
        self.assertEqual(items[0].region, "federal")

    def test_measures_empty_page_returns_nothing(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response("<html><body></body></html>")):
            items = source.fetch_items()
        self.assertEqual(items, [])


class McxSourceNewsTest(unittest.TestCase):

    def _source(self, max_items: int | None = 10) -> McxSource:
        config = SourceConfig(
            name=_NEWS_CONFIG.name,
            url=_NEWS_CONFIG.url,
            level=_NEWS_CONFIG.level,
            region=_NEWS_CONFIG.region,
            source_role=_NEWS_CONFIG.source_role,
            parser="mcx",
            max_items=max_items,
            description="test",
        )
        return McxSource(config)

    def test_news_items_extracted(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_NEWS_HTML)):
            items = source.fetch_items()
        self.assertEqual(len(items), 2)

    def test_news_titles_correct(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_NEWS_HTML)):
            items = source.fetch_items()
        titles = [item.title for item in items]
        self.assertIn("Объём реализации молока в сельхозорганизациях вырос на 2,6%", titles)
        self.assertIn("Субсидии на поддержку АПК увеличены", titles)

    def test_news_urls_absolute(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_NEWS_HTML)):
            items = source.fetch_items()
        for item in items:
            self.assertTrue(item.url.startswith("https://mcx.gov.ru"), item.url)

    def test_news_raw_text_populated(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_NEWS_HTML)):
            items = source.fetch_items()
        for item in items:
            self.assertIsNotNone(item.raw_text)
            self.assertGreater(len(item.raw_text or ""), 0)

    def test_news_raw_text_contains_label(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_NEWS_HTML)):
            items = source.fetch_items()
        self.assertIn("Новость АПК", items[0].raw_text or "")

    def test_news_raw_text_contains_title(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_NEWS_HTML)):
            items = source.fetch_items()
        self.assertIn("Объём реализации молока", items[0].raw_text or "")

    def test_news_date_parsed(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_NEWS_HTML)):
            items = source.fetch_items()
        expected = datetime(2026, 5, 12, tzinfo=timezone.utc)
        self.assertEqual(items[0].published_at, expected)

    def test_news_date_in_raw_text(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_NEWS_HTML)):
            items = source.fetch_items()
        self.assertIn("Дата: 12.05.2026", items[0].raw_text or "")

    def test_news_no_date_item(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_NEWS_HTML_NO_DATE)):
            items = source.fetch_items()
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0].published_at)
        self.assertNotIn("Дата:", items[0].raw_text or "")

    def test_news_max_items_respected(self) -> None:
        source = self._source(max_items=1)
        with patch.object(source, "get", return_value=_mock_response(_NEWS_HTML)):
            items = source.fetch_items()
        self.assertEqual(len(items), 1)

    def test_news_skips_item_without_title_link(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_NEWS_HTML_NO_TITLE_TAG)):
            items = source.fetch_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Нормальная новость")

    def test_news_only_one_http_request(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_NEWS_HTML)) as mock_get:
            source.fetch_items()
        self.assertEqual(mock_get.call_count, 1)

    def test_news_document_type_html(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_NEWS_HTML)):
            items = source.fetch_items()
        for item in items:
            self.assertEqual(item.document_type, "html")

    def test_news_source_metadata(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response(_NEWS_HTML)):
            items = source.fetch_items()
        self.assertEqual(items[0].source_name, _NEWS_CONFIG.name)
        self.assertEqual(items[0].level, "federal")
        self.assertEqual(items[0].region, "federal")

    def test_news_empty_page_returns_nothing(self) -> None:
        source = self._source()
        with patch.object(source, "get", return_value=_mock_response("<html><body></body></html>")):
            items = source.fetch_items()
        self.assertEqual(items, [])


class McxSourceUnknownUrlTest(unittest.TestCase):

    def test_unknown_url_returns_empty(self) -> None:
        config = SourceConfig(
            name="MCX Unknown",
            url="https://mcx.gov.ru/some/other/page/",
            level="federal",
            region="federal",
            source_role="strategy",
            parser="mcx",
            max_items=10,
            description="test",
        )
        source = McxSource(config)
        with patch.object(source, "get", return_value=_mock_response(_MEASURES_HTML)):
            items = source.fetch_items()
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
