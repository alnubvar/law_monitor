from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

from app.llm.facts_extractor import DocumentFacts
from app.models import SourceRole
from app.rules.news_rules import has_news_signal
from app.rules.noise_rules import looks_anti_corruption_noise, looks_irrelevant
from app.rules.page_type_rules import (
    ACTIONABLE_PAGE_TYPES,
    ACTION_MARKERS,
    REQUIRES_ATTENTION_SIGNALS,
    WATCHLIST_MARKERS,
    WATCHLIST_ONLY_PAGE_TYPES,
)
from app.rules.regional_npa_rules import (
    REGIONAL_DOMAINS,
    is_generic_regional_section_title,
    looks_generic_regional_section_page,
    looks_krasnodar_public_consultation_listing,
    looks_low_value_regional_section_page,
    looks_low_value_support_or_npa_page,
)
from app.rules.support_measure_rules import (
    GENERIC_SUPPORT_TITLES,
    IMPORTANT_FEDERAL_PERMANENT_MEASURE_MARKERS,
    is_federal_measure,
    is_support_context,
    is_target_region,
    looks_support_catalog_page,
)

TOPIC_RULES = {
    "молоч": "Молочное животноводство",
    "семен": "Элитное семеноводство",
    "экспорт": "Экспорт АПК",
    "зернов": "Производство и реализация зерновых культур",
    "переработ": "Переработка сельхозпродукции",
    "субсид": "Господдержка и субсидии",
    "кредит": "Льготное кредитование АПК",
    "финансирован": "Финансирование АПК",
}
PROJECT_DISCUSSION_SIGNALS = (
    "публичное обсуждение",
    "срок обсуждения",
    "изменение порядка",
    "проект постановления",
    "проект приказа",
    "консультац",
)
SUPPORT_CHANGE_MARKERS = (
    "внесены изменения",
    "внести изменения",
    "внесении изменений",
    "изменение порядка",
    "изменения в порядок",
    "утвержден порядок",
    "утверждён порядок",
    "новая мера поддержки",
    "новые меры поддержки",
    "изменение условий",
    "льготное кредитование",
    "субсид",
    "возмещение части затрат",
    "компенсация затрат",
)
EXPORT_CONTROL_MARKERS = (
    "экспортн",
    "импортн",
    "пошлин",
    "квот",
    "ограничен",
    "запрет",
)
GOVERNMENT_DECISION_MARKERS = (
    "правительств",
    "кабмин",
    "минсельхоз",
    "утверд",
    "ввод",
    "ввел",
    "продлил",
    "продлен",
    "продлён",
    "изменил",
    "подтверд",
    "решени",
    "поруч",
)
NEGATED_ACTION_PATTERNS = (
    r"без(?:\s+\w+){0,3}\s+изменени",
    r"без(?:\s+\w+){0,3}\s+срок\w*\s+подач",
    r"без(?:\s+\w+){0,3}\s+прием\w*\s+заяв",
    r"без(?:\s+\w+){0,3}\s+приём\w*\s+заяв",
)


def detect_importance(action_level: str) -> str:
    if action_level == "requires_attention":
        return "high"
    if action_level == "watchlist":
        return "medium"
    return "low"


def detect_topic(text: str) -> str | None:
    for marker, topic in TOPIC_RULES.items():
        if marker in text:
            return topic
    return "Общий мониторинг АПК" if "апк" in text or "сельск" in text else None


