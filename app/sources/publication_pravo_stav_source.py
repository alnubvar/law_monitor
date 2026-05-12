from __future__ import annotations

from datetime import datetime, timezone

from app.models import CollectedItem
from app.sources.base import BaseSource

_API_BASE = "http://publication.pravo.gov.ru/api/Documents"
_DOCUMENT_BASE = "http://publication.pravo.gov.ru/Document/View"

_STAVROPOL_AUTHORITIES: list[tuple[str, str]] = [
    ("3d93f00f-1af0-4669-8f98-0bff04215eb3", "Правительство Ставропольского края"),
    ("312c966b-0fca-4eb8-b084-1f80c7f5d1fe", "Губернатор Ставропольского края"),
    ("cc08a17d-628f-48c7-891c-0ddf540db83a", "Дума Ставропольского края"),
    ("ed16b61b-421b-4268-a87e-6c2cc131f949", "Министерство сельского хозяйства Ставропольского края"),
]

_MIN_PAGE_SIZE = 10


def _build_synthetic_text(doc: dict, authority_name: str) -> str:
    lines: list[str] = []
    complex_name = " ".join((doc.get("complexName") or "").split())
    if complex_name:
        lines.append(f"Акт: {complex_name}")
    name = " ".join((doc.get("name") or "").split())
    if name and name != complex_name:
        lines.append(f"Предмет: {name}")
    number = (doc.get("number") or "").strip()
    if number:
        lines.append(f"Номер: {number}")
    if authority_name:
        lines.append(f"Орган: {authority_name}")
    doc_date = _parse_date_field(doc.get("documentDate") or "")
    if doc_date:
        lines.append(f"Дата акта: {doc_date.strftime('%d.%m.%Y')}")
    pub_date = _parse_date_field(doc.get("publishDateShort") or "")
    if pub_date:
        lines.append(f"Дата публикации: {pub_date.strftime('%d.%m.%Y')}")
    pages = doc.get("pagesCount")
    if pages is not None:
        lines.append(f"Страниц: {pages}")
    return "\n".join(lines)


class PublicationPravoStavropolSource(BaseSource):
    """Fetches Stavropol Krai legal acts from the publication.pravo.gov.ru JSON API."""

    def fetch_items(self) -> list[CollectedItem]:
        num_authorities = len(_STAVROPOL_AUTHORITIES)
        per_authority = max(
            _MIN_PAGE_SIZE,
            (self.config.max_items or 40) // num_authorities,
        )

        items: list[CollectedItem] = []
        seen_urls: set[str] = set()

        for authority_id, authority_name in _STAVROPOL_AUTHORITIES:
            url = (
                f"{_API_BASE}"
                f"?SignatoryAuthorityId={authority_id}"
                f"&pageSize={per_authority}"
                f"&pageIndex=1"
            )
            try:
                response = self.get(url)
                data = response.json()
            except Exception as exc:
                self.logger.warning(
                    "Failed to fetch/parse JSON for authority %s (%s): %s",
                    authority_name,
                    authority_id,
                    exc,
                )
                continue

            if not isinstance(data, dict):
                self.logger.warning(
                    "Unexpected response type for authority %s: %s",
                    authority_name,
                    type(data),
                )
                continue

            authority_items = data.get("items", [])
            if not isinstance(authority_items, list):
                self.logger.warning(
                    "Unexpected items type for authority %s: %s",
                    authority_name,
                    type(authority_items),
                )
                continue

            for doc in authority_items:
                if not isinstance(doc, dict):
                    continue
                eo_number = (doc.get("eoNumber") or "").strip()
                if not eo_number:
                    continue
                title = " ".join(
                    (doc.get("complexName") or doc.get("name") or "").split()
                )
                if not title:
                    continue
                doc_url = f"{_DOCUMENT_BASE}/{eo_number}"
                if doc_url in seen_urls:
                    continue
                seen_urls.add(doc_url)
                published_at = _parse_date_field(doc.get("publishDateShort") or "")
                items.append(
                    CollectedItem(
                        source_name=self.config.name,
                        source_url=self.config.url,
                        level=self.config.level,
                        region=self.config.region,
                        title=title,
                        url=doc_url,
                        published_at=published_at,
                        document_type="html",
                        raw_text=_build_synthetic_text(doc, authority_name),
                    )
                )

        self.logger.info("Fetched %s items from %s", len(items), self.config.name)
        return items


def _parse_date_field(value: str) -> datetime | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None
