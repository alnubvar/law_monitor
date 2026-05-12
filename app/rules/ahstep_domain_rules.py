from __future__ import annotations

from collections.abc import Iterable
import re

from app.models import SourceRole

AHSTEP_DOMAIN_GATED_SOURCE_ROLES: set[SourceRole] = {
    "strategy",
    "support_documents",
    "regional_npa",
    "active_support_measures",
}

AHSTEP_DOMAIN_TERMS = (
    "апк",
    "агропромышлен",
    "сельхоз",
    "сельскохозяй",
    "сельское хозяй",
    "сельского хозяй",
    "сельскому хозяй",
    "сельским хозяй",
    "сельском хозяй",
    "аграр",
    "агропродук",
    "сельхозпродук",
    "сельхозтоваропроизвод",
    "сельскохозяйственн товаропроизвод",
    "фермер",
    "крестьянск",
    "растениевод",
    "зерн",
    "пшениц",
    "ячмен",
    "кукуруз",
    "маслич",
    "подсолнеч",
    "рапс",
    "соя",
    "соев",
    "семеновод",
    "семенн",
    "семян",
    "элеватор",
    "зернохран",
    "хранени зер",
    "хранение зер",
    "хранени сельхоз",
    "хранение сельхоз",
    "логистик сельхоз",
    "транспортировк товаров апк",
    "переработк сельхоз",
    "переработка сельхоз",
    "переработк продукции апк",
    "перерабатывающ промышленность апк",
    "молоч",
    "молок",
    "крупн рогат",
    "животновод",
    "скотовод",
    "молочная продукц",
    "молочной продукц",
    "сыродел",
    "садовод",
    "плодов",
    "ягод",
    "теплич",
    "овощевод",
    "овощн",
    "агрострах",
    "мелиора",
    "сельхозтехник",
    "сельскохозяйственн техник",
    "удобрен",
    "минеральн удобрен",
    "средств защиты растений",
    "средства защиты растений",
)

AHSTEP_DOMAIN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("крс", re.compile(r"\bкрс\b", re.IGNORECASE)),
    (
        "экспорт сельхозпродукции",
        re.compile(
            r"экспорт\w*(?:\s+\w+){0,5}\s+"
            r"(?:зерн\w*|пшениц\w*|ячмен\w*|кукуруз\w*|маслич\w*|"
            r"подсолнеч\w*|рапс\w*|со[ие]\w*|сельхоз\w*|агропродук\w*|"
            r"продукц\w*\s+апк)",
            re.IGNORECASE,
        ),
    ),
    (
        "зерновой экспорт",
        re.compile(
            r"(?:зерн\w*|пшениц\w*|ячмен\w*|кукуруз\w*|маслич\w*|"
            r"подсолнеч\w*|рапс\w*|со[ие]\w*)"
            r"(?:\s+\w+){0,5}\s+экспорт\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "льготное кредитование АПК",
        re.compile(r"льготн\w*\s+кредит\w*(?:\s+\w+){0,4}\s+апк", re.IGNORECASE),
    ),
)

NON_AGRO_NEGATIVE_TERMS = (
    "физическ культур",
    "спортив",
    "туризм",
    "турист",
    "культур",
    "образован",
    "школ",
    "молодеж",
    "молодёж",
    "патриот",
    "здравоохран",
    "медицин",
    "медиц",
    "социальн поддерж",
    "социальн защит",
    "поддержк насел",
    "благоустрой",
    "городск сред",
    "жкх",
    "дорожн",
    "мост",
    "пассажир",
    "транспорт пассаж",
    "некоммерческ",
    "средств массовой информации",
    "террор",
    "антитеррор",
    "безопасност насел",
    "муниципальн програм",
    "жиль",
    "строительств",
    "капремонт",
    "капитальн ремонт",
    "национальн проект",
)

NON_AGRO_NEGATIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("спорт", re.compile(r"\bспорт\w*\b", re.IGNORECASE)),
    ("нко", re.compile(r"\bнко\b", re.IGNORECASE)),
    ("сми", re.compile(r"\bсми\b", re.IGNORECASE)),
)


def should_apply_ahstep_domain_gate(source_role: SourceRole | None) -> bool:
    return source_role in AHSTEP_DOMAIN_GATED_SOURCE_ROLES


def has_ahstep_domain_relevance(*parts: object) -> bool:
    text = normalize_domain_text(*parts)
    return bool(find_ahstep_domain_terms(text))


def has_non_agro_negative_domain(*parts: object) -> bool:
    text = normalize_domain_text(*parts)
    return bool(find_non_agro_negative_terms(text))


def find_ahstep_domain_terms(text: str) -> tuple[str, ...]:
    normalized = normalize_domain_text(text)
    matches = [term for term in AHSTEP_DOMAIN_TERMS if term in normalized]
    matches.extend(label for label, pattern in AHSTEP_DOMAIN_PATTERNS if pattern.search(normalized))
    return tuple(dict.fromkeys(matches))


def find_non_agro_negative_terms(text: str) -> tuple[str, ...]:
    normalized = normalize_domain_text(text)
    matches = [term for term in NON_AGRO_NEGATIVE_TERMS if term in normalized]
    matches.extend(label for label, pattern in NON_AGRO_NEGATIVE_PATTERNS if pattern.search(normalized))
    return tuple(dict.fromkeys(matches))


def is_non_ahstep_domain_document(*parts: object) -> bool:
    text = normalize_domain_text(*parts)
    return not bool(find_ahstep_domain_terms(text))


def normalize_domain_text(*parts: object) -> str:
    return re.sub(
        r"\s+",
        " ",
        " ".join(_iter_text_parts(parts)).lower(),
    ).strip()


def _iter_text_parts(parts: Iterable[object]) -> Iterable[str]:
    for part in parts:
        if part is None:
            continue
        text = str(part).strip()
        if text:
            yield text