def build_impact(
    action_level: str,
    topic: str | None,
    *,
    facts: DocumentFacts | None = None,
) -> str:
    if facts is not None:
        if facts.support_status == "inactive":
            return (
                "Материал носит справочный характер: мера сейчас неактивна, поэтому его стоит "
                "использовать для истории условий и сравнения с будущими перезапусками."
            )
        if facts.application_status == "closed":
            if facts.deadline_text:
                return (
                    "Окно подачи уже завершено; документ полезен как справка по прошедшему отбору "
                    "и для проверки условий перед следующим запуском."
                )
            return "Прием заявок завершен; документ полезен как справка и ориентир для следующих отборов."
        if facts.application_status == "open" and facts.deadline_text:
            return (
                f"Есть действующий срок подачи ({facts.deadline_text}); GR-команде стоит проверить "
                "окно участия, условия меры и ответственных."
            )
        if facts.application_status == "open":
            return (
                "Прием заявок открыт; GR-команде стоит проверить условия участия, состав документов "
                "и ближайшие действия по мере."
            )
        if facts.support_status == "active" and facts.application_status == "regular":
            return (
                "Мера действует на регулярной основе без разового дедлайна; ее можно использовать "
                "для проработки участия, сверки условий и планирования финансирования."
            )
    if action_level == "requires_attention":
        return (
            "Материал требует реакции GR-команды: в тексте есть признаки мер поддержки, "
            "регуляторных изменений, сроков подачи или иных условий, которые могут потребовать действия."
        )
    if action_level == "watchlist":
        return (
            "Документ стоит держать на наблюдении: тема может затронуть АПК, регионы присутствия "
            "или профильные направления холдинга по направлению "
            f"{topic or 'АПК'}."
        )
    if action_level == "background":
        return "Документ относится к отраслевому фону и полезен для архива мониторинга, но не требует показа в ежедневной сводке."
    return "Документ не относится к GR-задаче и не должен попадать в ежедневную сводку."


