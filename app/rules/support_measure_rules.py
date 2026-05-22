from __future__ import annotations

from app.rules.ahstep_applicability import TARGET_REGION_CODES
from app.models import SourceRole

GENERIC_SUPPORT_TITLES = {
    "субсидии",
    "господдержка",
    "меры поддержки",
    "меры господдержки",
}
SUPPORT_LISTING_TITLE_PREFIXES = (
    "субсидирование и финансирование",
    "господдержка",
    "меры поддержки",
    "меры господдержки",
)
SUPPORT_LISTING_BODY_MARKERS = (
    "электронный бюджет",
    "актуальные отборы",
    "инструкция по заполнению",
    "инструкции по заполнению",
    "виноградарство",
    "животноводство",
    "инвестиционные кредиты",
    "льготное кредитование",
    "мелиорация",
    "экспорт продукции апк",
    "перерабатывающая промышленность",
    "растениеводство",
    "рыбоводство",
    "садоводство",
    "страхование в области растениеводства",
    "приобретение сельскохозяйственной техники",
    "ссылки все краевые порталы",
    "портал предоставления мер финансовой государственной поддержки",
)
SUPPORT_LISTING_URL_MARKERS = (
    "/subsidirovanie-i-finansirovanie",
    "/gospodderzhka/",
    "/subsidii/",
)
TARGET_REGIONS = set(TARGET_REGION_CODES)
IMPORTANT_FEDERAL_PERMANENT_MEASURE_MARKERS = (
    "льготное кредитование",
    "транспортировка товаров апк",
)


def is_support_context(
    *,
    source_name: str | None,
    source_role: SourceRole | None,
    url: str | None,
    level: str | None,
    page_type: str,
) -> bool:
    combined = f"{source_name or ''} {url or ''} {level or ''}".lower()
    if source_role in {"active_support_measures", "support_documents"}:
        return True
    if "gisp" in combined or "гисп" in combined:
        return True
    if "господдерж" in combined or "меры поддержки" in combined:
        return True
    if level == "support_measures":
        return True
    return page_type in {"measure_card", "selection_announcement", "deadline_update"} and (
        "subsid" in combined or "субсид" in combined
    )


def looks_support_listing_page(
    title: str,
    lead_text: str,
    *,
    source_name: str | None,
    url: str | None,
) -> bool:
    if title not in GENERIC_SUPPORT_TITLES:
        return False
    source_key = f"{source_name or ''} {url or ''}".lower()
    listing_markers = (
        "сравнить",
        "избранное",
        "смотреть все",
        "навига",
        "открытые данные",
        "главная",
        "гостехнадзор",
        "объявления",
    )
    if any(marker in lead_text for marker in listing_markers):
        return True
    if "гисп" in source_key and lead_text.count("активная") + lead_text.count("не активная") > 1:
        return True
    return False


def looks_support_catalog_page(
    title: str,
    lead_text: str,
    *,
    source_name: str | None,
    url: str | None,
) -> bool:
    title_text = title.lower().strip()
    lead_text_lower = lead_text.lower()
    source_key = f"{source_name or ''} {url or ''}".lower()

    has_generic_title = (
        title_text in GENERIC_SUPPORT_TITLES
        or any(title_text.startswith(prefix) for prefix in SUPPORT_LISTING_TITLE_PREFIXES)
    )
    has_listing_url = any(marker in source_key for marker in SUPPORT_LISTING_URL_MARKERS)
    listing_marker_hits = sum(
        1 for marker in SUPPORT_LISTING_BODY_MARKERS if marker in lead_text_lower
    )
    has_listing_chrome = any(
        marker in lead_text_lower
        for marker in (
            "главная документы",
            "электронный бюджет",
            "портал предоставления мер",
            "ссылки все краевые порталы",
        )
    )
    has_concrete_signal = any(
        marker in lead_text_lower
        for marker in (
            "прием заявок до",
            "приём заявок до",
            "заявки принимаются до",
            "срок подачи",
            "срок приема",
            "срок приёма",
            "нпа ",
            "постановление от",
            "приказ от",
            "распоряжение от",
            "размер субсидии",
            "размер поддержки",
            "ставка субсидии",
            "объявлен отбор",
            "конкурсный отбор",
        )
    )
    return (
        (has_generic_title or has_listing_url)
        and (listing_marker_hits >= 3 or has_listing_chrome)
        and not has_concrete_signal
    )


def is_target_region(
    *,
    region: str | None,
    title: str,
    source_name: str | None,
    url: str | None,
) -> bool:
    if region in TARGET_REGIONS - {"federal"}:
        return True
    combined = f"{title} {source_name or ''} {url or ''}".lower()
    return any(
        marker in combined
        for code, markers in TARGET_REGION_CODES.items()
        if code != "federal"
        for marker in markers
    )


def is_federal_measure(
    *,
    region: str | None,
    level: str | None,
    title: str,
    source_name: str | None,
) -> bool:
    combined = f"{title} {source_name or ''} {level or ''}".lower()
    if any(
        marker in combined
        for marker in (
            "томской области",
            "ульяновской области",
            "башкортостан",
            "башкир",
            "иркутской области",
            "омской области",
            "липецкой области",
            "пензенской области",
            "волгоградской области",
        )
    ):
        return False
    if region == "federal":
        return True
    if "гисп" in combined or "gisp" in combined:
        return True
    if level == "support_measures":
        return True
    return level in {"federal", "support_measures"} and "федерал" in combined
