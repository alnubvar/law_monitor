from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from app.models import CollectedItem
from app.sources.base import BaseSource

_API_ENDPOINT = "https://regulation.gov.ru/api/npalist"
_PAGE_SIZE = 20
_MAX_PAGES = 5

_FIELD_LABELS: list[tuple[str, str]] = [
    ("projectId", "Код"),
    ("department", "Министерство"),
    ("stage", "Стадия"),
    ("publishDate", "Дата публикации"),
    ("status", "Статус"),
    ("procedure", "Процедура"),
    ("startDiscussion", "Начало обсуждения"),
    ("endDiscussion", "Конец обсуждения"),
    ("problem", "Проблема"),
    ("objectives", "Цели"),
    ("rationale", "Обоснование"),
]

_DATE_FIELDS = {"publishDate", "startDiscussion", "endDiscussion"}


def _format_field_value(tag: str, value: str) -> str:
    if tag not in _DATE_FIELDS:
        return value
    dt = _parse_iso_date(value)
    return dt.strftime("%d.%m.%Y") if dt is not None else value


def _build_synthetic_text(project: ET.Element, pid: str, title: str) -> str:
    lines = [f"Проект НПА: {title}", f"ID: {pid}"]
    for tag, label in _FIELD_LABELS:
        value = (project.findtext(tag) or "").strip()
        if value:
            lines.append(f"{label}: {_format_field_value(tag, value)}")
    # When the project is in a discussion stage but lacks an endDiscussion date,
    # the rules engine would find no PROJECT_DISCUSSION_SIGNALS and silently
    # classify the item as background.  Inject the canonical marker so that
    # any stage containing "обсуждение" is correctly treated as an active
    # public discussion.
    stage_value = (project.findtext("stage") or "").strip().lower()
    if "обсуждение" in stage_value:
        lines.append("публичное обсуждение")
    return "\n".join(lines)


class RegulationGovSource(BaseSource):
    """Fetches recent NPA projects from the public regulation.gov.ru XML API."""

    def fetch_items(self) -> list[CollectedItem]:
        total_limit = self.config.max_items or _PAGE_SIZE
        page_size = min(_PAGE_SIZE, total_limit)
        items: list[CollectedItem] = []
        seen_ids: set[str] = set()
        offset = 0

        for _ in range(_MAX_PAGES):
            if len(items) >= total_limit:
                break
            request_limit = min(page_size, total_limit - len(items))
            url = f"{_API_ENDPOINT}?limit={request_limit}&offset={offset}&sort=desc"
            response = self.get(url)
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError as exc:
                self.logger.warning("Failed to parse XML from %s: %s", url, exc)
                return items

            projects = root.findall("project")
            if not projects:
                break

            new_items_count = 0
            for project in projects:
                if len(items) >= total_limit:
                    break
                pid = (project.get("id") or "").strip()
                if not pid or pid in seen_ids:
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
                        raw_text=_build_synthetic_text(project, pid, title),
                    )
                )
                seen_ids.add(pid)
                new_items_count += 1

            if new_items_count == 0:
                break

            offset += len(projects)
            total_count = _parse_int(root.get("total"))
            if len(projects) < request_limit:
                break
            if total_count is not None and offset >= total_count:
                break

        self.logger.info("Fetched %s items from %s", len(items), self.config.name)
        return items


def _parse_int(value: str | None) -> int | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    try:
        return int(normalized)
    except ValueError:
        return None


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