def detect_action_level(
    title: str,
    raw_text: str,
    matched_keywords: Sequence[str],
    *,
    page_type: str,
    content_quality: str,
    is_service_page: bool,
    source_role: SourceRole | None,
    source_name: str | None,
    url: str | None,
    domain: str,
    level: str | None,
    region: str | None,
    facts: DocumentFacts,
    has_strategy_signal: bool,
    has_support_document_signal: bool,
    has_regional_npa_signal: bool,
    has_news_signal_value: bool,
) -> str:
    title_text = title.lower().strip()
    body_text = raw_text.lower()
    lead_text = body_text[:1500]

    if is_service_page or page_type == "navigation" or content_quality in {"navigation", "empty"}:
        if source_role == "strategy" and has_strategy_signal:
            return "watchlist"
        if (
            source_role == "regional_npa"
            and has_regional_npa_signal
            and _has_strong_regional_npa_action_signal(
                title_text=title_text,
                lead_text=lead_text,
                facts=facts,
            )
        ):
            return "requires_attention"
        if source_role == "regional_npa" and has_regional_npa_signal:
            return "watchlist"
        return "irrelevant"
    if looks_irrelevant(title_text, body_text):
        if source_role == "strategy" and has_strategy_signal:
            return "watchlist"
        if (
            source_role == "regional_npa"
            and has_regional_npa_signal
            and _has_strong_regional_npa_action_signal(
                title_text=title_text,
                lead_text=lead_text,
                facts=facts,
            )
        ):
            return "requires_attention"
        if source_role == "regional_npa" and has_regional_npa_signal:
            return "watchlist"
        return "irrelevant"
    if looks_anti_corruption_noise(title_text, lead_text):
        return "irrelevant"

    has_watch_in_title = any(marker in title_text for marker in WATCHLIST_MARKERS)
    has_watch_in_body = any(marker in body_text for marker in WATCHLIST_MARKERS)
    explicit_keywords = bool(matched_keywords)
    has_strict_action_signal_in_title = any(
        signal in title_text for signal in REQUIRES_ATTENTION_SIGNALS
    )
    has_strict_action_signal = any(
        signal in title_text or signal in lead_text for signal in REQUIRES_ATTENTION_SIGNALS
    )
    has_any_action_signal = any(marker in title_text or marker in lead_text for marker in ACTION_MARKERS)
    is_generic_support_title = title_text in GENERIC_SUPPORT_TITLES
    is_support_context_value = is_support_context(
        source_name=source_name,
        source_role=source_role,
        url=url,
        level=level,
        page_type=page_type,
    )
    is_target_region_value = is_target_region(
        region=region,
        title=title_text,
        source_name=source_name,
        url=url,
    )
    is_federal_measure_value = is_federal_measure(
        region=region,
        level=level,
        title=title_text,
        source_name=source_name,
    )
    is_important_permanent_measure = any(
        marker in title_text for marker in IMPORTANT_FEDERAL_PERMANENT_MEASURE_MARKERS
    )
    has_project_discussion_signal = any(
        signal in title_text or signal in lead_text
        for signal in PROJECT_DISCUSSION_SIGNALS
    )

    if page_type in {"registry", "results_protocol", "reference_page"}:
        if source_role == "regional_npa":
            if looks_krasnodar_public_consultation_listing(
                title_text,
                url=url or "",
                domain=domain,
            ):
                return "background"
            if (
                page_type == "reference_page"
                and has_project_discussion_signal
                and has_regional_npa_signal
            ):
                return "watchlist"
            return "background"
        if source_role == "support_documents":
            if looks_support_catalog_page(
                title_text,
                lead_text,
                source_name=source_name,
                url=url,
            ):
                return "background"
            if (
                page_type == "reference_page"
                and not is_generic_support_title
                and not looks_generic_regional_section_page(title_text, lead_text, domain=domain, url=url or "")
                and (
                    has_strict_action_signal
                    or has_project_discussion_signal
                    or facts.deadline_text is not None
                    or facts.application_status == "open"
                )
                and has_support_document_signal
            ):
                return "watchlist"
            return "background"
        if (
            page_type == "reference_page"
            and domain in REGIONAL_DOMAINS
            and (is_generic_support_title or looks_generic_regional_section_page(title_text, lead_text, domain=domain, url=url or ""))
        ):
            return "background"
        if has_any_action_signal or has_watch_in_title or has_watch_in_body or explicit_keywords or is_support_context_value:
            return "watchlist"
        return "background"
    if page_type in {"section_page", "category_page"} and looks_low_value_regional_section_page(
        title_text,
        lead_text,
        domain=domain,
        url=url or "",
    ):
        if has_strict_action_signal or has_project_discussion_signal:
            return "watchlist"
        return "irrelevant"
    if looks_low_value_support_or_npa_page(
        title_text,
        lead_text,
        source_role=source_role,
        url=url or "",
    ):
        if has_strict_action_signal or has_project_discussion_signal or facts.deadline_text:
            return "watchlist"
        return "irrelevant"
    if source_role == "support_documents" and looks_support_catalog_page(
        title_text,
        lead_text,
        source_name=source_name,
        url=url,
    ):
        return "background"
    if source_role == "support_documents" and page_type in {"section_page", "category_page", "reference_page"}:
        if looks_generic_regional_section_page(
            title_text,
            lead_text,
            domain=domain,
            url=url or "",
        ):
            return "background"
    if source_role == "support_documents" and page_type in {"section_page", "category_page"}:
        if has_support_document_signal and has_strict_action_signal:
            return "watchlist"
        return "background"
    if source_role == "regional_npa" and page_type in {"section_page", "category_page", "year_archive"}:
        if has_regional_npa_signal and has_project_discussion_signal:
            return "watchlist"
        return "background"
    if facts.application_status == "closed":
        if has_any_action_signal or has_watch_in_title or has_watch_in_body or explicit_keywords or is_support_context_value:
            return "watchlist"
        return "background"
    if source_role == "strategy":
        if has_project_discussion_signal and facts.deadline_text:
            return "requires_attention"
        if has_strategy_signal or has_any_action_signal or explicit_keywords:
            return "watchlist"
        return "background"
    if domain == "regulation.gov.ru":
        if has_project_discussion_signal and (
            "срок обсуждения" in title_text
            or "срок обсуждения" in lead_text
            or facts.deadline_text
        ):
            return "requires_attention"
        if has_project_discussion_signal or has_any_action_signal or explicit_keywords:
            return "watchlist"
        return "background"
    if is_generic_support_title and not has_strict_action_signal_in_title:
        if has_any_action_signal or has_watch_in_title or has_watch_in_body or explicit_keywords or is_support_context_value:
            return "watchlist"
        return "background"
    if is_support_context_value and page_type in ACTIONABLE_PAGE_TYPES:
        if not (is_target_region_value or is_federal_measure_value):
            return "background"
        if (
            source_role == "regional_npa"
            and _has_strong_regional_npa_action_signal(
                title_text=title_text,
                lead_text=lead_text,
                facts=facts,
            )
        ):
            return "requires_attention"
        if facts.support_status == "inactive":
            return "background"
        if _has_strong_support_action_signal(
            page_type=page_type,
            title_text=title_text,
            lead_text=lead_text,
            facts=facts,
        ):
            return "requires_attention"
        if facts.support_status == "active" and facts.application_status == "open":
            return "requires_attention"
        if (
            facts.support_status == "active"
            and facts.application_status == "regular"
            and (
                facts.deadline_text
                or has_strict_action_signal
                or is_important_permanent_measure
            )
        ):
            return "requires_attention"
        if has_any_action_signal or has_watch_in_title or has_watch_in_body or explicit_keywords:
            return "watchlist"
        return "background"
    if source_role == "regional_npa":
        if (
            page_type in ACTIONABLE_PAGE_TYPES
            and has_regional_npa_signal
            and _has_strong_regional_npa_action_signal(
                title_text=title_text,
                lead_text=lead_text,
                facts=facts,
            )
        ):
            return "requires_attention"
        if has_project_discussion_signal and has_regional_npa_signal:
            return "watchlist"
        if page_type in ACTIONABLE_PAGE_TYPES and has_regional_npa_signal:
            return "watchlist"
        return "background"
    if source_role == "support_documents":
        if page_type in ACTIONABLE_PAGE_TYPES and has_strict_action_signal:
            return "requires_attention"
        if page_type in ACTIONABLE_PAGE_TYPES and (has_support_document_signal or has_any_action_signal or explicit_keywords):
            return "watchlist"
        return "background"
    if page_type in ACTIONABLE_PAGE_TYPES and has_strict_action_signal:
        if facts.support_status != "inactive" and facts.application_status != "closed":
            return "requires_attention"
    if (
        source_role == "news_signals"
        and has_news_signal_value
        and _has_strong_news_action_signal(title_text=title_text, lead_text=lead_text)
    ):
        return "requires_attention"
    if page_type in WATCHLIST_ONLY_PAGE_TYPES:
        if has_any_action_signal or has_watch_in_title or has_watch_in_body or explicit_keywords:
            return "watchlist"
        return "background"
    if source_role == "news_signals":
        if has_news_signal_value:
            return "watchlist"
        return "background"
    if page_type == "news_background":
        if has_watch_in_title or has_watch_in_body or explicit_keywords:
            return "watchlist"
        if has_any_action_signal:
            return "background"
        return "background"
    if has_watch_in_title or (explicit_keywords and has_watch_in_body):
        return "watchlist"
    if has_any_action_signal or explicit_keywords:
        return "background"
    return "irrelevant"


