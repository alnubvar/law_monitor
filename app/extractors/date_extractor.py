from __future__ import annotations

from datetime import date, datetime, time, timezone
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

RUSSIAN_MONTHS = {
    "января": 1,
    "январь": 1,
    "февраля": 2,
    "февраль": 2,
    "марта": 3,
    "март": 3,
    "апреля": 4,
    "апрель": 4,
    "мая": 5,
    "май": 5,
    "июня": 6,
    "июнь": 6,
    "июля": 7,
    "июль": 7,
    "августа": 8,
    "август": 8,
    "сентября": 9,
    "сентябрь": 9,
    "октября": 10,
    "октябрь": 10,
    "ноября": 11,
    "ноябрь": 11,
    "декабря": 12,
    "декабрь": 12,
}
PUBLICATION_MARKERS = (
    "опублик",
    "публикац",
    "размещ",
    "создан",
    "дата публикации",
    "publication",
    "published",
)
NEGATIVE_DATE_MARKERS = (
    "прием заяв",
    "приём заяв",
    "заявки принима",
    "срок подачи",
    "конкурсн",
    "отбор",
    "обсужден",
    "срок кредита",
    "срок займа",
    "срок действия",
    "до 12 месяцев",
    "до 5 лет",
    "до 2030 года",
)
HTML_DATE_HINT_RE = re.compile(r"(date|time|publish|posted|created)", re.IGNORECASE)
ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
SLASH_OR_DOT_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)")
RUSSIAN_DATE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s+"
    r"(января|январь|февраля|февраль|марта|март|апреля|апрель|мая|май|июня|июнь|"
    r"июля|июль|августа|август|сентября|сентябрь|октября|октябрь|ноября|ноябрь|"
    r"декабря|декабрь)"
    r"\s+(\d{4})(?!\d)",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
GOVERNMENT_REAL_PAGE_PATH_RE = re.compile(r"^/(?:news|docs)/\d+/?$")
GOVERNMENT_LEADING_DATE_TIME_RE = re.compile(
    r"(\d{1,2}\s+"
    r"(?:января|январь|февраля|февраль|марта|март|апреля|апрель|мая|май|июня|июнь|"
    r"июля|июль|августа|август|сентября|сентябрь|октября|октябрь|ноября|ноябрь|"
    r"декабря|декабрь)\s+\d{4})\s+\d{1,2}:\d{2}",
    re.IGNORECASE,
)
ADMKRAI_PUBLICATION_CONTEXT_RE = re.compile(
    r"(?:дата\s+публикации|дата\s+опубликования|опубликовано|опубликован|размещено|размещен|размещён)"
    r"[:\s,]+(.{0,120})",
    re.IGNORECASE,
)
ADMKRAI_BARE_DATE_RE = re.compile(
    r"^(?:\d{1,2}[./]\d{1,2}[./]\d{4}|"
    r"\d{1,2}\s+"
    r"(?:января|январь|февраля|февраль|марта|март|апреля|апрель|мая|май|июня|июнь|"
    r"июля|июль|августа|август|сентября|сентябрь|октября|октябрь|ноября|ноябрь|"
    r"декабря|декабрь)\s+\d{4})(?:\s+\d{1,2}:\d{2})?$",
    re.IGNORECASE,
)
ADMKRAI_HEADER_DATE_RE = re.compile(
    r"(?:п\s*р\s*и\s*к\s*а\s*з|постановлени[ея]|распоряжени[ея])\s*"
    r"(?:№\s*[\w./-]+\s*)?(?:от\s*)?"
    r"(\d{1,2}[./]\d{1,2}[./]\d{4}|"
    r"\d{1,2}\s+"
    r"(?:января|январь|февраля|февраль|марта|март|апреля|апрель|мая|май|июня|июнь|"
    r"июля|июль|августа|август|сентября|сентябрь|октября|октябрь|ноября|ноябрь|"
    r"декабря|декабрь)\s+\d{4})",
    re.IGNORECASE,
)
ADMKRAI_UNSAFE_DATE_MARKERS = (
    "приказ от",
    "постановление от",
    "распоряжение от",
    "областной закон от",
    "прием заяв",
    "приём заяв",
    "заявки принима",
    "срок подачи",
    "до ",
)


