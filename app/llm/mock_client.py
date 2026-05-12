from __future__ import annotations

from collections.abc import Sequence

from app.config import get_source_role
from app.extractors.site_extractors import clean_text_for_analysis
from app.llm.base import BaseLLMClient
from app.llm.facts_extractor import DocumentFacts, extract_document_facts
from app.models import AnalysisResult, SourceRole
from app.rules import extract_domain
from app.rules.business_signal_rules import (
    build_business_signal,
    build_impact,
    detect_action_level,
    detect_importance,
    detect_topic,
)
from app.rules.news_background_guard import guard_news_signal_action_level
from app.rules.news_rules import has_news_signal
from app.rules.noise_rules import (
    SENTENCE_SPLIT_REGEX,
    clean_summary_text,
    finalize_summary,
    limit_summary,
)
from app.rules.page_type_rules import WATCHLIST_ONLY_PAGE_TYPES, detect_page_type
from app.rules.source_role_rules import (
    has_regional_npa_signal,
    has_strategy_signal,
    has_support_document_signal,
)
from app.rules.title_normalization import normalize_document_title


class MockLLMClient(BaseLLMClient):
    def __init__(
        self,
        keywords: Sequence[str],
        *,
        keyword_groups: dict[str, Sequence[str]] | None = None,
    ):
        self.keywords = [keyword.strip() for keyword in keywords if keyword.strip()]
        self.keyword_groups = {
            group_name: [value.strip() for value in values if value.strip()]
            for group_name, values in (keyword_groups or {}).items()
        }

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
        source_role = self._get_source_role(source_name)
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
        normalized_title = normalize_document_title(
            title,
            raw_text=cleaned_text,
            summary=summary,
        )
        impact = self._build_impact(action_level, topic, facts=facts)
        business_signal = self._build_business_signal(
            action_level=action_level,
            page_type=page_type,
            facts=facts,
            title=title,
            raw_text=cleaned_text,
            source_name=source_name,
            url=url,
            level=level,
            region=region,
        )
        guarded_action_level = guard_news_signal_action_level(
            action_level,
            source_role=source_role,
            impact=impact,
            signal=business_signal,
        )
        if guarded_action_level != action_level:
            action_level = guarded_action_level or action_level
            is_relevant = action_level != "irrelevant"
            importance = self._detect_importance(action_level)
            impact = self._build_impact(action_level, topic, facts=facts)
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
            normalized_title=normalized_title,
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
        del region
        title_text = title.lower().strip()
        lead_text = raw_text.lower()[:2000]
        source_role = self._get_source_role(source_name)
        return detect_page_type(
            title,
            raw_text,
            matched_keywords,
            source_role=source_role,
            source_name=source_name,
            url=url,
            domain=extract_domain(source_name, url),
            content_quality=content_quality,
            is_service_page=is_service_page,
            has_strategy_signal=has_strategy_signal(
                title_text,
                lead_text,
                matched_keywords=matched_keywords,
                keyword_groups=self.keyword_groups,
            ),
            has_regional_npa_signal=has_regional_npa_signal(
                title_text,
                lead_text,
                matched_keywords=matched_keywords,
                keyword_groups=self.keyword_groups,
            ),
        )

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
        lead_text = raw_text.lower()[:1500]
        source_role = self._get_source_role(source_name)
        domain = extract_domain(source_name, url)
        return detect_action_level(
            title,
            raw_text,
            matched_keywords,
            page_type=page_type,
            content_quality=content_quality,
            is_service_page=is_service_page,
            source_role=source_role,
            source_name=source_name,
            url=url,
            domain=domain,
            level=level,
            region=region,
            facts=facts,
            has_strategy_signal=has_strategy_signal(
                title_text,
                lead_text,
                matched_keywords=matched_keywords,
                keyword_groups=self.keyword_groups,
            ),
            has_support_document_signal=has_support_document_signal(
                title_text,
                lead_text,
                matched_keywords=matched_keywords,
                keyword_groups=self.keyword_groups,
            ),
            has_regional_npa_signal=has_regional_npa_signal(
                title_text,
                lead_text,
                matched_keywords=matched_keywords,
                keyword_groups=self.keyword_groups,
            ),
            has_news_signal_value=has_news_signal(
                title_text,
                lead_text,
                matched_keywords=matched_keywords,
                keyword_groups=self.keyword_groups,
            ),
        )

    def _detect_importance(self, action_level: str) -> str:
        return detect_importance(action_level)

    def _detect_topic(self, text: str) -> str | None:
        return detect_topic(text)

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
            return limit_summary(
                f"Найден документ '{title}', но текст пока не извлечен полностью."
            )

        cleaned_text = clean_summary_text(
            raw_text,
            title=title,
            source_name=source_name,
            url=url,
        )
        sentences = SENTENCE_SPLIT_REGEX.split(cleaned_text)
        cleaned = [sentence.strip() for sentence in sentences if sentence.strip()]
        if not cleaned:
            return limit_summary(
                f"Найден документ '{title}', требуется дополнительная обработка текста."
            )

        if matched_keywords:
            keyword = matched_keywords[0].lower()
            for sentence in cleaned:
                if keyword in sentence.lower():
                    return finalize_summary(
                        sentence,
                        title=title,
                        source_name=source_name,
                        url=url,
                    )
        return finalize_summary(
            " ".join(cleaned[:2]),
            title=title,
            source_name=source_name,
            url=url,
        )

    def _build_impact(
        self,
        action_level: str,
        topic: str | None,
        *,
        facts: DocumentFacts,
    ) -> str:
        return build_impact(action_level, topic, facts=facts)

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
        raw_text: str,
        source_name: str | None,
        url: str | None,
        level: str | None,
        region: str | None,
    ) -> str:
        return build_business_signal(
            action_level=action_level,
            page_type=page_type,
            facts=facts,
            title=title,
            raw_text=raw_text,
            source_role=self._get_source_role(source_name),
            source_name=source_name,
            url=url,
            level=level,
            region=region,
            domain=extract_domain(source_name, url),
            keyword_groups=self.keyword_groups,
        )

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

    def _get_source_role(self, source_name: str | None) -> SourceRole | None:
        return get_source_role(source_name)
