from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from functools import lru_cache
from hashlib import sha256
from typing import Any, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    parse_deadline_date,
)

ELIGIBLE_ACTION_LEVELS: set[str] = {"requires_attention", "watchlist"}
READ_PATH_MIN_CONFIDENCE = 0.5
CONFIDENCE_TO_SCORE = {"high": 0.9, "medium": 0.75, "low": 0.45}
TARGET_REGIONS = {
    "federal": "РФ",
    "rostov": "Ростовская область",
    "krasnodar": "Краснодарский край",
    "stavropol": "Ставропольский край",
}


class DocumentCardFacts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_type: str = "другое"
    region: str | None = None
    authority: str | None = None
    status: str = "неизвестно"
    deadline: str | None = None
    effective_date: str | None = None
    support_type: str | None = None
    target_recipients: list[str] = Field(default_factory=list)
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
            deadline_hint=facts.deadline,
            confidence=CONFIDENCE_TO_SCORE.get(facts.confidence, 0.45),
        )

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
                f"Применимость к AHSTEP не следует автоматически; требуется проверить "
                f"региональные критерии и наличие активов/получателей в регионе: {formatted_region}."
            )
        return (
            "Применимость к AHSTEP требует отдельной проверки eligibility, отраслевых "
            "критериев и условий участия."
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
        if source_role == "regional_npa" and analysis.action_level == "requires_attention":
            return "Документ содержит изменения в порядке предоставления поддержки; требуется проверка условий и сроков."
        if source_role in {"active_support_measures", "support_documents"} or analysis.page_type in {
            "measure_card",
            "selection_announcement",
            "deadline_update",
        }:
            return "Мера поддержки требует проверки применимости, условий участия и возможных сроков."
        return "Документ оставлен на наблюдении как возможный стратегический сигнал."

    def _build_business_impact(
        self,
        analysis: AnalysisResult,
        *,
        source_role: str,
        region: str | None,
        source_name: str | None,
    ) -> str:
        if source_role == "regional_npa":
            base = "Изменения могут повлиять на порядок предоставления поддержки, круг получателей или сроки применения."
        elif source_role in {"active_support_measures", "support_documents"} or analysis.application_status in {
            "open",
            "regular",
        }:
            base = "Изменения могут повлиять на применимость меры, условия участия и организацию подачи."
        else:
            base = "Сигнал может повлиять на контекст господдержки и требует наблюдения со стороны GR."

        details: list[str] = []
        region_hint = self._format_region(region)
        if region_hint:
            details.append(f"Регион: {region_hint}.")
        if source_name:
            details.append(f"Источник: {source_name}.")
        suffix = f" {' '.join(details)}" if details else ""
        return f"{base}{suffix}".strip()

    def _build_recommended_action(self, analysis: AnalysisResult, *, source_role: str) -> str:
        if source_role == "regional_npa":
            return "Проверить изменения условий, сроки вступления в силу и затронутые организации."
        if analysis.action_level == "requires_attention":
            if analysis.application_status == "open" or analysis.deadline_text:
                return "Проверить применимость меры, сроки подачи и ответственного."
            return "Оценить срочность сигнала и определить следующий GR-шаг."
        if analysis.page_type in {"selection_announcement", "deadline_update"}:
            return "Проверить условия участия и окно подачи."
        return "Оставить в наблюдении до следующего подтверждающего обновления."

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
        normalized = " ".join(value.split()).strip()
        if len(normalized) <= max_chars:
            return normalized
        return f"{normalized[: max_chars - 3].rstrip(' ,.;:-')}..."


class OpenAICompatibleEnrichmentProvider(BaseEnrichmentProvider):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

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
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            repair_payload = self._build_repair_payload(invalid_content=content)
            response_payload = self._post_json(repair_payload)
            content = self._extract_content(response_payload)
            try:
                parsed = json.loads(content)
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
        return {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
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

    def _build_repair_payload(self, *, invalid_content: str) -> dict[str, object]:
        return {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": DOCUMENT_CARD_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": (
                        "Исправь предыдущий ответ в валидный JSON-объект строго по схеме. "
                        "Не добавляй Markdown и не придумывай недостающие факты.\n\n"
                        f"Предыдущий ответ:\n{invalid_content}"
                    ),
                },
            ],
        }

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
        request = urllib_request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib_request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib_error.URLError as exc:
            raise RuntimeError(f"LLM provider request failed: {exc.reason}") from exc

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
            return EnrichmentResult.failed(
                self.provider_error or "LLM enrichment provider is unavailable",
            )
        try:
            return self.provider.enrich_document(
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
                f"{type(exc).__name__}: {exc}",
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
    if status and status != "success":
        return None
    error = str(enrichment_row.get("error") or "").strip()
    if error:
        return None
    facts = _parse_facts_from_row(enrichment_row)
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
    fields = {
        "executive_summary": _sanitize_user_facing_enrichment_text(
            facts.get("short_summary") if facts else enrichment_row.get("executive_summary")
        ),
        "business_impact": _sanitize_user_facing_enrichment_text(
            facts.get("why_matters") if facts else enrichment_row.get("business_impact")
        ),
        "recommended_action": _sanitize_user_facing_enrichment_text(
            facts.get("what_to_check") if facts else enrichment_row.get("recommended_action")
        ),
        "deadline_hint": _sanitize_enrichment_text(
            facts.get("deadline") if facts else enrichment_row.get("deadline_hint")
        ),
    }
    if facts:
        fields["region"] = _sanitize_enrichment_text(facts.get("region"))
        fields["authority"] = _sanitize_enrichment_text(facts.get("authority"))
        fields["status"] = _sanitize_enrichment_text(facts.get("status"))
        fields["support_type"] = _sanitize_enrichment_text(facts.get("support_type"))
        fields["_document_card"] = "1"
    if not any(fields.values()):
        return None
    return fields


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
    text = _sanitize_enrichment_text(value)
    if not text or is_generic_enrichment_text(text):
        return ""
    return text


def is_generic_enrichment_text(value: str | None) -> bool:
    normalized = " ".join(str(value or "").lower().split()).strip()
    if not normalized:
        return False
    generic_markers = (
        "документ оставлен на наблюдении",
        "сигнал может повлиять",
        "оценить срочность сигнала",
        "изменения могут повлиять на",
        "title:",
        "shortname:",
        "enddate:",
        "acceptingapplicationsinfo:",
    )
    return any(marker in normalized for marker in generic_markers)


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
    extracted = clean_text_for_analysis(
        source_name=source_name,
        url=url,
        title=title,
        raw_text=raw_text or "",
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
        "raw_text_excerpt": (extracted.text or raw_text or "")[:safe_max_chars],
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
    normalized = " ".join(text.split()).strip()
    if len(normalized) <= max_chars:
        return normalized
    clipped = normalized[: max_chars - 3]
    last_space = clipped.rfind(" ")
    if last_space > max_chars // 2:
        clipped = clipped[:last_space]
    return f"{clipped.rstrip(' ,.;:-')}..."
