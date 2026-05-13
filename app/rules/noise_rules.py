from __future__ import annotations

import re

IRRELEVANT_MARKERS = (
    "поиск",
    "архив",
    "search",
    "archive",
    "catalog",
    "catalogue",
    "rss",
    "sitemap",
    "структура",
    "министерств",
    "ведомств",
    "коммент",
    "показать еще",
    "показать ещё",
)
IRRELEVANT_TITLES = {
    "документы",
    "новости",
    "правительство россии",
    "законопроектная деятельность",
    "поручения",
    "заседания",
    "о правительстве",
    "новости компаний",
    "маркетплейс",
    "предыдущий месяц <<<",
    "следующая",
    "предыдущая",
    "смотреть все",
    "просмотр",
    "политика в отношении обработки пдн",
    "политика конфиденциальности",
    "визитка",
    "административное деление",
    "госимущество",
    "приоритеты",
    "зернотрафик",
    "фотогалерея",
    "анонсы",
    "актуально",
}
IRRELEVANT_TITLE_FRAGMENTS = (
    "новости компаний",
    "маркетплейс",
    "предыдущий месяц",
    "следующая",
    "предыдущая",
    "политика в отношении обработки",
    "сельский клуб",
    "опрос по итогам прохождения обучения",
)
SENTENCE_SPLIT_REGEX = re.compile(r"(?<=[.!?])\s+")
WHITESPACE_RE = re.compile(r"\s+")
SUMMARY_UI_NOISE_PATTERNS = (
    r"\bглавная\b",
    r"\bнавига(?:тор)? мер поддержки\b",
    r"\bсравнить\s+\d+\b",
    r"\bнпа\b[^.]*",
    r"\bобщая информация\b",
    r"\bтребования\b",
    r"\bнеобходимые документы\b",
    r"\bскачать условия\b",
    r"\bадминистратор меры поддержки\b[^.]*",
)
ANTI_CORRUPTION_NOISE_MARKERS = (
    "противодейств",
    "коррупц",
    "декларирован",
    "конфликт интересов",
)
CULTURAL_HERITAGE_NPA_NOISE_MARKERS = (
    "культурного наследия",
    "объект культурного наследия",
    "объектов культурного наследия",
    "памятник истории и культуры",
    "памятники истории и культуры",
)
STATIC_BACKGROUND_TITLE_FRAGMENTS = (
    "формы документов",
    "противодействие коррупции",
    "оценка регулирующего воздействия",
    "публичные консультации",
    "публичные обсуждения",
    "обратная связь для сообщений о фактах коррупции",
    "комиссия по координации работы по противодействию коррупции",
    "комиссия администрации краснодарского края по соблюдению требований к служебному поведению",
)
GISP_UI_ID_RE = re.compile(r"(?<![\d-])\.?\s*\d{3,5}\)(?!\d)")
TITLE_DUPLICATE_RE_TEMPLATE = r"^({title}[.:]?\s+)(?:{title}[.:]?\s+)+"


def looks_irrelevant(title: str, raw_text: str) -> bool:
    if _looks_like_official_agro_order_title(title):
        return False
    if title in IRRELEVANT_TITLES:
        return True
    if any(fragment in title for fragment in IRRELEVANT_TITLE_FRAGMENTS):
        return True
    if any(marker in title for marker in IRRELEVANT_MARKERS):
        return True
    if not title or len(title.strip()) < 4:
        return True
    compact_body = " ".join(raw_text.split())
    if not compact_body:
        return True
    if compact_body.startswith("SERVICE_PAGE"):
        return True
    if len(compact_body) < 80 and any(marker in compact_body.lower() for marker in IRRELEVANT_MARKERS):
        return True
    return False


def _looks_like_official_agro_order_title(title: str) -> bool:
    normalized = title.lower().strip()
    if "приказ" not in normalized:
        return False
    return (
        "министерств" in normalized
        and (
            "сельского хозяйства" in normalized
            or "сельхоз" in normalized
            or "агропромышлен" in normalized
        )
    )


def looks_cultural_heritage_npa_noise(title: str, raw_text: str) -> bool:
    text = f"{title} {raw_text[:500]}"
    return any(marker in text for marker in CULTURAL_HERITAGE_NPA_NOISE_MARKERS)


def looks_anti_corruption_noise(title: str, raw_text: str) -> bool:
    if any(fragment in title for fragment in STATIC_BACKGROUND_TITLE_FRAGMENTS):
        return True
    return any(marker in title and marker in raw_text for marker in ANTI_CORRUPTION_NOISE_MARKERS)


def is_gisp_source(source_name: str | None, url: str | None) -> bool:
    combined = f"{source_name or ''} {url or ''}".lower()
    return "gisp.gov.ru" in combined or "гисп" in combined


def collapse_duplicate_title(text: str, title: str) -> str:
    normalized_title = WHITESPACE_RE.sub(" ", title).strip()
    if not normalized_title:
        return text
    title_pattern = re.escape(normalized_title)
    duplicate_re = re.compile(
        TITLE_DUPLICATE_RE_TEMPLATE.format(title=title_pattern),
        re.IGNORECASE,
    )
    collapsed = duplicate_re.sub(r"\1", text.strip())
    lowered_title = normalized_title.lower()
    if collapsed.lower().startswith(f"{lowered_title} {lowered_title}"):
        collapsed = collapsed[len(normalized_title):].strip(" .:-")
        collapsed = f"{normalized_title}. {collapsed}".strip()
    return collapsed


def clean_summary_text(
    text: str,
    *,
    title: str,
    source_name: str | None,
    url: str | None,
) -> str:
    cleaned = text
    for pattern in SUMMARY_UI_NOISE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"(телеграм-канал|мессенджер|читайте новости|новости по этой теме|перейти к списку новостей|установите мобильное приложение)[^.]*",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    if is_gisp_source(source_name, url):
        cleaned = GISP_UI_ID_RE.sub(" ", cleaned)
        cleaned = re.sub(r"\bконкурсное событие\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"\b(общая информация|требования|необходимые документы|скачать условия)\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = collapse_duplicate_title(cleaned, title)
    cleaned = WHITESPACE_RE.sub(" ", cleaned).strip(" .,-:")
    return cleaned


def finalize_summary(
    text: str,
    *,
    title: str,
    source_name: str | None,
    url: str | None,
) -> str:
    cleaned = WHITESPACE_RE.sub(" ", text).strip(" .,-:")
    if is_gisp_source(source_name, url) and title.lower() not in cleaned.lower():
        cleaned = f"{title}. {cleaned}".strip()
    return limit_summary(cleaned)


def limit_summary(text: str) -> str:
    normalized = WHITESPACE_RE.sub(" ", text).strip()
    if len(normalized) <= 300:
        return normalized
    truncated = normalized[:297].rstrip(" ,.;:-")
    return f"{truncated}..."
