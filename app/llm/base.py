from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import AnalysisResult


class BaseLLMClient(ABC):
    @abstractmethod
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
        """Analyze a document and return structured metadata."""
