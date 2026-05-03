from __future__ import annotations

import re

from app.models import SourceRole

SECTION_PAGE_TITLES = {
    "пресс-центр",
    "планы",
    "проекты документов",
    "действующие документы",
    "гражданам",
    "антимонопольный комплаенс",
    "государственные закупки",
    "нормотворческая деятельность",
    "контрольно-надзорная деятельность",
    "документы",
    "развитие апк",
    "опросы",
    "видеогалерея",
    "события",
    "прочие документы",
    "отчеты",
    "вопросы и ответы",
    "награды и поощрения",
    "противодействие коррупции",
    "государственная служба",
    "государственная гражданская служба",
    "аналитика и статистика",
    "государственные услуги",
    "государственные услуги и функции",
    "национальные проекты",
    "контакты и реквизиты",
    "контакты",
    "реквизиты",
    "информационные системы",
    "коллегиальные органы",
    "обратная связь",
    "горячая линия",
    "карта сайта",
    "телефонный справочник икц краснодарского края",
    "телефонный справочник гостехнадзора краснодарского края",
    "обращения граждан",
    "обзоры обращений",
    "онлайн приемная",
    "адрес",
    "шаблоны документов",
    "открытые данные",
    "субсидирование и финансирование",
    "предоставление льгот",
    "результаты проверок",
    "направления деятельности",
    "гостехнадзор",
    "информационные системы",
    "информационные ресурсы",
    "контакты министерства",
    "отчет о финансово-экономическом состоянии схтп апк",
    "руководство",
    "общая информация",
    "мероприятия",
    "технологические схемы",
    "противодействие терроризму",
    "пожарная безопасность",
    "бесплатная юридическая помощь",
    "аграрный совет",
    "полномочный представитель главы администрации (губернатора) краснодарского края по взаимодействию с крестьянскими (фермерскими) хозяйствами",
}
CATEGORY_PAGE_TITLES = {
    "экономика и финансы",
    "растениеводство",
    "животноводство",
    "рыбохозяйственный комплекс",
    "наука и образование",
    "виноградарство и виноделие",
    "пищевая и перерабатывающая промышленность",
    "агропромышленный комплекс",
    "малые формы хозяйствования",
    "господдержка",
    "производство и потребление сельскохозяйственного сырья",
    "доска почета «гордость апк кубани»",
}
YEAR_TITLE_RE = re.compile(r"^20(2[2-9]|3\d)$")
SERVICE_BODY_FRAGMENTS = (
    "раздел находится в стадии наполнения",
    "rss лента",
    "проектов не найдено",
)
RESULTS_LIKE_TITLES = (
    "информация по отказам участникам отбора",
    "результаты отборов и конкурсов на получение субсидий",
    "протоколы рассмотрения заявок",
    "итоги отбора",
    "список получателей",
    "реестр получателей",
)
DOMAIN_SECTION_TITLE_HINTS = {
    "mcx.donland.ru": SECTION_PAGE_TITLES
    | {
        "информация для участников отбора (заявителей)",
        "информация для организаторов отбора",
    },
    "admkrai.krasnodar.ru": SECTION_PAGE_TITLES
    | {
        "развитие апк",
        "приоритеты",
        "визитка",
        "административное деление",
    },
    "msh.krasnodar.ru": SECTION_PAGE_TITLES | {"2022", "2023", "2024", "2025", "2026"},
}
REGIONAL_GENERIC_SECTION_TITLES = {
    "господдержка",
    "субсидии",
    "приказы",
    "документы",
}
LOW_VALUE_REGIONAL_SECTION_TITLES = {
    "вакансии",
    "контактный центр по вопросам предоставления услуг в электронном виде",
    "генеральные планы",
    "градостроительная деятельность",
    "закупки",
    "международное сотрудничество",
    "защита от чс",
    "аналитика",
    "биржевая торговля в апк",
    "госслужба",
    "открытые данные",
    "противодействие коррупции",
    "калькулятор процедур",
}
LOW_VALUE_SUPPORT_NPA_TITLE_FRAGMENTS = (
    "политика обработки персональных данных",
    "персональные данные",
    "охраны объектов культурного наследия",
    "культурного наследия",
    "приоритеты",
    "структура",
    "контакты",
    "положение об органе",
    "госслужба",
    "вакансии",
    "кадры",
    "документы сайта",
    "служебная информация",
    "управление ",
)
SOFT_LOW_VALUE_SUPPORT_NPA_TITLE_FRAGMENTS = (
    "независимая экспертиза",
)
LOW_VALUE_SUPPORT_NPA_BODY_MARKERS = (
    "политика обработки персональных данных",
    "обработка персональных данных",
    "независимая экспертиза",
    "охрана объектов культурного наследия",
    "управление государственной охраны объектов культурного наследия",
    "положение об органе",
    "структура министерства",
    "контакты министерства",
    "государственная служба",
    "кадровое обеспечение",
    "документы сайта",
)
REGIONAL_DOMAINS = {"mcx.donland.ru", "msh.krasnodar.ru", "mshsk.ru", "admkrai.krasnodar.ru"}