def _has_strong_support_action_signal(
    *,
    page_type: str,
    title_text: str,
    lead_text: str,
    facts: DocumentFacts,
) -> bool:
    text = f"{title_text} {lead_text}"
    if facts.application_status == "open":
        return True
    if page_type == "deadline_update" and facts.deadline_text:
        return True
    if facts.deadline_text and any(marker in text for marker in SUPPORT_CHANGE_MARKERS):
        return True
    return False


def _has_strong_regional_npa_action_signal(
    *,
    title_text: str,
    lead_text: str,
    facts: DocumentFacts,
) -> bool:
    text = f"{title_text} {lead_text}"
    if any(re.search(pattern, text) for pattern in NEGATED_ACTION_PATTERNS):
        return False
    has_support_change = any(marker in text for marker in SUPPORT_CHANGE_MARKERS)
    if not has_support_change:
        return False
    if facts.deadline_text:
        return True
    return "порядок предоставления субсид" in text or "изменени" in text


def _has_strong_news_action_signal(*, title_text: str, lead_text: str) -> bool:
    text = f"{title_text} {lead_text}"
    has_export_control = any(marker in text for marker in EXPORT_CONTROL_MARKERS)
    has_decision = any(marker in text for marker in GOVERNMENT_DECISION_MARKERS)
    has_support_change = any(marker in text for marker in SUPPORT_CHANGE_MARKERS)
    if has_export_control and has_decision:
        return True
    if has_support_change and has_decision and (
        "экспорт" in text or "апк" in text or "сельск" in text
    ):
        return True
    return False


