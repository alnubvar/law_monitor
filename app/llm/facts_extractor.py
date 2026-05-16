from __future__ import annotations

from datetime import date, datetime, timezone
import re

from pydantic import BaseModel

from app.extractors.date_extractor import parse_russian_date
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
EXPLICIT_OPEN_MARKERS = (
    "прием открыт",
    "приём открыт",
    "прием заявок открыт",
    "приём заявок открыт",
    "идет прием заявок",
    "идёт прием заявок",
    "заявки принимаются",
    "объявлен конкурсный отбор",
    "объявление о проведении конкурсного отбора",
    "возобновлен прием заявок",
    "возобновлён прием заявок",
)
CLOSED_MARKERS = (
    "прием завершен",
    "приём завершен",
    "прием окончен",
    "приём окончен",
    "отбор завершен",
    "прием заявок завершен",
    "приём заявок завершен",
    "заявки не принимаются",
    "прием заявок приостановлен",
    "приём заявок приостановлен",
    "приостановить прием заявок",
)
DEADLINE_MARKERS = (
    "прием заявок",
    "приём заявок",
    "заявки принимаются",
    "подача заявок",
    "срок подачи",
    "конкурсный отбор",
    "отбор заявок",
    "прием документов",
    "приём документов",
    "публичное обсуждение",
    "срок обсуждения",
    "конец обсуждения",
    "окончание обсуждения",
    "завершение обсуждения",
)
DEADLINE_PRIORITY_RE = re.compile(
    r"(прием заявок|приём заявок|прием открыт|приём открыт|прием заявок открыт|приём заявок открыт|"
    r"заявки принимаются|подача заявок|срок подачи|конкурсный отбор|отбор заявок|"
    r"прием документов|приём документов|публичное обсуждение|срок обсуждения|"
    r"конец обсуждения|окончание обсуждения|завершение обсуждения)",
    re.IGNORECASE,
)
DATE_TOKEN_RE = re.compile(
    r"(?<!\d)("
    r"\d{1,2}[./]\d{1,2}[./]\d{4}"
    r"|"
    r"\d{4}-\d{2}-\d{2}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:января|январь|февраля|февраль|марта|март|апреля|апрель|мая|май|июня|июнь|"
    r"июля|июль|августа|август|сентября|сентябрь|октября|октябрь|ноября|ноябрь|"
    r"декабря|декабрь)"
    r"\s+\d{4}(?:\s+г(?:ода|\.))?"
    r")(?!\d)",
    re.IGNORECASE,
)
DEADLINE_DATE_RE = re.compile(
    r"\b(?:до|по)\s+(?P<date>"
    r"\d{1,2}[./]\d{1,2}[./]\d{4}"
    r"|"
    r"\d{4}-\d{2}-\d{2}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:января|январь|февраля|февраль|марта|март|апреля|апрель|мая|май|июня|июнь|"
    r"июля|июль|августа|август|сентября|сентябрь|октября|октябрь|ноября|ноябрь|"
    r"декабря|декабрь)"
    r"\s+\d{4}(?:\s+г(?:ода|\.))?"
    r")",
    re.IGNORECASE,
)
DEADLINE_RANGE_RE = re.compile(
    r"\bс\s+(?P<start>"
    r"\d{1,2}[./]\d{1,2}[./]\d{4}"
    r"|"
    r"\d{4}-\d{2}-\d{2}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:января|январь|февраля|февраль|марта|март|апреля|апрель|мая|май|июня|июнь|"
    r"июля|июль|августа|август|сентября|сентябрь|октября|октябрь|ноября|ноябрь|"
    r"декабря|декабрь)"
    r"\s+\d{4}(?:\s+г(?:ода|\.))?"
    r")\s+по\s+(?P<end>"
    r"\d{1,2}[./]\d{1,2}[./]\d{4}"
    r"|"
    r"\d{4}-\d{2}-\d{2}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:января|январь|февраля|февраль|марта|март|апреля|апрель|мая|май|июня|июнь|"
    r"июля|июль|августа|август|сентября|сентябрь|октября|октябрь|ноября|ноябрь|"
    r"декабря|декабрь)"
    r"\s+\d{4}(?:\s+г(?:ода|\.))?"
    r")",
    re.IGNORECASE,
)
DISCUSSION_DEADLINE_RE = re.compile(
    r"(?:конец|окончание|завершение)\s+(?:публичного\s+)?обсуждени[яй]\s*[:\-]?\s*(?P<date>"
    r"\d{1,2}[./]\d{1,2}[./]\d{4}"
    r"|"
    r"\d{4}-\d{2}-\d{2}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:января|январь|февраля|февраль|марта|март|апреля|апрель|мая|май|июня|июнь|"
    r"июля|июль|августа|август|сентября|сентябрь|октября|октябрь|ноября|ноябрь|"
    r"декабря|декабрь)"
    r"\s+\d{4}(?:\s+г(?:ода|\.))?"
    r")",
    re.IGNORECASE,
)
PUBLICATION_OR_NPA_MARKERS = (
    "дата публикации",
    "опублик",
    "размещ",
    "постановление от",
    "приказ от",
    "распоряжение от",
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
    deadline_text = _extract_deadline_text(combined_text)
    application_status = _extract_application_status(
        lower_text,
        is_continuous=is_continuous,
        support_status=support_status,
        deadline_text=deadline_text,
    )
    npa_number = _extract_npa_number(combined_text)
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
    support_status: SupportStatus,
    deadline_text: str | None,
) -> ApplicationStatus:
    if any(marker in text for marker in CLOSED_MARKERS):
        return "closed"
    if support_status == "inactive":
        if is_continuous and deadline_text is None:
            return "regular"
        return "unknown"
    deadline_date = _extract_deadline_date(deadline_text)
    if any(marker in text for marker in EXPLICIT_OPEN_MARKERS):
        return "open"
    if deadline_date is not None and deadline_date >= _today_utc():
        return "open"
    if is_continuous and deadline_text is None:
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
    candidates: list[tuple[int, str]] = []
    for index, sentence in enumerate(sentences):
        normalized_sentence = _normalize_text(sentence)
        lowered = normalized_sentence.lower()
        candidate = _build_deadline_candidate(normalized_sentence, lowered)
        if candidate is not None:
            candidates.append(candidate)
            continue
        if (
            index > 0
            and lowered.startswith("до ")
            and _has_deadline_context(sentences[index - 1].lower())
        ):
            merged_sentence = _normalize_text(f"{sentences[index - 1]} {sentence}")
            merged_lowered = merged_sentence.lower()
            candidate = _build_deadline_candidate(merged_sentence, merged_lowered)
            if candidate is not None:
                candidates.append(candidate)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


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


