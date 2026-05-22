from __future__ import annotations

import json
import logging
import random
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime
from functools import lru_cache
from hashlib import sha256
from typing import Any, Mapping
from urllib.parse import urlparse

import requests
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app import config
from app.config import get_source_role
from app.extractors.site_extractors import clean_text_for_analysis
from app.models import AnalysisResult, ActionLevel
from app.llm.prompts import (
    DOCUMENT_CARD_PROMPT_VERSION,
    DOCUMENT_CARD_SYSTEM_PROMPT,
    build_document_card_prompt,
)
from app.rules.deadline_truth import (
    format_iso_date,
    is_deadline_expired,
    is_deadline_today,
    parse_deadline_date,
)
from app.rules.ahstep_applicability import evaluate_support_measure_applicability
from app.text_utils import safe_truncate_text

LOGGER = logging.getLogger(__name__)

ELIGIBLE_ACTION_LEVELS: set[str] = {"requires_attention", "watchlist"}
READ_PATH_MIN_CONFIDENCE = 0.5
CONFIDENCE_TO_SCORE = {"high": 0.9, "medium": 0.75, "low": 0.45}
TARGET_REGIONS = {
    "federal": "РФ",
    "rostov": "Ростовская область",
    "krasnodar": "Краснодарский край",
    "stavropol": "Ставропольский край",
    "moscow_oblast": "Московская область",
}
LLM_PROVIDER_ERROR_SNIPPET_CHARS = 600
LLM_RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
LLM_RETRYABLE_REQUEST_EXCEPTIONS = (
    requests.Timeout,
    requests.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
)
LLM_RESPONSE_FORMAT_DISABLED_VALUES = {"", "none", "off", "false", "disabled", "omit"}
LLM_RESPONSE_FORMAT_JSON_OBJECT_VALUES = {"auto", "json", "json_object", "true", "1"}
LLM_PROXY_SCHEMES = {"http", "https", "socks5", "socks5h"}
URL_CREDENTIALS_RE = re.compile(
    r"([a-z][a-z0-9+.-]*://)[^/\s:@]+(?::[^@\s/]*)?@",
    re.IGNORECASE,
)
SECRET_TEXT_PATTERNS = (
    re.compile(r"(Authorization\s*:\s*Bearer\s+)[^\s,;]+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._~+\-/=]{12,}", re.IGNORECASE),
    re.compile(r"(api[_-]?key[\"'\s:=]+)[^\"'\s,;}]+", re.IGNORECASE),
    re.compile(r"\b(?:sk|sk-proj|sk-or-v1)-[A-Za-z0-9._\-]{8,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}\b"),
)
GENERIC_FALLBACK_TEXT_MARKERS = (
    "документ оставлен на наблюдении",
    "сигнал может повлиять",
    "оценить срочность сигнала",
    "изменения могут повлиять на",
    "оставить в наблюдении до следующего подтверждающего обновления",
)
RAW_SYNTHETIC_TEXT_MARKERS = (
    "title:",
    "shortname:",
    "enddate:",
    "acceptingapplicationsinfo:",
)
PARSER_RESIDUE_TEXT_MARKERS = (
    "статус:",
    "процедура:",
    "начало обсуждения:",
    "конец обсуждения:",
    "id:",
    "код:",
)
WEAK_GR_REASON_VALUES = {
    "открыт прием заявок",
    "прием заявок завершён",
    "проект нпа на публичном обсуждении",
    "обновлены правила поддержки",
    "изменены условия поддержки",
    "изменены условия субсидирования",
}
EFFECTIVE_DATE_MARKERS = (
    "вступает в силу",
    "вступление в силу",
    "вступают в силу",
    "планируемое вступление в силу",
)
WHITESPACE_RE = re.compile(r"\s+")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
SHARE_PHRASE_RE = re.compile(
    r"\b(?:поделиться\s+ссылкой|поделиться\s+в\s+социальных\s+сетях)\b",
    re.IGNORECASE,
)
SOCIAL_TOKEN_ONLY_RE = re.compile(r"^(?:vk|ok|telegram|whatsapp|viber)$", re.IGNORECASE)
INLINE_SOURCE_DATE_RE = re.compile(
    r"\b(?:дата|источник)\s*:\s*(?:https?://\S+|"
    r"\d{1,2}[./]\d{1,2}[./]\d{2,4}|"
    r"\d{1,2}\s+[а-яё]+\s+\d{4})",
    re.IGNORECASE,
)
PORTAL_METADATA_INLINE_PATTERNS = (
    re.compile(r"\bID\s*:\s*[A-Za-z0-9/_\-]+\b", re.IGNORECASE),
    re.compile(r"\bКод\s*:\s*[A-Za-z0-9/_\-]+\b", re.IGNORECASE),
    re.compile(r"\bЭтап\s+портала\s*:\s*[^.]+", re.IGNORECASE),
    re.compile(r"\bФайлы\s+этапа\s*:\s*[^.]+", re.IGNORECASE),
    re.compile(r"\bОтветственн(?:ый|ая)\s*:\s*[^.]+", re.IGNORECASE),
    re.compile(r"\bПроцедура\s*:\s*[^.]+", re.IGNORECASE),
)
LLM_NOISE_LINE_PREFIXES = (
    "id:",
    "код:",
    "этап портала:",
    "файлы этапа:",
    "ответственный:",
    "поделиться ссылкой",
    "поделиться в социальных сетях",
    "код для вставки в блог",
    "выделить фрагмент",
)
MCX_SITE_PREFIX_RE = re.compile(
    r"^(?:минсельхоз\s+россии\.?\s*)?(?:новость\s+апк\s*:?\s*)",
    re.IGNORECASE,
)
NOISY_OCR_TOKEN_RE = re.compile(
    r"(?:\bo_O\b|\bLOLGE\b|©Об|№\{|�|\ufffd|cid:|[□■])",
    re.IGNORECASE,
)
UPPERCASE_LETTER_RE = re.compile(r"[A-ZА-ЯЁ]")
LOWERCASE_LETTER_RE = re.compile(r"[a-zа-яё]")
DATE_ONLY_LINE_RE = re.compile(
    r"^\d{1,2}[./]\d{1,2}[./]\d{2,4}$|^\d{1,2}\s+[а-яё]+\s+\d{4}$",
    re.IGNORECASE,
)
# "П О С Т А Н О В Л Е Н И Е" — single uppercase Cyrillic letters separated by
# spaces, a common PDF-text-extraction artifact (letter-spacing → real spaces).
SPACED_CAPS_RE = re.compile(r"\b[А-ЯЁ](?:\s+[А-ЯЁ]){3,}\b")
# Inline social-network tokens left over from share buttons ("...19 мая 2026 : VK На...").
INLINE_SOCIAL_TOKEN_RE = re.compile(
    r"(?:^|(?<=\s))(?:VK|OK|Telegram|WhatsApp|Viber)(?=\s|$)",
    re.IGNORECASE,
)
# Truncated share phrase ("Поделиться сс...") after upstream summary clipping.
TRUNCATED_SHARE_PHRASE_RE = re.compile(
    r"\bподелиться\s+(?:сс|в)\w*\s*\.{2,}",
    re.IGNORECASE,
)
# Anchor that signals where meaningful Russian content begins. Used to decide
# how much OCR-garbage prefix to strip from the head of a noisy fragment.
# We intentionally do NOT use "г. CityName" as an anchor: the sentence
# splitter downstream treats "г." as a sentence terminator and would drop the
# resulting one-letter "sentence", so anchoring on the city name itself keeps
# the text dedupe-safe.
MEANINGFUL_RUSSIAN_PHRASE_RE = re.compile(
    r"(?:[А-ЯЁ][а-яё]{3,})"
    r"|(?:\b[а-яё]{4,})"
    r"|(?:\b(?:Об|О)\s+[а-яё])"
)
# Bureaucratic-act header keywords. Their presence in the dropped prefix
# confirms the prefix is a noise header (Министерство ... Приказ ...).
_BUREAUCRATIC_HEADER_KEYWORDS = (
    "министерств",
    "правительств",
    "администрац",
    "постановлен",
    "приказ",
    "распоряжен",
    "решен",
    "указ",
)
# English GR-jargon that corporate LLMs sometimes inject into Russian output.
# The verb-aware pattern keeps "проверки/проверке eligibility" grammatical
# after substitution.
_ELIGIBILITY_VERB_RE = re.compile(
    r"\b(проверк[аеиу])\s+eligibility\b",
    re.IGNORECASE,
)
_ENGLISH_GR_TERM_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\beligibility\b", re.IGNORECASE), "соответствие критериям"),
    (re.compile(r"\bcompliance\b", re.IGNORECASE), "соблюдение требований"),
    (re.compile(r"\bdeadline(?:s)?\b", re.IGNORECASE), "сроки"),
)
# Portal/announcement prefix used by news.mcx.gov.ru ("Минсельхоз России. Новость АПК: ...").
# Applied unconditionally on user-facing text so the LLM-copied prefix never
# reaches the GR report.
PORTAL_ANNOUNCEMENT_PREFIX_RE = re.compile(
    r"(?:^|(?<=[\s.;]))(?:минсельхоз\s+россии\.?\s*)?новость\s+апк\s*:?\s*",
    re.IGNORECASE,
)


