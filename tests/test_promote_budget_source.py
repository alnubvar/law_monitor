from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from app.models import SourceConfig
from app.sources.promote_budget_source import (
    PromoteBudgetSource,
    _build_public_reference_url,
    _build_raw_text,
    _extract_page_items,
    _is_agriculture_relevant,
    _parse_api_datetime,
)

_SOURCE_CONFIG = SourceConfig(
    name="promote.budget.gov.ru / Минфин - отборы и меры поддержки",
    url="https://promote.budget.gov.ru/public/minfin/activity",
    level="support_measures",
    region="federal",
    source_role="active_support_measures",
    parser="promote_budget",
    max_items=30,
    description="test",
)

_AGRO_ITEM = {
    "type": 0,
    "activityId": "activity-1",
    "competitionId": "competition-1",
    "pppItemName": "Министерство сельского хозяйства Российской Федерации",
    "title": "Грант Агростартап для фермеров",
    "shortName": "Агростартап",
    "status": 5,
    "startDate": "2026-05-04T21:01:00Z",
    "endDate": "2026-05-14T20:59:00Z",
    "maxAmountForPersonInfo": "13 682 538,80 ₽",
    "isActive": True,
    "isActiveCompetition": False,
    "isNotActive": False,
    "selectionAcceptingApplicationInfo": {
        "acceptingApplicationsInfo": "меньше 1 дня",
        "countDaysEndDate": 0.016,
    },
    "id": "card-1",
}

_SOCIAL_ITEM = {
    "type": 0,
    "activityId": "activity-2",
    "competitionId": "competition-2",
    "pppItemName": "Министерство культуры Российской Федерации",
    "title": "Субсидия на развитие внутреннего туризма",
    "shortName": "Туризм",
    "status": 5,
    "startDate": "2026-05-05T10:00:00Z",
    "endDate": "2026-05-20T20:59:00Z",
    "maxAmountForPersonInfo": "1 000 000,00 ₽",
    "isActive": True,
    "selectionAcceptingApplicationInfo": {
        "acceptingApplicationsInfo": "15 дней",
        "countDaysEndDate": 15,
    },
    "id": "card-2",
}

_EXPORT_ITEM = {
    "type": 0,
    "activityId": "activity-3",
    "competitionId": "competition-3",
    "pppItemName": "Российский экспортный центр",
    "title": "Субсидия на поддержку экспорта зерна",
    "shortName": "Экспорт зерна",
    "status": 5,
    "startDate": "2026-05-06T12:00:00Z",
    "endDate": "2026-05-26T20:59:00Z",
    "maxAmountForPersonInfo": "25 000 000,00 ₽",
    "isActive": True,
    "selectionAcceptingApplicationInfo": {
        "acceptingApplicationsInfo": "20 дней",
        "countDaysEndDate": 20,
    },
    "id": "card-3",
}


def _response_with(items: list[dict], *, total_pages: int = 1) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {
        "item1": {
            "startEntry": 1,
            "entryCount": len(items),
            "totalEntries": len(items),
            "items": items,
            "currentPage": 1,
            "totalPages": total_pages,
        },
        "item2": 0,
    }
    response.raise_for_status.return_value = None
    return response


def _make_source(max_items: int | None = 30) -> PromoteBudgetSource:
    config = SourceConfig(
        name=_SOURCE_CONFIG.name,
        url=_SOURCE_CONFIG.url,
        level=_SOURCE_CONFIG.level,
        region=_SOURCE_CONFIG.region,
        source_role=_SOURCE_CONFIG.source_role,
        parser="promote_budget",
        max_items=max_items,
        description="test",
    )
    return PromoteBudgetSource(config)


