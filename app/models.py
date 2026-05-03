from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

ActionLevel = Literal["requires_attention", "watchlist", "background", "irrelevant"]
ImportanceLevel = Literal["high", "medium", "low"]
SupportStatus = Literal["active", "inactive", "unknown"]
ApplicationStatus = Literal["open", "closed", "regular", "unknown"]
PageType = Literal[
    "selection_announcement",
    "measure_card",
    "new_rule",
    "deadline_update",
    "results_protocol",
    "registry",
    "reference_page",
    "news_background",
    "section_page",
    "category_page",
    "year_archive",
    "navigation",
    "unknown",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SourceConfig(BaseModel):
    name: str
    url: str
    level: Literal["federal", "regional", "news", "support_measures"]
    region: Literal["federal", "rostov", "krasnodar", "stavropol"]
    source_type: str = "html_listing"
    enabled: bool = True
    parser: Literal["generic_html", "government", "regional_law"] = "generic_html"
    verify_ssl: bool = True
    request_timeout: int | None = None
    request_headers: dict[str, str] = Field(default_factory=dict)
    max_items: int | None = None
    deny_patterns: list[str] = Field(default_factory=list)
    allow_patterns: list[str] = Field(default_factory=list)
    description: str


class CollectedItem(BaseModel):
    source_name: str
    source_url: str
    level: str
    region: str
    title: str
    url: str
    published_at: datetime | None = None
    document_type: str = "unknown"


class ExtractionResult(BaseModel):
    raw_text: str = ""
    local_file_path: str | None = None
    document_type: str = "unknown"
    published_at: datetime | None = None
    needs_ocr: bool = False
    error: str | None = None


class RawDocument(BaseModel):
    id: int | None = None
    source_name: str
    source_url: str
    level: str
    region: str
    title: str
    url: str
    published_at: datetime | None = None
    collected_at: datetime = Field(default_factory=utc_now)
    content_hash: str
    raw_text: str = ""
    local_file_path: str | None = None
    document_type: str = "unknown"
    is_relevant: bool | None = None
    relevance_reason: str | None = None
    topic: str | None = None
    importance: ImportanceLevel | None = None
    action_level: ActionLevel | None = None
    page_type: PageType | None = None
    summary: str | None = None
    impact: str | None = None
    support_status: SupportStatus | None = None
    is_active: bool | None = None
    is_continuous: bool | None = None
    application_status: ApplicationStatus | None = None
    npa_number: str | None = None
    deadline_text: str | None = None
    terms_text: str | None = None
    business_signal: str | None = None
    risk_notes: str | None = None
    notified: bool = False
    status: str = "collected"
    error: str | None = None


class AnalysisResult(BaseModel):
    is_relevant: bool
    relevance_reason: str
    topic: str | None = None
    importance: ImportanceLevel
    action_level: ActionLevel
    page_type: PageType
    summary: str
    impact: str
    support_status: SupportStatus = "unknown"
    is_active: bool | None = None
    is_continuous: bool | None = None
    application_status: ApplicationStatus = "unknown"
    npa_number: str | None = None
    deadline_text: str | None = None
    terms_text: str | None = None
    business_signal: str | None = None
    risk_notes: str | None = None
    key_dates: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    source_facts: list[str] = Field(default_factory=list)


class AnalyzedDocument(RawDocument):
    is_relevant: bool
    relevance_reason: str
    importance: ImportanceLevel
    action_level: ActionLevel
    page_type: PageType
    summary: str
    impact: str


class DigestItem(BaseModel):
    title: str
    region: str
    source_name: str
    url: str
    importance: str | None = None
    action_level: str | None = None
    page_type: str | None = None
    summary: str | None = None
    impact: str | None = None
    relevance_reason: str | None = None
    published_at: datetime | None = None
    support_status: SupportStatus | None = None
    is_active: bool | None = None
    is_continuous: bool | None = None
    application_status: ApplicationStatus | None = None
    npa_number: str | None = None
    deadline_text: str | None = None
    terms_text: str | None = None
    business_signal: str | None = None
    risk_notes: str | None = None


class SourceErrorRecord(BaseModel):
    id: int | None = None
    source_name: str
    source_url: str
    error: str
    collected_at: datetime = Field(default_factory=utc_now)