class DocumentCardFacts(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    document_type: str = "другое"
    region: str | None = None
    authority: str | None = None
    status: str = "неизвестно"
    deadline: str | None = None
    effective_date: str | None = None
    support_type: str | None = None
    target_recipients: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("target_recipients", "recipients"),
    )
    what_changed: str = ""
    why_matters: str = ""
    what_to_check: str = ""
    applicability_note: str = ""
    short_summary: str = ""
    confidence: str = "low"
    source_quotes: list[str] = Field(default_factory=list)

    @field_validator(
        "document_type",
        "status",
        "what_changed",
        "why_matters",
        "what_to_check",
        "applicability_note",
        "short_summary",
        "confidence",
        mode="before",
    )
    @classmethod
    def _normalize_text_field(cls, value: object) -> str:
        if value is None:
            return ""
        return " ".join(str(value).split()).strip()

    @field_validator("region", "authority", "deadline", "effective_date", "support_type", mode="before")
    @classmethod
    def _normalize_nullable_text(cls, value: object) -> str | None:
        if value is None:
            return None
        text = " ".join(str(value).split()).strip()
        return text or None

    @field_validator("target_recipients", "source_quotes", mode="before")
    @classmethod
    def _normalize_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            candidates = [value]
        else:
            candidates = list(value) if isinstance(value, list | tuple | set) else []
        normalized: list[str] = []
        for item in candidates:
            text = " ".join(str(item).split()).strip()
            if text:
                normalized.append(text)
        return normalized

    @field_validator("document_type")
    @classmethod
    def _validate_document_type(cls, value: str) -> str:
        allowed = {"отбор", "НПА", "проект НПА", "новость", "мера поддержки", "другое"}
        return value if value in allowed else "другое"

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        allowed = {
            "прием открыт",
            "прием завершен",
            "проект обсуждается",
            "принято",
            "неизвестно",
        }
        return value if value in allowed else "неизвестно"

    @field_validator("support_type")
    @classmethod
    def _validate_support_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        allowed = {
            "субсидия",
            "грант",
            "льготный кредит",
            "компенсация",
            "экспорт",
            "другое",
        }
        return value if value in allowed else None

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, value: str) -> str:
        return value if value in {"high", "medium", "low"} else "low"


class EnrichmentResult(BaseModel):
    prompt_version: str = DOCUMENT_CARD_PROMPT_VERSION
    status: str = "success"
    facts: DocumentCardFacts | None = None
    facts_json: dict[str, Any] | None = None
    source_hash: str | None = None
    executive_summary: str | None = None
    business_impact: str | None = None
    recommended_action: str | None = None
    deadline_hint: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    error: str | None = None

    @classmethod
    def from_facts(
        cls,
        facts: DocumentCardFacts,
        *,
        prompt_version: str = DOCUMENT_CARD_PROMPT_VERSION,
        source_hash: str | None = None,
    ) -> "EnrichmentResult":
        payload = facts.model_dump(mode="json")
        return cls(
            prompt_version=prompt_version,
            status="success",
            facts=facts,
            facts_json=payload,
            source_hash=source_hash,
            executive_summary=facts.short_summary,
            business_impact=facts.why_matters,
            recommended_action=facts.what_to_check,
            deadline_hint=_build_deadline_hint_from_facts(facts),
            confidence=CONFIDENCE_TO_SCORE.get(facts.confidence, 0.45),
        )

    @classmethod
    def fallback_from_facts(
        cls,
        facts: DocumentCardFacts,
        *,
        error: str,
        prompt_version: str = DOCUMENT_CARD_PROMPT_VERSION,
        source_hash: str | None = None,
    ) -> "EnrichmentResult":
        result = cls.from_facts(
            facts,
            prompt_version=prompt_version,
            source_hash=source_hash,
        )
        result.status = "fallback"
        result.error = error
        return result

    @classmethod
    def failed(
        cls,
        error: str,
        *,
        prompt_version: str = DOCUMENT_CARD_PROMPT_VERSION,
        source_hash: str | None = None,
    ) -> "EnrichmentResult":
        return cls(
            prompt_version=prompt_version,
            status="failed",
            source_hash=source_hash,
            confidence=0.0,
            error=error,
        )

    def facts_payload(self) -> dict[str, Any] | None:
        if self.facts is not None:
            return self.facts.model_dump(mode="json")
        if isinstance(self.facts_json, dict):
            return self.facts_json
        return None


class BaseEnrichmentProvider(ABC):
    @abstractmethod
    def enrich_document(
        self,
        *,
        title: str,
        raw_text: str,
        analysis: AnalysisResult,
        source_name: str | None = None,
        url: str | None = None,
        level: str | None = None,
        region: str | None = None,
        published_at: datetime | None = None,
        document_type: str | None = None,
        max_document_chars: int | None = None,
    ) -> EnrichmentResult:
        """Return optional user-facing enrichment without changing classification."""


