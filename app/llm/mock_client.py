from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlparse

from app.extractors.site_extractors import clean_text_for_analysis
from app.llm.base import BaseLLMClient
from app.llm.facts_extractor import DocumentFacts, extract_document_facts
from app.models import AnalysisResult

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
    "прием заявок",
    "приём заявок",
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

SENTENCE_SPLIT_REGEX = re.compile(r"(?<=[.!?])\s+")
REFERENCE_TITLE_WORD_RE = re.compile(
    r"\b(анкета|форма|формы|памятка|инструкция|инструкции|образец)\b",
    re.IGNORECASE,
)
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
PROJECT_DISCUSSION_SIGNALS = (
    "публичное обсуждение",
    "срок обсуждения",
    "изменение порядка",
    "проект постановления",
    "проект приказа",
)
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
GISP_UI_ID_RE = re.compile(r"(?<![\d-])\.?\s*\d{3,5}\)(?!\d)")
TITLE_DUPLICATE_RE_TEMPLATE = r"^({title}[.:]?\s+)(?:{title}[.:]?\s+)+"


class MockLLMClient(BaseLLMClient):
    def __init__(self, keywords: Sequence[str]):
        self.keywords = [keyword.strip() for keyword in keywords if keyword.strip()]

    def analyze_document(
        self,
        title: str,
        raw_text: str,
        *,
        source_name: str | None = None,
        url: str | None = None,
        level: str | None = None,
        region: str | None = None,
    ) -> AnalysisResult:
        extracted = clean_text_for_analysis(
            source_name=source_name,
            url=url,
            title=title,
            raw_text=raw_text,
        )
        cleaned_text = extracted.text
        combined_text = f"{title}\n{cleaned_text}".lower()
        facts = extract_document_facts(title, cleaned_text)
        matched_keywords = [
            keyword for keyword in self.keywords if keyword.lower() in combined_text
        ]
        page_type = self._detect_page_type(
            title,
            cleaned_text,
            matched_keywords,
            source_name=source_name,
            url=url,
            content_quality=extracted.content_quality,
            is_service_page=extracted.is_service_page,
            region=region,
        )
        action_level = self._detect_action_level(
            title,
            cleaned_text,
            matched_keywords,
            page_type=page_type,
            content_quality=extracted.content_quality,
            is_service_page=extracted.is_service_page,
            source_name=source_name,
            url=url,
            level=level,
            region=region,
            facts=facts,
        )
        is_relevant = action_level != "irrelevant"
        importance = self._detect_importance(action_level)
        topic = self._detect_topic(combined_text)
        summary = self._build_summary(
            title,
            cleaned_text,
            matched_keywords,
            source_name=source_name,
            url=url,
        )
        impact = self._build_impact(action_level, topic)
        business_signal = self._build_business_signal(
            action_level=action_level,
            page_type=page_type,
            facts=facts,
            title=title,
            source_name=source_name,
            url=url,
            level=level,
            region=region,
        )
        reason = self._build_reason(
            action_level,
            matched_keywords,
            topic,
            page_type=page_type,
            content_quality=extracted.content_quality,
            is_service_page=extracted.is_service_page,
            facts=facts,
            business_signal=business_signal,
        )

        return AnalysisResult(
            is_relevant=is_relevant,
            relevance_reason=reason,
            topic=topic,
            importance=importance,
            action_level=action_level,
            page_type=page_type,
            summary=summary,
            impact=impact,
            support_status=facts.support_status,
            is_active=facts.is_active,
            is_continuous=facts.is_continuous,
            application_status=facts.application_status,
            npa_number=facts.npa_number,
            deadline_text=facts.deadline_text,
            terms_text=facts.terms_text,
            business_signal=business_signal,
            risk_notes=facts.risk_notes,
            key_dates=[facts.deadline_text] if facts.deadline_text else [],
            regions=[],
            source_facts=self._build_source_facts(matched_keywords, facts),
        )

    def _detect_page_type(
        self,
        title: str,
        raw_text: str,
        matched_keywords: Sequence[str],
        *,
        source_name: str | None,
        url: str | None,
        content_quality: str,
        is_service_page: bool,
        region: str | None,
    ) -> str:
        title_text = title.lower().strip()
        body_text = raw_text.lower()
        lead_text = body_text[:2000]
        domain = self._extract_domain(source_name, url)

        if YEAR_TITLE_RE.fullmatch(title_text):
            return "year_archive"
        if self._looks_orders_listing_page(title_text, url=url or "", domain=domain):
            return "reference_page"
        if self._looks_low_value_regional_section_page(
            title_text,
            lead_text,
            domain=domain,
            url=url or "",
        ):
            return "section_page"
        if self._looks_generic_regional_section_page(
            title_text,
            lead_text,
            domain=domain,
            url=url or "",
        ):
            return "reference_page"
        if is_service_page or content_quality == "navigation" or self._looks_irrelevant(title_text, body_text):
            return "navigation"
        source_specific_page_type = self._detect_source_specific_page_type(
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
        if self._looks_support_listing_page(title_text, lead_text, source_name=source_name, url=url):
            return "reference_page"
        if self._looks_reference_title(title_text):
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

    def _detect_action_level(
        self,
        title: str,
        raw_text: str,
        matched_keywords: Sequence[str],
        *,
        page_type: str,
        content_quality: str,
        is_service_page: bool,
        source_name: str | None,
        url: str | None,
        level: str | None,
        region: str | None,
        facts: DocumentFacts,
    ) -> str:
        title_text = title.lower().strip()
        body_text = raw_text.lower()
        lead_text = body_text[:1500]
        domain = self._extract_domain(source_name, url)

        if is_service_page or page_type == "navigation" or content_quality in {"navigation", "empty"}:
            return "irrelevant"
        if self._looks_irrelevant(title_text, body_text):
            return "irrelevant"
        if self._looks_anti_corruption_noise(title_text, lead_text):
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
        is_support_context = self._is_support_context(
            source_name=source_name,
            url=url,
            level=level,
            page_type=page_type,
        )
        is_target_region = self._is_target_region(
            region=region,
            title=title_text,
            source_name=source_name,
            url=url,
        )
        is_federal_measure = self._is_federal_measure(
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
            if (
                page_type == "reference_page"
                and domain in {"mcx.donland.ru", "msh.krasnodar.ru", "mshsk.ru", "admkrai.krasnodar.ru"}
                and (is_generic_support_title or self._looks_generic_regional_section_page(title_text, lead_text, domain=domain, url=url or ""))
            ):
                return "background"
            if has_any_action_signal or has_watch_in_title or has_watch_in_body or explicit_keywords or is_support_context:
                return "watchlist"
            return "background"
        if page_type in {"section_page", "category_page"} and self._looks_low_value_regional_section_page(
            title_text,
            lead_text,
            domain=domain,
            url=url or "",
        ):
            if has_strict_action_signal or has_project_discussion_signal:
                return "watchlist"
            return "irrelevant"
        if facts.application_status == "closed":
            if has_any_action_signal or has_watch_in_title or has_watch_in_body or explicit_keywords or is_support_context:
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
            if has_any_action_signal or has_watch_in_title or has_watch_in_body or explicit_keywords or is_support_context:
                return "watchlist"
            return "background"
        if is_support_context and page_type in ACTIONABLE_PAGE_TYPES:
            if not (is_target_region or is_federal_measure):
                return "watchlist" if facts.support_status == "active" else "background"
            if facts.support_status == "inactive":
                return "watchlist"
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
        if page_type in ACTIONABLE_PAGE_TYPES and has_strict_action_signal:
            if facts.support_status != "inactive" and facts.application_status != "closed":
                return "requires_attention"
        if page_type in WATCHLIST_ONLY_PAGE_TYPES:
            if has_any_action_signal or has_watch_in_title or has_watch_in_body or explicit_keywords:
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

    def _detect_importance(self, action_level: str) -> str:
        if action_level == "requires_attention":
            return "high"
        if action_level == "watchlist":
            return "medium"
        return "low"

    def _detect_topic(self, text: str) -> str | None:
        for marker, topic in TOPIC_RULES.items():
            if marker in text:
                return topic
        return "Общий мониторинг АПК" if "апк" in text or "сельск" in text else None

    def _build_summary(
        self,
        title: str,
        raw_text: str,
        matched_keywords: Sequence[str],
        *,
        source_name: str | None,
        url: str | None,
    ) -> str:
        if not raw_text.strip():
            return self._limit_summary(
                f"Найден документ '{title}', но текст пока не извлечен полностью."
            )

        cleaned_text = self._clean_summary_text(
            raw_text,
            title=title,
            source_name=source_name,
            url=url,
        )
        sentences = SENTENCE_SPLIT_REGEX.split(cleaned_text)
        cleaned = [sentence.strip() for sentence in sentences if sentence.strip()]
        if not cleaned:
            return self._limit_summary(
                f"Найден документ '{title}', требуется дополнительная обработка текста."
            )

        if matched_keywords:
            keyword = matched_keywords[0].lower()
            for sentence in cleaned:
                if keyword in sentence.lower():
                    return self._finalize_summary(
                        sentence,
                        title=title,
                        source_name=source_name,
                        url=url,
                    )
        return self._finalize_summary(
            " ".join(cleaned[:2]),
            title=title,
            source_name=source_name,
            url=url,
        )

    def _build_impact(self, action_level: str, topic: str | None) -> str:
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

    def _build_reason(
        self,
        action_level: str,
        matched_keywords: Sequence[str],
        topic: str | None,
        *,
        page_type: str,
        content_quality: str,
        is_service_page: bool,
        facts: DocumentFacts,
        business_signal: str | None,
    ) -> str:
        if is_service_page or page_type == "navigation" or content_quality == "navigation":
            return "Документ похож на служебную, навигационную или нерелевантную страницу и не содержит признаков действия для GR."
        if page_type in WATCHLIST_ONLY_PAGE_TYPES:
            return (
                "Страница распознана как справочная, категорийная или архивная, поэтому не может быть повышена выше watchlist "
                "даже при наличии слов про субсидии, отборы или постановления."
            )
        if facts.application_status == "closed":
            return "В тексте найден признак завершенного приема заявок или отбора, поэтому документ не требует срочной реакции."
        if facts.support_status == "inactive":
            return "Мера поддержки распознана как неактивная, поэтому документ остается в справочном или наблюдаемом блоке."
        if action_level == "irrelevant":
            return "Документ не содержит достаточно признаков отраслевого контекста или действия для GR и исключен из ежедневной сводки."
        if action_level == "background":
            return "Документ содержит общий отраслевой контекст, но без признаков мер поддержки, сроков, отборов или регуляторного действия."
        if action_level == "watchlist":
            preview = ", ".join(matched_keywords[:5]) or "общие отраслевые маркеры"
            return (
                "Документ отнесен в watchlist: он связан с АПК или регионами присутствия, "
                f"но без прямого сигнала к действию. Маркеры: {preview}. "
                f"Бизнес-сигнал: {business_signal or 'не выделен'}."
            )
        preview = ", ".join(matched_keywords[:5])
        topic_part = f" Тема: {topic}." if topic else ""
        return (
            "Документ требует внимания GR, потому что содержит конкретный action-сигнал "
            f"для АПК и интересов агрохолдинга: {preview or 'action markers detected'}.{topic_part} "
            f"Бизнес-сигнал: {business_signal or 'не выделен'}."
        )

    def _build_business_signal(
        self,
        *,
        action_level: str,
        page_type: str,
        facts: DocumentFacts,
        title: str,
        source_name: str | None,
        url: str | None,
        level: str | None,
        region: str | None,
    ) -> str:
        is_support_context = self._is_support_context(
            source_name=source_name,
            url=url,
            level=level,
            page_type=page_type,
        )
        is_target_region = self._is_target_region(
            region=region,
            title=title.lower(),
            source_name=source_name,
            url=url,
        )
        is_federal_measure = self._is_federal_measure(
            region=region,
            level=level,
            title=title.lower(),
            source_name=source_name,
        )
        is_important_permanent_measure = any(
            marker in title.lower()
            for marker in IMPORTANT_FEDERAL_PERMANENT_MEASURE_MARKERS
        )
        if page_type in {"results_protocol", "registry"}:
            return "Результаты/протокол отбора: не требует срочной реакции"
        if page_type == "reference_page" and "приказ" in title.lower():
            return "Общий раздел документов/приказов; прямой GR-сигнал не выявлен."
        if (
            page_type in {"reference_page", "section_page"}
            and self._is_generic_regional_section_title(title)
            and self._extract_domain(source_name, url) in {"mcx.donland.ru", "msh.krasnodar.ru", "mshsk.ru", "admkrai.krasnodar.ru"}
        ):
            return "Общий раздел/список документов; прямой GR-сигнал не выявлен."
        if page_type == "reference_page" and is_support_context:
            return "Страница похожа на раздел/листинг мер поддержки, не на конкретную карточку меры."
        if is_support_context and not (is_target_region or is_federal_measure) and facts.support_status == "active":
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
        if is_support_context:
            return "Справочная мера поддержки без срочного действия"
        return "Отраслевой фон без прямого действия"

    def _build_source_facts(
        self,
        matched_keywords: Sequence[str],
        facts: DocumentFacts,
    ) -> list[str]:
        values = list(matched_keywords[:5])
        if facts.support_status != "unknown":
            values.append(f"support_status:{facts.support_status}")
        if facts.application_status != "unknown":
            values.append(f"application_status:{facts.application_status}")
        if facts.npa_number:
            values.append(f"NPA:{facts.npa_number}")
        return values[:5]

    def _is_support_context(
        self,
        *,
        source_name: str | None,
        url: str | None,
        level: str | None,
        page_type: str,
    ) -> bool:
        combined = f"{source_name or ''} {url or ''} {level or ''}".lower()
        if "gisp" in combined or "гисп" in combined:
            return True
        if "господдерж" in combined or "меры поддержки" in combined:
            return True
        if level == "support_measures":
            return True
        return page_type in {"measure_card", "selection_announcement", "deadline_update"} and (
            "subsid" in combined or "субсид" in combined
        )

    def _looks_support_listing_page(
        self,
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

    def _looks_generic_regional_section_page(
        self,
        title: str,
        lead_text: str,
        *,
        domain: str,
        url: str,
    ) -> bool:
        if domain not in {"mcx.donland.ru", "msh.krasnodar.ru", "mshsk.ru", "admkrai.krasnodar.ru"}:
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

    def _looks_low_value_regional_section_page(
        self,
        title: str,
        lead_text: str,
        *,
        domain: str,
        url: str,
    ) -> bool:
        if domain not in {"mcx.donland.ru", "msh.krasnodar.ru", "mshsk.ru", "admkrai.krasnodar.ru"}:
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

    def _looks_orders_listing_page(
        self,
        title: str,
        *,
        url: str,
        domain: str,
    ) -> bool:
        return (
            domain in {"msh.krasnodar.ru", "mcx.donland.ru", "admkrai.krasnodar.ru", "mshsk.ru"}
            and title.startswith("приказы ")
            and "/documents/" in url.lower()
        )

    def _is_target_region(
        self,
        *,
        region: str | None,
        title: str,
        source_name: str | None,
        url: str | None,
    ) -> bool:
        if region in {"rostov", "krasnodar", "stavropol"}:
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

    def _is_federal_measure(
        self,
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

    def _looks_irrelevant(self, title: str, raw_text: str) -> bool:
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

    def _looks_anti_corruption_noise(self, title: str, raw_text: str) -> bool:
        if any(fragment in title for fragment in STATIC_BACKGROUND_TITLE_FRAGMENTS):
            return True
        return any(marker in title and marker in raw_text for marker in ANTI_CORRUPTION_NOISE_MARKERS)

    def _extract_domain(self, source_name: str | None, url: str | None) -> str:
        combined = f"{source_name or ''} {url or ''}".lower()
        for candidate in (
            "mcx.donland.ru",
            "admkrai.krasnodar.ru",
            "msh.krasnodar.ru",
            "mshsk.ru",
            "gisp.gov.ru",
            "zol.ru",
        ):
            if candidate in combined:
                return candidate
        parsed = urlparse(url or "")
        return parsed.netloc.lower()

    def _detect_source_specific_page_type(
        self,
        *,
        domain: str,
        title: str,
        lead_text: str,
        url: str,
    ) -> str | None:
        if title in RESULTS_LIKE_TITLES:
            if "реестр" in title or "получател" in title or "список" in title:
                return "registry"
            return "results_protocol"

        if domain in DOMAIN_SECTION_TITLE_HINTS and title in DOMAIN_SECTION_TITLE_HINTS[domain]:
            if YEAR_TITLE_RE.fullmatch(title):
                return "year_archive"
            return "section_page"

        if domain == "gisp.gov.ru":
            if "/measure/" in url.lower():
                return "measure_card"
            if "/nmp/main/" in url.lower() or "навига" in lead_text:
                if title in GENERIC_SUPPORT_TITLES or self._looks_support_listing_page(
                    title,
                    lead_text,
                    source_name=domain,
                    url=url,
                ):
                    return "reference_page"
                return "navigation"

        if domain in {"mcx.donland.ru", "admkrai.krasnodar.ru", "msh.krasnodar.ru", "mshsk.ru"}:
            lower_url = url.lower()
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
                if any(
                    signal in title or signal in lead_text
                    for signal in REQUIRES_ATTENTION_SIGNALS
                ):
                    return None
                if title in CATEGORY_PAGE_TITLES:
                    return "category_page"
                return "section_page"

        return None

    def _looks_reference_title(self, title: str) -> bool:
        if title in {"нормативные документы", "креативные индустрии"}:
            return False
        return bool(REFERENCE_TITLE_WORD_RE.search(title))

    def _clean_summary_text(
        self,
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
        if self._is_gisp_source(source_name, url):
            cleaned = GISP_UI_ID_RE.sub(" ", cleaned)
            cleaned = re.sub(r"\bконкурсное событие\b", " ", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(
                r"\b(общая информация|требования|необходимые документы|скачать условия)\b",
                " ",
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = self._collapse_duplicate_title(cleaned, title)
        cleaned = WHITESPACE_RE.sub(" ", cleaned).strip(" .,-:")
        return cleaned

    def _collapse_duplicate_title(self, text: str, title: str) -> str:
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

    def _finalize_summary(
        self,
        text: str,
        *,
        title: str,
        source_name: str | None,
        url: str | None,
    ) -> str:
        cleaned = WHITESPACE_RE.sub(" ", text).strip(" .,-:")
        if self._is_gisp_source(source_name, url) and title.lower() not in cleaned.lower():
            cleaned = f"{title}. {cleaned}".strip()
        return self._limit_summary(cleaned)

    def _is_gisp_source(self, source_name: str | None, url: str | None) -> bool:
        combined = f"{source_name or ''} {url or ''}".lower()
        return "gisp.gov.ru" in combined or "гисп" in combined

    def _is_generic_regional_section_title(self, title: str) -> bool:
        return title.lower().strip() in REGIONAL_GENERIC_SECTION_TITLES or title.lower().strip().startswith("приказы ")

    def _limit_summary(self, text: str) -> str:
        normalized = WHITESPACE_RE.sub(" ", text).strip()
        if len(normalized) <= 300:
            return normalized
        truncated = normalized[:297].rstrip(" ,.;:-")
        return f"{truncated}..."
