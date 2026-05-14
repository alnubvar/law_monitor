from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.models import SourceConfig
from app.sources.regulation_gov_source import RegulationGovSource, _build_synthetic_text, _parse_iso_date

_SOURCE_CONFIG = SourceConfig(
    name="Regulation.gov.ru",
    url="https://regulation.gov.ru/",
    level="federal",
    region="federal",
    source_role="strategy",
    parser="regulation_gov",
    max_items=20,
    description="test",
)

_XML_MULTIPLE = """<?xml version="1.0" encoding="utf-8"?>
<projects offset="0" limit="5" total="125826">
  <project id="167857">
    <title>Об утверждении административного регламента предоставления услуги</title>
    <projectId>04/15/05-26/00167857</projectId>
    <date>2026-05-12T08:47:30.34Z</date>
    <publishDate>2026-05-12T08:49:21.343Z</publishDate>
    <stage id="10">Разработка</stage>
    <department>МЭР</department>
  </project>
  <project id="167856">
    <title>О внесении изменений в постановление Минсельхоза об АПК</title>
    <projectId>02/07/05-26/00167856</projectId>
    <date>2026-05-12T08:44:26.214Z</date>
    <publishDate>2026-05-12T09:07:48.87Z</publishDate>
    <stage id="20">Обсуждение</stage>
    <department>Минсельхоз России</department>
  </project>
  <project id="167855">
    <title>Об утверждении порядка предоставления субсидий сельхозпроизводителям</title>
    <projectId>01/02/05-26/00167855</projectId>
    <date>2026-05-12T08:38:04.922Z</date>
    <publishDate>2026-05-12T08:43:42.585Z</publishDate>
    <stage id="10">Разработка</stage>
    <department>Минсельхоз России</department>
  </project>
</projects>
""".encode("utf-8")

_XML_NO_PUBLISHDATE = """<?xml version="1.0" encoding="utf-8"?>
<projects offset="0" limit="2" total="10">
  <project id="100">
    <title>Проект без даты публикации</title>
    <date>2026-01-15T10:00:00Z</date>
  </project>
  <project id="101">
    <title>Второй проект без publishDate</title>
  </project>
</projects>
""".encode("utf-8")

_XML_SKIP_BAD = """<?xml version="1.0" encoding="utf-8"?>
<projects offset="0" limit="3" total="3">
  <project id="">
    <title>Проект с пустым id</title>
    <publishDate>2026-05-01T12:00:00Z</publishDate>
  </project>
  <project id="200">
    <title></title>
    <publishDate>2026-05-01T12:00:00Z</publishDate>
  </project>
  <project id="201">
    <title>   </title>
    <publishDate>2026-05-01T12:00:00Z</publishDate>
  </project>
</projects>
""".encode("utf-8")

_XML_MAX_ITEMS = """<?xml version="1.0" encoding="utf-8"?>
<projects offset="0" limit="5" total="50">
  <project id="1"><title>Item one</title><publishDate>2026-01-01T00:00:00Z</publishDate></project>
  <project id="2"><title>Item two</title><publishDate>2026-01-02T00:00:00Z</publishDate></project>
  <project id="3"><title>Item three</title><publishDate>2026-01-03T00:00:00Z</publishDate></project>
  <project id="4"><title>Item four</title><publishDate>2026-01-04T00:00:00Z</publishDate></project>
  <project id="5"><title>Item five</title><publishDate>2026-01-05T00:00:00Z</publishDate></project>
</projects>
""".encode("utf-8")

_XML_PAGE_ONE = """<?xml version="1.0" encoding="utf-8"?>
<projects offset="0" limit="2" total="5">
  <project id="1"><title>Item one</title><publishDate>2026-01-01T00:00:00Z</publishDate></project>
  <project id="2"><title>Item two</title><publishDate>2026-01-02T00:00:00Z</publishDate></project>
</projects>
""".encode("utf-8")