def parse_russian_date(text: str) -> date | None:
    normalized = " ".join((text or "").strip().split())
    if not normalized:
        return None

    iso_match = ISO_DATE_RE.search(normalized)
    if iso_match:
        return _safe_date(
            int(iso_match.group(1)),
            int(iso_match.group(2)),
            int(iso_match.group(3)),
        )

    slash_or_dot_match = SLASH_OR_DOT_DATE_RE.search(normalized)
    if slash_or_dot_match:
        return _safe_date(
            int(slash_or_dot_match.group(3)),
            int(slash_or_dot_match.group(2)),
            int(slash_or_dot_match.group(1)),
        )

    russian_match = RUSSIAN_DATE_RE.search(normalized.lower())
    if russian_match:
        month = RUSSIAN_MONTHS.get(russian_match.group(2).lower())
        if month is None:
            return None
        return _safe_date(
            int(russian_match.group(3)),
            month,
            int(russian_match.group(1)),
        )
    return None


def normalize_date_to_iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def extract_published_at_from_html(html: str, source_name: str, url: str) -> date | None:
    if _is_government_source(source_name, url) and not _is_government_real_page_url(url):
        return None
    soup = BeautifulSoup(html or "", "html.parser")

    meta_candidates: list[str] = []
    for attrs in (
        {"property": "article:published_time"},
        {"property": "og:published_time"},
        {"name": "article:published_time"},
        {"name": "publish_date"},
        {"name": "pubdate"},
        {"name": "date"},
        {"itemprop": "datePublished"},
    ):
        for tag in soup.find_all("meta", attrs=attrs):
            content = str(tag.get("content", "")).strip()
            if content:
                meta_candidates.append(content)
    for candidate in meta_candidates:
        parsed = parse_russian_date(candidate)
        if parsed is not None:
            return parsed

    for tag in soup.find_all("time"):
        datetime_attr = str(tag.get("datetime", "")).strip()
        if datetime_attr:
            parsed = parse_russian_date(datetime_attr)
            if parsed is not None:
                return parsed
        parsed = _extract_published_at_from_text(
            " ".join(tag.stripped_strings),
            source_name=source_name,
            url=url,
            allow_leading_date=True,
        )
        if parsed is not None:
            return parsed

    for tag in soup.find_all(True):
        if not isinstance(tag, Tag):
            continue
        class_names = " ".join(str(item) for item in tag.get("class", []))
        tag_id = str(tag.get("id", ""))
        hint_text = f"{class_names} {tag_id}".strip()
        if not hint_text or not HTML_DATE_HINT_RE.search(hint_text):
            continue
        parsed = _extract_published_at_from_text(
            " ".join(tag.stripped_strings),
            source_name=source_name,
            url=url,
            allow_leading_date=True,
        )
        if parsed is not None:
            return parsed

    if _is_admkrai_source(source_name, url):
        parsed = _extract_admkrai_publication_from_text(" ".join(soup.stripped_strings))
        if parsed is not None:
            return parsed
    if _is_government_source(source_name, url):
        parsed = _extract_government_publication_from_text(" ".join(soup.stripped_strings))
        if parsed is not None:
            return parsed

    return extract_published_at_from_url(url)


