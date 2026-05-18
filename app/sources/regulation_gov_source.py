from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.models import CollectedItem
from app.sources.base import BaseSource

_API_ENDPOINT = "https://regulation.gov.ru/api/npalist"
_DETAIL_CARD_ENDPOINT = "https://regulation.gov.ru/api/public/PublicProjects/GetCardInfo/{pid}"
_DETAIL_STAGES_ENDPOINT = "https://regulation.gov.ru/api/public/PublicProjects/GetProjectStages/{pid}"
_DETAIL_STAGE_INFO_ENDPOINT = (
    "https://regulation.gov.ru/api/public/PublicProjects/GetProjectStageInfo/{pid}/{stage}"
)
_PAGE_SIZE = 20
_MAX_PAGES = 5
_MAX_DETAIL_ENRICHMENTS = 10

_DETAIL_ENRICHMENT_MARKERS = (
    "апк",
    "агро",
    "сельск",
    "субсид",
    "грант",
    "льгот",
    "кредит",
    "зерн",
    "животновод",
    "растениевод",
    "удобр",
    "ветерин",
    "экспорт",
    "импорт",
    "пошлин",
    "квот",
    "логист",
    "продоволь",
    "госпрограмм",
)

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


@dataclass(slots=True)
class _DetailEnrichment:
    stage_title: str | None = None
    issuer_name: str | None = None
    responsible_name: str | None = None
    public_discussion_start: str | None = None
    public_discussion_end: str | None = None
    anti_corruption_start: str | None = None
    anti_corruption_end: str | None = None
    effective_date: str | None = None
    file_names: list[str] = field(default_factory=list)

    def has_data(self) -> bool:
        return any(
            (
                self.stage_title,
                self.issuer_name,
                self.responsible_name,
                self.public_discussion_start,
                self.public_discussion_end,
                self.anti_corruption_start,
                self.anti_corruption_end,
                self.effective_date,
                self.file_names,
            )
        )


def _format_field_value(tag: str, value: str) -> str:
    if tag not in _DATE_FIELDS:
        return value
    dt = _parse_iso_date(value)
    return dt.strftime("%d.%m.%Y") if dt is not None else value


def _build_synthetic_text(
    project: ET.Element,
    pid: str,
    title: str,
    enrichment: _DetailEnrichment | None = None,
) -> str:
    lines = [f"Проект НПА: {title}", f"ID: {pid}"]
    for tag, label in _FIELD_LABELS:
        value = (project.findtext(tag) or "").strip()
        if value:
            lines.append(f"{label}: {_format_field_value(tag, value)}")
    if enrichment is not None:
        base_department = (project.findtext("department") or "").strip()
        if enrichment.stage_title:
            lines.append(f"Этап портала: {enrichment.stage_title}")
        if enrichment.issuer_name and enrichment.issuer_name != base_department:
            lines.append(f"Орган-разработчик: {enrichment.issuer_name}")
        if enrichment.responsible_name:
            lines.append(f"Ответственный: {enrichment.responsible_name}")
        public_discussion_range = _format_date_range(
            enrichment.public_discussion_start,
            enrichment.public_discussion_end,
        )
        if public_discussion_range:
            lines.append(f"Публичное обсуждение: {public_discussion_range}")
        anti_corruption_range = _format_date_range(
            enrichment.anti_corruption_start,
            enrichment.anti_corruption_end,
        )
        if anti_corruption_range:
            lines.append(
                "Независимая антикоррупционная экспертиза: "
                f"{anti_corruption_range}"
            )
        if enrichment.effective_date:
            lines.append(f"Планируемое вступление в силу: {enrichment.effective_date}")
        if enrichment.file_names:
            lines.append("Файлы этапа: " + "; ".join(enrichment.file_names))
    # When the project is in a discussion stage but lacks an endDiscussion date,
    # the rules engine would find no PROJECT_DISCUSSION_SIGNALS and silently
    # classify the item as background.  Inject the canonical marker so that
    # any stage containing "обсуждение" is correctly treated as an active
    # public discussion.
    stage_value = " ".join(
        value
        for value in (
            (project.findtext("stage") or "").strip().lower(),
            (enrichment.stage_title or "").strip().lower() if enrichment else "",
        )
        if value
    )
    if "обсуждение" in stage_value or (
        enrichment is not None
        and (enrichment.public_discussion_start or enrichment.public_discussion_end)
    ):
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
        detail_candidates_count = 0
        detail_attempted_count = 0
        detail_success_count = 0
        detail_failed_count = 0
        detail_skipped_limit_count = 0

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
                enrichment = None
                if _is_detail_enrichment_candidate(project, title):
                    detail_candidates_count += 1
                    if detail_attempted_count < _MAX_DETAIL_ENRICHMENTS:
                        detail_attempted_count += 1
                        try:
                            enrichment = self._fetch_detail_enrichment(pid)
                        except Exception as exc:
                            detail_failed_count += 1
                            self.logger.warning(
                                "Regulation.gov detail enrichment failed for project %s: %s",
                                pid,
                                exc,
                            )
                        else:
                            if enrichment is not None and enrichment.has_data():
                                detail_success_count += 1
                    else:
                        detail_skipped_limit_count += 1
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
                        raw_text=_build_synthetic_text(project, pid, title, enrichment),
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

        self.last_fetch_stats = {
            "links_found_count": len(items),
            "detail_enrichment_candidates_count": detail_candidates_count,
            "detail_enrichment_attempted_count": detail_attempted_count,
            "detail_enrichment_success_count": detail_success_count,
            "detail_enrichment_failed_count": detail_failed_count,
            "detail_enrichment_skipped_limit_count": detail_skipped_limit_count,
        }
        self.logger.info("Fetched %s items from %s", len(items), self.config.name)
        return items

    def _fetch_detail_enrichment(self, pid: str) -> _DetailEnrichment | None:
        enrichment = _DetailEnrichment()
        card_payload = self._load_detail_json_safe(
            _DETAIL_CARD_ENDPOINT.format(pid=pid),
            pid=pid,
            label="card",
        )
        if isinstance(card_payload, dict):
            issuer_name = _text_from_mapping(card_payload, "developerDepartment", "description")
            if issuer_name:
                enrichment.issuer_name = issuer_name

        stages_payload = self._load_detail_json_safe(
            _DETAIL_STAGES_ENDPOINT.format(pid=pid),
            pid=pid,
            label="stages",
        )
        current_stage = _find_current_stage(stages_payload)
        if current_stage is not None:
            enrichment.stage_title = _text(current_stage.get("title"))
            stage_code = _text(current_stage.get("stage"))
            if stage_code:
                stage_info_payload = self._load_detail_json_safe(
                    _DETAIL_STAGE_INFO_ENDPOINT.format(pid=pid, stage=stage_code),
                    pid=pid,
                    label=f"stage_info:{stage_code}",
                )
                if isinstance(stage_info_payload, dict):
                    _apply_stage_info(enrichment, stage_info_payload)

        return enrichment if enrichment.has_data() else None

    def _load_detail_json(self, url: str) -> dict[str, Any] | list[Any] | None:
        response = self.get(url)
        try:
            return json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at {url}") from exc

    def _load_detail_json_safe(
        self,
        url: str,
        *,
        pid: str,
        label: str,
    ) -> dict[str, Any] | list[Any] | None:
        try:
            return self._load_detail_json(url)
        except Exception as exc:
            self.logger.warning(
                "Regulation.gov detail %s request failed for project %s: %s",
                label,
                pid,
                exc,
            )
            return None


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