class PromoteBudgetSourceTest(unittest.TestCase):
    def test_fetch_items_keeps_agro_and_filters_social_noise(self) -> None:
        source = _make_source()
        with patch.object(
            source.session,
            "post",
            return_value=_response_with([_AGRO_ITEM, _SOCIAL_ITEM, _EXPORT_ITEM]),
        ):
            items = source.fetch_items()

        self.assertEqual(len(items), 2)
        titles = [item.title for item in items]
        self.assertIn("Грант Агростартап для фермеров", titles)
        self.assertIn("Субсидия на поддержку экспорта зерна", titles)
        self.assertNotIn("Субсидия на развитие внутреннего туризма", titles)

    def test_published_at_uses_start_date(self) -> None:
        source = _make_source()
        with patch.object(
            source.session,
            "post",
            return_value=_response_with([_AGRO_ITEM]),
        ):
            items = source.fetch_items()
        self.assertEqual(
            items[0].published_at,
            datetime(2026, 5, 4, 21, 1, 0, tzinfo=timezone.utc),
        )

    def test_raw_text_contains_deadline_amount_and_identifiers(self) -> None:
        source = _make_source()
        with patch.object(
            source.session,
            "post",
            return_value=_response_with([_AGRO_ITEM]),
        ):
            items = source.fetch_items()
        raw_text = items[0].raw_text or ""
        self.assertIn("endDate: 2026-05-14T20:59:00Z", raw_text)
        self.assertIn("maxAmountForPersonInfo: 13 682 538,80 ₽", raw_text)
        self.assertNotIn("acceptingApplicationsInfo", raw_text)
        self.assertNotIn("меньше 1 дня", raw_text)
        self.assertNotIn("countDaysEndDate", raw_text)
        self.assertIn("activityId: activity-1", raw_text)
        self.assertIn("competitionId: competition-1", raw_text)
        self.assertIn("id: card-1", raw_text)

    def test_public_reference_url_uses_real_detail_route_from_competition_id(self) -> None:
        url = _build_public_reference_url(_SOURCE_CONFIG.url, _AGRO_ITEM)
        self.assertEqual(
            url,
            "https://promote.budget.gov.ru/public/minfin/selection/view/competition-1?showBackButton=true&competitionType=0&tab=1",
        )

    def test_public_reference_url_falls_back_to_synthetic_fragment_when_competition_id_missing(self) -> None:
        url = _build_public_reference_url(
            _SOURCE_CONFIG.url,
            {"activityId": "activity-1", "id": "card-1"},
        )
        self.assertEqual(
            url,
            "https://promote.budget.gov.ru/public/minfin/activity#activityId=activity-1&id=card-1",
        )

    def test_public_reference_url_falls_back_to_title_and_date_when_ids_missing(self) -> None:
        url = _build_public_reference_url(
            _SOURCE_CONFIG.url,
            {"title": "Грант для фермеров", "startDate": "2026-05-04T21:01:00Z"},
        )
        self.assertEqual(
            url,
            "https://promote.budget.gov.ru/public/minfin/activity#title=%D0%93%D1%80%D0%B0%D0%BD%D1%82+%D0%B4%D0%BB%D1%8F+%D1%84%D0%B5%D1%80%D0%BC%D0%B5%D1%80%D0%BE%D0%B2&startDate=2026-05-04T21%3A01%3A00Z",
        )

    def test_pagination_stops_when_max_items_reached(self) -> None:
        source = _make_source(max_items=21)
        page_one_items = [
            {
                **_AGRO_ITEM,
                "activityId": f"activity-{index}",
                "competitionId": f"competition-{index}",
                "id": f"card-{index}",
                "title": f"Субсидия на развитие растениеводства {index}",
                "shortName": f"Растениеводство {index}",
            }
            for index in range(1, 21)
        ]
        responses = [
            _response_with(page_one_items, total_pages=5),
            _response_with(
                [
                    {
                        **_AGRO_ITEM,
                        "activityId": "activity-21",
                        "competitionId": "competition-21",
                        "id": "card-21",
                        "title": "Субсидия на переработку молока",
                        "shortName": "Переработка молока",
                    },
                    {
                        **_EXPORT_ITEM,
                        "activityId": "activity-22",
                        "competitionId": "competition-22",
                        "id": "card-22",
                        "title": "Субсидия на развитие растениеводства",
                        "shortName": "Растениеводство",
                    },
                ],
                total_pages=5,
            ),
        ]
        with patch.object(source.session, "post", side_effect=responses) as mock_post:
            items = source.fetch_items()

        self.assertEqual(len(items), 21)
        self.assertEqual(mock_post.call_count, 2)
        first_payload = mock_post.call_args_list[0].kwargs["json"]
        second_payload = mock_post.call_args_list[1].kwargs["json"]
        self.assertEqual(first_payload["currentPage"], 1)
        self.assertEqual(second_payload["currentPage"], 2)
        self.assertEqual(first_payload["entryCount"], 20)
        self.assertEqual(second_payload["entryCount"], 20)

    def test_empty_response_is_handled_safely(self) -> None:
        source = _make_source()
        with patch.object(
            source.session,
            "post",
            return_value=_response_with([]),
        ):
            items = source.fetch_items()
        self.assertEqual(items, [])

    def test_malformed_json_is_handled_safely(self) -> None:
        source = _make_source()
        bad_response = MagicMock()
        bad_response.json.side_effect = ValueError("bad json")
        bad_response.raise_for_status.return_value = None
        with patch.object(source.session, "post", return_value=bad_response):
            items = source.fetch_items()
        self.assertEqual(items, [])