def extract_published_at_from_link_tag(
    link: Tag,
    *,
    source_name: str,
    url: str,
) -> date | None:
    if _is_government_source(source_name, url):
        if not _is_government_real_page_url(url):
            return None
        parsed = _extract_government_publication_from_link(link)
        if parsed is not None:
            return parsed

    if _is_admkrai_source(source_name, url):
        parsed = _extract_admkrai_publication_from_link(link)
        if parsed is not None:
            return parsed

    time_tag = link.find_previous("time") or link.find("time")
    if isinstance(time_tag, Tag):
        datetime_attr = str(time_tag.get("datetime", "")).strip()
        parsed = parse_russian_date(datetime_attr or " ".join(time_tag.stripped_strings))
        if parsed is not None:
            return parsed

    for ancestor in link.parents:
        if not isinstance(ancestor, Tag):
            continue
        if ancestor.name not in {"article", "li", "tr", "div", "section"}:
            continue
        container_text = " ".join(ancestor.stripped_strings)
        if not container_text:
            continue
        parsed = _extract_published_at_from_text(
            container_text,
            source_name=source_name,
            url=url,
            allow_leading_date=_is_news_like_source(source_name, url),
        )
        if parsed is not None:
            return parsed
        if len(container_text) > 600:
            break

    return extract_published_at_from_url(url)


def infer_published_at(
    *,
    title: str,
    raw_text: str,
    source_name: str,
    url: str,
) -> datetime | None:
    if _is_government_source(source_name, url):
        if not _is_government_real_page_url(url):
            return None
        parsed = _extract_government_publication_from_text(raw_text)
        if parsed is not None:
            return _to_utc_datetime(parsed)

    if _is_admkrai_source(source_name, url):
        parsed = _extract_admkrai_publication_from_text(raw_text)
        if parsed is not None:
            return _to_utc_datetime(parsed)

    for candidate_text, allow_leading_date in (
        (raw_text, _is_news_like_source(source_name, url)),
        (title, False),
    ):
        parsed = _extract_published_at_from_text(
            candidate_text,
            source_name=source_name,
            url=url,
            allow_leading_date=allow_leading_date,
        )
        if parsed is not None:
            return _to_utc_datetime(parsed)

    parsed_from_url = extract_published_at_from_url(url)
    if parsed_from_url is not None:
        return _to_utc_datetime(parsed_from_url)
    return None


def extract_published_at_from_url(url: str) -> date | None:
    parsed_url = urlparse(url)
    path = parsed_url.path

    year_month_day = re.search(r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)", path)
    if year_month_day:
        return _safe_date(
            int(year_month_day.group(1)),
            int(year_month_day.group(2)),
            int(year_month_day.group(3)),
        )

    compact_date = parse_russian_date(path.replace("_", "-"))
    if compact_date is not None:
        return compact_date
    return None


def _extract_published_at_from_text(
    text: str,
    *,
    source_name: str,
    url: str,
    allow_leading_date: bool,
) -> date | None:
    normalized = " ".join((text or "").split())
    if not normalized:
        return None

    lowered = normalized.lower()
    if any(marker in lowered for marker in NEGATIVE_DATE_MARKERS):
        return None

    if allow_leading_date:
        leading_fragment = normalized[:180]
        parsed = parse_russian_date(leading_fragment)
        if parsed is not None:
            if TIME_RE.search(leading_fragment) or _is_news_like_source(source_name, url):
                return parsed

    if any(marker in lowered for marker in PUBLICATION_MARKERS):
        parsed = parse_russian_date(normalized[:240])
        if parsed is not None:
            return parsed

    return None


def _is_news_like_source(source_name: str, url: str) -> bool:
    source_key = f"{source_name} {url}".lower()
    return any(
        marker in source_key
        for marker in (
            "zol.ru",
            "regulation.gov.ru",
        )
    )


def _is_government_source(source_name: str, url: str) -> bool:
    source_key = f"{source_name} {url}".lower()
    return "government.ru" in source_key or "правительство рф" in source_key


def _is_government_real_page_url(url: str) -> bool:
    parsed = urlparse(url or "")
    if parsed.netloc.lower() != "government.ru":
        return False
    if parsed.query:
        return False
    return GOVERNMENT_REAL_PAGE_PATH_RE.fullmatch(parsed.path.lower()) is not None


