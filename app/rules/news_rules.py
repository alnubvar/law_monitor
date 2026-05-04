from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from app.rules import matches_keyword_groups

NEWS_SIGNAL_GROUPS = (
    "support_measures",
    "subsidy_terms",
    "credit_terms",
    "export_support",
    "grain_support",
    "regulatory_change",
)
STRICT_NEWS_SIGNAL_PATTERNS = (
    r"господдерж",
    r"субсиди",
    r"льготн\w*\s+кредит",
    r"экспортн\w*\s+пошлин",
    r"импортн\w*\s+пошлин",
    r"пошлин\w*.*экспорт",
    r"пошлин\w*.*импорт",
    r"ограничен\w+.*экспорт",
    r"ограничен\w+.*импорт",
    r"запрет\w*.*экспорт",
    r"запрет\w*.*импорт",
    r"квот\w*.*экспорт",
    r"квот\w*.*импорт",
    r"правительств\w*.*(решил|утверд|поруч|изменил)",
    r"минсельхоз\w*.*(утверд|поруч|изменил|запуст|расшир)",
    r"изменени\w*.*программ\w*.*финансирован",
    r"финансирован\w*.*программ",
    r"страхован\w*.*(минсельхоз|господдерж)",
)
KRASNODAR_EXPORT_SIGNAL_PATTERNS = (
    r"китай",
    r"порт\w*.*краснодар",
    r"краснодар\w*.*порт",
    r"порт\w*.*кубан",
    r"новороссий",
    r"таман",
)
EXPORT_CONTEXT_PATTERNS = (
    r"экспорт",
    r"отгруз",
    r"перевал",
    r"поставк",
)
AGRICULTURE_CONTEXT_PATTERNS = (
    r"апк",
    r"сельск",
    r"агропром",
    r"агропрод",
    r"агроэкспорт",
    r"зерн",
    r"пшениц",
    r"кукуруз",
    r"удобрен",
    r"маслич",
    r"шрот",
    r"горох",
    r"картофел",
    r"овощ",
)
FALSE_POSITIVE_PATTERNS = (
    r"прогноз\w*.*экспорт",
    r"урожайн",
    r"посевн",
    r"полев\w*\s+работ",
    r"сев",
    r"уборк",
    r"рын\w*",
)
NEGATED_SIGNAL_PATTERNS = (
    r"без(?:\s+\w+){0,4}\s+господдерж",
    r"без(?:\s+\w+){0,4}\s+субсид",
    r"без(?:\s+\w+){0,4}\s+регулир",
    r"без(?:\s+\w+){0,4}\s+решени\w*\s+правительств",
    r"без(?:\s+\w+){0,4}\s+решени\w*\s+минсельхоз",
)
BACKGROUND_NEWS_TITLE_PATTERNS = (
    r"прогноз",
    r"посевн",
    r"полев\w*\s+работ",
    r"урожайн",
    r"обзор\w*\s+рынк",
    r"цен\w*",
)
BROAD_MARKET_STORY_TITLE_PATTERNS = (
    r"причин\w*.*экспорт",
    r"рекорд\w*.*экспорт",
    r"итог\w*.*экспорт",
    r"обзор\w*.*экспорт",
    r"экспорт.*вырос",
    r"экспорт.*сниз",
)
TITLE_STRONG_SIGNAL_PATTERNS = (
    r"господдерж",
    r"субсид",
    r"льготн",
    r"пошлин",
    r"квот",
    r"ограничен",
    r"запрет",
    r"правительств",
    r"минсельхоз",
    r"постановлен",
    r"приказ",
)


def has_news_signal(
    title: str,
    lead_text: str,
    *,
    matched_keywords: Sequence[str],
    keyword_groups: Mapping[str, Sequence[str]],
) -> bool:
    normalized_title = title.lower()
    combined = f"{normalized_title} {lead_text}".lower()
    if re.search(r"без сигналов\s+(господдерж|субсид|регулир|экспорт)", combined):
        return False
    if any(re.search(pattern, combined) for pattern in NEGATED_SIGNAL_PATTERNS):
        return False
    if _should_demote_by_title(normalized_title) and not _has_target_region_export_signal(combined):
        return False
    if _looks_broad_market_story(normalized_title) and not _has_target_region_export_signal(combined):
        return False
    if _has_target_region_export_signal(combined):
        return True
    if any(re.search(pattern, combined) for pattern in STRICT_NEWS_SIGNAL_PATTERNS):
        if not _has_agriculture_context(combined):
            return False
        return True
    if _looks_generic_background_news(combined):
        return False
    keyword_hits = [keyword.lower() for keyword in matched_keywords if keyword.strip()]
    if any(_looks_strict_keyword_hit(keyword, combined) for keyword in keyword_hits):
        if not _has_agriculture_context(combined):
            return False
        return True
    return (
        matches_keyword_groups(combined, NEWS_SIGNAL_GROUPS, keyword_groups)
        and _has_agriculture_context(combined)
        and not _looks_generic_background_news(combined)
    )


def _has_target_region_export_signal(text: str) -> bool:
    return (
        any(re.search(pattern, text) for pattern in KRASNODAR_EXPORT_SIGNAL_PATTERNS)
        and any(re.search(pattern, text) for pattern in EXPORT_CONTEXT_PATTERNS)
    )


def _has_agriculture_context(text: str) -> bool:
    return any(re.search(pattern, text) for pattern in AGRICULTURE_CONTEXT_PATTERNS)


def _looks_generic_background_news(text: str) -> bool:
    if any(re.search(pattern, text) for pattern in FALSE_POSITIVE_PATTERNS):
        if not any(re.search(pattern, text) for pattern in STRICT_NEWS_SIGNAL_PATTERNS):
            return True
    return False


def _should_demote_by_title(title: str) -> bool:
    if any(re.search(pattern, title) for pattern in BACKGROUND_NEWS_TITLE_PATTERNS):
        return not any(re.search(pattern, title) for pattern in TITLE_STRONG_SIGNAL_PATTERNS)
    return False


def _looks_broad_market_story(title: str) -> bool:
    return any(re.search(pattern, title) for pattern in BROAD_MARKET_STORY_TITLE_PATTERNS) and not any(
        re.search(pattern, title) for pattern in TITLE_STRONG_SIGNAL_PATTERNS
    )


def _looks_strict_keyword_hit(keyword: str, text: str) -> bool:
    if keyword not in text:
        return False
    return (
        "господдерж" in keyword
        or "субсид" in keyword
        or "льгот" in keyword
        or "пошлин" in keyword
        or "огранич" in keyword
        or "квот" in keyword
        or "минсельхоз" in keyword
        or "правительств" in keyword
        or "страхован" in keyword
        or ("китай" in keyword and _has_target_region_export_signal(text))
    )
