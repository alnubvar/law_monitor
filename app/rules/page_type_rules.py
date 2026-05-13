from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlparse

from app.models import SourceRole
from app.rules.noise_rules import looks_irrelevant
from app.rules.regional_npa_rules import (
    CATEGORY_PAGE_TITLES,
    DOMAIN_SECTION_TITLE_HINTS,
    REGIONAL_DOMAINS,
    RESULTS_LIKE_TITLES,
    SECTION_PAGE_TITLES,
    SERVICE_BODY_FRAGMENTS,
    YEAR_TITLE_RE,
    looks_generic_regional_section_page,
    looks_krasnodar_public_consultation_listing,
    looks_low_value_regional_section_page,
    looks_low_value_support_or_npa_page,
    looks_orders_listing_page,
)
from app.rules.support_measure_rules import (
    GENERIC_SUPPORT_TITLES,
    looks_support_catalog_page,
    looks_support_listing_page,
)

ACTION_MARKERS = (
    "субсид",
    "господдерж",
    "мера поддержки",
    "отбор",
    "конкурсный отбор",
    "льготн",
    "кредит",
    "возмещ",
    "финансирован",
    "постановлен",
    "приказ",
    "распоряжен",
    "изменение порядка",
    "вступает в силу",
    "срок подачи",
    "заявк",
    "распределение субсид",
)
REQUIRES_ATTENTION_SIGNALS = (
    "объявление об отборе",
    "прием заявок",
    "прием заявок открыт",
    "приём заявок",
    "приём заявок открыт",
    "срок подачи",
    "объявлен отбор",
    "утвержден порядок",
    "утверждён порядок",
    "внесены изменения",
    "постановление от",
    "приказ от",
    "распоряжение от",
    "субсидия на",
    "возмещение части затрат",
    "льготное кредитование",
    "компенсация затрат",
)
WATCHLIST_MARKERS = (
    "апк",
    "сельск",
    "зерно",
    "урож",
    "посевн",
    "мелиора",
    "экспорт",
    "импорт",
    "переработ",
    "земл",
    "животновод",
)
PAGE_TYPE_MARKERS: dict[str, tuple[str, ...]] = {
    "registry": (
        "реестр",
        "реестры",
        "перечень получателей",
        "реестр получател",
        "список получател",
    ),
    "results_protocol": (
        "результаты отбора",
        "результаты отборов",
        "протокол",
        "итоги отбора",
        "отказ",
        "рассмотрения заявок",
        "итоги конкурсного отбора",
    ),
    "selection_announcement": (
        "объявление об отборе",
        "объявление о приеме заявочной документации",
        "объявлен отбор",
        "прием заявок",
        "приём заявок",
        "начало приема",
        "начало приёма",
        "срок подачи заявок",
        "заявки принимаются",
    ),
    "measure_card": (
        "мера поддержки",
        "льготный лизинг",
        "льготное кредитование",
        "компенсация затрат",
        "возмещение части затрат",
        "субсидия на",
    ),
    "new_rule": (
        "постановление",
        "приказ",
        "распоряжение",
        "внесены изменения",
        "утвержден порядок",
        "утверждён порядок",
        "вступает в силу",
    ),
    "deadline_update": (
        "срок подачи",
        "сроки приема документов",
        "сроки приёма документов",
        "продлен прием заявок",
        "продлён приём заявок",
        "заявки принимаются до",
        "начало приема документов",
        "начало приёма документов",
    ),
    "reference_page": (
        "сроки предоставления государственных услуг",
        "информация для заявителей",
        "порядок предоставления услуги",
        "анкета получателя мер государственной поддержки",
        "образец заявления",
    ),
}
ACTIONABLE_PAGE_TYPES = {
    "selection_announcement",
    "measure_card",
    "new_rule",
    "deadline_update",
}
WATCHLIST_ONLY_PAGE_TYPES = {
    "results_protocol",
    "registry",
    "reference_page",
    "section_page",
    "category_page",
    "year_archive",
}
REFERENCE_TITLE_WORD_RE = re.compile(
    r"\b(анкета|форма|формы|памятка|инструкция|инструкции|образец)\b",
    re.IGNORECASE,
)
MCX_DONLAND_REFERENCE_TITLES = {
    "агропромышленный комплекс",
    "виноградарство и виноделие",
    "государственные закупки",
    "действующие документы",
    "животноводство",
    "малые формы хозяйствования",
    "меры государственной поддержки",
    "наука и образование",
    "пищевая и перерабатывающая промышленность",
    "пресс-центр",
    "растениеводство",
    "рыбохозяйственный комплекс",
    "страхование и инвестиции",
    "экономика и финансы",
}
PRAVO_DONLAND_REFERENCE_TITLES = {
    "за сегодня",
    "за неделю",
    "за месяц",
    "календарь опубликования",
    "официальное опубликование",
    "отмененные документы",
    "правовая информация",
    "правовая информатизация",
    "правовые акты",
    "проекты правовых актов",
}
PRAVO_DONLAND_DOCUMENT_MARKERS = (
    "закон",
    "постановление",
    "приказ",
    "распоряжение",
    "решение",
    "указ",
)
MSHSK_REFERENCE_TITLES = {
    "гранты",
    "объявления",
    "результаты",
    "субсидии",
    "электронный бюджет",
    "грантовая поддержка \"агростартап\"",
    "решения о порядке предоставления субсидии",
    "справочник по мерам государственной поддержки",
}
PRAVO_STAV_REFERENCE_TITLES = {
    "архив документов",
    "документы",
    "о портале",
    "поиск документов",
    "правовые акты",
    "проекты документов",
    "расширенный поиск",
}
PRAVO_STAV_DOCUMENT_MARKERS = (
    "закон",
    "постановление",
    "приказ",
    "распоряжение",
    "решение",
    "указ",
)
KRASNODAR_NPA_FILE_RE = re.compile(r"^/rest/files/\d+/?$", re.IGNORECASE)
KRASNODAR_SUPPORT_ORDER_MARKERS = (
    "субсид",
    "грант",
    "порядок предоставления",
    "отбор",
    "крестьянск",
    "фермерск",
    "агротуризм",
    "картофел",
    "овощ",
    "мелиорац",
)


