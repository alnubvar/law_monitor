from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import urlparse


def extract_domain(source_name: str | None, url: str | None) -> str:
    combined = f"{source_name or ''} {url or ''}".lower()
    for candidate in (
        "mcx.donland.ru",
        "admkrai.krasnodar.ru",
        "msh.krasnodar.ru",
        "mshsk.ru",
        "gisp.gov.ru",
        "zol.ru",
    ):
        if candidate in combined:
            return candidate
    parsed = urlparse(url or "")
    return parsed.netloc.lower()


def matches_keyword_groups(
    text: str,
    group_names: Sequence[str],
    keyword_groups: Mapping[str, Sequence[str]],
) -> bool:
    for group_name in group_names:
        keywords = keyword_groups.get(group_name, [])
        if any(keyword.lower() in text for keyword in keywords):
            return True
    return False
