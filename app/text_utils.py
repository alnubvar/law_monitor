from __future__ import annotations

import re

ELLIPSIS = "..."


def normalize_visible_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def safe_truncate_text(value: object, max_chars: int, *, ellipsis: str = ELLIPSIS) -> str:
    """Shorten user-facing text without cutting words in half."""
    normalized = normalize_visible_text(value)
    if max_chars <= 0 or len(normalized) <= max_chars:
        return normalized
    if max_chars <= len(ellipsis):
        return ellipsis[:max_chars]

    content_limit = max_chars - len(ellipsis)
    candidate = normalized[:content_limit].rstrip()
    if not candidate:
        return ellipsis

    sentence_cut = _last_sentence_boundary(candidate)
    if sentence_cut >= max(content_limit // 2, 40):
        candidate = candidate[:sentence_cut]
    else:
        whitespace_cut = candidate.rfind(" ")
        if whitespace_cut >= max(content_limit // 3, 20):
            candidate = candidate[:whitespace_cut]
        else:
            candidate = _trim_partial_word(candidate)

    candidate = candidate.rstrip(" \t\r\n,;:-.!?")
    if not candidate:
        candidate = _trim_partial_word(normalized[:content_limit]).rstrip(" ,;:-.!?")
    return f"{candidate}{ellipsis}" if candidate else ellipsis


def _last_sentence_boundary(text: str) -> int:
    best = -1
    for match in re.finditer(r"[.!?](?:[\"')\]]|[»”])?(?=\s|$)", text):
        best = match.end()
    return best


def _trim_partial_word(text: str) -> str:
    match = re.search(r"[\wА-Яа-яЁё]+$", text, flags=re.IGNORECASE)
    if not match:
        return text
    return text[: match.start()]