def _build_deadline_candidate(sentence: str, lowered: str) -> tuple[int, str] | None:
    if not _has_deadline_context(lowered):
        return None
    if _is_excluded_deadline_sentence(lowered):
        return None

    deadline_match = None
    for match in DEADLINE_RANGE_RE.finditer(sentence):
        parsed_date = parse_russian_date(match.group("end"))
        if parsed_date is not None:
            deadline_match = match
    if deadline_match is None:
        for match in DEADLINE_DATE_RE.finditer(sentence):
            parsed_date = parse_russian_date(match.group("date"))
            if parsed_date is not None:
                deadline_match = match
    if deadline_match is None:
        for match in DISCUSSION_DEADLINE_RE.finditer(sentence):
            parsed_date = parse_russian_date(match.group("date"))
            if parsed_date is not None:
                deadline_match = match
    if deadline_match is None:
        return None

    priority_match = DEADLINE_PRIORITY_RE.search(sentence)
    if priority_match:
        start = priority_match.start()
    else:
        start = max(deadline_match.start() - 40, 0)
    end = min(deadline_match.end() + 30, len(sentence))
    snippet = _limit_snippet(sentence[start:end].strip(" ,.;:-"))
    score = _score_deadline_candidate(lowered, deadline_match.group(0))
    return score, snippet


def _has_deadline_context(text: str) -> bool:
    return any(marker in text for marker in DEADLINE_MARKERS) or any(
        marker in text for marker in EXPLICIT_OPEN_MARKERS
    )


def _is_excluded_deadline_sentence(text: str) -> bool:
    if any(
        excluded in text
        for excluded in (
            "срок кредита",
            "срок займа",
            "срок действия соглашения",
            "до 12 месяцев",
            "до 5 лет",
        )
    ):
        return True
    if "до 2030 года" in text and "прием" not in text and "подач" not in text and "обсужден" not in text:
        return True
    if any(marker in text for marker in PUBLICATION_OR_NPA_MARKERS) and not _has_deadline_context(text):
        return True
    return False


def _score_deadline_candidate(text: str, matched_date_text: str) -> int:
    score = 0
    if any(marker in text for marker in EXPLICIT_OPEN_MARKERS):
        score += 5
    if "срок подачи" in text:
        score += 4
    if "прием заявок" in text or "приём заявок" in text:
        score += 4
    if "заявки принимаются" in text:
        score += 4
    if "до " in matched_date_text.lower():
        score += 3
    if "по " in matched_date_text.lower():
        score += 2
    return score


def _extract_deadline_date(deadline_text: str | None) -> date | None:
    if not deadline_text:
        return None
    candidate_date: date | None = None
    for match in DEADLINE_RANGE_RE.finditer(deadline_text):
        parsed_date = parse_russian_date(match.group("end"))
        if parsed_date is not None:
            candidate_date = parsed_date
    for match in DEADLINE_DATE_RE.finditer(deadline_text):
        parsed_date = parse_russian_date(match.group("date"))
        if parsed_date is not None:
            candidate_date = parsed_date
    if candidate_date is not None:
        return candidate_date
    for match in DATE_TOKEN_RE.finditer(deadline_text):
        parsed_date = parse_russian_date(match.group(1))
        if parsed_date is not None:
            candidate_date = parsed_date
    return candidate_date


def _limit_snippet(text: str) -> str:
    if len(text) <= 180:
        return text
    return text[:177].rstrip(" ,.;:-") + "..."


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()