def _format_date(value: str | None) -> str | None:
    if not value:
        return None
    dt = _parse_iso_date(value)
    return dt.strftime("%d.%m.%Y") if dt is not None else value.strip() or None


def _format_date_range(start: str | None, end: str | None) -> str | None:
    if start and end:
        return f"{start} - {end}"
    return start or end


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _text_from_mapping(payload: dict[str, Any], *path: str) -> str | None:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return _text(current)


def _first_text_value(values: Any) -> str | None:
    if isinstance(values, list):
        for value in values:
            result = _first_text_value(value)
            if result:
                return result
        return None
    return _text(values)


def _file_names_from_values(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    names: list[str] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        name = _text(value.get("description"))
        if name and name not in names:
            names.append(name)
    return names


def _is_detail_enrichment_candidate(project: ET.Element, title: str) -> bool:
    fields = [
        title,
        project.findtext("department") or "",
        project.findtext("procedure") or "",
        project.findtext("problem") or "",
        project.findtext("objectives") or "",
        project.findtext("rationale") or "",
        project.findtext("kind") or "",
    ]
    haystack = " ".join(value.lower() for value in fields if value).strip()
    if not haystack:
        return False
    return any(marker in haystack for marker in _DETAIL_ENRICHMENT_MARKERS)


def _find_current_stage(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, list):
        return None
    for stage in payload:
        if isinstance(stage, dict) and stage.get("isCurrent") is True:
            return stage
    return None


def _apply_stage_info(enrichment: _DetailEnrichment, payload: dict[str, Any]) -> None:
    values = payload.get("values")
    if not isinstance(values, list):
        return
    for entry in values:
        if not isinstance(entry, dict):
            continue
        description = (entry.get("description") or "").strip().lower()
        entry_values = entry.get("values")
        if entry.get("type") == "File":
            for name in _file_names_from_values(entry_values):
                if name not in enrichment.file_names:
                    enrichment.file_names.append(name)
            continue
        if "ответственный за разработку" in description:
            enrichment.responsible_name = _first_text_value(entry_values) or enrichment.responsible_name
            continue
        if "планируемый срок вступления в силу" in description:
            enrichment.effective_date = _format_date(_first_text_value(entry_values))
            continue
        if (
            "дата начала публичного обсуждения" in description
            or "дата начала общественного обсуждения" in description
        ):
            enrichment.public_discussion_start = _format_date(_first_text_value(entry_values))
            continue
        if (
            "дата окончания публичного обсуждения" in description
            or "дата окончания общественного обсуждения" in description
        ):
            enrichment.public_discussion_end = _format_date(_first_text_value(entry_values))
            continue
        if "дата начала независимой антикоррупционной экспертизы" in description:
            enrichment.anti_corruption_start = _format_date(_first_text_value(entry_values))
            continue
        if "дата окончания независимой антикоррупционной экспертизы" in description:
            enrichment.anti_corruption_end = _format_date(_first_text_value(entry_values))
