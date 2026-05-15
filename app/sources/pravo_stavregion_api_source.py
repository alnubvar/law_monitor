from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

from app.config import REQUEST_TIMEOUT
from app.models import CollectedItem
from app.sources.base import BaseSource

_API_ENDPOINT = "https://pravo.stavregion.ru/api/law/legalact/byFilter/"
_DATE_FLOOR_DAYS = 365
_STRONG_MARKERS = (
    "апк",
    "агро",
    "сельск",
    "сельхоз",
    "растениевод",
    "животновод",
    "молок",
    "зерн",
    "мелиорац",
    "семен",
    "фермер",
    "крестьянск",
    "пищев",
    "переработ",
    "агротехнолог",
    "агротуризм",
)
_SUPPORT_MARKERS = (
    "субсид",
    "грант",
    "господдерж",
    "возмещ",
    "компенсац",
    "отбор",
    "заяв",
)
_PDF_PATH_RE = re.compile(r"(?P<path>.*?\.pdf)", re.IGNORECASE)


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _parse_date_array(value: object) -> datetime | None:
    if not isinstance(value, list) or len(value) < 3:
        return None
    try:
        year = int(value[0])
        month = int(value[1])
        day = int(value[2])
        return datetime(year, month, day, tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _build_first_date_floor(*, now: datetime | None = None) -> str:
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    floor_date = reference.astimezone(timezone.utc).date() - timedelta(days=_DATE_FLOOR_DAYS)
    return floor_date.isoformat()


def _build_pdf_url(href: str) -> str:
    normalized_href = href.strip()
    match = _PDF_PATH_RE.match(normalized_href)
    if match is not None:
        normalized_href = match.group("path")
    return urljoin("https://pravo.stavregion.ru/", normalized_href.lstrip("/"))


def _build_title(item: Mapping[str, object]) -> str:
    type_title = _normalize_text(_nested_mapping_value(item.get("type"), "title"))
    num = _normalize_text(item.get("num"))
    title = _normalize_text(item.get("title"))
    parts = [part for part in (type_title, num, title) if part]
    return " ".join(parts)


def _nested_mapping_value(value: object, key: str) -> object:
    if not isinstance(value, Mapping):
        return None
    return value.get(key)


def _is_agriculture_relevant(item: Mapping[str, object]) -> bool:
    combined = " ".join(
        _normalize_text(value)
        for value in (
            _nested_mapping_value(item.get("authority1"), "title"),
            _nested_mapping_value(item.get("type"), "title"),
            item.get("num"),
            item.get("title"),
            item.get("href"),
        )
    ).lower()
    if not combined:
        return False
    if "министерство сельского хозяйства" in combined:
        return True
    if any(marker in combined for marker in _STRONG_MARKERS):
        return True
    return any(marker in combined for marker in _SUPPORT_MARKERS) and any(
        marker in combined for marker in (
            "правительство ставропольского края",
            "губернатор ставропольского края",
            "сельск",
            "агро",
            "апк",
        )
    )


def _build_raw_text(item: Mapping[str, object]) -> str:
    authority_title = _normalize_text(_nested_mapping_value(item.get("authority1"), "title"))
    type_title = _normalize_text(_nested_mapping_value(item.get("type"), "title"))
    raw_date = item.get("date")
    if isinstance(raw_date, list):
        date_text = ",".join(str(part) for part in raw_date)
    else:
        date_text = _normalize_text(raw_date)
    lines = [
        f"authority: {authority_title}",
        f"type: {type_title}",
        f"num: {_normalize_text(item.get('num'))}",
        f"date: {date_text}",
        f"title: {_normalize_text(item.get('title'))}",
        f"id: {_normalize_text(item.get('id'))}",
        f"pubId: {_normalize_text(item.get('pubId'))}",
        f"regId: {_normalize_text(item.get('regId'))}",
        f"href: {_normalize_text(item.get('href'))}",
    ]
    return "\n".join(lines)


class PravoStavregionApiSource(BaseSource):
    """Fetches Stavropol regional NPAs from the public pravo.stavregion.ru listing API."""

    def fetch_items(self) -> list[CollectedItem]:
        params = {
            "first": _build_first_date_floor(),
            "loggingWith": "id",
            "timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
        }
        response = self.session.get(
            _API_ENDPOINT,
            params=params,
            timeout=self.config.request_timeout or REQUEST_TIMEOUT,
            verify=self.config.verify_ssl,
        )
        response.raise_for_status()

        try:
            payload = response.json()
        except ValueError as exc:
            self.logger.warning("pravo.stavregion API returned invalid JSON: %s", exc)
            return []

        if not isinstance(payload, list):
            self.logger.warning(
                "pravo.stavregion API returned unexpected payload type: %s",
                type(payload),
            )
            return []

        items: list[CollectedItem] = []
        seen_urls: set[str] = set()
        filtered_count = 0

        for row in payload:
            if not isinstance(row, Mapping):
                filtered_count += 1
                continue
            href = _normalize_text(row.get("href"))
            if not href:
                filtered_count += 1
                continue
            if not _is_agriculture_relevant(row):
                filtered_count += 1
                continue
            title = _build_title(row)
            if not title:
                filtered_count += 1
                continue
            url = _build_pdf_url(href)
            if url in seen_urls:
                filtered_count += 1
                continue
            seen_urls.add(url)
            items.append(
                CollectedItem(
                    source_name=self.config.name,
                    source_url=self.config.url,
                    level=self.config.level,
                    region=self.config.region,
                    title=title,
                    url=url,
                    published_at=_parse_date_array(row.get("date")),
                    document_type="pdf",
                    raw_text=_build_raw_text(row),
                )
            )
            if self.config.max_items is not None and len(items) >= self.config.max_items:
                break

        self.last_fetch_stats = {
            "links_found_count": len(payload),
            "links_filtered_count": filtered_count,
            "pdf_links_count": len(items),
        }
        self.logger.info("Fetched %s items from %s", len(items), self.config.name)
        return items
