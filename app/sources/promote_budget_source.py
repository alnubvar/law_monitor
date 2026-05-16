from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from urllib.parse import urlencode

from requests import RequestException

from app.config import REQUEST_TIMEOUT
from app.models import CollectedItem
from app.sources.base import BaseSource

_API_ENDPOINT = "https://promote.budget.gov.ru/m-data/api/v1/activity/public-view/list-activity-card"
_DETAIL_URL_TEMPLATE = (
    "https://promote.budget.gov.ru/public/minfin/selection/view/"
    "{competition_id}?showBackButton=true&competitionType=0&tab=1"
)
_PAGE_SIZE = 20
_MAX_PAGES = 5
_STRONG_AGRO_MARKERS = (
    "апк",
    "агро",
    "сельскохозяй",
    "сельхоз",
    "растениевод",
    "животновод",
    "молок",
    "зерн",
    "мелиорац",
    "семен",
    "фермер",
    "крестьянск",
)
_WEAK_AGRO_MARKERS = ("пищев", "переработ", "экспорт")
_SUPPORT_MARKERS = ("субсид", "грант")
_CLOSED_APPLICATION_MARKERS = (
    "прием заверш",
    "приём заверш",
    "заявки не принимаются",
    "отбор заверш",
)


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _parse_api_datetime(value: str) -> datetime | None:
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return None


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _format_deadline_date(value: str) -> str | None:
    parsed = _parse_api_datetime(value)
    if parsed is None:
        return None
    return parsed.strftime("%d.%m.%Y")


def _build_operational_status_lines(
    item: Mapping[str, object], *, accepting_applications: str
) -> list[str]:
    lines: list[str] = []
    is_active = item.get("isActive")
    if is_active is True:
        lines.append("Активная мера поддержки.")
    elif is_active is False:
        lines.append("Не активная мера поддержки.")

    end_date_raw = _normalize_text(item.get("endDate"))
    deadline_date = _format_deadline_date(end_date_raw)
    end_datetime = _parse_api_datetime(end_date_raw) if end_date_raw else None
    accepting_lower = accepting_applications.lower()
    is_closed_window = any(
        marker in accepting_lower for marker in _CLOSED_APPLICATION_MARKERS
    ) or (
        end_datetime is not None and end_datetime.date() < _today_utc()
    )

    if deadline_date:
        if is_closed_window:
            lines.append(f"Прием заявок до {deadline_date}.")
            lines.append("Прием завершен. Отбор завершен.")
        elif is_active is True:
            lines.append(f"Прием заявок открыт до {deadline_date}.")
        else:
            lines.append(f"Прием заявок до {deadline_date}.")
    elif is_closed_window:
        lines.append("Прием завершен. Отбор завершен.")
    elif is_active is True and accepting_applications:
        lines.append("Прием заявок открыт.")

    if accepting_applications:
        lines.append(f"Статус приема заявок: {accepting_applications}.")

    return lines


def _is_agriculture_relevant(item: Mapping[str, object]) -> bool:
    combined = " ".join(
        _normalize_text(item.get(field))
        for field in ("title", "shortName", "pppItemName")
    ).lower()
    if not combined:
        return False
    has_support_marker = any(marker in combined for marker in _SUPPORT_MARKERS)
    if any(marker in combined for marker in _STRONG_AGRO_MARKERS):
        return True
    if has_support_marker and any(marker in combined for marker in _WEAK_AGRO_MARKERS):
        return True
    return has_support_marker and any(
        marker in combined for marker in ("минсельхоз", "сельское хозяйство", "агростартап")
    )


def _build_public_reference_url(base_url: str, item: Mapping[str, object]) -> str:
    competition_id = _normalize_text(item.get("competitionId"))
    if competition_id:
        return _DETAIL_URL_TEMPLATE.format(competition_id=competition_id)

    fragment_pairs: list[tuple[str, str]] = []
    for field in ("activityId", "competitionId", "id"):
        value = _normalize_text(item.get(field))
        if value:
            fragment_pairs.append((field, value))
    if not fragment_pairs:
        for field in ("title", "startDate"):
            value = _normalize_text(item.get(field))
            if value:
                fragment_pairs.append((field, value))
    if not fragment_pairs:
        return base_url
    # The list API does not expose enough data to reconstruct the true SPA detail route
    # reliably, so keep a public listing URL and attach identifiers as a transparent fragment.
    return f"{base_url}#{urlencode(fragment_pairs)}"


