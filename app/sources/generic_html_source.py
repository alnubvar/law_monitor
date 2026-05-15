from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.extractors.date_extractor import extract_published_at_from_link_tag
from app.models import CollectedItem
from app.sources.base import BaseSource

DOCUMENT_EXTENSIONS = {
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "docx",
    ".xls": "xls",
    ".xlsx": "xlsx",
    ".zip": "zip",
    ".htm": "html",
    ".html": "html",
    ".php": "html",
    ".xml": "xml",
    ".rss": "xml",
}
GLOBAL_DENY_PATTERNS = (
    "sitemap",
    "about",
    "search",
    "archive",
    "persons",
    "person/",
    "photos",
    "photo/",
    "social",
    "rss",
    "comments",
    "catalogue",
    "catalog",
    "page=",
    "rugovclassifier",
    "structure",
    "ministries",
    "agencies",
)
SHORT_TITLE_SKIP_VALUES = {
    "показать еще",
    "показать ещё",
    "еще",
    "ещё",
    "more",
    "далее",
}
NUMERIC_TITLE_REGEX = re.compile(r"^\d{1,3}$")
GISP_LISTING_PATH_RE = re.compile(r"^/nmp/main/(?P<page>\d+)/?$", re.IGNORECASE)
GISP_MEASURE_PATH_RE = re.compile(r"^/nmp/measure/[^/?#]+/?$", re.IGNORECASE)
GISP_MAX_PAGINATION_PAGES = 5


