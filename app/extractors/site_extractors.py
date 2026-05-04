from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

SERVICE_PATH_MARKERS = (
    "search",
    "archive",
    "rugovclassifier",
    "sitemap",
    "about",
    "persons",
    "photos",
    "social",
    "page=",
)
COMMON_NAVIGATION_PHRASES = (
    "правительство россии работа правительства",
    "новости цены зерновой еженедельник",
    "поиск по сайту",
    "реклама на сайте",
    "следующая новость предыдущая новость",
)
GISP_NAVIGATION_MARKERS = (
    "главная",
    "избранное",
    "сравнить",
    "навига",
    "найти",
    "рекомендованные меры",
    "смотреть все",
    "подбор подходящих мер поддержки",
    "скачать результаты pdf",
    "фильтр",
)
GISP_UI_SECTION_RE = re.compile(
    r"\b(общая информация|требования|необходимые документы|скачать условия)\b",
    re.IGNORECASE,
)
GISP_UI_ID_RE = re.compile(r"(?<![\d-])\.?\s*\d{3,5}\)(?!\d)")
REGIONAL_BREADCRUMB_MARKERS = (
    "главная документы",
    "главная деятельность",
    "главная господдержка",
    "объявления министерство сельского хозяйства",
)
ZOL_TAIL_MARKERS = (
    "комментарии",
    "похожие новости",
    "подписка",
    "реклама на сайте",
    "маркетплейс",
    "каталог предприятий апк",
    "телеграм-канал",
    "мессенджер",
    "читайте новости",
    "новости по этой теме",
    "перейти к списку новостей",
    "установите мобильное приложение",
)
GOVERNMENT_TAIL_MARKERS = (
    "поделиться в социальных сетях",
    "код для вставки в блог",
    "выделить фрагмент",
)
WHITESPACE_RE = re.compile(r"\s+")
DATE_LINE_RE = re.compile(
    r"\b\d{1,2}\s+[а-яё]+(?:\s+\d{4})?\s+\d{2}:\d{2}\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ExtractedContent:
    text: str
    is_service_page: bool = False
    content_quality: str = "good"


def extract_government_content(html: str, url: str) -> ExtractedContent:
    if _looks_like_service_url(url):
        return ExtractedContent(
            text="SERVICE_PAGE government.ru navigation page",
            is_service_page=True,
            content_quality="navigation",
        )

    soup = BeautifulSoup(html, "html.parser")
    _remove_noise(
        soup,
        keywords=(
            "nav",
            "menu",
            "footer",
            "header",
            "share",
            "social",
            "archive",
            "search",
            "rugovclassifier",
            "sidebar",
            "related",
        ),
    )

    candidates = _candidate_blocks(
        soup,
        selectors=(
            "article",
            "main",
            ".news",
            ".news-item",
            ".document",
            ".page-content",
            ".content",
            "#content",
        ),
    )
    text = _best_candidate_text(candidates)
    text = _trim_tail_markers(text, GOVERNMENT_TAIL_MARKERS)
    text = _normalize_text(text)
    if not text:
        return ExtractedContent(text="", content_quality="empty")
    if _looks_like_navigation_text(text):
        return ExtractedContent(
            text="SERVICE_PAGE government.ru navigation page",
            is_service_page=True,
            content_quality="navigation",
        )
    if len(text) < 200:
        return ExtractedContent(text=text, content_quality="weak")
    return ExtractedContent(text=text, content_quality="good")


def extract_zol_content(html: str, url: str) -> ExtractedContent:
    soup = BeautifulSoup(html, "html.parser")
    _remove_noise(
        soup,
        keywords=(
            "nav",
            "menu",
            "footer",
            "header",
            "sidebar",
            "banner",
            "advert",
            "reklam",
            "comment",
            "catalog",
            "price",
            "analytics",
            "similar",
            "related",
            "subscribe",
            "forum",
        ),
    )

    candidates = _candidate_blocks(
        soup,
        selectors=(
            "article",
            "main",
            ".content",
            ".news",
            ".news-item",
            "#content",
            "td",
            "body",
        ),
    )
    text = _best_candidate_text(candidates)
    text = _trim_tail_markers(text, ZOL_TAIL_MARKERS)
    text = _normalize_text(text)
    if not text:
        return ExtractedContent(text="", content_quality="empty")
    if _looks_like_navigation_text(text):
        return ExtractedContent(
            text="SERVICE_PAGE zol.ru navigation page",
            is_service_page=True,
            content_quality="navigation",
        )
    if len(text) < 200:
        return ExtractedContent(text=text, content_quality="weak")
    return ExtractedContent(text=text, content_quality="good")


def clean_text_for_analysis(
    *,
    source_name: str | None,
    url: str | None,
    title: str,
    raw_text: str,
) -> ExtractedContent:
    normalized_text = _normalize_text(raw_text)
    if not normalized_text:
        return ExtractedContent(text="", content_quality="empty")

    source_key = f"{source_name or ''} {url or ''}".lower()
    if "government.ru" in source_key or "правительство рф" in source_key:
        return _clean_government_text(title, url or "", normalized_text)
    if "zol.ru" in source_key or "зерновые новости" in source_key:
        return _clean_zol_text(title, normalized_text)
    if "gisp.gov.ru" in source_key or "гисп - меры поддержки апк" in source_key:
        return _clean_gisp_text(title, url or "", normalized_text)
    if any(
        marker in source_key
        for marker in (
            "mcx.donland.ru",
            "pravo.donland.ru",
            "msh.krasnodar.ru",
            "mshsk.ru",
            "pravo.stavregion.ru",
            "admkrai.krasnodar.ru",
        )
    ):
        return _clean_regional_portal_text(title, normalized_text)
    if normalized_text.startswith("SERVICE_PAGE"):
        return ExtractedContent(
            text=normalized_text,
            is_service_page=True,
            content_quality="navigation",
        )
    if len(normalized_text) < 60:
        return ExtractedContent(text=normalized_text, content_quality="weak")
    return ExtractedContent(text=normalized_text, content_quality="good")


def _clean_government_text(title: str, url: str, raw_text: str) -> ExtractedContent:
    if _looks_like_service_url(url):
        return ExtractedContent(
            text="SERVICE_PAGE government.ru navigation page",
            is_service_page=True,
            content_quality="navigation",
        )

    lowered = raw_text.lower()
    if _looks_like_navigation_text(lowered):
        return ExtractedContent(
            text="SERVICE_PAGE government.ru navigation page",
            is_service_page=True,
            content_quality="navigation",
        )

    title_start = _find_effective_title_start(raw_text, title)
    cleaned = raw_text[title_start:].strip()
    cleaned = _drop_repeated_prefix(cleaned, title)
    cleaned = _trim_tail_markers(cleaned, GOVERNMENT_TAIL_MARKERS)
    cleaned = _normalize_text(cleaned)
    if _looks_like_navigation_text(cleaned):
        return ExtractedContent(
            text="SERVICE_PAGE government.ru navigation page",
            is_service_page=True,
            content_quality="navigation",
        )
    if len(cleaned) < 120:
        return ExtractedContent(text=cleaned, content_quality="weak")
    return ExtractedContent(text=cleaned, content_quality="good")


def _clean_zol_text(title: str, raw_text: str) -> ExtractedContent:
    lowered = raw_text.lower()
    if _looks_like_navigation_text(lowered):
        return ExtractedContent(
            text="SERVICE_PAGE zol.ru navigation page",
            is_service_page=True,
            content_quality="navigation",
        )

    title_occurrences = _find_occurrences(raw_text.lower(), title.lower().strip())
    if len(title_occurrences) >= 2:
        cleaned = raw_text[title_occurrences[1]:]
    elif "новости аграрного рынка" in lowered:
        marker_index = lowered.find("новости аграрного рынка")
        cleaned = raw_text[marker_index + len("новости аграрного рынка") :]
    else:
        cleaned = raw_text

    cleaned = _trim_tail_markers(cleaned, ZOL_TAIL_MARKERS)
    cleaned = _drop_repeated_prefix(cleaned, title)
    cleaned = _normalize_text(cleaned)
    if cleaned.lower().startswith("новости цены зерновой еженедельник"):
        return ExtractedContent(
            text="SERVICE_PAGE zol.ru navigation page",
            is_service_page=True,
            content_quality="navigation",
        )
    if len(cleaned) < 120:
        return ExtractedContent(text=cleaned, content_quality="weak")
    return ExtractedContent(text=cleaned, content_quality="good")


def _clean_gisp_text(title: str, url: str, raw_text: str) -> ExtractedContent:
    lowered = raw_text.lower()
    if "/nmp/main/" in url.lower() and sum(marker in lowered for marker in GISP_NAVIGATION_MARKERS) >= 3:
        return ExtractedContent(
            text=_normalize_text(raw_text),
            is_service_page=False,
            content_quality="weak",
        )

    cleaned = GISP_UI_ID_RE.sub(" ", raw_text)
    cleaned = re.sub(r"\bконкурсное событие\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = GISP_UI_SECTION_RE.sub(" ", cleaned)
    cleaned = _drop_repeated_prefix(cleaned, title)
    cleaned = _normalize_text(cleaned)
    cleaned = _remove_duplicate_title_block(cleaned, title)
    cleaned = _normalize_text(cleaned)
    if len(cleaned) < 80:
        return ExtractedContent(text=cleaned, content_quality="weak")
    return ExtractedContent(text=cleaned, content_quality="good")


def _clean_regional_portal_text(title: str, raw_text: str) -> ExtractedContent:
    cleaned = raw_text
    lowered = cleaned.lower()
    for marker in REGIONAL_BREADCRUMB_MARKERS:
        index = lowered.find(marker)
        if index != -1:
            cleaned = cleaned[index + len(marker):]
            break
    cleaned = _drop_repeated_prefix(cleaned, title, keep_title_if_missing=False)
    cleaned = _normalize_text(cleaned)
    if len(cleaned) < 60:
        return ExtractedContent(text=cleaned, content_quality="weak")
    return ExtractedContent(text=cleaned, content_quality="good")


def _candidate_blocks(soup: BeautifulSoup, selectors: tuple[str, ...]) -> list[Tag]:
    candidates: list[Tag] = []
    seen_ids: set[int] = set()
    for selector in selectors:
        for candidate in soup.select(selector):
            if not isinstance(candidate, Tag):
                continue
            candidate_id = id(candidate)
            if candidate_id in seen_ids:
                continue
            seen_ids.add(candidate_id)
            candidates.append(candidate)
    return candidates or [soup]


def _best_candidate_text(candidates: list[Tag]) -> str:
    scored: list[tuple[int, str]] = []
    for candidate in candidates:
        text = _normalize_text(" ".join(candidate.stripped_strings))
        if not text:
            continue
        score = len(text)
        lowered = text.lower()
        for phrase in COMMON_NAVIGATION_PHRASES:
            if phrase in lowered:
                score -= 2000
        scored.append((score, text))
    if not scored:
        return ""
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _remove_noise(soup: BeautifulSoup, *, keywords: tuple[str, ...]) -> None:
    for tag_name in (
        "script",
        "style",
        "noscript",
        "svg",
        "header",
        "footer",
        "nav",
        "aside",
        "form",
    ):
        for tag in soup.find_all(tag_name):
            tag.decompose()

    for tag in soup.find_all(True):
        if tag is None:
            continue
        if not isinstance(tag, Tag):
            continue
        if not hasattr(tag, "attrs"):
            continue
        if tag.attrs is None:
            continue
        attrs = " ".join(
            str(value)
            for value in (
                tag.attrs.get("id"),
                " ".join(tag.attrs.get("class", [])),
                tag.attrs.get("role"),
                tag.attrs.get("aria-label"),
            )
            if value
        ).lower()
        if attrs and any(keyword in attrs for keyword in keywords):
            tag.decompose()


def _normalize_text(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text.replace("\xa0", " ")).strip()


def _trim_tail_markers(text: str, markers: tuple[str, ...]) -> str:
    lowered = text.lower()
    cutoff = len(text)
    for marker in markers:
        index = lowered.find(marker)
        if index != -1:
            cutoff = min(cutoff, index)
    return text[:cutoff].strip()


def _find_occurrences(text: str, needle: str) -> list[int]:
    if not needle:
        return []
    positions: list[int] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index == -1:
            break
        positions.append(index)
        start = index + len(needle)
    return positions


def _find_effective_title_start(text: str, title: str) -> int:
    occurrences = _find_occurrences(text.lower(), title.lower().strip())
    if not occurrences:
        return 0
    near_start = [index for index in occurrences if index < 2000]
    if near_start:
        return near_start[-1]
    return occurrences[0]


def _drop_repeated_prefix(
    text: str,
    title: str,
    *,
    keep_title_if_missing: bool = True,
) -> str:
    cleaned = text.strip()
    normalized_title = title.strip()
    if not normalized_title:
        return cleaned
    lowered_title = normalized_title.lower()
    while cleaned.lower().startswith(lowered_title):
        cleaned = cleaned[len(normalized_title) :].strip(" -:")
    cleaned = DATE_LINE_RE.sub("", cleaned, count=2).strip()
    if keep_title_if_missing and normalized_title.lower() not in cleaned.lower():
        cleaned = f"{normalized_title}. {cleaned}".strip()
    return cleaned


def _remove_duplicate_title_block(text: str, title: str) -> str:
    cleaned = text.strip()
    normalized_title = _normalize_text(title)
    if not normalized_title:
        return cleaned
    title_pattern = re.escape(normalized_title)
    duplicate_re = re.compile(
        rf"^({title_pattern}[.:]?\s+)(?:{title_pattern}[.:]?\s+)+",
        re.IGNORECASE,
    )
    return duplicate_re.sub(r"\1", cleaned)


def _looks_like_service_url(url: str) -> bool:
    lowered = url.lower()
    path = urlparse(lowered).path
    return any(marker in lowered or marker in path for marker in SERVICE_PATH_MARKERS)


def _looks_like_navigation_text(text: str) -> bool:
    lowered = text.lower()
    if lowered.startswith("service_page"):
        return True
    markers_found = sum(1 for phrase in COMMON_NAVIGATION_PHRASES if phrase in lowered)
    if markers_found >= 2:
        return True
    return lowered.count("правительство россии") >= 2 and "следующая новость" in lowered