class MockEnrichmentProvider(BaseEnrichmentProvider):
    def enrich_document(
        self,
        *,
        title: str,
        raw_text: str,
        analysis: AnalysisResult,
        source_name: str | None = None,
        url: str | None = None,
        level: str | None = None,
        region: str | None = None,
        published_at: datetime | None = None,
        document_type: str | None = None,
        max_document_chars: int | None = None,
    ) -> EnrichmentResult:
        del level, published_at, document_type
        source_role = _normalized_source_role(source_name)
        prepared = build_document_card_input(
            title=title,
            raw_text=raw_text,
            analysis=analysis,
            source_name=source_name,
            url=url,
            region=region,
            max_document_chars=max_document_chars or config.LLM_MAX_DOCUMENT_CHARS,
        )
        facts = DocumentCardFacts(
            document_type=self._detect_document_type(analysis, raw_text=raw_text, url=url),
            region=self._format_region(region),
            authority=source_name or None,
            status=self._detect_status(analysis, url=url),
            deadline=self._build_deadline_iso(analysis),
            effective_date=None,
            support_type=self._detect_support_type(title, raw_text),
            target_recipients=self._detect_target_recipients(title, raw_text),
            what_changed=self._clip_text(
                analysis.summary or "Содержит обновление, требующее проверки по исходному документу.",
                max_chars=320,
            ),
            why_matters=self._clip_text(
                self._build_business_impact(
                    analysis,
                    source_role=source_role,
                    region=region,
                    source_name=source_name,
                ),
                max_chars=300,
            ),
            what_to_check=self._clip_text(
                self._build_recommended_action(analysis, source_role=source_role),
                max_chars=260,
            ),
            applicability_note=self._build_applicability_note(region=region),
            short_summary=self._clip_text(
                self._build_executive_summary(analysis, source_role=source_role),
                max_chars=260,
            ),
            confidence="high",
            source_quotes=_extract_source_quotes(
                title=title,
                raw_text=prepared["raw_text_excerpt"],
                analysis=analysis,
            ),
        )
        result = EnrichmentResult.from_facts(
            facts,
            source_hash=compute_document_card_source_hash(prepared),
        )
        result.deadline_hint = self._build_deadline_hint(analysis)
        return result

    def _detect_document_type(
        self,
        analysis: AnalysisResult,
        *,
        raw_text: str,
        url: str | None,
    ) -> str:
        text = f"{analysis.page_type or ''} {analysis.summary or ''} {raw_text[:500]} {url or ''}".lower()
        if "regulation.gov" in text or "публичн" in text and "обсужден" in text:
            return "проект НПА"
        if analysis.page_type == "selection_announcement" or "отбор" in text:
            return "отбор"
        if analysis.page_type == "new_rule" or "постановлен" in text or "приказ" in text:
            return "НПА"
        if analysis.page_type == "measure_card":
            return "мера поддержки"
        if analysis.page_type == "news_background":
            return "новость"
        return "другое"

    def _detect_status(self, analysis: AnalysisResult, *, url: str | None) -> str:
        if analysis.application_status == "open":
            return "прием открыт"
        if analysis.application_status == "closed":
            return "прием завершен"
        if "regulation.gov" in (url or "").lower() or analysis.page_type == "new_rule":
            return "проект обсуждается" if analysis.deadline_text else "принято"
        return "неизвестно"

    def _build_deadline_iso(self, analysis: AnalysisResult) -> str | None:
        parsed = parse_deadline_date(analysis.deadline_text)
        return parsed.isoformat() if parsed else None

    def _detect_support_type(self, title: str, raw_text: str) -> str | None:
        text = f"{title} {raw_text[:2000]}".lower()
        if "льготн" in text and "кредит" in text:
            return "льготный кредит"
        if "грант" in text:
            return "грант"
        if "компенсац" in text or "возмещен" in text or "возмещение" in text:
            return "компенсация"
        if "экспорт" in text:
            return "экспорт"
        if "субсид" in text:
            return "субсидия"
        if any(marker in text for marker in ("поддержк", "отбор", "мера")):
            return "другое"
        return None

    def _detect_target_recipients(self, title: str, raw_text: str) -> list[str]:
        text = f"{title} {raw_text[:3000]}".lower()
        recipients: list[str] = []
        if any(marker in text for marker in ("юридическ", "юрлиц")):
            recipients.append("юрлица")
        if re.search(r"\bип\b|индивидуальн\w+\s+предпринимател", text):
            recipients.append("ИП")
        if "сельхозтоваропроизвод" in text or "сельскохозяйственн" in text:
            recipients.append("сельхозтоваропроизводители")
        return recipients

    def _build_applicability_note(self, *, region: str | None) -> str:
        formatted_region = self._format_region(region)
        if formatted_region and formatted_region != "РФ":
            return (
                f"Автоматически считать меру применимой к AHSTEP нельзя; нужно отдельно "
                f"проверить критерии получателя и наличие операционного контура в регионе: {formatted_region}."
            )
        return (
            "Применимость к AHSTEP требует отдельной проверки критериев получателя, "
            "отраслевых условий и формата участия."
        )

    def _build_legacy_enrichment(
        self,
        *,
        analysis: AnalysisResult,
        source_role: str,
        region: str | None,
        source_name: str | None,
    ) -> EnrichmentResult:
        executive_summary = self._clip_text(
            self._build_executive_summary(analysis, source_role=source_role),
            max_chars=220,
        )
        business_impact = self._clip_text(
            self._build_business_impact(
                analysis,
                source_role=source_role,
                region=region,
                source_name=source_name,
            ),
            max_chars=280,
        )
        recommended_action = self._clip_text(
            self._build_recommended_action(analysis, source_role=source_role),
            max_chars=220,
        )
        return EnrichmentResult(
            executive_summary=executive_summary,
            business_impact=business_impact,
            recommended_action=recommended_action,
            deadline_hint=self._build_deadline_hint(analysis),
            confidence=0.85,
        )

    def _build_executive_summary(
        self,
        analysis: AnalysisResult,
        *,
        source_role: str,
    ) -> str:
        if _is_regulation_discussion_context(analysis, url=None):
            return (
                "Проект НПА вынесен на публичное обсуждение. "
                "Нужно понять, меняет ли он порядок поддержки, требования к получателям или связанные процедуры."
            )
        if source_role == "regional_npa" and analysis.action_level == "requires_attention":
            return (
                "Документ меняет действующий порядок поддержки в регионе. "
                "По полному тексту или приложению нужно уточнить, какие условия, получатели и сроки изменены."
            )
        if source_role in {"active_support_measures", "support_documents"} or analysis.page_type in {
            "measure_card",
            "selection_announcement",
            "deadline_update",
        }:
            if analysis.application_status == "closed":
                return (
                    "Приём по мере завершён, но документ полезен как ориентир по условиям отбора. "
                    "По нему стоит проверить цикл меры, требования к участникам и вероятность повторного окна."
                )
            return (
                "По документу видно окно поддержки или отбора. "
                "Нужно уточнить условия участия, круг получателей и рабочие сроки, чтобы оценить применимость к AHSTEP."
            )
        return (
            "Документ содержит отраслевой или регуляторный сигнал для GR-мониторинга. "
            "Нужно уточнить, есть ли у него практические последствия для AHSTEP."
        )

    def _build_business_impact(
        self,
        analysis: AnalysisResult,
        *,
        source_role: str,
        region: str | None,
        source_name: str | None,
    ) -> str:
        if _is_regulation_discussion_context(analysis, url=None):
            base = (
                "По проекту можно заранее оценить изменение регулирования, которое еще не вступило в силу. "
                "Это дает время проверить влияние на действующие меры поддержки и при необходимости подготовить GR-позицию до завершения обсуждения."
            )
        elif source_role == "regional_npa":
            base = (
                "Документ может изменить правила доступа к региональной поддержке, состав получателей, "
                "условия участия или набор обязательных документов. Для AHSTEP важно понять, меняется ли практический порядок работы по мере."
            )
        elif source_role in {"active_support_measures", "support_documents"} or analysis.application_status in {
            "open",
            "regular",
        }:
            base = (
                "Документ помогает понять, применима ли мера к контуру AHSTEP, какие есть критерии участия, "
                "и нужен ли срочный организационный шаг по подаче, проверке соответствия критериям "
                "или сбору документов."
            )
        else:
            base = (
                "Сигнал важен для GR-мониторинга условий господдержки и смежного регулирования. "
                "Он может быть контекстом для планирования позиции, даже если немедленного действия пока нет."
            )

        details: list[str] = []
        region_hint = self._format_region(region)
        if region_hint:
            details.append(f"Регион: {region_hint}.")
        if source_name:
            details.append(f"Источник: {source_name}.")
        suffix = f" {' '.join(details)}" if details else ""
        return f"{base}{suffix}".strip()

    def _build_recommended_action(self, analysis: AnalysisResult, *, source_role: str) -> str:
        if _is_regulation_discussion_context(analysis, url=None):
            parsed_deadline = parse_deadline_date(analysis.deadline_text)
            if parsed_deadline is not None:
                return (
                    "Сверить предмет проекта с действующими мерами и процедурами AHSTEP; "
                    "проверить, какие нормы меняются; при наличии замечаний подготовить позицию "
                    f"до {format_iso_date(parsed_deadline)}."
                )
            return (
                "Сверить предмет проекта с действующими мерами и процедурами AHSTEP; "
                "проверить, какие нормы меняются; решить, нужна ли GR-позиция."
            )
        if source_role == "regional_npa":
            return (
                "Проверить, какие пункты порядка изменены; уточнить, есть ли новые критерии получателей, "
                "документы, сроки или приложения; определить внутреннего владельца проверки."
            )
        if analysis.action_level == "requires_attention":
            if analysis.application_status == "open" or analysis.deadline_text:
                return (
                    "Проверить критерии получателя; окно подачи или текущий статус отбора; "
                    "перечень документов; бюджетные условия; ответственного за следующий шаг внутри AHSTEP."
                )
            return (
                "Проверить влияние документа на текущие GR-процессы, определить затронутые подразделения "
                "и назначить следующий шаг."
            )
        if analysis.page_type in {"selection_announcement", "deadline_update"}:
            return "Проверить условия участия, актуальность окна подачи и перечень требуемых документов."
        return "Оставить документ в наблюдении и вернуться при следующем подтверждающем обновлении."

    def _build_deadline_hint(self, analysis: AnalysisResult) -> str | None:
        deadline_text = getattr(analysis, "deadline_text", None)
        if deadline_text:
            text = str(deadline_text).strip()
            parsed = parse_deadline_date(text)
            # Truth-format expired hints so non-truth-aware surfaces (Telegram
            # digest, search) never render an elapsed deadline as if it were
            # still alive.
            if parsed is not None and is_deadline_expired(text):
                return f"Срок истёк: {format_iso_date(parsed)}"
            if getattr(analysis, "application_status", None) == "closed":
                return None
            return text
        key_dates = getattr(analysis, "key_dates", []) or []
        if key_dates:
            first_date = str(key_dates[0]).strip()
            if first_date:
                return first_date
        return None

    def _format_region(self, region: str | None) -> str:
        if not region:
            return ""
        normalized = region.replace("_", " ").strip()
        if not normalized:
            return ""
        return TARGET_REGIONS.get(normalized.lower(), normalized)

    def _clip_text(self, value: str, *, max_chars: int) -> str:
        return safe_truncate_text(value, max_chars)


