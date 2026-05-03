from __future__ import annotations

from app.models import SourceRole

GENERIC_SUPPORT_TITLES = {
    "субсидии",
    "господдержка",
    "меры поддержки",
    "меры господдержки",
}
TARGET_REGIONS = {"federal", "rostov", "krasnodar", "stavropol"}
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
        for marker in (
            "ростов",
            "краснодар",
            "ставрополь",
            "донланд",
            "кубан",
        )
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
