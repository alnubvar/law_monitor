from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from app.models import CollectedItem
from app.sources.base import BaseSource

_API_ENDPOINT = "https://regulation.gov.ru/api/npalist"


class RegulationGovSource(BaseSource):
    """Fetches recent NPA projects from the public regulation.gov.ru XML API."""

    def fetch_items(self) -> list[CollectedItem]:
        limit = self.config.max_items or 20
        url = f"{_API_ENDPOINT}?limit={limit}&sort=desc"
        response = self.get(url)
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            self.logger.warning("Failed to parse XML from %s: %s", url, exc)
            return []

        items: list[CollectedItem] = []
        for project in root.findall("project"):
            pid = (project.get("id") or "").strip()
            if not pid:
                continue
            title = (project.findtext("title") or "").strip()
            if not title:
                continue
            published_at = _parse_iso_date(project.findtext("publishDate") or "")
            items.append(
                CollectedItem(
                    source_name=self.config.name,
                    source_url=self.config.url,
                    level=self.config.level,
                    region=self.config.region,
                    title=title,
                    url=f"https://regulation.gov.ru/projects/{pid}",
                    published_at=published_at,
                    document_type="html",
                )
            )
        self.logger.info("Fetched %s items from %s", len(items), self.config.name)
        return items


def _parse_iso_date(value: str) -> datetime | None:
    """Parse ISO 8601 datetime from the API (e.g. '2026-05-12T08:49:21.343Z').

    Truncates sub-second precision and strips offset/Z for broad Python version compat.
    """
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