_XML_PAGE_TWO = """<?xml version="1.0" encoding="utf-8"?>
<projects offset="2" limit="2" total="5">
  <project id="3"><title>Item three</title><publishDate>2026-01-03T00:00:00Z</publishDate></project>
  <project id="4"><title>Item four</title><publishDate>2026-01-04T00:00:00Z</publishDate></project>
</projects>
""".encode("utf-8")

_XML_DUPLICATE_PAGE = """<?xml version="1.0" encoding="utf-8"?>
<projects offset="1" limit="1" total="5">
  <project id="1"><title>Item one</title><publishDate>2026-01-01T00:00:00Z</publishDate></project>
</projects>
""".encode("utf-8")

_XML_MALFORMED = b"<projects><project id=1>broken xml"


def _make_source(max_items: int | None = 20) -> RegulationGovSource:
    config = SourceConfig(
        name=_SOURCE_CONFIG.name,
        url=_SOURCE_CONFIG.url,
        level=_SOURCE_CONFIG.level,
        region=_SOURCE_CONFIG.region,
        source_role=_SOURCE_CONFIG.source_role,
        parser="regulation_gov",
        max_items=max_items,
        description="test",
    )
    return RegulationGovSource(config)


def _mock_response(content: bytes) -> MagicMock:
    mock = MagicMock()
    mock.content = content
    mock.text = content.decode("utf-8")
    return mock


def _xml_page(*, offset: int, limit: int, total: int, start_id: int, count: int) -> bytes:
    projects = []
    for index in range(count):
        item_id = start_id + index
        projects.append(
            f'<project id="{item_id}"><title>Item {item_id}</title>'
            f"<publishDate>2026-01-{(item_id % 28) + 1:02d}T00:00:00Z</publishDate></project>"
        )
    xml = (
        f'<?xml version="1.0" encoding="utf-8"?>\n'
        f'<projects offset="{offset}" limit="{limit}" total="{total}">\n'
        + "\n".join(projects)
        + "\n</projects>"
    )
    return xml.encode("utf-8")