def detect_page_type(
    title: str,
    raw_text: str,
    matched_keywords: Sequence[str],
    *,
    source_role: SourceRole | None,
    source_name: str | None,
    url: str | None,
    domain: str,
    content_quality: str,
    is_service_page: bool,
    has_strategy_signal: bool,
    has_regional_npa_signal: bool,
) -> str:
    title_text = title.lower().strip()
    body_text = raw_text.lower()
    lead_text = body_text[:2000]

    if YEAR_TITLE_RE.fullmatch(title_text):
        return "year_archive"
    if looks_low_value_support_or_npa_page(
        title_text,
        lead_text,
        source_role=source_role,
        url=url or "",
    ):
        return "section_page"
    if looks_orders_listing_page(title_text, url=url or "", domain=domain):
        return "reference_page"
    if looks_krasnodar_public_consultation_listing(
        title_text,
        url=url or "",
        domain=domain,
    ):
        return "reference_page"
    if looks_krasnodar_support_file_order(
        title_text,
        lead_text,
        source_name=source_name,
        url=url or "",
        domain=domain,
    ):
        return "new_rule"
    if domain == "admkrai.krasnodar.ru" and (url or "").lower().endswith((".pdf", ".doc", ".docx")):
        return "new_rule"
    if looks_low_value_regional_section_page(
        title_text,
        lead_text,
        domain=domain,
        url=url or "",
    ):
        return "section_page"
    if looks_generic_regional_section_page(
        title_text,
        lead_text,
        domain=domain,
        url=url or "",
    ):
        return "reference_page"
    if is_service_page or content_quality == "navigation" or looks_irrelevant(title_text, body_text):
        if domain == "government.ru":
            return "navigation"
        if source_role == "strategy" and has_strategy_signal:
            return "new_rule" if has_regional_npa_signal else "news_background"
        if source_role == "regional_npa" and has_regional_npa_signal:
            return "news_background"
        return "navigation"
    source_specific_page_type = detect_source_specific_page_type(
        domain=domain,
        title=title_text,
        lead_text=lead_text,
        url=url or "",
    )
    if source_specific_page_type is not None:
        return source_specific_page_type
    if title_text in SECTION_PAGE_TITLES:
        return "section_page"
    if title_text in CATEGORY_PAGE_TITLES:
        return "category_page"
    if source_role == "support_documents" and looks_support_catalog_page(
        title_text,
        lead_text,
        source_name=source_name,
        url=url,
    ):
        return "reference_page"
    if looks_support_listing_page(title_text, lead_text, source_name=source_name, url=url):
        return "reference_page"
    if looks_reference_title(title_text):
        return "reference_page"

    for page_type in (
        "registry",
        "results_protocol",
        "selection_announcement",
        "measure_card",
        "new_rule",
        "deadline_update",
    ):
        markers = PAGE_TYPE_MARKERS[page_type]
        if any(marker in title_text for marker in markers):
            return page_type
        if any(marker in lead_text for marker in markers):
            return page_type

    if any(marker in title_text for marker in PAGE_TYPE_MARKERS["reference_page"]):
        return "reference_page"

    has_watch_markers = any(marker in title_text or marker in lead_text for marker in WATCHLIST_MARKERS)
    if any(fragment in lead_text for fragment in SERVICE_BODY_FRAGMENTS):
        return "section_page"
    if has_watch_markers or matched_keywords:
        return "news_background"
    return "unknown"


