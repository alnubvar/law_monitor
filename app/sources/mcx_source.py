from __future__ import annotations

from datetime import datetime, time, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.extractors.date_extractor import parse_russian_date
from app.models import CollectedItem
from app.sources.base import BaseSource

_MCX_BASE = "https://mcx.gov.ru"


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
        max_items = self.config.max_items

        for link in soup.select("a.b-siteNavListMobile-3lvl__link"):
            title = " ".join(link.stripped_strings).strip()
            href = (link.get("href") or "").strip()
            if not title or not href:
                continue
            url = urljoin(self.config.url, href)
            raw_text = (
                f"Минсельхоз России. Мера господдержки АПК: {title}\n"
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
                    published_at=None,
                    document_type="html",
                    raw_text=raw_text,
                )
            )
            if max_items is not None and len(items) >= max_items:
                break

        self.logger.info("Fetched %s measures from %s", len(items), self.config.name)
        return items

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