class RegulationGovSourceTest(unittest.TestCase):

    def test_multiple_projects_parsed(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_XML_MULTIPLE)):
            items = source.fetch_items()
        self.assertEqual(len(items), 3)

    def test_urls_use_project_id(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_XML_MULTIPLE)):
            items = source.fetch_items()
        self.assertEqual(items[0].url, "https://regulation.gov.ru/projects/167857")
        self.assertEqual(items[1].url, "https://regulation.gov.ru/projects/167856")
        self.assertEqual(items[2].url, "https://regulation.gov.ru/projects/167855")

    def test_titles_preserved(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_XML_MULTIPLE)):
            items = source.fetch_items()
        self.assertEqual(items[0].title, "Об утверждении административного регламента предоставления услуги")
        self.assertIn("АПК", items[1].title)

    def test_published_at_parsed_from_publishdate(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_XML_MULTIPLE)):
            items = source.fetch_items()
        expected = datetime(2026, 5, 12, 8, 49, 21, tzinfo=timezone.utc)
        self.assertEqual(items[0].published_at, expected)

    def test_published_at_none_when_publishdate_missing(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_XML_NO_PUBLISHDATE)):
            items = source.fetch_items()
        self.assertEqual(len(items), 2)
        self.assertIsNone(items[0].published_at)
        self.assertIsNone(items[1].published_at)

    def test_source_metadata_from_config(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_XML_MULTIPLE)):
            items = source.fetch_items()
        for item in items:
            self.assertEqual(item.source_name, "Regulation.gov.ru")
            self.assertEqual(item.source_url, "https://regulation.gov.ru/")
            self.assertEqual(item.level, "federal")
            self.assertEqual(item.region, "federal")
            self.assertEqual(item.document_type, "html")

    def test_skips_empty_id(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_XML_SKIP_BAD)):
            items = source.fetch_items()
        self.assertEqual(items, [])

    def test_skips_empty_title(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_XML_SKIP_BAD)):
            items = source.fetch_items()
        self.assertEqual(items, [])

    def test_max_items_limits_result(self) -> None:
        source = _make_source(max_items=3)
        with patch.object(source, "get", return_value=_mock_response(_XML_MAX_ITEMS)) as mock_get:
            items = source.fetch_items()
        called_url = mock_get.call_args[0][0]
        self.assertIn("limit=3", called_url)
        self.assertIn("offset=0", called_url)
        self.assertEqual(len(items), 3)

    def test_malformed_xml_returns_empty(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_XML_MALFORMED)):
            items = source.fetch_items()
        self.assertEqual(items, [])

    def test_api_url_includes_sort_desc(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_XML_MULTIPLE)) as mock_get:
            source.fetch_items()
        called_url = mock_get.call_args[0][0]
        self.assertIn("sort=desc", called_url)
        self.assertIn("api/npalist", called_url)
        self.assertIn("offset=0", called_url)

    def test_raw_text_is_populated_for_all_items(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_XML_MULTIPLE)):
            items = source.fetch_items()
        for item in items:
            self.assertIsNotNone(item.raw_text)
            self.assertGreater(len(item.raw_text or ""), 0)

    def test_raw_text_contains_title_and_department(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_XML_MULTIPLE)):
            items = source.fetch_items()
        # Second item has Минсельхоз России
        self.assertIn("АПК", items[1].raw_text or "")
        self.assertIn("Минсельхоз России", items[1].raw_text or "")

    def test_different_projects_produce_different_raw_text(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_XML_MULTIPLE)):
            items = source.fetch_items()
        texts = [item.raw_text for item in items]
        self.assertEqual(len(texts), len(set(texts)), "Each project must produce unique raw_text")

    def test_raw_text_contains_stage(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_XML_MULTIPLE)):
            items = source.fetch_items()
        self.assertIn("Разработка", items[0].raw_text or "")
        self.assertIn("Обсуждение", items[1].raw_text or "")

    def test_raw_text_still_set_when_optional_fields_absent(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_XML_NO_PUBLISHDATE)):
            items = source.fetch_items()
        for item in items:
            self.assertIsNotNone(item.raw_text)
            self.assertIn("Проект НПА:", item.raw_text or "")

    def test_date_fields_formatted_as_ddmmyyyy_not_iso(self) -> None:
        xml_with_dates = """<?xml version="1.0" encoding="utf-8"?>
<projects offset="0" limit="1" total="1">
  <project id="999">
    <title>Проект с датами обсуждения</title>
    <publishDate>2026-05-12T08:49:21.343Z</publishDate>
    <startDiscussion>2026-05-13T10:00:00.000Z</startDiscussion>
    <endDiscussion>2026-05-26T23:59:59.000Z</endDiscussion>
  </project>
</projects>""".encode("utf-8")
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(xml_with_dates)):
            items = source.fetch_items()
        raw_text = items[0].raw_text or ""
        self.assertIn("12.05.2026", raw_text)
        self.assertIn("13.05.2026", raw_text)
        self.assertIn("26.05.2026", raw_text)
        self.assertNotIn("2026-05-12T", raw_text)
        self.assertNotIn("2026-05-13T", raw_text)
        self.assertNotIn("2026-05-26T", raw_text)

    def test_fetch_paginates_with_offset_until_max_items_reached(self) -> None:
        source = _make_source(max_items=25)
        responses = [
            _mock_response(_xml_page(offset=0, limit=20, total=40, start_id=1, count=20)),
            _mock_response(_xml_page(offset=20, limit=5, total=40, start_id=21, count=5)),
        ]
        with patch.object(source, "get", side_effect=responses) as mock_get:
            items = source.fetch_items()
        self.assertEqual(len(items), 25)
        called_urls = [call.args[0] for call in mock_get.call_args_list]
        self.assertEqual(
            called_urls,
            [
                "https://regulation.gov.ru/api/npalist?limit=20&offset=0&sort=desc",
                "https://regulation.gov.ru/api/npalist?limit=5&offset=20&sort=desc",
            ],
        )

    def test_fetch_uses_second_page_when_first_page_returns_fewer_new_items(self) -> None:
        source = _make_source(max_items=22)
        first_page_projects = []
        for item_id in range(1, 21):
            if item_id in {3, 17}:
                first_page_projects.append('<project id=""><title>Broken item</title></project>')
                continue
            first_page_projects.append(
                f'<project id="{item_id}"><title>Item {item_id}</title>'
                f"<publishDate>2026-01-{(item_id % 28) + 1:02d}T00:00:00Z</publishDate></project>"
            )
        first_page = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<projects offset="0" limit="20" total="40">\n'
            + "\n".join(first_page_projects)
            + "\n</projects>"
        ).encode("utf-8")
        second_page = """<?xml version="1.0" encoding="utf-8"?>
<projects offset="20" limit="4" total="40">
  <project id="21"><title>Item twenty one</title><publishDate>2026-01-21T00:00:00Z</publishDate></project>
  <project id="22"><title>Item twenty two</title><publishDate>2026-01-22T00:00:00Z</publishDate></project>
</projects>
""".encode("utf-8")
        with patch.object(source, "get", side_effect=[_mock_response(first_page), _mock_response(second_page)]) as mock_get:
            items = source.fetch_items()
        self.assertEqual(len(items), 20)
        called_urls = [call.args[0] for call in mock_get.call_args_list]
        self.assertEqual(
            called_urls,
            [
                "https://regulation.gov.ru/api/npalist?limit=20&offset=0&sort=desc",
                "https://regulation.gov.ru/api/npalist?limit=4&offset=20&sort=desc",
            ],
        )
        self.assertEqual(items[-1].url, "https://regulation.gov.ru/projects/22")

    def test_fetch_stops_when_page_returns_no_new_items(self) -> None:
        source = _make_source(max_items=25)
        with patch.object(
            source,
            "get",
            side_effect=[
                _mock_response(_xml_page(offset=0, limit=20, total=40, start_id=1, count=20)),
                _mock_response(_XML_DUPLICATE_PAGE),
            ],
        ) as mock_get:
            items = source.fetch_items()
        self.assertEqual(len(items), 20)
        self.assertEqual(mock_get.call_count, 2)
        called_urls = [call.args[0] for call in mock_get.call_args_list]
        self.assertEqual(
            called_urls,
            [
                "https://regulation.gov.ru/api/npalist?limit=20&offset=0&sort=desc",
                "https://regulation.gov.ru/api/npalist?limit=5&offset=20&sort=desc",
            ],
        )


class ParseIsoDateTest(unittest.TestCase):

    def test_standard_with_milliseconds_z(self) -> None:
        result = _parse_iso_date("2026-05-12T08:49:21.343Z")
        self.assertEqual(result, datetime(2026, 5, 12, 8, 49, 21, tzinfo=timezone.utc))

    def test_standard_with_two_decimal_z(self) -> None:
        result = _parse_iso_date("2026-05-12T09:07:48.87Z")
        self.assertEqual(result, datetime(2026, 5, 12, 9, 7, 48, tzinfo=timezone.utc))

    def test_no_fractional_seconds(self) -> None:
        result = _parse_iso_date("2026-01-15T10:00:00Z")
        self.assertEqual(result, datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(_parse_iso_date(""))

    def test_whitespace_only_returns_none(self) -> None:
        self.assertIsNone(_parse_iso_date("   "))

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(_parse_iso_date("not-a-date"))

    def test_result_is_utc(self) -> None:
        result = _parse_iso_date("2026-03-01T12:00:00Z")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