class OpenAICompatibleEnrichmentProvider(BaseEnrichmentProvider):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int,
        proxy_url: str | None = None,
        response_format: str | None = "auto",
        max_retries: int | None = None,
        retry_backoff_seconds: int | None = None,
        retry_max_backoff_seconds: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.proxy_url = (proxy_url or "").strip()
        self.response_format = (response_format or "auto").strip().lower()
        self.max_retries = max(
            0,
            int(max_retries if max_retries is not None else config.LLM_MAX_RETRIES),
        )
        self.retry_backoff_seconds = max(
            0,
            int(
                retry_backoff_seconds
                if retry_backoff_seconds is not None
                else config.LLM_RETRY_BACKOFF_SECONDS
            ),
        )
        self.retry_max_backoff_seconds = max(
            1,
            int(
                retry_max_backoff_seconds
                if retry_max_backoff_seconds is not None
                else config.LLM_RETRY_MAX_BACKOFF_SECONDS
            ),
        )

    def enrich_document(
        self,
        *,
        title: str,
        raw_text: str,
        analysis: AnalysisResult,
        source_name: str | None = None,
        url: str | None = None,
        level: str | None = None,
        region: str | None = None,
        published_at: datetime | None = None,
        document_type: str | None = None,
        max_document_chars: int | None = None,
    ) -> EnrichmentResult:
        prepared = build_document_card_input(
            title=title,
            raw_text=raw_text,
            analysis=analysis,
            source_name=source_name,
            url=url,
            level=level,
            region=region,
            published_at=published_at,
            document_type=document_type,
            max_document_chars=max_document_chars or config.LLM_MAX_DOCUMENT_CHARS,
        )
        source_hash = compute_document_card_source_hash(prepared)
        payload = self._build_payload(
            document_payload=prepared,
        )
        response_payload = self._post_json(payload)
        content = self._extract_content(response_payload)
        try:
            parsed = self._parse_json_content(content)
        except json.JSONDecodeError as exc:
            repair_payload = self._build_repair_payload(invalid_content=content)
            response_payload = self._post_json(repair_payload)
            content = self._extract_content(response_payload)
            try:
                parsed = self._parse_json_content(content)
            except json.JSONDecodeError as repair_exc:
                raise ValueError(
                    f"invalid JSON from LLM provider: {repair_exc.msg}"
                ) from exc
        facts = DocumentCardFacts.model_validate(parsed)
        return EnrichmentResult.from_facts(facts, source_hash=source_hash)

    def _build_payload(
        self,
        *,
        document_payload: Mapping[str, Any],
    ) -> dict[str, object]:
        user_prompt = build_document_card_prompt(
            json.dumps(document_payload, ensure_ascii=False, sort_keys=True)
        )
        payload: dict[str, object] = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": DOCUMENT_CARD_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
        }
        if self._uses_json_object_response_format():
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _build_repair_payload(self, *, invalid_content: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": DOCUMENT_CARD_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": (
                        "Исправь предыдущий ответ в валидный JSON-объект строго по схеме. "
                        "Не добавляй Markdown, комментарии и не придумывай недостающие факты.\n\n"
                        f"Предыдущий ответ:\n{invalid_content}"
                    ),
                },
            ],
        }
        if self._uses_json_object_response_format():
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _uses_json_object_response_format(self) -> bool:
        mode = self.response_format
        if mode in LLM_RESPONSE_FORMAT_DISABLED_VALUES:
            return False
        if mode == "auto":
            return not self._is_google_openai_compatible_endpoint()
        return mode in LLM_RESPONSE_FORMAT_JSON_OBJECT_VALUES

    def _is_google_openai_compatible_endpoint(self) -> bool:
        parsed = urlparse(self.base_url)
        host = parsed.netloc.lower()
        return host == "generativelanguage.googleapis.com" or host.endswith(
            ".generativelanguage.googleapis.com"
        )

    def _parse_json_content(self, content: str) -> dict[str, Any]:
        normalized = content.strip()
        candidates = [normalized]
        stripped_fence = _strip_markdown_fence(normalized)
        if stripped_fence != normalized:
            candidates.append(stripped_fence)
        extracted = _extract_json_object_text(normalized)
        if extracted and extracted not in candidates:
            candidates.append(extracted)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        raise json.JSONDecodeError("JSON object expected", normalized, 0)

    def _post_json(self, payload: dict[str, object]) -> dict[str, Any]:
        if not self.base_url:
            raise ValueError("LLM_BASE_URL is not configured")
        if not self.model:
            raise ValueError("LLM_MODEL is not configured")
        endpoint = f"{self.base_url}/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        proxies = _build_llm_proxy_config(self.proxy_url)
        max_attempts = self.max_retries + 1
        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.post(
                    endpoint,
                    data=body,
                    headers=headers,
                    timeout=self.timeout_seconds,
                    proxies=proxies,
                )
                response.raise_for_status()
                return response.json()
            except requests.HTTPError as exc:
                if self._should_retry_http_error(
                    exc,
                    attempt=attempt,
                    max_attempts=max_attempts,
                ):
                    self._log_retry_attempt(
                        exc,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        payload=payload,
                    )
                    self._sleep_before_retry(attempt)
                    continue
                raise self._build_http_runtime_error(exc, payload) from exc
            except LLM_RETRYABLE_REQUEST_EXCEPTIONS as exc:
                if attempt < max_attempts:
                    self._log_retry_attempt(
                        exc,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        payload=payload,
                    )
                    self._sleep_before_retry(attempt)
                    continue
                raise self._build_request_runtime_error(exc) from exc
            except requests.RequestException as exc:
                raise self._build_request_runtime_error(exc) from exc
        raise RuntimeError("LLM provider request failed: retry attempts exhausted")

    def _should_retry_http_error(
        self,
        exc: requests.HTTPError,
        *,
        attempt: int,
        max_attempts: int,
    ) -> bool:
        response = exc.response
        return (
            attempt < max_attempts
            and response is not None
            and response.status_code in LLM_RETRYABLE_HTTP_STATUS_CODES
        )

    def _retry_delay_seconds(self, attempt: int) -> float:
        base_delay = min(
            self.retry_max_backoff_seconds,
            self.retry_backoff_seconds * (2 ** max(0, attempt - 1)),
        )
        if base_delay <= 0:
            return 0.0
        jitter = random.uniform(0, min(1.0, base_delay * 0.25))
        return min(self.retry_max_backoff_seconds, base_delay + jitter)

    def _sleep_before_retry(self, attempt: int) -> None:
        delay = self._retry_delay_seconds(attempt)
        if delay > 0:
            time.sleep(delay)

    def _log_retry_attempt(
        self,
        exc: BaseException,
        *,
        attempt: int,
        max_attempts: int,
        payload: Mapping[str, object],
    ) -> None:
        if isinstance(exc, requests.HTTPError):
            detail = self._format_http_error_detail(exc, payload)
        else:
            detail = _safe_llm_provider_error_snippet(
                str(exc),
                secrets=(self.api_key, self.proxy_url),
            )
        LOGGER.warning(
            "LLM provider transient failure; retrying attempt %s/%s: %s",
            attempt + 1,
            max_attempts,
            detail,
        )

    def _format_http_error_detail(
        self,
        exc: requests.HTTPError,
        payload: Mapping[str, object],
    ) -> str:
        response = exc.response
        status = "HTTP provider error"
        body_text = ""
        if response is not None:
            status = f"HTTP {response.status_code} {response.reason}".strip()
            body_text = response.text or ""
        body_snippet = _safe_llm_provider_error_snippet(
            body_text,
            secrets=(self.api_key, self.proxy_url),
        )
        detail = status
        if body_snippet:
            detail = f"{detail}; body={body_snippet!r}"
        return f"{detail}; payload_keys={','.join(payload.keys())}"

    def _build_http_runtime_error(
        self,
        exc: requests.HTTPError,
        payload: Mapping[str, object],
    ) -> RuntimeError:
        detail = self._format_http_error_detail(exc, payload)
        return RuntimeError(f"LLM provider request failed: {detail}")

    def _build_request_runtime_error(self, exc: requests.RequestException) -> RuntimeError:
        detail = _safe_llm_provider_error_snippet(
            str(exc),
            secrets=(self.api_key, self.proxy_url),
        )
        return RuntimeError(f"LLM provider request failed: {detail}")

    def _extract_content(self, payload: dict[str, Any]) -> str:
        try:
            choices = payload["choices"]
            first_choice = choices[0]
            message = first_choice["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("unexpected LLM provider response shape") from exc
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty LLM provider content")
        return content.strip()


class DocumentEnricher:
    def __init__(
        self,
        *,
        enabled: bool,
        provider: BaseEnrichmentProvider | None = None,
        provider_error: str | None = None,
        provider_name: str = "",
        model_name: str = "",
    ) -> None:
        self.enabled = enabled
        self.provider = provider
        self.provider_error = provider_error
        self.provider_name = provider_name
        self.model_name = model_name

    def maybe_enrich_document(
        self,
        *,
        title: str,
        raw_text: str,
        analysis: AnalysisResult,
        source_name: str | None = None,
        url: str | None = None,
        level: str | None = None,
        region: str | None = None,
        published_at: datetime | None = None,
        document_type: str | None = None,
        max_document_chars: int | None = None,
    ) -> EnrichmentResult | None:
        if not self.enabled:
            return None
        if not is_enrichment_eligible(analysis.action_level):
            return None
        if self.provider is None:
            return self._build_fallback_result(
                error_text=self.provider_error or "LLM enrichment provider is unavailable",
                title=title,
                raw_text=raw_text,
                analysis=analysis,
                source_name=source_name,
                url=url,
                level=level,
                region=region,
                published_at=published_at,
                document_type=document_type,
                max_document_chars=max_document_chars,
            )
        try:
            result = self.provider.enrich_document(
                title=title,
                raw_text=raw_text,
                analysis=analysis,
                source_name=source_name,
                url=url,
                level=level,
                region=region,
                published_at=published_at,
                document_type=document_type,
                max_document_chars=max_document_chars,
            )
            return self._finalize_result(
                result=result,
                title=title,
                raw_text=raw_text,
                analysis=analysis,
                source_name=source_name,
                url=url,
                level=level,
                region=region,
                published_at=published_at,
                document_type=document_type,
                max_document_chars=max_document_chars,
            )
        except Exception as exc:
            return self._build_fallback_result(
                error_text=f"{type(exc).__name__}: {exc}",
                title=title,
                raw_text=raw_text,
                analysis=analysis,
                source_name=source_name,
                url=url,
                level=level,
                region=region,
                published_at=published_at,
                document_type=document_type,
                max_document_chars=max_document_chars,
            )

    def _finalize_result(
        self,
        *,
        result: EnrichmentResult,
        title: str,
        raw_text: str,
        analysis: AnalysisResult,
        source_name: str | None,
        url: str | None,
        level: str | None,
        region: str | None,
        published_at: datetime | None,
        document_type: str | None,
        max_document_chars: int | None,
    ) -> EnrichmentResult:
        prepared = build_document_card_input(
            title=title,
            raw_text=raw_text,
            analysis=analysis,
            source_name=source_name,
            url=url,
            level=level,
            region=region,
            published_at=published_at,
            document_type=document_type,
            max_document_chars=max_document_chars or config.LLM_MAX_DOCUMENT_CHARS,
        )
        source_hash = compute_document_card_source_hash(prepared)
        facts_payload = result.facts_payload()
        if not facts_payload:
            return self._build_fallback_result(
                error_text="LLM enrichment returned empty facts payload",
                title=title,
                raw_text=raw_text,
                analysis=analysis,
                source_name=source_name,
                url=url,
                level=level,
                region=region,
                published_at=published_at,
                document_type=document_type,
                max_document_chars=max_document_chars,
            )
        facts = _harden_facts(
            DocumentCardFacts.model_validate(facts_payload),
            analysis=analysis,
            raw_text=raw_text,
            source_name=source_name,
            region=region,
            url=url,
        )
        weak_reason = _detect_weak_facts_reason(facts, analysis=analysis, url=url)
        if weak_reason:
            return self._build_fallback_result(
                error_text=f"LLM enrichment fallback: {weak_reason}",
                title=title,
                raw_text=raw_text,
                analysis=analysis,
                source_name=source_name,
                url=url,
                level=level,
                region=region,
                published_at=published_at,
                document_type=document_type,
                max_document_chars=max_document_chars,
            )
        finalized = EnrichmentResult.from_facts(
            facts,
            prompt_version=result.prompt_version,
            source_hash=result.source_hash or source_hash,
        )
        if result.status == "fallback":
            finalized.status = "fallback"
            finalized.error = result.error
        return finalized

    def _build_fallback_result(
        self,
        *,
        error_text: str,
        title: str,
        raw_text: str,
        analysis: AnalysisResult,
        source_name: str | None,
        url: str | None,
        level: str | None,
        region: str | None,
        published_at: datetime | None,
        document_type: str | None,
        max_document_chars: int | None,
    ) -> EnrichmentResult:
        try:
            fallback = MockEnrichmentProvider().enrich_document(
                title=title,
                raw_text=raw_text,
                analysis=analysis,
                source_name=source_name,
                url=url,
                level=level,
                region=region,
                published_at=published_at,
                document_type=document_type,
                max_document_chars=max_document_chars,
            )
            fallback_facts = _harden_facts(
                fallback.facts or DocumentCardFacts(),
                analysis=analysis,
                raw_text=raw_text,
                source_name=source_name,
                region=region,
                url=url,
            )
            return EnrichmentResult.fallback_from_facts(
                fallback_facts,
                error=error_text,
                prompt_version=fallback.prompt_version,
                source_hash=fallback.source_hash,
            )
        except Exception:
            prepared = build_document_card_input(
                title=title,
                raw_text=raw_text,
                analysis=analysis,
                source_name=source_name,
                url=url,
                level=level,
                region=region,
                published_at=published_at,
                document_type=document_type,
                max_document_chars=max_document_chars or config.LLM_MAX_DOCUMENT_CHARS,
            )
            return EnrichmentResult.failed(
                error_text,
                source_hash=compute_document_card_source_hash(prepared),
            )


def is_enrichment_eligible(action_level: ActionLevel | str | None) -> bool:
    return str(action_level or "").strip() in ELIGIBLE_ACTION_LEVELS


def build_document_enricher() -> DocumentEnricher:
    enabled = bool(
        getattr(config, "LLM_DOCUMENT_ENRICHMENT_ENABLED", False)
        or getattr(config, "LLM_ENRICHMENT_ENABLED", False)
    )
    if not enabled:
        return DocumentEnricher(enabled=False)
    provider_name = (config.LLM_PROVIDER or "mock").strip().lower()
    if provider_name == "mock":
        return DocumentEnricher(
            enabled=True,
            provider=MockEnrichmentProvider(),
            provider_name="mock",
            model_name=config.LLM_MODEL or "mock-enrichment",
        )
    if provider_name in {"openai", "openai-compatible", "openai_compatible", "lmstudio", "ollama"}:
        return DocumentEnricher(
            enabled=True,
            provider=OpenAICompatibleEnrichmentProvider(
                base_url=config.LLM_BASE_URL,
                api_key=config.LLM_API_KEY,
                model=config.LLM_MODEL,
                timeout_seconds=config.LLM_TIMEOUT_SECONDS,
                proxy_url=getattr(config, "LLM_PROXY_URL", ""),
                response_format=getattr(config, "LLM_RESPONSE_FORMAT", "auto"),
                max_retries=getattr(config, "LLM_MAX_RETRIES", 2),
                retry_backoff_seconds=getattr(config, "LLM_RETRY_BACKOFF_SECONDS", 2),
                retry_max_backoff_seconds=getattr(
                    config,
                    "LLM_RETRY_MAX_BACKOFF_SECONDS",
                    10,
                ),
            ),
            provider_name=provider_name,
            model_name=config.LLM_MODEL or "",
        )
    return DocumentEnricher(
        enabled=True,
        provider_error=f"unsupported LLM provider: {provider_name}",
        provider_name=provider_name,
        model_name=config.LLM_MODEL or "",
    )


@lru_cache(maxsize=1)
def get_document_enricher() -> DocumentEnricher:
    return build_document_enricher()


def get_display_enrichment(
    enrichment_row: Mapping[str, Any] | None,
    *,
    min_confidence: float = READ_PATH_MIN_CONFIDENCE,
) -> dict[str, str] | None:
    if not enrichment_row:
        return None
    status = str(enrichment_row.get("status") or "success").strip().lower()
    if status and status not in {"success", "fallback"}:
        return None
    error = str(enrichment_row.get("error") or "").strip()
    if error and status != "fallback":
        return None
    facts = _parse_facts_from_row(enrichment_row)
    row_prompt_version = str(enrichment_row.get("prompt_version") or "").strip()
    if facts and row_prompt_version and row_prompt_version != DOCUMENT_CARD_PROMPT_VERSION:
        return None
    confidence = enrichment_row.get("confidence")
    confidence_score = (
        CONFIDENCE_TO_SCORE.get(str(facts.get("confidence") or "").strip().lower(), 0.0)
        if facts
        else None
    )
    if isinstance(confidence, (int, float)):
        confidence_score = float(confidence)
    if confidence_score is not None and confidence_score < min_confidence:
        return None
    recipients = ""
    if facts:
        raw_recipients = facts.get("target_recipients", [])
        if isinstance(raw_recipients, str):
            recipient_values = [raw_recipients]
        elif isinstance(raw_recipients, (list, tuple, set)):
            recipient_values = list(raw_recipients)
        else:
            recipient_values = []
        recipients = ", ".join(
            _sanitize_enrichment_text(value) for value in recipient_values if value
        ).strip(" ,")
    fields = {
        "executive_summary": _sanitize_user_facing_enrichment_text(
            facts.get("short_summary") if facts else enrichment_row.get("executive_summary")
        ),
        "factual_summary": _sanitize_user_facing_enrichment_text(
            facts.get("what_changed") if facts else None
        ),
        "business_impact": _sanitize_user_facing_enrichment_text(
            facts.get("why_matters") if facts else enrichment_row.get("business_impact")
        ),
        "recommended_action": _sanitize_user_facing_enrichment_text(
            facts.get("what_to_check") if facts else enrichment_row.get("recommended_action")
        ),
        "deadline_hint": _sanitize_enrichment_text(
            _display_deadline_hint_from_facts(facts) if facts else enrichment_row.get("deadline_hint")
        ),
    }
    if facts:
        fields["document_type"] = _sanitize_enrichment_text(facts.get("document_type"))
        fields["region"] = _sanitize_enrichment_text(facts.get("region"))
        fields["authority"] = _sanitize_enrichment_text(facts.get("authority"))
        fields["status"] = _sanitize_enrichment_text(facts.get("status"))
        fields["deadline"] = _sanitize_enrichment_text(facts.get("deadline"))
        fields["effective_date"] = _sanitize_enrichment_text(facts.get("effective_date"))
        fields["support_type"] = _sanitize_enrichment_text(facts.get("support_type"))
        fields["target_recipients"] = recipients
        fields["applicability_note"] = _sanitize_enrichment_text(facts.get("applicability_note"))
        fields["_document_card"] = "1"
    if not any(fields.values()):
        return None
    return fields


def _build_deadline_hint_from_facts(facts: DocumentCardFacts) -> str | None:
    if not facts.deadline:
        return None
    parsed = parse_deadline_date(facts.deadline)
    if parsed is None:
        return None
    formatted = format_iso_date(parsed)
    if facts.status == "проект обсуждается" or facts.document_type == "проект НПА":
        return f"Конец обсуждения: {formatted}"
    if is_deadline_expired(facts.deadline):
        return f"Срок истёк: {formatted}"
    if is_deadline_today(facts.deadline):
        return f"Срок: сегодня ({formatted})"
    return f"Срок: до {formatted}"


def _display_deadline_hint_from_facts(facts: Mapping[str, Any]) -> str | None:
    try:
        validated = DocumentCardFacts.model_validate(dict(facts))
    except Exception:
        return str(facts.get("deadline") or "").strip() or None
    return _build_deadline_hint_from_facts(validated)


def _parse_facts_from_row(enrichment_row: Mapping[str, Any]) -> dict[str, Any]:
    raw_facts = enrichment_row.get("facts_json")
    if isinstance(raw_facts, dict):
        return raw_facts
    if isinstance(raw_facts, str) and raw_facts.strip():
        try:
            parsed = json.loads(raw_facts)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _sanitize_enrichment_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\s*AI-(?:сводка|оценка влияния|рекомендация)\s*:\s*", "", text, flags=re.IGNORECASE)
    return " ".join(text.split()).strip()


def _sanitize_user_facing_enrichment_text(value: Any) -> str:
    return _clean_user_facing_text(value)


def _strip_markdown_fence(value: str) -> str:
    normalized = value.strip()
    if not normalized.startswith("```"):
        return normalized
    normalized = re.sub(r"^```(?:json)?\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s*```$", "", normalized)
    return normalized.strip()


def _extract_json_object_text(value: str) -> str:
    start = value.find("{")
    end = value.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ""
    return value[start : end + 1].strip()


def is_generic_enrichment_text(value: str | None) -> bool:
    normalized = " ".join(str(value or "").lower().split()).strip()
    if not normalized:
        return False
    generic_markers = GENERIC_FALLBACK_TEXT_MARKERS + RAW_SYNTHETIC_TEXT_MARKERS
    return any(marker in normalized for marker in generic_markers)


def _looks_like_parser_residue(value: str | None) -> bool:
    normalized = " ".join(str(value or "").lower().split()).strip()
    if not normalized:
        return False
    return any(marker in normalized for marker in PARSER_RESIDUE_TEXT_MARKERS)


def _looks_like_weak_user_text(value: str | None) -> bool:
    normalized = " ".join(str(value or "").lower().split()).strip()
    if not normalized:
        return True
    if is_generic_enrichment_text(normalized) or _looks_like_parser_residue(normalized):
        return True
    return False


def _normalize_text(value: Any) -> str:
    return WHITESPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _build_llm_proxy_config(proxy_url: str | None) -> dict[str, str] | None:
    normalized = (proxy_url or "").strip()
    if not normalized:
        return None
    scheme = urlparse(normalized).scheme.lower()
    if scheme not in LLM_PROXY_SCHEMES:
        safe_scheme = scheme or "missing"
        raise ValueError(f"Unsupported LLM_PROXY_URL scheme: {safe_scheme}")
    return {
        "http": normalized,
        "https": normalized,
    }


def _safe_llm_provider_error_snippet(
    value: bytes | str,
    *,
    secrets: tuple[str, ...] = (),
    max_chars: int = LLM_PROVIDER_ERROR_SNIPPET_CHARS,
) -> str:
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value or "")
    text = _normalize_text(text)
    for secret in secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, "[REDACTED]")
    text = URL_CREDENTIALS_RE.sub(r"\1[REDACTED]@", text)
    for pattern in SECRET_TEXT_PATTERNS:
        text = pattern.sub(
            lambda match: (
                f"{match.group(1)}[REDACTED]"
                if match.groups()
                else "[REDACTED]"
            ),
            text,
        )
    if len(text) > max_chars:
        return f"{text[: max_chars - 3].rstrip()}..."
    return text


