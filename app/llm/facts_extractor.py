from __future__ import annotations

import re

from pydantic import BaseModel

from app.models import ApplicationStatus, SupportStatus

WHITESPACE_RE = re.compile(r"\s+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
OPEN_MARKERS = (
    "прием заявок",
    "приём заявок",
    "заявки принимаются",
    "срок подачи",
    "подача заявок",
    "конкурсный отбор",
    "отбор заявок",
    "прием документов",
    "приём документов",
)
CLOSED_MARKERS = (
    "прием завершен",
    "приём завершен",
    "отбор завершен",
    "прием заявок завершен",
    "приём заявок завершен",
)
DEADLINE_MARKERS = (
    "прием заявок",
    "приём заявок",
    "заявки принимаются",
    "подача заявок",
    "срок подачи",
    "конкурсный отбор",
    "отбор заявок",
    "публичное обсуждение",
    "срок обсуждения",
)
DEADLINE_PRIORITY_RE = re.compile(
    r"(прием заявок|приём заявок|заявки принимаются|подача заявок|срок подачи|конкурсный отбор|отбор заявок|публичное обсуждение|срок обсуждения)",
    re.IGNORECASE,
)
NPA_PATTERNS = (
    re.compile(r"\b\d{2}-\d{5}-\d{5}-[A-ZА-ЯЁ]\b", re.IGNORECASE),
    re.compile(r"\bНПА\s*\d+[0-9A-ZА-Яа-я-]*\b", re.IGNORECASE),
    re.compile(r"\bбывш\.\s*\d+\b", re.IGNORECASE),
)


class DocumentFacts(BaseModel):
    support_status: SupportStatus = "unknown"
    is_active: bool | None = None
    is_continuous: bool | None = None
    application_status: ApplicationStatus = "unknown"
    npa_number: str | None = None
    deadline_text: str | None = None
    terms_text: str | None = None
    business_signal: str | None = None
    risk_notes: str | None = None


def extract_document_facts(title: str, raw_text: str) -> DocumentFacts:
    combined_text = _normalize_text(f"{title} {raw_text}")
    lower_text = combined_text.lower()

    support_status, is_active = _extract_support_status(lower_text)
    is_continuous = _extract_continuous_flag(lower_text)
    application_status = _extract_application_status(lower_text, is_continuous=is_continuous)
    npa_number = _extract_npa_number(combined_text)
    deadline_text = _extract_deadline_text(combined_text)
    terms_text = _extract_terms_text(combined_text)
    risk_notes = _build_risk_notes(
        support_status=support_status,
        application_status=application_status,
        deadline_text=deadline_text,
    )

    return DocumentFacts(
        support_status=support_status,
        is_active=is_active,
        is_continuous=is_continuous,
        application_status=application_status,
        npa_number=npa_number,
        deadline_text=deadline_text,
        terms_text=terms_text,
        risk_notes=risk_notes,
    )


def _normalize_text(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text).strip()


def _extract_support_status(text: str) -> tuple[SupportStatus, bool | None]:
    if "не активная" in text or "неактивная" in text:
        return "inactive", False
    if "активная" in text:
        return "active", True
    return "unknown", None


def _extract_continuous_flag(text: str) -> bool | None:
    if "на регулярной основе" in text:
        return True
    return None


def _extract_application_status(
    text: str,
    *,
    is_continuous: bool | None,
) -> ApplicationStatus:
    if any(marker in text for marker in CLOSED_MARKERS):
        return "closed"
    if any(marker in text for marker in OPEN_MARKERS):
        return "open"
    if is_continuous:
        return "regular"
    return "unknown"


def _extract_npa_number(text: str) -> str | None:
    for pattern in NPA_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0).strip()
    return None


def _extract_deadline_text(text: str) -> str | None:
    sentences = SENTENCE_SPLIT_RE.split(text)
    for sentence in sentences:
        normalized_sentence = _normalize_text(sentence)
        lowered = normalized_sentence.lower()
        if not any(marker in lowered for marker in DEADLINE_MARKERS):
            continue
        if any(
            excluded in lowered
            for excluded in (
                "срок кредита",
                "срок займа",
                "срок действия соглашения",
                "до 12 месяцев",
                "до 5 лет",
            )
        ):
            continue
        if "до 2030 года" in lowered and not any(
            marker in lowered
            for marker in (
                "прием заявок",
                "приём заявок",
                "подача заявок",
                "срок подачи",
                "публичное обсуждение",
                "срок обсуждения",
            )
        ):
            continue
        match = DEADLINE_PRIORITY_RE.search(normalized_sentence)
        if not match:
            continue
        start = max(match.start() - 30, 0)
        end = min(match.end() + 120, len(normalized_sentence))
        snippet = normalized_sentence[start:end].strip(" ,.;:-")
        if len(snippet) <= 180:
            return snippet
        return snippet[:177].rstrip(" ,.;:-") + "..."
    return None


def _extract_terms_text(text: str) -> str | None:
    sentences = SENTENCE_SPLIT_RE.split(text)
    for sentence in sentences:
        normalized_sentence = _normalize_text(sentence)
        lowered = normalized_sentence.lower()
        if any(
            marker in lowered
            for marker in (
                "срок кредита",
                "срок займа",
                "срок действия соглашения",
                "до 12 месяцев",
                "до 5 лет",
                "размер поддержки",
                "ставка:",
                "сумма:",
                "авансового платежа",
                "фактических понесенных затрат",
            )
        ):
            if len(normalized_sentence) <= 180:
                return normalized_sentence
            return normalized_sentence[:177].rstrip(" ,.;:-") + "..."
    return None


def _build_risk_notes(
    *,
    support_status: SupportStatus,
    application_status: ApplicationStatus,
    deadline_text: str | None,
) -> str | None:
    if support_status == "inactive" and deadline_text:
        return "Срок найден в описании неактивной меры; не является текущим окном подачи."
    if support_status == "inactive":
        return "Мера помечена как неактивная."
    if application_status == "closed" and deadline_text:
        return "Срок найден в описании закрытой меры; не является текущим окном подачи."
    if application_status == "closed":
        return "Прием заявок или отбор завершены."
    if application_status == "unknown" and not deadline_text:
        return "Не найден явный статус приема заявок или срок."
    return None
