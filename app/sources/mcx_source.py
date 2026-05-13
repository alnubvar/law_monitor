from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.extractors.date_extractor import parse_russian_date
from app.models import CollectedItem
from app.sources.base import BaseSource

_MCX_BASE = "https://mcx.gov.ru"
_MEASURE_SUPPORT_MARKERS = (
    "господдерж",
    "государственная поддерж",
    "мера поддержки",
    "меры поддержки",
    "субсид",
    "льгот",
    "кредит",
    "лизинг",
    "компенсац",
    "возмещ",
    "отбор",
    "заяв",
    "программ",
    "тариф",
    "апк",
    "агропром",
    "экспорт",
    "логист",
)
_MEASURE_DENY_PATH_FRAGMENTS = (
    "/ministry/",
    "/press-service/",
    "/about/",
    "/contacts",
    "/contact",
    "/structure",
    "/leadership",
    "/activity/goals",
    "/activity/tasks",
    "/photos",
    "/photo",
    "/videos",
    "/video",
    "/reports",
    "/report",
    "/fish",
    "/program-2013-",
)
_MEASURE_DENY_TITLE_FRAGMENTS = (
    "биография",
    "фотоотчет",
    "фотоотчёт",
    "видеоотчет",
    "видеоотчёт",
    "цели и задачи",
    "контакты",
    "структура",
    "рыбохозяйственн",
)
_MEASURE_LISTING_PATHS = {
    "/activity/state-support/",
    "/activity/state-support/measures/",
    "/activity/state-support/programs/",
}


class McxSource(BaseSource):
    """Fetches mcx.gov.ru listing pages and builds CollectedItem with synthetic raw_text.

    Makes exactly one HTTP request (the listing page). Never fetches detail pages.
    """

    def fetch_items(self) -> list[CollectedItem]:
        response = self.get(self.config.url)
        soup = BeautifulSoup(response.text, "html.parser")

        if "/activity/state-support/measures/" in self.config.url:
            return self._parse_measures(soup)
        if "/press-service/news/" in self.config.url:
            return self._parse_news(soup)

        self.logger.warning("McxSource: unrecognized URL pattern %s", self.config.url)
        return []

    def _parse_measures(self, soup: BeautifulSoup) -> list[CollectedItem]:
        items: list[CollectedItem] = []
        seen_urls: set[str] = set()
        max_items = self.config.max_items
        links_found_count = 0
        links_filtered_count = 0
        duplicate_filtered_count = 0

        for link in soup.find_all("a", href=True):
            links_found_count += 1
            title = " ".join(link.stripped_strings).strip()
            href = (link.get("href") or "").strip()
            if not title or not href:
                links_filtered_count += 1
                continue
            url = urljoin(self.config.url, href)
            if url in seen_urls:
                duplicate_filtered_count += 1
                links_filtered_count += 1
                continue
            if not self._should_include_measure_link(title, url):
                links_filtered_count += 1
                continue
            raw_text = (
                f"Минсельхоз России. Мера господдержки АПК: {title}\n"
                f"Источник: {url}"
            )
            seen_urls.add(url)
            items.append(
                CollectedItem(
                    source_name=self.config.name,
                    source_url=self.config.url,
                    level=self.config.level,
                    region=self.config.region,
                    title=title,
                    url=url,
                    published_at=None,
                    document_type="html",
                    raw_text=raw_text,
                )
            )
            if max_items is not None and len(items) >= max_items:
                break

        self.last_fetch_stats = {
            "links_found_count": links_found_count,
            "links_filtered_count": links_filtered_count,
            "duplicate_filtered_count": duplicate_filtered_count,
            "html_links_count": len(items),
        }
        self.logger.info("Fetched %s measures from %s", len(items), self.config.name)
        return items

    def _should_include_measure_link(self, title: str, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        if parsed.netloc.lower() != urlparse(_MCX_BASE).netloc:
            return False

        path = parsed.path.lower()
        normalized_path = path.rstrip("/") + "/"
        if normalized_path in _MEASURE_LISTING_PATHS:
            return False
        if PurePosixPath(path).suffix:
            return False

        title_text = " ".join(title.lower().split())
        combined = f"{title_text} {url.lower()}"
        if any(fragment in path for fragment in _MEASURE_DENY_PATH_FRAGMENTS):
            return False
        if any(fragment in title_text for fragment in _MEASURE_DENY_TITLE_FRAGMENTS):
            return False
        if self.config.deny_patterns and any(
            pattern.lower() in combined for pattern in self.config.deny_patterns
        ):
            return False

        has_support_marker = any(marker in combined for marker in _MEASURE_SUPPORT_MARKERS)
        if not has_support_marker:
            return False

        if path.startswith("/activity/state-support/"):
            return True
        if path.startswith("/docs/"):
            return True

        if not self.config.allow_patterns:
            return False
        return any(pattern.lower() in combined for pattern in self.config.allow_patterns)

    def _parse_news(self, soup: BeautifulSoup) -> list[CollectedItem]:
        items: list[CollectedItem] = []
        max_items = self.config.max_items

        for li in soup.select("li.newsList__item"):
            title_tag = li.select_one("a.newsList__title")
            if not title_tag:
                continue
            title = " ".join(title_tag.stripped_strings).strip()
            href = (title_tag.get("href") or "").strip()
            if not title or not href:
                continue
            url = urljoin(self.config.url, href)

            container_text = " ".join(li.stripped_strings)
            date_obj = parse_russian_date(container_text[:80])
            published_at: datetime | None = None
            if date_obj is not None:
                published_at = datetime.combine(date_obj, time.min, tzinfo=timezone.utc)

            date_line = (
                f"\nДата: {date_obj.strftime('%d.%m.%Y')}" if date_obj else ""
            )
            raw_text = (
                f"Минсельхоз России. Новость АПК: {title}"
                f"{date_line}\n"
                f"Источник: {url}"
            )

            items.append(
                CollectedItem(
                    source_name=self.config.name,
                    source_url=self.config.url,
                    level=self.config.level,
                    region=self.config.region,
                    title=title,
                    url=url,
                    published_at=published_at,
                    document_type="html",
                    raw_text=raw_text,
                )
            )
            if max_items is not None and len(items) >= max_items:
                break

        self.logger.info("Fetched %s news items from %s", len(items), self.config.name)
        return items
