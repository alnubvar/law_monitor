from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Mapping, Sequence

from app.config import load_gr_topic_families

SPECIFIC_FAMILY_ORDER = (
    "postanovlenie_1528",
    "regional_subsidy_distribution",
    "direct_indirect_subsidies",
    "grain_compensation",
    "processing_modernization",
    "concessional_credit",
    "export_support",
    "dairy",
    "elite_seed",
    "subsidies",
    "support_general",
)


@dataclass(frozen=True)
class GRTopicMatch:
    family: str
    label: str
    matched_markers: tuple[str, ...]
    confidence: str
    risk: str


def detect_gr_topic(*parts: object) -> GRTopicMatch | None:
    text = normalize_ontology_text(*parts)
    if not text:
        return None
    families = load_gr_topic_families()
    for family_name in _ordered_family_names(families):
        raw_config = families.get(family_name)
        if not isinstance(raw_config, Mapping):
            continue
        match = _match_family(family_name, raw_config, text)
        if match is not None:
            return match
    return None


def detect_gr_topic_family(*parts: object) -> str | None:
    match = detect_gr_topic(*parts)
    return match.family if match is not None else None


def gr_topic_label(family: str | None) -> str:
    if not family:
        return ""
    config = load_gr_topic_families().get(family, {})
    label = config.get("label") if isinstance(config, Mapping) else None
    return str(label or family).strip()


def normalize_ontology_text(*parts: object) -> str:
    values = []
    for part in parts:
        if part is None:
            continue
        value = str(part).strip()
        if value:
            values.append(value)
    text = " ".join(values).lower().replace("ё", "е")
    text = re.sub(r"[№#]", " ", text)
    text = re.sub(r"[^0-9a-zа-я]+", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _ordered_family_names(
    families: Mapping[str, Mapping[str, object]],
) -> tuple[str, ...]:
    ordered = [name for name in SPECIFIC_FAMILY_ORDER if name in families]
    ordered.extend(name for name in families if name not in ordered)
    return tuple(ordered)


def _match_family(
    family_name: str,
    raw_config: Mapping[str, object],
    text: str,
) -> GRTopicMatch | None:
    if _has_any_marker(text, _sequence(raw_config.get("exclude_any"))):
        return None

    exact_hits = _matching_markers(text, _sequence(raw_config.get("exact")))
    if exact_hits:
        return _build_match(
            family_name,
            raw_config,
            exact_hits,
            confidence="high",
        )

    for raw_rule in _sequence(raw_config.get("rules")):
        if not isinstance(raw_rule, Mapping):
            continue
        rule_hits = _match_rule(text, raw_rule)
        if rule_hits:
            risk = str(raw_config.get("risk") or "medium")
            return _build_match(
                family_name,
                raw_config,
                rule_hits,
                confidence="high" if risk == "low" else "medium",
            )
    return None


def _match_rule(text: str, rule: Mapping[str, object]) -> tuple[str, ...]:
    if _has_any_marker(text, _sequence(rule.get("exclude_any"))):
        return ()

    all_markers = _sequence(rule.get("all"))
    any_markers = _sequence(rule.get("any"))
    context_markers = _sequence(rule.get("context_any"))

    if all_markers and not all(_contains_marker(text, marker) for marker in all_markers):
        return ()
    any_hits = _matching_markers(text, any_markers)
    if any_markers and not any_hits:
        return ()
    context_hits = _matching_markers(text, context_markers)
    if context_markers and not context_hits:
        return ()

    all_hits = _matching_markers(text, all_markers)
    hits = (*all_hits, *any_hits, *context_hits)
    return tuple(dict.fromkeys(hits))


def _build_match(
    family_name: str,
    raw_config: Mapping[str, object],
    matched_markers: Sequence[str],
    *,
    confidence: str,
) -> GRTopicMatch:
    return GRTopicMatch(
        family=family_name,
        label=str(raw_config.get("label") or family_name),
        matched_markers=tuple(dict.fromkeys(matched_markers)),
        confidence=confidence,
        risk=str(raw_config.get("risk") or "medium"),
    )


def _matching_markers(text: str, markers: Sequence[object]) -> tuple[str, ...]:
    hits = []
    for marker in markers:
        normalized = normalize_ontology_text(marker)
        if normalized and _contains_marker(text, normalized):
            hits.append(normalized)
    return tuple(hits)


def _has_any_marker(text: str, markers: Sequence[object]) -> bool:
    return bool(_matching_markers(text, markers))


def _contains_marker(text: str, marker: str) -> bool:
    normalized_marker = normalize_ontology_text(marker)
    if not normalized_marker:
        return False
    if " " in normalized_marker:
        return normalized_marker in text
    if normalized_marker in {"апк", "крс"} or len(normalized_marker) <= 3:
        return f" {normalized_marker} " in f" {text} "
    return normalized_marker in text


def _sequence(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(value)
    return ()
