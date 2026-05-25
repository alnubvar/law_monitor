from __future__ import annotations

from http.client import IncompleteRead
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import requests

from app.models import SourceConfig
from app.sources.pravo_stavregion_api_source import (
    PravoStavregionApiSource,
    _build_pdf_url,
    _build_raw_text,
    _build_title,
    _is_agriculture_relevant,
    _parse_date_array,
)

_SOURCE_CONFIG = SourceConfig(
    name="Право Ставропольского края",
    url="https://pravo.stavregion.ru/",
    level="regional",
    region="stavropol",
    source_role="regional_npa",
    parser="pravo_stavregion_api",
    max_items=25,
    description="test",
)

_AGRO_ROW = {
    "id": 28851,
    "authority1": {
        "id": 25,
        "title": "Правительство Ставропольского края",
    },
    "type": {
        "id": 2,
        "title": "Постановление",
    },
    "num": "182-п",
    "date": [2026, 4, 15],
    "title": "О внесении изменений в порядок предоставления субсидий сельскохозяйственным товаропроизводителям",
    "href": "api/app/repo/law/7f46e5314b54a9aa6d784f9d27ec11f4.pdf;182-п.pdf",
    "pubId": 25898,
    "regId": None,
}

_IRRELEVANT_ROW = {
    "id": 28852,
    "authority1": {
        "id": 25,
        "title": "Правительство Ставропольского края",
    },
    "type": {
        "id": 2,
        "title": "Постановление",
    },
    "num": "183-п",
    "date": [2026, 4, 15],
    "title": "О внесении изменений в положение о культурных мероприятиях",
    "href": "api/app/repo/law/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf;183-п.pdf",
    "pubId": 25899,
    "regId": None,
}


def _make_source(max_items: int | None = 25) -> PravoStavregionApiSource:
    config = SourceConfig(
        name=_SOURCE_CONFIG.name,
        url=_SOURCE_CONFIG.url,
        level=_SOURCE_CONFIG.level,
        region=_SOURCE_CONFIG.region,
        source_role=_SOURCE_CONFIG.source_role,
        parser="pravo_stavregion_api",
        max_items=max_items,
        description="test",
    )
    return PravoStavregionApiSource(config)


def _mock_response(payload) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class PravoStavregionApiSourceTest(unittest.TestCase):
    def test_parses_sample_row(self) -> None:
        source = _make_source()
        with patch.object(source.session, "get", return_value=_mock_response([_AGRO_ROW])):
            items = source.fetch_items()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Постановление 182-п О внесении изменений в порядок предоставления субсидий сельскохозяйственным товаропроизводителям")
        self.assertEqual(
            items[0].url,
            "https://pravo.stavregion.ru/api/app/repo/law/7f46e5314b54a9aa6d784f9d27ec11f4.pdf",
        )
        self.assertEqual(items[0].document_type, "pdf")

    def test_date_array_parsing(self) -> None:
        self.assertEqual(
            _parse_date_array([2026, 4, 15]),
            datetime(2026, 4, 15, tzinfo=timezone.utc),
        )

    def test_pdf_url_built_from_href_with_semicolon_suffix(self) -> None:
        self.assertEqual(
            _build_pdf_url("api/app/repo/law/7f46e5314b54a9aa6d784f9d27ec11f4.pdf;182-п.pdf"),
            "https://pravo.stavregion.ru/api/app/repo/law/7f46e5314b54a9aa6d784f9d27ec11f4.pdf",
        )

    def test_max_items_local_cap(self) -> None:
        source = _make_source(max_items=1)
        with patch.object(
            source.session,
            "get",
            return_value=_mock_response([_AGRO_ROW, {**_AGRO_ROW, "id": 28853, "href": "api/app/repo/law/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.pdf;184-п.pdf", "num": "184-п"}]),
        ):
            items = source.fetch_items()
        self.assertEqual(len(items), 1)

    def test_empty_response_is_handled_safely(self) -> None:
        source = _make_source()
        with patch.object(source.session, "get", return_value=_mock_response([])):
            items = source.fetch_items()
        self.assertEqual(items, [])

    def test_malformed_json_is_handled_safely(self) -> None:
        source = _make_source()
        bad_response = MagicMock()
        bad_response.json.side_effect = ValueError("bad json")
        bad_response.raise_for_status.return_value = None
        with patch.object(source.session, "get", return_value=bad_response):
            items = source.fetch_items()
        self.assertEqual(items, [])

    def test_incomplete_read_is_retried_once(self) -> None:
        source = _make_source()
        good_response = _mock_response([_AGRO_ROW])

        with patch.object(
            source.session,
            "get",
            side_effect=[IncompleteRead(b"{}", 10), good_response],
        ) as mock_get:
            items = source.fetch_items()

        self.assertEqual(len(items), 1)
        self.assertEqual(mock_get.call_count, 2)

    def test_repeated_incomplete_read_raises_connection_error(self) -> None:
        source = _make_source()

        with patch.object(
            source.session,
            "get",
            side_effect=[IncompleteRead(b"{", 10), IncompleteRead(b"{", 10)],
        ):
            with self.assertRaises(requests.exceptions.ConnectionError) as ctx:
                source.fetch_items()

        self.assertIn("incomplete/connection-broken", str(ctx.exception))

    def test_irrelevant_row_filtered(self) -> None:
        source = _make_source()
        with patch.object(
            source.session,
            "get",
            return_value=_mock_response([_IRRELEVANT_ROW]),
        ):
            items = source.fetch_items()
        self.assertEqual(items, [])


class PravoStavregionApiHelpersTest(unittest.TestCase):
    def test_build_title(self) -> None:
        self.assertEqual(
            _build_title(_AGRO_ROW),
            "Постановление 182-п О внесении изменений в порядок предоставления субсидий сельскохозяйственным товаропроизводителям",
        )

    def test_build_raw_text_contains_required_fields(self) -> None:
        raw_text = _build_raw_text(_AGRO_ROW)
        self.assertIn("authority: Правительство Ставропольского края", raw_text)
        self.assertIn("type: Постановление", raw_text)
        self.assertIn("num: 182-п", raw_text)
        self.assertIn("date: 2026,4,15", raw_text)
        self.assertIn("id: 28851", raw_text)
        self.assertIn("pubId: 25898", raw_text)
        self.assertIn("regId:", raw_text)
        self.assertIn("href: api/app/repo/law/7f46e5314b54a9aa6d784f9d27ec11f4.pdf;182-п.pdf", raw_text)

    def test_agriculture_relevance_true_for_agro_row(self) -> None:
        self.assertTrue(_is_agriculture_relevant(_AGRO_ROW))

    def test_agriculture_relevance_false_for_irrelevant_row(self) -> None:
        self.assertFalse(_is_agriculture_relevant(_IRRELEVANT_ROW))


if __name__ == "__main__":
    unittest.main()