def _extract_government_publication_from_link(link: Tag) -> date | None:
    time_tag = link.find("time")
    if isinstance(time_tag, Tag):
        datetime_attr = str(time_tag.get("datetime", "")).strip()
        parsed = parse_russian_date(datetime_attr or " ".join(time_tag.stripped_strings))
        if parsed is not None:
            return parsed

    for sibling in list(link.previous_siblings)[:3]:
        candidate = _to_candidate_text(sibling).strip(" .,:;")
        if not candidate:
            continue
        if len(candidate) > 80:
            continue
        match = GOVERNMENT_LEADING_DATE_TIME_RE.search(candidate)
        if match:
            parsed = parse_russian_date(match.group(1))
            if parsed is not None:
                return parsed
    return None


def _extract_government_publication_from_text(text: str) -> date | None:
    normalized = " ".join((text or "").split())
    if not normalized:
        return None
    header_fragment = normalized[:900]
    match = GOVERNMENT_LEADING_DATE_TIME_RE.search(header_fragment)
    if match is None:
        return None
    return parse_russian_date(match.group(1))


def _is_admkrai_source(source_name: str, url: str) -> bool:
    source_key = f"{source_name} {url}".lower()
    return "admkrai.krasnodar.ru" in source_key or "нормативные акты краснодарского края" in source_key


def _extract_admkrai_publication_from_link(link: Tag) -> date | None:
    time_tag = link.find_previous("time") or link.find("time")
    if isinstance(time_tag, Tag):
        datetime_attr = str(time_tag.get("datetime", "")).strip()
        parsed = parse_russian_date(datetime_attr or " ".join(time_tag.stripped_strings))
        if parsed is not None:
            return parsed

    for candidate in _collect_admkrai_link_candidates(link):
        parsed = _parse_admkrai_safe_date_candidate(candidate)
        if parsed is not None:
            return parsed
    return None


def _collect_admkrai_link_candidates(link: Tag) -> list[str]:
    candidates: list[str] = []
    for sibling in list(link.previous_siblings)[:4]:
        text = _to_candidate_text(sibling)
        if text:
            candidates.append(text)
    parent = link.parent
    if isinstance(parent, Tag) and parent.name in {"td", "th"}:
        for sibling in list(parent.previous_siblings)[:3]:
            text = _to_candidate_text(sibling)
            if text:
                candidates.append(text)
    return candidates


def _to_candidate_text(node: object) -> str:
    if isinstance(node, Tag):
        return " ".join(node.stripped_strings)
    if isinstance(node, NavigableString):
        return str(node).strip()
    return ""


def _parse_admkrai_safe_date_candidate(text: str) -> date | None:
    normalized = " ".join((text or "").split())
    if not normalized or len(normalized) > 140:
        return None

    lowered = normalized.lower()
    if any(marker in lowered for marker in ADMKRAI_UNSAFE_DATE_MARKERS):
        return None

    context_match = ADMKRAI_PUBLICATION_CONTEXT_RE.search(normalized)
    if context_match:
        parsed = parse_russian_date(context_match.group(1))
        if parsed is not None:
            return parsed

    stripped = normalized.strip(" .,:;")
    if ADMKRAI_BARE_DATE_RE.fullmatch(stripped):
        return parse_russian_date(stripped)
    return None


def _extract_admkrai_publication_from_text(text: str) -> date | None:
    normalized = " ".join((text or "").split())
    if not normalized:
        return None

    lowered = normalized.lower()
    if "requires ocr extraction" in lowered:
        return None

    for match in ADMKRAI_PUBLICATION_CONTEXT_RE.finditer(normalized[:1600]):
        parsed = parse_russian_date(match.group(1))
        if parsed is not None:
            return parsed

    header_fragment = normalized[:700]
    header_match = ADMKRAI_HEADER_DATE_RE.search(header_fragment)
    if header_match is not None:
        return parse_russian_date(header_match.group(1))
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _to_utc_datetime(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)
