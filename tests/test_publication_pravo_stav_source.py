from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

from app.models import SourceConfig
from app.sources.publication_pravo_stav_source import (
    PublicationPravoStavropolSource,
    _STAVROPOL_AUTHORITIES,
    _build_synthetic_text,
    _parse_date_field,
)

_SOURCE_CONFIG = SourceConfig(
    name="Официальные акты Ставропольского края (НПА)",
    url="http://publication.pravo.gov.ru/api/Documents",
    level="regional",
    region="stavropol",
    source_role="regional_npa",
    parser="publication_pravo_stav",
    max_items=40,
    description="test",
)

_DOC_PRAVITELSTVO = {
    "eoNumber": "2600202605120010",
    "complexName": "Постановление Правительства Ставропольского края от 08.05.2026 № 237-п «Об оказании мер социальной поддержки»",
    "name": "Об оказании мер социальной поддержки",
    "number": "237-п",
    "documentDate": "2026-05-08T00:00:00",
    "publishDateShort": "2026-05-12T00:00:00",
    "pagesCount": 2,
    "signatoryAuthorityId": "3d93f00f-1af0-4669-8f98-0bff04215eb3",
}

_DOC_GUBERNATOR = {
    "eoNumber": "2600202605070001",
    "complexName": "Постановление Губернатора Ставропольского края от 07.05.2026 № 259 «О внесении изменений»",
    "name": "О внесении изменений",
    "number": "259",
    "documentDate": "2026-05-07T00:00:00",
    "publishDateShort": "2026-05-07T00:00:00",
    "pagesCount": 1,
    "signatoryAuthorityId": "312c966b-0fca-4eb8-b084-1f80c7f5d1fe",
}

_DOC_MINSELKHOZ = {
    "eoNumber": "2601201902260002",
    "complexName": "Приказ Министерства сельского хозяйства Ставропольского края от 25.02.2019 № 67-од",
    "name": "О признании утратившими силу некоторых приказов",
    "number": "67-од",
    "documentDate": "2019-02-25T00:00:00",
    "publishDateShort": "2019-02-26T00:00:00",
    "pagesCount": 2,
    "signatoryAuthorityId": "ed16b61b-421b-4268-a87e-6c2cc131f949",
}

_DOC_NO_COMPLEX_NAME = {
    "eoNumber": "9900000000000001",
    "complexName": "",
    "name": "Документ без complexName",
    "number": "1",
    "documentDate": "2026-01-01T00:00:00",
    "publishDateShort": "2026-01-02T00:00:00",
    "pagesCount": 1,
    "signatoryAuthorityId": "3d93f00f-1af0-4669-8f98-0bff04215eb3",
}

_EMPTY_RESPONSE = {"items": [], "itemsTotalCount": 0, "pagesTotalCount": 0}


def _make_source(max_items: int | None = 40) -> PublicationPravoStavropolSource:
    config = SourceConfig(
        name=_SOURCE_CONFIG.name,
        url=_SOURCE_CONFIG.url,
        level=_SOURCE_CONFIG.level,
        region=_SOURCE_CONFIG.region,
        source_role=_SOURCE_CONFIG.source_role,
        parser="publication_pravo_stav",
        max_items=max_items,
        description="test",
    )
    return PublicationPravoStavropolSource(config)


def _mock_response(data: dict) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = data
    return mock


def _response_with(docs: list[dict]) -> MagicMock:
    return _mock_response({"items": docs, "itemsTotalCount": len(docs), "pagesTotalCount": 1})