def looks_generic_regional_section_page(
    title: str,
    lead_text: str,
    *,
    domain: str,
    url: str,
) -> bool:
    if domain not in REGIONAL_DOMAINS:
        return False
    if title not in REGIONAL_GENERIC_SECTION_TITLES and not title.startswith("приказы "):
        return False
    lower_url = url.lower()
    listing_markers = (
        "архив",
        "раздел",
        "главная",
        "документы",
        "приказы",
        "субсидии",
        "господдержка",
        "перечень",
    )
    if any(marker in lead_text for marker in listing_markers):
        return True
    return any(fragment in lower_url for fragment in ("/documents/", "/activity/", "/content/"))


def looks_low_value_regional_section_page(
    title: str,
    lead_text: str,
    *,
    domain: str,
    url: str,
) -> bool:
    if domain not in REGIONAL_DOMAINS:
        return False
    normalized_title = title.lower().strip()
    if normalized_title not in LOW_VALUE_REGIONAL_SECTION_TITLES:
        return False
    actionable_signals = (
        "прием заявок",
        "приём заявок",
        "срок подачи",
        "публичные консультации",
        "публичное обсуждение",
        "проект постановления",
        "субсидия на",
        "объявлен отбор",
    )
    if any(signal in lead_text for signal in actionable_signals):
        return False
    lower_url = url.lower()
    return any(fragment in lower_url for fragment in ("/activity/", "/content/", "/documents/", "/vacancy", "/purchases"))


def looks_low_value_support_or_npa_page(
    title: str,
    lead_text: str,
    *,
    source_role: SourceRole | None,
    url: str,
) -> bool:
    if source_role not in {"support_documents", "regional_npa"}:
        return False
    combined = f"{title} {lead_text} {url.lower()}"
    actionable_markers = (
        "субсид",
        "льготн",
        "кредит",
        "экспорт",
        "отбор",
        "прием заявок",
        "приём заявок",
        "срок подачи",
        "порядок предоставления",
        "изменение порядка",
        "проект постановления",
        "проект приказа",
        "публичные консультации",
        "публичное обсуждение",
        "оценка регулирующего воздействия",
        "орв",
    )
    if any(fragment in title for fragment in LOW_VALUE_SUPPORT_NPA_TITLE_FRAGMENTS):
        return True
    if any(marker in combined for marker in actionable_markers):
        if any(fragment in title for fragment in SOFT_LOW_VALUE_SUPPORT_NPA_TITLE_FRAGMENTS) and not any(
            marker in combined
            for marker in (
                "проект постановления",
                "проект приказа",
                "оценка регулирующего воздействия",
                "публичные консультации",
                "субсид",
            )
        ):
            return True
        return False

    if any(fragment in title for fragment in SOFT_LOW_VALUE_SUPPORT_NPA_TITLE_FRAGMENTS):
        return True
    if any(fragment in combined for fragment in LOW_VALUE_SUPPORT_NPA_BODY_MARKERS):
        return True
    return any(
        fragment in url.lower()
        for fragment in (
            "/department/",
            "/contacts/",
            "/structure/",
            "/policy/",
            "/personal-data",
            "/personalnye-dannye",
        )
    )


def looks_orders_listing_page(
    title: str,
    *,
    url: str,
    domain: str,
) -> bool:
    return (
        domain in REGIONAL_DOMAINS
        and title.startswith("приказы ")
        and "/documents/" in url.lower()
    )


def is_generic_regional_section_title(title: str) -> bool:
    normalized_title = title.lower().strip()
    return normalized_title in REGIONAL_GENERIC_SECTION_TITLES or normalized_title.startswith("приказы ")
