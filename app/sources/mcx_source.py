from __future__ import annotations

import re
from datetime import datetime, time, timezone
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

import requests
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
    "/subsidy-credit-2017/",
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
_DETAIL_NOISE_TAGS = ("script", "style", "nav", "footer", "header", "noscript", "svg", "form", "aside")
_DETAIL_WHITESPACE_RE = re.compile(r"\s+")
_DETAIL_TEXT_MAX_LENGTH = 4000
_DETAIL_LINKS_MAX_COUNT = 3
_DETAIL_SERVER_ERROR_THRESHOLD = 3


class McxSource(BaseSource):
    """Fetches mcx.gov.ru listing pages and selectively enriches shortlisted HTML items."""

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
            raw_text = self._build_measure_raw_text(title, url)
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
        self._enrich_items_with_details(items)
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
        suffix = PurePosixPath(path).suffix.lower()
        if suffix and suffix not in {".pdf", ".doc", ".docx"}:
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

            raw_text = self._build_news_raw_text(title, url, date_obj)

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

        self._enrich_items_with_details(items)
        self.logger.info("Fetched %s news items from %s", len(items), self.config.name)
        return items

    def _enrich_items_with_details(self, items: list[CollectedItem]) -> None:
        server_errors = 0
        detail_disabled = False
        for item in items:
            if item.document_type != "html":
                continue
            if detail_disabled:
                continue
            if not self._should_fetch_detail(item.url):
                continue
            base_raw_text = item.raw_text or ""
            try:
                response = self.get(item.url)
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status is not None and status >= 500:
                    server_errors += 1
                    self.logger.warning(
                        "McxSource detail fetch HTTP %s for %s (%d/%d)",
                        status, item.url, server_errors, _DETAIL_SERVER_ERROR_THRESHOLD,
                    )
                    if server_errors >= _DETAIL_SERVER_ERROR_THRESHOLD:
                        detail_disabled = True
                        self.logger.warning(
                            "McxSource: detail fetch disabled for this run after %d server errors",
                            server_errors,
                        )
                else:
                    self.logger.warning(
                        "McxSource detail fetch HTTP %s for %s", status, item.url
                    )
                continue
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                server_errors += 1
                self.logger.warning(
                    "McxSource detail fetch connectivity error for %s: %s (%d/%d)",
                    item.url, exc, server_errors, _DETAIL_SERVER_ERROR_THRESHOLD,
                )
                if server_errors >= _DETAIL_SERVER_ERROR_THRESHOLD:
                    detail_disabled = True
                    self.logger.warning(
                        "McxSource: detail fetch disabled for this run after %d infrastructure errors",
                        server_errors,
                    )
                continue
            except Exception as exc:
                self.logger.warning("McxSource detail fetch failed for %s: %s", item.url, exc)
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            detail_text = self._extract_detail_text(soup, item.title)
            related_links = self._extract_safe_related_links(soup, item.url, current_url=item.url)
            if not detail_text and not related_links:
                continue
            parts = [base_raw_text]
            if detail_text:
                parts.append(detail_text)
            if related_links:
                parts.append("Связанные документы: " + "; ".join(related_links))
            item.raw_text = "\n".join(part for part in parts if part)

    def _should_fetch_detail(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        if parsed.netloc.lower() != urlparse(_MCX_BASE).netloc:
            return False
        suffix = PurePosixPath(parsed.path).suffix.lower()
        return suffix not in {".pdf", ".doc", ".docx"}

    def _extract_detail_text(self, soup: BeautifulSoup, title: str) -> str:
        for tag_name in _DETAIL_NOISE_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()
        container = soup.select_one("article") or soup.select_one("main") or soup.body or soup
        text = _DETAIL_WHITESPACE_RE.sub(" ", " ".join(container.stripped_strings)).strip()
        if not text:
            return ""
        normalized_title = _DETAIL_WHITESPACE_RE.sub(" ", title).strip()
        lowered_title = normalized_title.lower()
        while normalized_title and text.lower().startswith(lowered_title):
            text = text[len(normalized_title):].strip(" .:-")
        if len(text) > _DETAIL_TEXT_MAX_LENGTH:
            text = text[:_DETAIL_TEXT_MAX_LENGTH].rsplit(" ", 1)[0].rstrip(" .,:;") + "..."
        return text

    def _extract_safe_related_links(
        self,
        soup: BeautifulSoup,
        base_url: str,
        *,
        current_url: str,
    ) -> list[str]:
        links: list[str] = []
        seen: set[str] = set()
        for link in soup.find_all("a", href=True):
            href = (link.get("href") or "").strip()
            if not href:
                continue
            normalized_url = urljoin(base_url, href)
            if normalized_url == current_url or normalized_url in seen:
                continue
            parsed = urlparse(normalized_url)
            if parsed.scheme not in {"http", "https"}:
                continue
            if parsed.netloc.lower() != urlparse(_MCX_BASE).netloc:
                continue
            path = parsed.path.lower()
            suffix = PurePosixPath(path).suffix.lower()
            is_document_file = suffix in {".pdf", ".doc", ".docx"}
            is_document_page = path.startswith("/docs/")
            if not (is_document_file or is_document_page):
                continue
            seen.add(normalized_url)
            links.append(normalized_url)
            if len(links) >= _DETAIL_LINKS_MAX_COUNT:
                break
        return links

    @staticmethod
    def _build_measure_raw_text(title: str, url: str) -> str:
        return (
            f"Минсельхоз России. Мера господдержки АПК: {title}\n"
            f"Источник: {url}"
        )

    @staticmethod
    def _build_news_raw_text(title: str, url: str, date_obj) -> str:
        date_line = (
            f"\nДата: {date_obj.strftime('%d.%m.%Y')}" if date_obj else ""
        )
        return (
            f"Минсельхоз России. Новость АПК: {title}"
            f"{date_line}\n"
            f"Источник: {url}"
        )