class GenericHTMLSource(BaseSource):
    def fetch_items(self) -> list[CollectedItem]:
        if self._is_gisp_source():
            return self._fetch_gisp_items()
        response = self.get(self.config.url)
        soup = BeautifulSoup(response.text, "html.parser")
        return self._extract_items_from_soup(soup, response.url)

    def _fetch_gisp_items(self) -> list[CollectedItem]:
        items: list[CollectedItem] = []
        seen_urls: set[str] = set()
        aggregated_stats: dict[str, int | str] = {}
        next_url = self.config.url
        visited_pages: set[str] = set()

        for _ in range(GISP_MAX_PAGINATION_PAGES):
            remaining = self._remaining_item_budget(items)
            if remaining == 0:
                break
            response = self.get(next_url)
            if response.url in visited_pages:
                break
            visited_pages.add(response.url)
            soup = BeautifulSoup(response.text, "html.parser")
            page_items = self._extract_items_from_soup(
                soup,
                response.url,
                seen_urls=seen_urls,
                max_items_override=remaining,
            )
            self._merge_fetch_stats(aggregated_stats, self.last_fetch_stats)
            items.extend(page_items)
            if not page_items:
                break
            if self._remaining_item_budget(items) == 0:
                break
            next_page_url = self._next_gisp_page_url(response.url)
            if next_page_url is None or next_page_url in visited_pages:
                break
            next_url = next_page_url

        if aggregated_stats:
            aggregated_stats["items_collected_count"] = len(items)
            self.last_fetch_stats = aggregated_stats
        return items

    def _remaining_item_budget(self, items: list[CollectedItem]) -> int | None:
        max_items = self.config.max_items
        if max_items is None:
            return None
        return max(0, max_items - len(items))

    def _next_gisp_page_url(self, url: str) -> str | None:
        parsed = urlparse(url)
        match = GISP_LISTING_PATH_RE.fullmatch(parsed.path)
        if match is None:
            return None
        current_page = int(match.group("page"))
        next_path = GISP_LISTING_PATH_RE.sub(f"/nmp/main/{current_page + 1}", parsed.path)
        return parsed._replace(path=next_path).geturl()

    def _merge_fetch_stats(
        self,
        aggregate: dict[str, int | str],
        page_stats: dict[str, int | str],
    ) -> None:
        if not page_stats:
            return
        sample_keys = ("navigation", "archive", "external", "duplicate", "unsupported", "pdf_filtered", "docx_filtered")
        existing_samples = self._decode_filtered_samples(str(aggregate.get("filtered_samples", "")))
        new_samples = self._decode_filtered_samples(str(page_stats.get("filtered_samples", "")))
        for key, value in page_stats.items():
            if key == "filtered_samples":
                continue
            if isinstance(value, int):
                aggregate[key] = int(aggregate.get(key, 0)) + value
        for key in sample_keys:
            bucket = existing_samples.setdefault(key, [])
            for sample in new_samples.get(key, []):
                if sample in bucket:
                    continue
                if len(bucket) >= 2:
                    break
                bucket.append(sample)
        filtered_payload = {key: value for key, value in existing_samples.items() if value}
        aggregate["filtered_samples"] = json.dumps(filtered_payload, ensure_ascii=False) if filtered_payload else ""

    @staticmethod
    def _decode_filtered_samples(raw_value: str) -> dict[str, list[str]]:
        if not raw_value:
            return {}
        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        decoded: dict[str, list[str]] = {}
        for key, value in payload.items():
            if isinstance(value, list):
                decoded[str(key)] = [str(item) for item in value[:2]]
        return decoded

    def _is_gisp_source(self) -> bool:
        parsed = urlparse(self.config.url.lower())
        return parsed.netloc == "gisp.gov.ru" and GISP_LISTING_PATH_RE.fullmatch(parsed.path) is not None

    def _extract_items_from_soup(
        self,
        soup: BeautifulSoup,
        base_url: str,
        *,
        seen_urls: set[str] | None = None,
        max_items_override: int | None = None,
    ) -> list[CollectedItem]:
        items: list[CollectedItem] = []
        local_seen_urls = seen_urls if seen_urls is not None else set()
        max_items = self.config.max_items if max_items_override is None else max_items_override
        links_found_count = 0
        links_filtered_count = 0
        document_type_counts: dict[str, int] = {"pdf": 0, "docx": 0, "doc": 0, "xls": 0, "xlsx": 0, "zip": 0, "html": 0, "xml": 0, "unknown": 0}
        filtered_reason_counts: dict[str, int] = {
            "navigation": 0,
            "archive": 0,
            "external": 0,
            "duplicate": 0,
            "unsupported": 0,
            "pdf_filtered": 0,
            "docx_filtered": 0,
        }
        filtered_samples: dict[str, list[str]] = {key: [] for key in filtered_reason_counts}

        for link in soup.find_all("a", href=True):
            links_found_count += 1
            if not isinstance(link, Tag):
                links_filtered_count += 1
                self._track_filter(filtered_reason_counts, filtered_samples, "unsupported", "")
                continue
            normalized_url = self._normalize_url(link.get("href", ""), base_url)
            if not normalized_url:
                links_filtered_count += 1
                self._track_filter(filtered_reason_counts, filtered_samples, "unsupported", link.get("href", ""))
                continue
            if normalized_url in local_seen_urls:
                links_filtered_count += 1
                self._track_filter(filtered_reason_counts, filtered_samples, "duplicate", normalized_url)
                continue

            title = self._extract_title(link, normalized_url)
            if not self._should_include_title(title):
                links_filtered_count += 1
                self._track_filter(filtered_reason_counts, filtered_samples, "navigation", normalized_url)
                continue
            if not self._should_include_url(normalized_url, base_url, title):
                links_filtered_count += 1
                reason = self._guess_filter_reason(normalized_url, base_url, title)
                self._track_filter(filtered_reason_counts, filtered_samples, reason, normalized_url)
                continue
            document_type = self._detect_document_type(normalized_url)
            if document_type not in document_type_counts:
                document_type = "unknown"
            document_type_counts[document_type] += 1
            items.append(
                CollectedItem(
                    source_name=self.config.name,
                    source_url=self.config.url,
                    level=self.config.level,
                    region=self.config.region,
                    title=title,
                    url=normalized_url,
                    published_at=self._extract_published_at(link, normalized_url),
                    document_type=document_type,
                )
            )
            local_seen_urls.add(normalized_url)
            if max_items is not None and len(items) >= max_items:
                break

        self.last_fetch_stats = {
            "links_found_count": links_found_count,
            "links_filtered_count": links_filtered_count,
            "items_collected_count": len(items),
            "pdf_links_count": document_type_counts["pdf"],
            "docx_links_count": document_type_counts["docx"],
            "doc_links_count": document_type_counts["doc"],
            "html_links_count": document_type_counts["html"],
            "xml_links_count": document_type_counts["xml"],
            "unknown_links_count": document_type_counts["unknown"],
            "navigation_filtered_count": filtered_reason_counts["navigation"],
            "archive_filtered_count": filtered_reason_counts["archive"],
            "external_filtered_count": filtered_reason_counts["external"],
            "duplicate_filtered_count": filtered_reason_counts["duplicate"],
            "unsupported_filtered_count": filtered_reason_counts["unsupported"],
            "pdf_filtered_count": filtered_reason_counts["pdf_filtered"],
            "docx_filtered_count": filtered_reason_counts["docx_filtered"],
            "filtered_samples": json.dumps(
                {key: value for key, value in filtered_samples.items() if value},
                ensure_ascii=False,
            ),
        }
        self.logger.info(
            "Fetched %s items from %s", len(items), self.config.name
        )
        return items

    @staticmethod
    def _track_filter(
        counters: dict[str, int],
        samples: dict[str, list[str]],
        reason: str,
        candidate: str,
    ) -> None:
        if reason not in counters:
            return
        counters[reason] += 1
        value = (candidate or "").strip()
        if not value:
            return
        bucket = samples.get(reason)
        if bucket is None:
            return
        if len(bucket) >= 2:
            return
        if value not in bucket:
            bucket.append(value[:140])

    def _guess_filter_reason(self, url: str, base_url: str, title: str) -> str:
        document_type = self._detect_document_type(url)
        parsed = urlparse(url)
        path = parsed.path.lower()
        query = parsed.query.lower()
        base_host = urlparse(base_url).netloc.lower()
        target_host = parsed.netloc.lower()
        if target_host != base_host and document_type not in {"pdf", "doc", "docx"}:
            reason = "external"
        elif "archive" in path or "archive" in query:
            reason = "archive"
        elif any(marker in f"{url} {title}".lower() for marker in GLOBAL_DENY_PATTERNS):
            reason = "navigation"
        elif document_type == "unknown":
            reason = "unsupported"
        else:
            reason = "navigation"
        if document_type == "pdf":
            return "pdf_filtered"
        if document_type == "docx":
            return "docx_filtered"
        return reason

    def _normalize_url(self, href: str, base_url: str) -> str | None:
        href = (href or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            return None
        absolute_url = urljoin(base_url, href)
        parsed = urlparse(absolute_url)
        if parsed.scheme not in {"http", "https"}:
            return None
        normalized = parsed._replace(fragment="").geturl()
        return normalized

    def _extract_title(self, link: Tag, normalized_url: str) -> str:
        title = " ".join(link.stripped_strings)
        if title:
            return title[:500]
        title_attr = link.get("title")
        if title_attr:
            return str(title_attr).strip()[:500]
        path = PurePosixPath(urlparse(normalized_url).path)
        if path.name:
            return path.name
        return normalized_url

    def _detect_document_type(self, url: str) -> str:
        path = urlparse(url).path.lower()
        extension = PurePosixPath(path).suffix.lower()
        if extension in DOCUMENT_EXTENSIONS:
            return DOCUMENT_EXTENSIONS[extension]
        if not extension:
            return "html"
        return "unknown"

    def _should_include_url(self, url: str, base_url: str, title: str) -> bool:
        if self._is_gisp_source():
            return self._should_include_gisp_url(url, base_url)
        base_host = urlparse(base_url).netloc.lower()
        target = urlparse(url)
        target_host = target.netloc.lower()
        document_type = self._detect_document_type(url)
        if not (target_host == base_host or document_type in {"pdf", "doc", "docx"}):
            return False

        combined = f"{url} {title}".lower()
        if url.rstrip("/") == base_url.rstrip("/"):
            return False
        if any(pattern in combined for pattern in GLOBAL_DENY_PATTERNS):
            return False
        if self.config.allow_patterns:
            patterns = [pattern.lower() for pattern in self.config.allow_patterns]
            if not any(pattern in combined for pattern in patterns):
                return False
        if self.config.deny_patterns:
            patterns = [pattern.lower() for pattern in self.config.deny_patterns]
            if any(pattern in combined for pattern in patterns):
                return False
        return True

    def _should_include_gisp_url(self, url: str, base_url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        if parsed.netloc.lower() != "gisp.gov.ru":
            return False
        if url.rstrip("/") == base_url.rstrip("/"):
            return False
        return GISP_MEASURE_PATH_RE.fullmatch(parsed.path) is not None

    def _should_include_title(self, title: str) -> bool:
        normalized = title.strip().lower()
        if not normalized:
            return False
        if normalized in SHORT_TITLE_SKIP_VALUES:
            return False
        if len(normalized) < 4 and NUMERIC_TITLE_REGEX.fullmatch(normalized):
            return False
        if len(normalized) < 4 and normalized in {"rss", "pdf", "doc"}:
            return False
        return True

    def _extract_published_at(self, link: Tag, normalized_url: str):
        extracted = extract_published_at_from_link_tag(
            link,
            source_name=self.config.name,
            url=normalized_url,
        )
        if extracted is None:
            return None
        from datetime import datetime, time, timezone

        return datetime.combine(extracted, time.min, tzinfo=timezone.utc)