def _sanitize_text_for_llm_input(
    *,
    title: str,
    raw_text: str,
    source_name: str | None,
    url: str | None,
) -> str:
    source_key = f"{source_name or ''} {url or ''}".lower()
    raw_lines = str(raw_text or "").replace("\r", "\n").splitlines()
    kept_lines: list[str] = []
    seen_lines: set[str] = set()
    for raw_line in raw_lines:
        line = _normalize_text(raw_line)
        if not line:
            continue
        if _is_noise_line_for_llm(line, source_key=source_key):
            continue
        key = line.lower()
        if key in seen_lines:
            continue
        seen_lines.add(key)
        kept_lines.append(line)
    candidate = "\n".join(kept_lines) if kept_lines else _normalize_text(raw_text)
    candidate = _strip_inline_noise_fragments(candidate, source_key=source_key)
    candidate = _drop_repeated_title_prefix(candidate, title=title)
    candidate = _dedupe_sentences(candidate)
    return _normalize_text(candidate)


def _is_noise_line_for_llm(line: str, *, source_key: str) -> bool:
    lowered = line.lower()
    if not lowered:
        return True
    if URL_RE.fullmatch(line):
        return True
    if DATE_ONLY_LINE_RE.fullmatch(line):
        return True
    if SHARE_PHRASE_RE.fullmatch(lowered) or SOCIAL_TOKEN_ONLY_RE.fullmatch(lowered):
        return True
    if any(lowered.startswith(prefix) for prefix in LLM_NOISE_LINE_PREFIXES):
        return True
    if lowered.startswith("источник:") and "http" in lowered:
        return True
    if lowered.startswith("дата:") and len(lowered) < 40:
        return True
    if "regulation.gov" in source_key and (
        lowered.startswith("id:")
        or " код:" in lowered
        or lowered.startswith("этап портала:")
        or lowered.startswith("файлы этапа:")
        or lowered.startswith("ответственный:")
        or lowered.startswith("процедура:")
    ):
        return True
    return _looks_like_ocr_noise_text(line)