class PublicationPravoStavropolSourceTest(unittest.TestCase):

    def test_multiple_authorities_are_queried(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_EMPTY_RESPONSE)) as mock_get:
            source.fetch_items()
        self.assertEqual(mock_get.call_count, len(_STAVROPOL_AUTHORITIES))
        called_urls = [str(c.args[0]) for c in mock_get.call_args_list]
        authority_ids = [auth_id for auth_id, _ in _STAVROPOL_AUTHORITIES]
        for auth_id in authority_ids:
            self.assertTrue(
                any(auth_id in url for url in called_urls),
                f"authority {auth_id} not found in any API call",
            )

    def test_items_from_multiple_authorities_combined(self) -> None:
        source = _make_source()
        responses = [
            _response_with([_DOC_PRAVITELSTVO]),
            _response_with([_DOC_GUBERNATOR]),
            _mock_response(_EMPTY_RESPONSE),
            _response_with([_DOC_MINSELKHOZ]),
        ]
        with patch.object(source, "get", side_effect=responses):
            items = source.fetch_items()
        self.assertEqual(len(items), 3)
        urls = {item.url for item in items}
        self.assertIn("http://publication.pravo.gov.ru/Document/View/2600202605120010", urls)
        self.assertIn("http://publication.pravo.gov.ru/Document/View/2600202605070001", urls)
        self.assertIn("http://publication.pravo.gov.ru/Document/View/2601201902260002", urls)

    def test_title_uses_complex_name_when_present(self) -> None:
        source = _make_source()
        responses = [_response_with([_DOC_PRAVITELSTVO])] + [_mock_response(_EMPTY_RESPONSE)] * 3
        with patch.object(source, "get", side_effect=responses):
            items = source.fetch_items()
        self.assertIn("Постановление Правительства", items[0].title)

    def test_title_falls_back_to_name_when_complex_name_empty(self) -> None:
        source = _make_source()
        responses = [_response_with([_DOC_NO_COMPLEX_NAME])] + [_mock_response(_EMPTY_RESPONSE)] * 3
        with patch.object(source, "get", side_effect=responses):
            items = source.fetch_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Документ без complexName")

    def test_document_url_uses_eo_number(self) -> None:
        source = _make_source()
        responses = [_response_with([_DOC_PRAVITELSTVO])] + [_mock_response(_EMPTY_RESPONSE)] * 3
        with patch.object(source, "get", side_effect=responses):
            items = source.fetch_items()
        self.assertEqual(
            items[0].url,
            "http://publication.pravo.gov.ru/Document/View/2600202605120010",
        )

    def test_published_at_parsed_from_publish_date_short(self) -> None:
        source = _make_source()
        responses = [_response_with([_DOC_PRAVITELSTVO])] + [_mock_response(_EMPTY_RESPONSE)] * 3
        with patch.object(source, "get", side_effect=responses):
            items = source.fetch_items()
        expected = datetime(2026, 5, 12, 0, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(items[0].published_at, expected)

    def test_raw_text_is_populated(self) -> None:
        source = _make_source()
        responses = [_response_with([_DOC_PRAVITELSTVO])] + [_mock_response(_EMPTY_RESPONSE)] * 3
        with patch.object(source, "get", side_effect=responses):
            items = source.fetch_items()
        self.assertIsNotNone(items[0].raw_text)
        self.assertGreater(len(items[0].raw_text or ""), 0)

    def test_raw_text_contains_authority_name(self) -> None:
        source = _make_source()
        responses = [_response_with([_DOC_PRAVITELSTVO])] + [_mock_response(_EMPTY_RESPONSE)] * 3
        with patch.object(source, "get", side_effect=responses):
            items = source.fetch_items()
        self.assertIn("Правительство Ставропольского края", items[0].raw_text or "")

    def test_different_documents_produce_different_raw_text(self) -> None:
        source = _make_source()
        responses = [
            _response_with([_DOC_PRAVITELSTVO, _DOC_GUBERNATOR]),
            _mock_response(_EMPTY_RESPONSE),
            _mock_response(_EMPTY_RESPONSE),
            _mock_response(_EMPTY_RESPONSE),
        ]
        with patch.object(source, "get", side_effect=responses):
            items = source.fetch_items()
        self.assertEqual(len(items), 2)
        self.assertNotEqual(items[0].raw_text, items[1].raw_text)

    def test_skips_doc_without_eo_number(self) -> None:
        doc_missing_eo = {**_DOC_PRAVITELSTVO, "eoNumber": ""}
        source = _make_source()
        responses = [_response_with([doc_missing_eo])] + [_mock_response(_EMPTY_RESPONSE)] * 3
        with patch.object(source, "get", side_effect=responses):
            items = source.fetch_items()
        self.assertEqual(items, [])

    def test_skips_doc_without_title(self) -> None:
        doc_no_title = {**_DOC_PRAVITELSTVO, "complexName": "", "name": ""}
        source = _make_source()
        responses = [_response_with([doc_no_title])] + [_mock_response(_EMPTY_RESPONSE)] * 3
        with patch.object(source, "get", side_effect=responses):
            items = source.fetch_items()
        self.assertEqual(items, [])

    def test_malformed_json_skips_that_authority(self) -> None:
        source = _make_source()
        bad_mock = MagicMock()
        bad_mock.json.side_effect = ValueError("bad json")
        responses = [
            bad_mock,
            _response_with([_DOC_GUBERNATOR]),
            _mock_response(_EMPTY_RESPONSE),
            _mock_response(_EMPTY_RESPONSE),
        ]
        with patch.object(source, "get", side_effect=responses):
            items = source.fetch_items()
        self.assertEqual(len(items), 1)
        self.assertIn("2600202605070001", items[0].url)

    def test_non_dict_response_skips_that_authority(self) -> None:
        source = _make_source()
        bad_mock = _mock_response([])  # returns a list, not dict
        responses = [bad_mock] + [_mock_response(_EMPTY_RESPONSE)] * 3
        with patch.object(source, "get", side_effect=responses):
            items = source.fetch_items()
        self.assertEqual(items, [])

    def test_empty_items_array_returns_nothing(self) -> None:
        source = _make_source()
        with patch.object(source, "get", return_value=_mock_response(_EMPTY_RESPONSE)):
            items = source.fetch_items()
        self.assertEqual(items, [])

    def test_duplicate_eo_numbers_across_authorities_deduplicated(self) -> None:
        doc_dup = {**_DOC_GUBERNATOR, "signatoryAuthorityId": "3d93f00f-1af0-4669-8f98-0bff04215eb3"}
        source = _make_source()
        responses = [
            _response_with([_DOC_GUBERNATOR]),
            _response_with([doc_dup]),
            _mock_response(_EMPTY_RESPONSE),
            _mock_response(_EMPTY_RESPONSE),
        ]
        with patch.object(source, "get", side_effect=responses):
            items = source.fetch_items()
        self.assertEqual(len(items), 1)

    def test_source_metadata_from_config(self) -> None:
        source = _make_source()
        responses = [_response_with([_DOC_PRAVITELSTVO])] + [_mock_response(_EMPTY_RESPONSE)] * 3
        with patch.object(source, "get", side_effect=responses):
            items = source.fetch_items()
        self.assertEqual(items[0].source_name, _SOURCE_CONFIG.name)
        self.assertEqual(items[0].level, "regional")
        self.assertEqual(items[0].region, "stavropol")
        self.assertEqual(items[0].document_type, "html")

    def test_api_url_includes_authority_id_and_page_params(self) -> None:
        source = _make_source(max_items=40)
        with patch.object(source, "get", return_value=_mock_response(_EMPTY_RESPONSE)) as mock_get:
            source.fetch_items()
        first_url = mock_get.call_args_list[0].args[0]
        self.assertIn("SignatoryAuthorityId=3d93f00f", first_url)
        self.assertIn("pageIndex=1", first_url)
        self.assertIn("pageSize=", first_url)


class BuildSyntheticTextTest(unittest.TestCase):

    def test_contains_act_field(self) -> None:
        text = _build_synthetic_text(_DOC_PRAVITELSTVO, "Правительство СК")
        self.assertIn("Акт:", text)
        self.assertIn("Постановление", text)

    def test_contains_authority_name(self) -> None:
        text = _build_synthetic_text(_DOC_PRAVITELSTVO, "Правительство СК")
        self.assertIn("Орган: Правительство СК", text)

    def test_contains_number(self) -> None:
        text = _build_synthetic_text(_DOC_PRAVITELSTVO, "")
        self.assertIn("Номер: 237-п", text)

    def test_contains_publication_date(self) -> None:
        text = _build_synthetic_text(_DOC_PRAVITELSTVO, "")
        self.assertIn("Дата публикации: 12.05.2026", text)

    def test_contains_doc_date(self) -> None:
        text = _build_synthetic_text(_DOC_PRAVITELSTVO, "")
        self.assertIn("Дата акта: 08.05.2026", text)

    def test_contains_page_count(self) -> None:
        text = _build_synthetic_text(_DOC_PRAVITELSTVO, "")
        self.assertIn("Страниц: 2", text)

    def test_empty_doc_returns_minimal_text(self) -> None:
        text = _build_synthetic_text({}, "")
        self.assertIsInstance(text, str)


class ParseDateFieldTest(unittest.TestCase):

    def test_standard_iso_parsed(self) -> None:
        result = _parse_date_field("2026-05-12T00:00:00")
        self.assertEqual(result, datetime(2026, 5, 12, tzinfo=timezone.utc))

    def test_with_time_component(self) -> None:
        result = _parse_date_field("2026-05-08T13:45:00")
        self.assertEqual(result, datetime(2026, 5, 8, 13, 45, 0, tzinfo=timezone.utc))

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(_parse_date_field(""))

    def test_whitespace_returns_none(self) -> None:
        self.assertIsNone(_parse_date_field("  "))

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(_parse_date_field("not-a-date"))

    def test_result_is_utc(self) -> None:
        result = _parse_date_field("2026-03-01T12:00:00")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
