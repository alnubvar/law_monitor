from __future__ import annotations

import re
from datetime import datetime, time, timezone
from pathlib import PurePosixPath
from urllib.parse import urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

from app.extractors.date_extractor import parse_russian_date
from app.sources.generic_html_source import GenericHTMLSource

SAFE_PUBLICATION_MARKERS = (
    "дата публикации",
    "дата опубликования",
    "опубликовано",
    "опубликован",
    "размещено",
    "размещен",
    "размещён",
)
UNSAFE_DATE_MARKERS = (
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
MCX_ACTIONABLE_TITLE_MARKERS = (
    "объявление о проведении отбора",
    "объявлен отбор",
    "прием заявок",
    "приём заявок",
    "срок подачи",
    "заявки принимаются",
    "субсид",
    "господдерж",
    "мера поддержки",
    "отбор",
    "конкурс",
    "льготн",
    "лизинг",
    "кредит",
    "постановление",
    "приказ",
    "распоряжение",
    "о внесении изменений",
)
MCX_REFERENCE_TITLES = {
    "агропромышленный комплекс",
    "аналитика и статистика",
    "антимонопольный комплаенс",
    "видеогалерея",
    "виноградарство и виноделие",
    "вопросы и ответы",
    "государственные закупки",
    "государственные услуги",
    "гражданам",
    "действующие документы",
    "деятельность",
    "документы",
    "животноводство",
    "информация для организаторов отбора",
    "информация для участников отбора (заявителей)",
    "карта сайта",
    "малые формы хозяйствования",
    "меры государственной поддержки",
    "наука и образование",
    "нормотворческая деятельность",
    "опросы",
    "открытые данные",
    "пищевая и перерабатывающая промышленность",
    "пресс-центр",
    "прочие документы",
    "проекты документов",
    "растениеводство",
    "реестр сельскохозяйственных товаропроизводителей",
    "реестры получателей субсидий на поддержку отрасли",
    "результаты отборов и конкурсов на получение субсидий",
    "рыбохозяйственный комплекс",
    "события",
    "страхование и инвестиции",
    "утратившие силу",
    "экономика и финансы",
}
MCX_REFERENCE_PATHS = {
    "/",
    "/activity",
    "/documents",
    "/documents/abolished",
    "/documents/active",
    "/documents/other",
    "/documents/plans",
    "/documents/projects",
    "/documents/reports",
    "/feedback",
    "/hotline",
    "/opendata",
    "/presscenter",
    "/presscenter/events",
    "/presscenter/video",
    "/request",
    "/request/faq",
    "/sitemap",
    "/vote",
}
MCX_TRAVERSAL_TITLES = {
    "животноводство",
    "малые формы хозяйствования",
    "наука и образование",
    "пищевая и перерабатывающая промышленность",
    "растениеводство",
    "рыбохозяйственный комплекс",
    "страхование и инвестиции",
    "виноградарство и виноделие",
}
MCX_YEAR_OR_PERIOD_RE = re.compile(r"^(?:20\d{2}|за\s+(?:сегодня|неделю|месяц))$", re.IGNORECASE)
MCX_ACTIVITY_PATH_RE = re.compile(r"^/activity/\d+/?$", re.IGNORECASE)
EMBEDDED_HOST_SEGMENT_RE = re.compile(r"(^|/)([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?=/)", re.IGNORECASE)
PRAVO_DOCUMENT_TITLE_MARKERS = (
    "закон",
    "постановление",
    "приказ",
    "распоряжение",
    "решение",
    "указ",
)
PRAVO_DOCUMENT_FILENAME_MARKERS = PRAVO_DOCUMENT_TITLE_MARKERS + (
    "subsid",
    "support",
    "apk",
    "agro",
)
PRAVO_REFERENCE_TITLES = {
    "за сегодня",
    "за неделю",
    "за месяц",
    "календарь опубликования",
    "контакты",
    "новости",
    "о портале",
    "официальное опубликование",
    "официальные баннеры",
    "поиск документов по реквизитам",
    "правила использования материалов, размещенных на портале",
    "правовая информация",
    "правовая информатизация",
    "правовые акты",
    "проекты правовых актов",
    "отмененные документы",
    "госимущество",
}
PRAVO_REFERENCE_PATH_PREFIXES = (
    "/calendar/",
    "/doc/list/",
    "/legalinfo/",
    "/news/list/",
    "/rss/",
    "/search-main/",
    "/static/",
)
DATE_CONTEXT_RE = re.compile(
    r"(?:дата публикации|дата опубликования|опубликовано|опубликован|размещено|размещен|размещён)"
    r"[:\s,]+(.{0,120})",
    re.IGNORECASE,
)


class DonlandSource(GenericHTMLSource):
    """Source-specific filtering for Ростовские portals on donland.ru."""

    def fetch_items(self):
        if not self._is_mcx_support_root_source():
            return super().fetch_items()

        response = self.get(self.config.url)
        soup = BeautifulSoup(response.text, "html.parser")
        items = []
        seen_urls: set[str] = set()
        traversal_seen: set[str] = set()
        aggregated_stats: dict[str, int | str] = {}

        root_items = self._extract_items_from_soup(soup, response.url, seen_urls=seen_urls)
        self._merge_fetch_stats(aggregated_stats, self.last_fetch_stats)
        items.extend(root_items)

        traversed_page_count = 0
        for traversal_url in self._extract_mcx_traversal_urls(soup, response.url):
            if traversal_url in traversal_seen:
                continue
            remaining = self._remaining_item_budget(items)
            if remaining == 0:
                break
            traversal_seen.add(traversal_url)
            nested_response = self.get(traversal_url)
            nested_soup = BeautifulSoup(nested_response.text, "html.parser")
            nested_items = self._extract_items_from_soup(
                nested_soup,
                nested_response.url,
                seen_urls=seen_urls,
                max_items_override=remaining,
            )
            self._merge_fetch_stats(aggregated_stats, self.last_fetch_stats)
            items.extend(nested_items)
            traversed_page_count += 1

        if aggregated_stats:
            aggregated_stats["items_collected_count"] = len(items)
            aggregated_stats["traversed_page_count"] = traversed_page_count
            self.last_fetch_stats = aggregated_stats
        return items

    def _should_include_url(self, url: str, base_url: str, title: str) -> bool:
        if not super()._should_include_url(url, base_url, title):
            return False

        source_key = f"{self.config.name} {self.config.url}".lower()
        if "mcx.donland.ru" in source_key:
            return self._should_include_mcx_url(url, title)
        if "pravo.donland.ru" in source_key:
            return self._should_include_pravo_url(url, title)
        return True

    def _should_include_mcx_url(self, url: str, title: str) -> bool:
        document_type = self._detect_document_type(url)
        if document_type in {"pdf", "doc", "docx"}:
            return True

        lower_url = url.lower()
        path = urlparse(lower_url).path.rstrip("/") or "/"
        title_text = _normalize_title(title)
        if MCX_YEAR_OR_PERIOD_RE.fullmatch(title_text):
            return False
        if path in MCX_REFERENCE_PATHS:
            return False
        if title_text in MCX_REFERENCE_TITLES and not _has_mcx_actionable_title(title_text):
            return False
        if path.startswith("/presscenter/") and title_text in {"события", "пресс-центр", "видеогалерея"}:
            return False
        if MCX_ACTIVITY_PATH_RE.fullmatch(path) and title_text in MCX_REFERENCE_TITLES:
            return False
        return _has_mcx_actionable_title(f"{title_text} {lower_url}")

    def _should_include_pravo_url(self, url: str, title: str) -> bool:
        lower_url = url.lower()
        path = urlparse(lower_url).path.rstrip("/") or "/"
        title_text = _normalize_title(title)
        document_type = self._detect_document_type(url)

        if title_text in PRAVO_REFERENCE_TITLES or MCX_YEAR_OR_PERIOD_RE.fullmatch(title_text):
            return False
        if any(path.startswith(prefix.rstrip("/")) for prefix in PRAVO_REFERENCE_PATH_PREFIXES):
            return False
        if document_type in {"pdf", "doc", "docx"}:
            return _has_pravo_document_title(title_text) or _has_pravo_document_filename(path)
        if path.startswith("/doc/view/"):
            return True
        return False

    def _extract_published_at(self, link: Tag, normalized_url: str):
        extracted = _extract_safe_publication_date(link)
        if extracted is None:
            return super()._extract_published_at(link, normalized_url)
        return datetime.combine(extracted, time.min, tzinfo=timezone.utc)

    def _normalize_url(self, href: str, base_url: str) -> str | None:
        normalized = super()._normalize_url(href, base_url)
        if normalized is None:
            return None
        if self._has_embedded_foreign_host_path(normalized):
            return None
        return normalized

    def _is_mcx_support_root_source(self) -> bool:
        source_key = f"{self.config.name} {self.config.url}".lower()
        return (
            "минсельхоз ростовской области - господдержка" in source_key
            and self.config.url.rstrip("/") == "https://mcx.donland.ru/activity/35217"
        )

    def _extract_mcx_traversal_urls(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        traversal_urls: list[str] = []
        local_seen: set[str] = set()
        base_host = urlparse(base_url).netloc.lower()

        for link in soup.find_all("a", href=True):
            if not isinstance(link, Tag):
                continue
            normalized_url = self._normalize_url(link.get("href", ""), base_url)
            if not normalized_url or normalized_url in local_seen:
                continue
            parsed = urlparse(normalized_url)
            if parsed.netloc.lower() != base_host:
                continue
            title = self._extract_title(link, normalized_url)
            if not self._should_include_title(title):
                continue
            if self._should_traverse_mcx_category(normalized_url, title):
                traversal_urls.append(normalized_url)
                local_seen.add(normalized_url)
        return traversal_urls

    def _should_traverse_mcx_category(self, url: str, title: str) -> bool:
        if self._detect_document_type(url) != "html":
            return False

        path = urlparse(url).path.rstrip("/") or "/"
        title_text = _normalize_title(title)

        if path.startswith("/documents/active"):
            return title_text == "действующие документы" or bool(MCX_YEAR_OR_PERIOD_RE.fullmatch(title_text))
        if path.startswith("/presscenter/"):
            return False
        return MCX_ACTIVITY_PATH_RE.fullmatch(path) is not None and title_text in MCX_TRAVERSAL_TITLES

    def _has_embedded_foreign_host_path(self, url: str) -> bool:
        parsed = urlparse(url)
        base_host = parsed.netloc.lower()
        for _prefix, candidate_host in EMBEDDED_HOST_SEGMENT_RE.findall(parsed.path.lower()):
            if candidate_host == base_host:
                continue
            if candidate_host.endswith((".ru", ".gov.ru", ".org", ".com", ".рф")):
                return True
        return False


def _normalize_title(title: str) -> str:
    return " ".join((title or "").lower().split())


def _has_mcx_actionable_title(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in MCX_ACTIONABLE_TITLE_MARKERS)


def _has_pravo_document_title(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in PRAVO_DOCUMENT_TITLE_MARKERS)


def _has_pravo_document_filename(path: str) -> bool:
    filename = PurePosixPath(path).name.lower()
    return any(marker in filename for marker in PRAVO_DOCUMENT_FILENAME_MARKERS)


def _extract_safe_publication_date(link: Tag):
    time_tag = link.find_previous("time") or link.find("time")
    if isinstance(time_tag, Tag):
        datetime_attr = str(time_tag.get("datetime", "")).strip()
        parsed = parse_russian_date(datetime_attr or " ".join(time_tag.stripped_strings))
        if parsed is not None:
            return parsed

    for candidate in _nearby_date_candidates(link):
        parsed = _parse_safe_date_candidate(candidate)
        if parsed is not None:
            return parsed

    for ancestor in link.parents:
        if not isinstance(ancestor, Tag):
            continue
        if ancestor.name not in {"article", "li", "tr", "div", "section"}:
            continue
        text = " ".join(ancestor.stripped_strings)
        lowered = text.lower()
        if not any(marker in lowered for marker in SAFE_PUBLICATION_MARKERS):
            continue
        context_match = DATE_CONTEXT_RE.search(text)
        parsed = parse_russian_date(context_match.group(1) if context_match else text)
        if parsed is not None:
            return parsed
        if len(text) > 800:
            break
    return None


def _nearby_date_candidates(link: Tag) -> list[str]:
    candidates: list[str] = []
    for sibling in list(link.previous_siblings)[:3]:
        text = _extract_candidate_text(sibling)
        if text:
            candidates.append(text)
    parent = link.parent
    if isinstance(parent, Tag):
        for sibling in list(parent.previous_siblings)[:3]:
            text = _extract_candidate_text(sibling)
            if text:
                candidates.append(text)
    return candidates


def _extract_candidate_text(node: object) -> str:
    if isinstance(node, Tag):
        return " ".join(node.stripped_strings)
    if isinstance(node, NavigableString):
        return str(node).strip()
    return ""


def _parse_safe_date_candidate(text: str):
    normalized = " ".join((text or "").split())
    if not normalized or len(normalized) > 80:
        return None
    lowered = normalized.lower()
    if any(marker in lowered for marker in UNSAFE_DATE_MARKERS):
        return None
    return parse_russian_date(normalized)
