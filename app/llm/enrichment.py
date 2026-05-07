from __future__ import annotations

import json
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from pydantic import BaseModel, Field

from app import config
from app.models import AnalysisResult, ActionLevel

ELIGIBLE_ACTION_LEVELS: set[str] = {"requires_attention", "watchlist"}


class EnrichmentResult(BaseModel):
    executive_summary: str | None = None
    business_impact: str | None = None
    recommended_action: str | None = None
    deadline_hint: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    error: str | None = None


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
    ) -> EnrichmentResult:
        del raw_text, url, level
        executive_summary = analysis.summary.strip() or title.strip()
        source_hint = source_name or "источник"
        region_hint = self._format_region(region)
        impact = analysis.impact.strip()
        if region_hint and "регион" not in impact.lower():
            impact = f"{impact} Регион: {region_hint}."
        impact = impact.strip()
        if source_hint and source_hint not in impact:
            impact = f"{impact} Источник: {source_hint}.".strip()
        return EnrichmentResult(
            executive_summary=executive_summary,
            business_impact=impact,
            recommended_action=self._build_recommended_action(analysis),
            deadline_hint=self._build_deadline_hint(analysis),
            confidence=0.35,
        )

    def _build_recommended_action(self, analysis: AnalysisResult) -> str:
        if analysis.action_level == "requires_attention":
            if analysis.application_status == "open" or analysis.deadline_text:
                return "Проверить применимость меры, сроки подачи и ответственного."
            return "Оценить срочность сигнала и определить следующий GR-шаг."
        if analysis.page_type in {"selection_announcement", "deadline_update"}:
            return "Проверить условия участия и окно подачи."
        return "Оставить в наблюдении до следующего подтверждающего обновления."

    def _build_deadline_hint(self, analysis: AnalysisResult) -> str | None:
        if analysis.deadline_text:
            return analysis.deadline_text.strip()
        if analysis.key_dates:
            first_date = str(analysis.key_dates[0]).strip()
            if first_date:
                return first_date
        return None

    def _format_region(self, region: str | None) -> str:
        if not region:
            return ""
        normalized = region.replace("_", " ").strip()
        if not normalized:
            return ""
        return normalized


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
    ) -> EnrichmentResult:
        payload = self._build_payload(
            title=title,
            raw_text=raw_text,
            analysis=analysis,
            source_name=source_name,
            url=url,
            level=level,
            region=region,
        )
        response_payload = self._post_json(payload)
        content = self._extract_content(response_payload)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON from LLM provider: {exc.msg}") from exc
        return EnrichmentResult.model_validate(parsed)

    def _build_payload(
        self,
        *,
        title: str,
        raw_text: str,
        analysis: AnalysisResult,
        source_name: str | None,
        url: str | None,
        level: str | None,
        region: str | None,
    ) -> dict[str, object]:
        user_prompt = {
            "title": title,
            "source_name": source_name,
            "url": url,
            "level": level,
            "region": region,
            "action_level": analysis.action_level,
            "page_type": analysis.page_type,
            "summary": analysis.summary,
            "impact": analysis.impact,
            "deadline_text": analysis.deadline_text,
            "application_status": analysis.application_status,
            "raw_text_excerpt": raw_text[:4000],
        }
        return {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return JSON with keys executive_summary, business_impact, "
                        "recommended_action, deadline_hint, confidence, error. "
                        "Do not change action_level or classification."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(user_prompt, ensure_ascii=False),
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
    ) -> None:
        self.enabled = enabled
        self.provider = provider
        self.provider_error = provider_error

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
    ) -> EnrichmentResult | None:
        if not self.enabled:
            return None
        if not is_enrichment_eligible(analysis.action_level):
            return None
        if self.provider is None:
            return EnrichmentResult(
                confidence=0.0,
                error=self.provider_error or "LLM enrichment provider is unavailable",
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
            )
        except Exception as exc:
            return EnrichmentResult(confidence=0.0, error=f"{type(exc).__name__}: {exc}")


def is_enrichment_eligible(action_level: ActionLevel | str | None) -> bool:
    return str(action_level or "").strip() in ELIGIBLE_ACTION_LEVELS


def build_document_enricher() -> DocumentEnricher:
    if not config.LLM_ENRICHMENT_ENABLED:
        return DocumentEnricher(enabled=False)
    provider_name = (config.LLM_PROVIDER or "mock").strip().lower()
    if provider_name == "mock":
        return DocumentEnricher(enabled=True, provider=MockEnrichmentProvider())
    if provider_name in {"openai", "openai-compatible", "openai_compatible", "lmstudio", "ollama"}:
        return DocumentEnricher(
            enabled=True,
            provider=OpenAICompatibleEnrichmentProvider(
                base_url=config.LLM_BASE_URL,
                api_key=config.LLM_API_KEY,
                model=config.LLM_MODEL,
                timeout_seconds=config.REQUEST_TIMEOUT,
            ),
        )
    return DocumentEnricher(
        enabled=True,
        provider_error=f"unsupported LLM provider: {provider_name}",
    )


@lru_cache(maxsize=1)
def get_document_enricher() -> DocumentEnricher:
    return build_document_enricher()