def _strip_inline_noise_fragments(text: str, *, source_key: str = "") -> str:
    cleaned = str(text or "")
    if not cleaned:
        return ""
    for pattern in PORTAL_METADATA_INLINE_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(
        r"(?:поделиться\s+ссылкой|поделиться\s+в\s+социальных\s+сетях)(?:\s+(?:vk|ok|telegram|whatsapp|viber))*",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = TRUNCATED_SHARE_PHRASE_RE.sub(" ", cleaned)
    cleaned = INLINE_SOURCE_DATE_RE.sub(" ", cleaned)
    cleaned = URL_RE.sub(" ", cleaned)
    cleaned = NOISY_OCR_TOKEN_RE.sub(" ", cleaned)
    cleaned = INLINE_SOCIAL_TOKEN_RE.sub(" ", cleaned)
    # MCX/portal announcement prefix is stripped unconditionally — an LLM that
    # copies it into user-facing facts must not leak it into the GR report.
    cleaned = PORTAL_ANNOUNCEMENT_PREFIX_RE.sub("", cleaned)
    if "mcx.gov.ru" in source_key or "минсельхоз россии" in source_key:
        cleaned = MCX_SITE_PREFIX_RE.sub("", cleaned)
    cleaned = _collapse_spaced_caps(cleaned)
    cleaned = _strip_leading_ocr_noise(cleaned)
    cleaned = _replace_english_gr_terms(cleaned)
    return _normalize_text(cleaned)


def _collapse_spaced_caps(text: str) -> str:
    """Collapse "П О С Т А Н О В Л Е Н И Е" → "ПОСТАНОВЛЕНИЕ"."""
    return SPACED_CAPS_RE.sub(lambda match: match.group(0).replace(" ", ""), text)


def _strip_leading_ocr_noise(text: str) -> str:
    """Remove OCR/bureaucratic-header garbage from the head of a fragment.

    Finds the first occurrence of a "real" Russian phrase anchor in the text
    (see ``MEANINGFUL_RUSSIAN_PHRASE_RE``). If the prefix before that anchor
    looks like OCR noise — uppercase-dominant, Latin-mixed, or a known
    bureaucratic-act header (Министерство ... Приказ от … № … г. …) — the
    prefix is dropped. Otherwise the text is returned unchanged so genuine
    Russian sentences are never truncated.
    """
    if not text:
        return text
    match = MEANINGFUL_RUSSIAN_PHRASE_RE.search(text)
    if match is None or match.start() == 0:
        return text
    prefix = text[: match.start()]
    if not _looks_like_ocr_prefix(prefix):
        return text
    return text[match.start():].lstrip(" ,.;:‚–-")


def _looks_like_ocr_prefix(prefix: str) -> bool:
    """Heuristic: does ``prefix`` look like OCR/bureaucratic garbage worth dropping?

    Conservative on purpose — returns False when the prefix is a short
    acronym/event name or a normal lowercase Russian fragment, so we never
    truncate a real sentence. Returns True when the prefix has any of the
    obvious noise signatures: bureaucratic-act keywords, Latin-character mix,
    short bracketed OCR fragments, or a long uppercase block.
    """
    normalized = prefix.strip()
    if not normalized:
        return False
    if len(normalized) > 600:
        return False
    lowered = normalized.lower()
    if any(keyword in lowered for keyword in _BUREAUCRATIC_HEADER_KEYWORDS):
        return True
    cyrillic_lower = len(re.findall(r"[а-яё]", normalized))
    cyrillic_upper = len(re.findall(r"[А-ЯЁ]", normalized))
    latin_chars = len(re.findall(r"[A-Za-z]", normalized))
    if cyrillic_lower >= 20 and latin_chars < cyrillic_lower * 0.2 and cyrillic_lower > cyrillic_upper:
        # Mostly real lowercase Russian — not OCR noise.
        return False
    # Latin admixture in a Russian fragment is a strong OCR signature.
    if latin_chars >= 4 and latin_chars >= cyrillic_lower:
        return True
    # Bracketed short fragments characteristic of OCR debris.
    if re.search(r"\([^)]{2,15}\)", normalized) and len(normalized) <= 200:
        return True
    # Numeric + Latin token mix ("4ОТО ZB", "G4 2-78").
    if re.search(r"\d{1,3}[-/.]\d", normalized) and latin_chars > 0:
        return True
    # All-uppercase block of substantial length — likely a stamped header
    # rather than a real sentence start. The thresholds intentionally leave
    # short acronyms (e.g. "ЦИПР-2026", "АПК") untouched.
    if cyrillic_upper >= 18 and cyrillic_upper >= cyrillic_lower * 3:
        return True
    return False


def _replace_english_gr_terms(text: str) -> str:
    """Replace English GR-jargon (eligibility/compliance/deadline) with Russian."""
    if not text:
        return text
    result = _ELIGIBILITY_VERB_RE.sub(r"\1 соответствия критериям", text)
    for pattern, replacement in _ENGLISH_GR_TERM_REPLACEMENTS:
        result = pattern.sub(replacement, result)
    return result


def _drop_repeated_title_prefix(text: str, *, title: str) -> str:
    cleaned = _normalize_text(text)
    normalized_title = _normalize_text(title)
    if not cleaned or not normalized_title:
        return cleaned
    lowered_title = normalized_title.lower()
    while cleaned.lower().startswith(lowered_title):
        cleaned = cleaned[len(normalized_title) :].strip(" .,-:;")
    if normalized_title.lower() not in cleaned.lower():
        cleaned = f"{normalized_title}. {cleaned}".strip()
    return cleaned


def _dedupe_sentences(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    unique_sentences: list[str] = []
    seen: set[str] = set()
    for sentence in SENTENCE_SPLIT_RE.split(normalized):
        candidate = sentence.strip(" ;,")
        if not candidate:
            continue
        key = re.sub(r"[\W_]+", " ", candidate.lower()).strip()
        if len(key) < 5 or key in seen:
            continue
        seen.add(key)
        unique_sentences.append(candidate)
    return _normalize_text(" ".join(unique_sentences))


def _looks_like_ocr_noise_text(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if NOISY_OCR_TOKEN_RE.search(normalized):
        return True
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", normalized)
    if len(letters) < 18:
        return False
    uppercase_count = len(UPPERCASE_LETTER_RE.findall(normalized))
    lowercase_count = len(LOWERCASE_LETTER_RE.findall(normalized))
    if uppercase_count < 12 or uppercase_count <= lowercase_count * 2:
        return False
    if not re.search(r"министерств|правительств|постановлен|приказ", normalized, re.IGNORECASE):
        return False
    weird_symbol_count = len(re.findall(r"[©{}|~_]", normalized))
    return weird_symbol_count > 0


def _looks_like_bureaucratic_header_text(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", normalized)
    if len(letters) < 24:
        return False
    uppercase_count = len(UPPERCASE_LETTER_RE.findall(normalized))
    lowercase_count = len(LOWERCASE_LETTER_RE.findall(normalized))
    if uppercase_count < 12 or uppercase_count <= max(lowercase_count * 3, 12):
        return False
    return bool(
        re.search(
            r"министерств|правительств|администрац|постановлен|приказ",
            normalized,
            re.IGNORECASE,
        )
    )


def _clean_user_facing_text(value: Any) -> str:
    text = _sanitize_enrichment_text(value)
    if not text:
        return ""
    # Hard reject any text that still carries synthetic-API field markers like
    # "title:", "shortName:", "endDate:", "acceptingApplicationsInfo:". Those
    # markers prove the LLM copied a raw portal payload structure, not Russian
    # business content — stripping the prefix and keeping the tail would only
    # conceal the leak.
    lowered_raw = text.lower()
    if any(marker in lowered_raw for marker in RAW_SYNTHETIC_TEXT_MARKERS):
        return ""
    text = _strip_inline_noise_fragments(text)
    text = _dedupe_sentences(text)
    if _looks_like_noisy_user_facing_text(text):
        return ""
    return _normalize_text(text)


def _looks_like_noisy_user_facing_text(value: str | None) -> bool:
    normalized = _normalize_text(value)
    lowered = normalized.lower()
    if not normalized:
        return True
    if is_generic_enrichment_text(lowered) or _looks_like_parser_residue(lowered):
        return True
    if SHARE_PHRASE_RE.search(lowered):
        return True
    if URL_RE.search(normalized):
        return True
    if _looks_like_ocr_noise_text(normalized):
        return True
    if _looks_like_bureaucratic_header_text(normalized):
        return True
    if "этап портала" in lowered or "файлы этапа" in lowered:
        return True
    if re.search(r"\bid\s*:\s*\S+", normalized, re.IGNORECASE):
        return True
    if re.search(r"\bкод\s*:\s*\S+", normalized, re.IGNORECASE):
        return True
    return False


def _is_regulation_discussion_context(
    analysis: AnalysisResult,
    *,
    url: str | None,
    raw_text: str = "",
) -> bool:
    combined = " ".join(
        part
        for part in (
            url or "",
            analysis.page_type or "",
            analysis.summary or "",
            analysis.business_signal or "",
            analysis.deadline_text or "",
            raw_text[:1200],
        )
        if part
    ).lower()
    return "regulation.gov" in combined and (
        "обсуждени" in combined or "публичн" in combined
    )


def _needs_effective_date_reset(*, facts: DocumentCardFacts, raw_text: str) -> bool:
    if not facts.effective_date or not facts.deadline:
        return False
    if facts.effective_date != facts.deadline:
        return False
    lowered = raw_text.lower()
    return not any(marker in lowered for marker in EFFECTIVE_DATE_MARKERS)


def _harden_facts(
    facts: DocumentCardFacts,
    *,
    analysis: AnalysisResult,
    raw_text: str,
    source_name: str | None,
    region: str | None,
    url: str | None,
) -> DocumentCardFacts:
    payload = facts.model_dump(mode="json")
    hardened = DocumentCardFacts.model_validate(payload)
    fallback_provider = MockEnrichmentProvider()
    source_role = _normalized_source_role(source_name)
    if _is_regulation_discussion_context(analysis, url=url, raw_text=raw_text):
        hardened.document_type = "проект НПА"
        hardened.status = "проект обсуждается"
        if not hardened.deadline:
            parsed_deadline = parse_deadline_date(analysis.deadline_text)
            hardened.deadline = parsed_deadline.isoformat() if parsed_deadline else None
        if _needs_effective_date_reset(facts=hardened, raw_text=raw_text):
            hardened.effective_date = None
    if not hardened.region and region:
        hardened.region = TARGET_REGIONS.get(region.lower(), region)
    if not hardened.authority and source_name:
        hardened.authority = source_name
    applicability = evaluate_support_measure_applicability(
        title=getattr(analysis, "normalized_title", None) or "",
        raw_text=raw_text,
        source_name=source_name,
        url=url,
        level=None,
        region=region,
        source_role=get_source_role(source_name),
        page_type=analysis.page_type,
    )
    if not applicability.is_applicable:
        if applicability.detected_region:
            hardened.region = applicability.detected_region
        hardened.applicability_note = (
            "Регион вне фокуса AHSTEP; документ сохранён справочно."
        )
        if applicability.has_low_relevance_signal:
            hardened.why_matters = (
                "Мера относится к региональной или тематически нерелевантной поддержке "
                "вне текущего фокуса AHSTEP."
            )
    if not hardened.applicability_note:
        hardened.applicability_note = fallback_provider._build_applicability_note(
            region=region
        )
    hardened.what_changed = (
        _clean_user_facing_text(hardened.what_changed)
        or _clean_user_facing_text(analysis.summary)
    )
    hardened.short_summary = _clean_user_facing_text(hardened.short_summary) or _clean_user_facing_text(
        fallback_provider._build_executive_summary(
            analysis,
            source_role=source_role,
        )
    )
    hardened.why_matters = _clean_user_facing_text(hardened.why_matters) or _clean_user_facing_text(
        fallback_provider._build_business_impact(
            analysis,
            source_role=source_role,
            region=region,
            source_name=source_name,
        )
    )
    hardened.what_to_check = _clean_user_facing_text(hardened.what_to_check) or _clean_user_facing_text(
        fallback_provider._build_recommended_action(
            analysis,
            source_role=source_role,
        )
    )
    hardened.applicability_note = _clean_user_facing_text(
        hardened.applicability_note
    ) or _clean_user_facing_text(
        fallback_provider._build_applicability_note(region=region)
    )
    return hardened


def _detect_weak_facts_reason(
    facts: DocumentCardFacts,
    *,
    analysis: AnalysisResult,
    url: str | None,
) -> str | None:
    if _looks_like_weak_user_text(facts.short_summary):
        return "weak short_summary"
    if _looks_like_weak_user_text(facts.why_matters):
        return "weak why_matters"
    if _looks_like_weak_user_text(facts.what_to_check):
        return "weak what_to_check"
    normalized_reason = " ".join(facts.why_matters.lower().split()).strip()
    if normalized_reason in WEAK_GR_REASON_VALUES:
        return "generic why_matters"
    normalized_action = " ".join(facts.what_to_check.lower().split()).strip()
    if analysis.action_level == "requires_attention" and "наблюдени" in normalized_action:
        return "passive what_to_check for requires_attention"
    if _is_regulation_discussion_context(analysis, url=url):
        if facts.status != "проект обсуждается":
            return "incorrect regulation discussion status"
        if not facts.deadline and parse_deadline_date(analysis.deadline_text):
            return "missing regulation discussion deadline"
    return None


def _normalized_source_role(source_name: str | None) -> str:
    return str(get_source_role(source_name) or "").strip()


def build_document_card_input(
    *,
    title: str,
    raw_text: str,
    analysis: AnalysisResult,
    source_name: str | None = None,
    url: str | None = None,
    level: str | None = None,
    region: str | None = None,
    published_at: datetime | None = None,
    document_type: str | None = None,
    max_document_chars: int | None = None,
) -> dict[str, Any]:
    safe_max_chars = max(1000, int(max_document_chars or config.LLM_MAX_DOCUMENT_CHARS))
    sanitized_raw_text = _sanitize_text_for_llm_input(
        title=title,
        raw_text=raw_text or "",
        source_name=source_name,
        url=url,
    )
    extracted = clean_text_for_analysis(
        source_name=source_name,
        url=url,
        title=title,
        raw_text=sanitized_raw_text,
    )
    topic_family = _extract_topic_family(getattr(analysis, "source_facts", []))
    return {
        "prompt_version": DOCUMENT_CARD_PROMPT_VERSION,
        "title": title,
        "url": url,
        "source": source_name,
        "published_at": published_at.isoformat() if published_at else None,
        "level": level,
        "region": region,
        "document_type": document_type,
        "action_level": analysis.action_level,
        "page_type": analysis.page_type,
        "topic": analysis.topic,
        "topic_family": topic_family,
        "deterministic_summary": analysis.summary,
        "deterministic_impact": analysis.impact,
        "business_signal": analysis.business_signal,
        "application_status": analysis.application_status,
        "deadline_text": analysis.deadline_text,
        "support_status": analysis.support_status,
        "raw_text_excerpt": (extracted.text or sanitized_raw_text or raw_text or "")[:safe_max_chars],
    }


def compute_document_card_source_hash(document_payload: Mapping[str, Any]) -> str:
    hash_payload = {
        key: document_payload.get(key)
        for key in (
            "prompt_version",
            "title",
            "url",
            "source",
            "published_at",
            "level",
            "region",
            "document_type",
            "action_level",
            "page_type",
            "topic",
            "topic_family",
            "deterministic_summary",
            "deterministic_impact",
            "business_signal",
            "application_status",
            "deadline_text",
            "support_status",
            "raw_text_excerpt",
        )
    }
    serialized = json.dumps(hash_payload, ensure_ascii=False, sort_keys=True)
    return sha256(serialized.encode("utf-8")).hexdigest()


def _extract_topic_family(source_facts: list[str]) -> str | None:
    for value in source_facts or []:
        if value.startswith("topic_family:"):
            return value.split(":", 1)[1].strip() or None
    return None


def _extract_source_quotes(
    *,
    title: str,
    raw_text: str,
    analysis: AnalysisResult,
    max_quotes: int = 3,
) -> list[str]:
    text = " ".join((raw_text or "").split())
    if not text:
        return [title.strip()[:160]] if title.strip() else []
    sentence_candidates = re.split(r"(?<=[.!?])\s+", text)
    markers = [
        "субсид",
        "грант",
        "кредит",
        "компенсац",
        "отбор",
        "прием",
        "приём",
        "заяв",
        "срок",
        "обсужден",
        "постановлен",
        "приказ",
        "сельхоз",
    ]
    if analysis.deadline_text:
        markers.append(str(analysis.deadline_text).lower()[:20])
    quotes: list[str] = []
    for sentence in sentence_candidates:
        normalized = " ".join(sentence.split()).strip(" ;")
        if not normalized:
            continue
        lowered = normalized.lower()
        if any(marker and marker in lowered for marker in markers):
            quotes.append(_clip_quote(normalized))
        if len(quotes) >= max_quotes:
            break
    if not quotes:
        quotes.append(_clip_quote(text))
    seen: set[str] = set()
    result: list[str] = []
    for quote in quotes:
        if quote and quote not in seen:
            seen.add(quote)
            result.append(quote)
    return result[:max_quotes]


def _clip_quote(text: str, max_chars: int = 180) -> str:
    return safe_truncate_text(text, max_chars)