def build_business_signal(
    *,
    action_level: str,
    page_type: str,
    facts: DocumentFacts,
    title: str,
    source_role: SourceRole | None,
    source_name: str | None,
    url: str | None,
    level: str | None,
    region: str | None,
    domain: str,
    keyword_groups: Mapping[str, Sequence[str]],
) -> str:
    is_support_context_value = is_support_context(
        source_name=source_name,
        source_role=source_role,
        url=url,
        level=level,
        page_type=page_type,
    )
    is_target_region_value = is_target_region(
        region=region,
        title=title.lower(),
        source_name=source_name,
        url=url,
    )
    is_federal_measure_value = is_federal_measure(
        region=region,
        level=level,
        title=title.lower(),
        source_name=source_name,
    )
    is_important_permanent_measure = any(
        marker in title.lower()
        for marker in IMPORTANT_FEDERAL_PERMANENT_MEASURE_MARKERS
    )
    title_text = title.lower()
    combined_text = f"{title_text} {(url or '').lower()}"
    if page_type in {"results_protocol", "registry"}:
        return "Результаты/протокол отбора: не требует срочной реакции"
    if looks_low_value_support_or_npa_page(
        title.lower(),
        f"{title.lower()} {(url or '').lower()}",
        source_role=source_role,
        url=url or "",
    ):
        return "Общий раздел/служебная страница; прямой GR-сигнал не выявлен."
    if page_type == "reference_page" and "приказ" in title.lower():
        return "Общий раздел документов/приказов; прямой GR-сигнал не выявлен."
    if source_role == "strategy":
        if facts.deadline_text or "публич" in combined_text:
            return "Стратегический федеральный документ с обсуждением или сроком; держать на контроле."
        return "Стратегический федеральный сигнал по господдержке или порядку регулирования."
    if source_role == "regional_npa":
        if "публич" in combined_text or "консультац" in combined_text:
            return "Региональный НПА / публичные консультации по профильной теме; держать на наблюдении."
        if page_type in {"reference_page", "section_page", "category_page", "year_archive"}:
            return "Общий раздел/архив НПА; прямой GR-сигнал не выявлен."
        return "Региональный НПА по профильной теме: оставить в наблюдении."
    if source_role == "support_documents" and page_type in {"reference_page", "section_page", "category_page"}:
        return "Общий раздел/список документов; прямой GR-сигнал не выявлен."
    if source_role == "news_signals":
        if has_news_signal(
            title_text,
            title_text,
            matched_keywords=(),
            keyword_groups=keyword_groups,
        ):
            return "Новостной предвестник возможных изменений господдержки, экспорта или регулирования."
        return "Рыночный или отраслевой фон без прямого регуляторного сигнала."
    if (
        page_type in {"reference_page", "section_page"}
        and is_generic_regional_section_title(title)
        and domain in REGIONAL_DOMAINS
    ):
        return "Общий раздел/список документов; прямой GR-сигнал не выявлен."
    if page_type == "reference_page" and is_support_context_value:
        return "Страница похожа на раздел/листинг мер поддержки, не на конкретную карточку меры."
    if is_support_context_value and not (is_target_region_value or is_federal_measure_value) and facts.support_status == "active":
        return "Активная мера поддержки вне целевой географии; оставлена для справки."
    if facts.support_status == "inactive":
        return "Неактивная мера поддержки: оставить в справочном блоке"
    if facts.application_status == "closed":
        return "Прием заявок или отбор завершены: срочная реакция не требуется"
    if (
        facts.support_status == "active"
        and facts.application_status == "regular"
        and not is_important_permanent_measure
    ):
        return "Постоянная федеральная мера поддержки; срочное окно подачи не выявлено."
    if facts.support_status == "active" and facts.application_status == "regular":
        return "Активная федеральная мера поддержки, действует на регулярной основе"
    if facts.application_status == "open":
        return "Найден признак приема заявок"
    if action_level == "requires_attention" and page_type == "new_rule":
        return "Найдено изменение порядка или новый регуляторный акт"
    if is_support_context_value:
        return "Справочная мера поддержки без срочного действия"
    return "Отраслевой фон без прямого действия"
