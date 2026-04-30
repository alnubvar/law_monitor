from __future__ import annotations

import hashlib


def compute_content_hash(raw_text: str, fallback: str = "") -> str:
    source = raw_text.strip() or fallback.strip()
    return hashlib.sha256(source.encode("utf-8")).hexdigest()