class PromoteBudgetHelpersTest(unittest.TestCase):
    def test_is_agriculture_relevant_true_for_agro_item(self) -> None:
        self.assertTrue(_is_agriculture_relevant(_AGRO_ITEM))

    def test_is_agriculture_relevant_false_for_social_item(self) -> None:
        self.assertFalse(_is_agriculture_relevant(_SOCIAL_ITEM))

    def test_build_raw_text_keeps_required_fields_even_when_optional_data_missing(self) -> None:
        raw_text = _build_raw_text({"title": "Тест", "id": "x"})
        self.assertIn("title: Тест", raw_text)
        self.assertIn("endDate:", raw_text)
        self.assertIn("id: x", raw_text)

    def test_build_raw_text_excludes_volatile_countdown_fields(self) -> None:
        raw_text = _build_raw_text(_AGRO_ITEM)
        self.assertNotIn("countDaysEndDate", raw_text)
        self.assertNotIn("acceptingApplicationsInfo", raw_text)
        self.assertNotIn("меньше 1 дня", raw_text)

    def test_build_raw_text_renders_open_window_in_analysis_friendly_form(self) -> None:
        item = {
            **_AGRO_ITEM,
            "endDate": "2026-05-20T20:59:00Z",
            "selectionAcceptingApplicationInfo": {
                "acceptingApplicationsInfo": "4 дня",
                "countDaysEndDate": 4,
            },
        }
        with patch("app.sources.promote_budget_source._today_utc", return_value=date(2026, 5, 16)):
            raw_text = _build_raw_text(item)

        self.assertIn("Активная мера поддержки.", raw_text)
        self.assertIn("Прием заявок открыт до 20.05.2026.", raw_text)
        self.assertNotIn("Статус приема заявок", raw_text)
        self.assertNotIn("4 дня", raw_text)
        self.assertNotIn("countDaysEndDate", raw_text)

    def test_build_raw_text_marks_expired_window_as_closed(self) -> None:
        with patch("app.sources.promote_budget_source._today_utc", return_value=date(2026, 5, 16)):
            raw_text = _build_raw_text(_AGRO_ITEM)

        self.assertIn("Прием заявок до 14.05.2026.", raw_text)
        self.assertIn("Прием завершен. Отбор завершен.", raw_text)

    def test_build_raw_text_is_stable_when_only_countdown_changes(self) -> None:
        open_item = {
            **_AGRO_ITEM,
            "endDate": "2026-05-20T20:59:00Z",
            "selectionAcceptingApplicationInfo": {
                "acceptingApplicationsInfo": "4 дня",
                "countDaysEndDate": 4,
            },
        }
        changed_count_days_item = {
            **open_item,
            "selectionAcceptingApplicationInfo": {
                "acceptingApplicationsInfo": "3 дня",
                "countDaysEndDate": 3.25,
            },
        }

        with patch("app.sources.promote_budget_source._today_utc", return_value=date(2026, 5, 16)):
            first = _build_raw_text(open_item)
            second = _build_raw_text(changed_count_days_item)

        self.assertEqual(first, second)
        self.assertIn("Прием заявок открыт до 20.05.2026.", first)
        self.assertNotIn("4 дня", first)
        self.assertNotIn("3 дня", second)

    def test_extract_page_items_returns_only_mapping_items(self) -> None:
        items = _extract_page_items(
            {
                "item1": {
                    "items": [_AGRO_ITEM, "skip-me", {"title": "Льготное зерно", "id": "ok"}]
                }
            }
        )
        self.assertEqual(len(items), 2)

    def test_parse_api_datetime_parses_utc_z_suffix(self) -> None:
        self.assertEqual(
            _parse_api_datetime("2026-05-04T21:01:00Z"),
            datetime(2026, 5, 4, 21, 1, 0, tzinfo=timezone.utc),
        )

    def test_parse_api_datetime_returns_none_for_invalid_value(self) -> None:
        self.assertIsNone(_parse_api_datetime("not-a-date"))

    def test_is_agriculture_relevant_false_for_nko_grant_from_rural_settlement(self) -> None:
        item = {
            "pppItemName": "Администрация Михайловского сельского поселения",
            "title": "Предоставление субсидии некоммерческим организациям",
            "shortName": "Поддержка НКО",
        }
        self.assertFalse(_is_agriculture_relevant(item))

    def test_is_agriculture_relevant_false_for_sports_grant_with_rural_wording(self) -> None:
        item = {
            "pppItemName": "Комитет по спорту",
            "title": "Грант на проведение летней олимпиады сельских спортсменов",
            "shortName": "Спорт",
        }
        self.assertFalse(_is_agriculture_relevant(item))


if __name__ == "__main__":
    unittest.main()