def _build_raw_text(item: Mapping[str, object]) -> str:
    accepting_info = item.get("selectionAcceptingApplicationInfo")
    if isinstance(accepting_info, Mapping):
        accepting_applications = _normalize_text(
            accepting_info.get("acceptingApplicationsInfo")
        )
    else:
        accepting_applications = ""
    operational_status_lines = _build_operational_status_lines(
        item,
        accepting_applications=accepting_applications,
    )

    lines = [
        f"title: {_normalize_text(item.get('title'))}",
        f"shortName: {_normalize_text(item.get('shortName'))}",
        f"pppItemName: {_normalize_text(item.get('pppItemName'))}",
        f"startDate: {_normalize_text(item.get('startDate'))}",
        f"endDate: {_normalize_text(item.get('endDate'))}",
        f"maxAmountForPersonInfo: {_normalize_text(item.get('maxAmountForPersonInfo'))}",
        f"isActive: {_normalize_text(item.get('isActive'))}",
        f"acceptingApplicationsInfo: {accepting_applications}",
        f"activityId: {_normalize_text(item.get('activityId'))}",
        f"competitionId: {_normalize_text(item.get('competitionId'))}",
        f"id: {_normalize_text(item.get('id'))}",
    ]
    lines.extend(operational_status_lines)
    return "\n".join(lines)


def _extract_page_items(data: object) -> list[Mapping[str, object]]:
    if not isinstance(data, Mapping):
        return []
    payload = data.get("item1")
    if not isinstance(payload, Mapping):
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, Mapping)]


class PromoteBudgetSource(BaseSource):
    """Fetches bounded pages from the public promote.budget.gov.ru activity API."""

    def fetch_items(self) -> list[CollectedItem]:
        total_limit = max(1, self.config.max_items or 30)
        items: list[CollectedItem] = []
        seen_urls: set[str] = set()
        api_items_seen = 0
        filtered_count = 0

        for current_page in range(1, _MAX_PAGES + 1):
            if len(items) >= total_limit:
                break
            try:
                response = self._post_page(
                    current_page=current_page,
                    entry_count=_PAGE_SIZE,
                )
            except RequestException as exc:
                self.logger.warning(
                    "Promote budget API request failed on page %s: %s",
                    current_page,
                    exc,
                )
                raise

            try:
                data = response.json()
            except ValueError as exc:
                self.logger.warning(
                    "Promote budget API returned invalid JSON on page %s: %s",
                    current_page,
                    exc,
                )
                break

            page_items = _extract_page_items(data)
            if not page_items:
                if current_page == 1:
                    self.logger.warning(
                        "Promote budget API returned empty or unexpected payload on page %s",
                        current_page,
                    )
                break

            api_items_seen += len(page_items)
            for entry in page_items:
                title = _normalize_text(entry.get("title") or entry.get("shortName"))
                if not title:
                    filtered_count += 1
                    continue
                if not _is_agriculture_relevant(entry):
                    filtered_count += 1
                    continue
                item_url = _build_public_reference_url(self.config.url, entry)
                if item_url in seen_urls:
                    filtered_count += 1
                    continue

                seen_urls.add(item_url)
                items.append(
                    CollectedItem(
                        source_name=self.config.name,
                        source_url=self.config.url,
                        level=self.config.level,
                        region=self.config.region,
                        title=title,
                        url=item_url,
                        published_at=_parse_api_datetime(
                            _normalize_text(entry.get("startDate"))
                        ),
                        document_type="html",
                        raw_text=_build_raw_text(entry),
                    )
                )
                if len(items) >= total_limit:
                    break

            total_pages = None
            if isinstance(data, Mapping):
                payload = data.get("item1")
                if isinstance(payload, Mapping):
                    raw_total_pages = payload.get("totalPages")
                    if isinstance(raw_total_pages, int):
                        total_pages = raw_total_pages
            if len(page_items) < _PAGE_SIZE:
                break
            if total_pages is not None and current_page >= total_pages:
                break

        self.last_fetch_stats = {
            "links_found_count": api_items_seen,
            "links_filtered_count": filtered_count,
            "html_links_count": len(items),
        }
        self.logger.info("Fetched %s items from %s", len(items), self.config.name)
        return items

    def _post_page(self, *, current_page: int, entry_count: int):
        response = self.session.post(
            _API_ENDPOINT,
            json={
                "currentPage": current_page,
                "entryCount": entry_count,
                "recipientCategory": [],
                "recipientSelectionWayId": [],
                "minActivityAmountForPerson": None,
                "maxActivityAmountForPerson": None,
                "coFinancing": [],
                "activityYear": [],
                "subsidyTypeId": [],
                "budgetType": [],
                "activityCategory": [],
                "directionId": [],
                "okvedId": [],
                "textTerms": [],
                "realizationPlace": [],
                "pppCode": [],
                "activityType": [],
                "maxAmountType": [],
                "distributionType": [],
                "sortDirection": 0,
                "sortMember": "Default",
                "isSelection": True,
                "geography": [],
                "tags": [],
                "selectionLicenseRequired": [],
                "accreditationRequired": [],
                "selectionType": 0,
                "soOktmos": [],
            },
            timeout=self.config.request_timeout or REQUEST_TIMEOUT,
            verify=self.config.verify_ssl,
        )
        response.raise_for_status()
        return response