def detect_source_specific_page_type(
    *,
    domain: str,
    title: str,
    lead_text: str,
    url: str,
) -> str | None:
    lower_url = url.lower()

    if title in RESULTS_LIKE_TITLES:
        if "реестр" in title or "получател" in title or "список" in title:
            return "registry"
        return "results_protocol"

    if domain == "pravo.donland.ru":
        if any(
            fragment in lower_url
            for fragment in (
                "/doc/list/",
                "/search-main/",
                "/calendar/",
                "/rss/",
                "/news/list/",
                "/static/",
                "/legalinfo/",
            )
        ):
            return "reference_page"
        if title in PRAVO_DONLAND_REFERENCE_TITLES:
            return "reference_page"
        if (
            "/doc/view/" in lower_url
            or lower_url.endswith((".pdf", ".doc", ".docx"))
        ) and any(marker in title for marker in PRAVO_DONLAND_DOCUMENT_MARKERS):
            return "new_rule"

    if domain == "pravo.stavregion.ru":
        if any(
            fragment in lower_url
            for fragment in (
                "/search",
                "/archive",
                "/news",
                "/rss",
                "/about",
                "/contacts",
                "/document/list",
                "/documents/list",
            )
        ):
            return "reference_page"
        if title in PRAVO_STAV_REFERENCE_TITLES:
            return "reference_page"
        if (
            any(fragment in lower_url for fragment in ("/document/", "/documents/", "/doc/", "/law/"))
            or lower_url.endswith((".pdf", ".doc", ".docx"))
        ) and any(marker in title for marker in PRAVO_STAV_DOCUMENT_MARKERS):
            return "new_rule"

    if domain in DOMAIN_SECTION_TITLE_HINTS and title in DOMAIN_SECTION_TITLE_HINTS[domain]:
        if YEAR_TITLE_RE.fullmatch(title):
            return "year_archive"
        return "section_page"

    if domain == "mcx.donland.ru":
        if title in MCX_DONLAND_REFERENCE_TITLES and any(
            fragment in lower_url
            for fragment in ("/activity/", "/documents/", "/presscenter/")
        ):
            return "reference_page"
    if domain == "mshsk.ru":
        if title in MSHSK_REFERENCE_TITLES:
            return "reference_page"
        if lower_url.endswith(("/gospodderzhka/", "/subsidii/")):
            return "reference_page"
        if "/gospodderzhka/obyavleniya-20" in lower_url:
            return "reference_page"

    if domain == "gisp.gov.ru":
        if "/measure/" in lower_url:
            return "measure_card"
        if "/nmp/main/" in lower_url or "навига" in lead_text:
            if title in GENERIC_SUPPORT_TITLES or looks_support_listing_page(
                title,
                lead_text,
                source_name=domain,
                url=url,
            ):
                return "reference_page"
            return "navigation"

    if domain in REGIONAL_DOMAINS:
        if domain == "admkrai.krasnodar.ru" and lower_url.endswith((".pdf", ".doc", ".docx")):
            return "new_rule"
        if (
            domain == "msh.krasnodar.ru"
            and "/subsidirovanie-i-finansirovanie" in lower_url
            and YEAR_TITLE_RE.search(title)
        ):
            return "reference_page"
        if title.startswith("приказы ") and "/documents/" in lower_url:
            return "reference_page"
        if any(
            fragment in lower_url
            for fragment in (
                "/about/",
                "/contacts/",
                "/feedback/",
                "/hotline/",
                "/sitemap/",
                "/virtual_reception",
                "/reviews",
                "/templates",
                "/otkrytye-dannye",
            )
        ):
            return "section_page"
        if any(fragment in lead_text for fragment in SERVICE_BODY_FRAGMENTS):
            if "реестр" in title or "получател" in title:
                return "registry"
            if any(marker in title for marker in ("отбор", "протокол", "отказ", "итоги")):
                return "results_protocol"
            return "section_page"
        if "главная контакты" in lead_text or "главная документы" in lead_text or "главная деятельность" in lead_text:
            if any(signal in title or signal in lead_text for signal in REQUIRES_ATTENTION_SIGNALS):
                return None
            if title in CATEGORY_PAGE_TITLES:
                return "category_page"
            return "section_page"

    return None


def looks_reference_title(title: str) -> bool:
    if title in {"нормативные документы", "креативные индустрии"}:
        return False
    return bool(REFERENCE_TITLE_WORD_RE.search(title))


def looks_krasnodar_support_file_order(
    title: str,
    lead_text: str,
    *,
    source_name: str | None,
    url: str,
    domain: str,
) -> bool:
    if domain != "npa.krasnodar.ru":
        return False
    if "минсельхоз краснодарского края - субсидирование и финансирование" not in (
        source_name or ""
    ).lower():
        return False
    parsed = urlparse(url)
    if parsed.scheme != "https" or KRASNODAR_NPA_FILE_RE.fullmatch(parsed.path) is None:
        return False
    text = f"{title} {lead_text}"
    if "приказ" not in text:
        return False
    return any(marker in text for marker in KRASNODAR_SUPPORT_ORDER_MARKERS)
