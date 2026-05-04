from __future__ import annotations

import re

GENERIC_TITLES = {
    "просмотр",
    "скачать",
    "документ",
    "pdf",
    "файл",
}
TITLE_SIGNAL_PATTERNS = (
    r"о\s+внесении\s+изменений[^.!\n]{0,220}",
    r"об\s+утверждении[^.!\n]{0,220}",
    r"о\s+предоставлении[^.!\n]{0,220}",
    r"о\s+порядке[^.!\n]{0,220}",
    r"об\s+установлении[^.!\n]{0,220}",
    r"о\s+распределении[^.!\n]{0,220}",
    r"о\s+реализации[^.!\n]{0,220}",
    r"о\s+субсиди[^.!\n]{0,220}",
    r"о\s+льготн[^.!\n]{0,220}",
    r"об\s+экспортн[^.!\n]{0,220}",
)
AGENCY_LINE_MARKERS = (
    "министерств",
    "правительств",
    "департамент",
    "администраци",
    "губернат",
    "управлен",
    "служб",
)


def normalize_document_title(title: str, *, raw_text: str, summary: str | None = None) -> str:
    normalized_title = _normalize_space(title)
    if not _looks_generic_title(normalized_title):
        return normalized_title

    extracted_title = _extract_title_candidate(raw_text, summary)
    if extracted_title:
        return extracted_title
    return normalized_title


def _looks_generic_title(title: str) -> bool:
    lowered = _normalize_space(title).lower().strip(" .:-")
    if lowered in GENERIC_TITLES:
        return True
    if len(lowered) <= 6:
        return True
    return bool(re.fullmatch(r"(pdf|docx?|файл|документ)\s*\d*", lowered))


def _extract_title_candidate(raw_text: str, summary: str | None) -> str | None:
    combined = "\n".join(part for part in (summary or "", raw_text) if part).strip()
    if not combined:
        return None

    for pattern in TITLE_SIGNAL_PATTERNS:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            return _finalize_title(match.group(0))

    lines = [_normalize_space(line) for line in combined.splitlines()]
    meaningful_lines = [
        line
        for line in lines
        if _is_meaningful_line(line)
    ]
    for line in meaningful_lines[:8]:
        cleaned_line = _strip_agency_prefix(line)
        if _looks_like_title_candidate(cleaned_line):
            return _finalize_title(cleaned_line)
    return None


def _is_meaningful_line(line: str) -> bool:
    if len(line) < 12:
        return False
    lowered = line.lower()
    if lowered in GENERIC_TITLES:
        return False
    return True


def _strip_agency_prefix(text: str) -> str:
    parts = re.split(r"(?<=[.:])\s+", text, maxsplit=2)
    for part in parts:
        if _looks_like_title_candidate(part):
            return part
    return text


def _looks_like_title_candidate(text: str) -> bool:
    lowered = text.lower()
    if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in TITLE_SIGNAL_PATTERNS):
        return True
    if any(marker in lowered for marker in AGENCY_LINE_MARKERS):
        return False
    return (
        len(text) >= 18
        and not re.fullmatch(r"[A-ZА-ЯЁ\s\"()0-9№-]+", text)
    )


def _finalize_title(text: str) -> str:
    cleaned = _normalize_space(text).strip(" .;:-")
    if _is_mostly_upper(cleaned):
        cleaned = cleaned.lower().capitalize()
    if len(cleaned) > 180:
        cleaned = cleaned[:177].rstrip(" ,.;:-") + "..."
    return cleaned


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _is_mostly_upper(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    if len(letters) < 10:
        return False
    upper_count = sum(1 for char in letters if char.isupper())
    return upper_count / len(letters) > 0.8
