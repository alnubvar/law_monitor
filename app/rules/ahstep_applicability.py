from __future__ import annotations

from dataclasses import dataclass
import re

from app import config
from app.models import SourceRole

TARGET_REGION_CODES = {
    "federal": ("россия", "рф", "федеральный", "федеральная", "российская федерация"),
    "rostov": ("ростов", "ростовская область", "ростовской области", "донланд"),
    "krasnodar": ("краснодар", "краснодарский край", "краснодарского края", "кубань", "кубани"),
    "stavropol": ("ставрополь", "ставропольский край", "ставропольского края"),
    "moscow_oblast": ("московская область", "московской области", "подмосковье"),
}
FEDERAL_SCOPE_MARKERS = (
    "российская федерация",
    "российской федерации",
    "министерство сельского хозяйства российской федерации",
    "минсельхоз россии",
    "правительство российской федерации",
    "федеральный бюджет",
    "федерального бюджета",
    "бюджетам субъектов рф",
    "субъектам российской федерации",
    "по всей россии",
    "на территории российской федерации",
)
NON_TARGET_REGION_MARKERS = (
    "ненецкий автономный округ",
    "ненецкого автономного округа",
    "нао",
    "республика бурятия",
    "республики бурятия",
    "бурятия",
    "бурятии",
    "калужская область",
    "калужской области",
    "калуга",
)
LOW_RELEVANCE_SUPPORT_MARKERS = (
    "северные олени",
    "северных оленей",
    "оленеводство",
    "оленеводства",
    "коренные малочисленные народы",
    "коренных малочисленных народов",
    "кмнс",
    "семейные родовые общины",
    "семейным родовым общинам",
    "семейные (родовые) общины",
    "семейным (родовым) общинам",
    "ненецкий автономный округ",
    "ненецкого автономного округа",
)
REGIONAL_AUTHORITY_RE = re.compile(
    r"\b(?:министерство|департамент|комитет|управление|администрация)\b.{0,260}?"
    r"\b(?:области|края|республики|автономного округа)\b",
    re.IGNORECASE,
)
FEDERAL_AUTHORITY_RE = re.compile(
    r"\b(?:российской федерации|рф|минсельхоз россии|правительство россии)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ApplicabilityDecision:
    is_applicable: bool
    recommended_action_level: str | None = None
    reason: str = ""
    detected_region: str = ""
    is_non_target_region: bool = False
    has_low_relevance_signal: bool = False
    has_federal_scope: bool = False


def target_region_values() -> tuple[str, ...]:
    return tuple(config.AHSTEP_TARGET_REGIONS)


def is_configured_target_region(value: str | None) -> bool:
    normalized = normalize_region_text(value)
    if not normalized:
        return False
    return any(
        normalized == marker or normalized in _target_region_marker_set()
        for marker in _target_region_marker_set()
    )


def evaluate_support_measure_applicability(
    *,
    title: str,
    raw_text: str,
    source_name: str | None,
    url: str | None,
    level: str | None,
    region: str | None,
    source_role: SourceRole | None,
    page_type: str | None,
) -> ApplicabilityDecision:
    if not is_support_measure_surface(
        source_name=source_name,
        url=url,
        level=level,
        source_role=source_role,
        page_type=page_type,
    ):
        return ApplicabilityDecision(is_applicable=True)

    combined = normalize_region_text(
        " ".join(
            part
            for part in (
                title,
                raw_text[:4000],
                source_name or "",
                url or "",
                level or "",
                region or "",
            )
            if part
        )
    )
    non_target_region = _detect_non_target_region(combined)
    target_match = _target_region_match(combined, region=region)
    if non_target_region and target_match == "federal":
        target_match = ""
    federal_scope = _has_federal_scope(combined, region=region)
    has_low_signal = any(marker in combined for marker in LOW_RELEVANCE_SUPPORT_MARKERS)

    if non_target_region and not target_match:
        return ApplicabilityDecision(
            is_applicable=False,
            recommended_action_level="irrelevant" if has_low_signal else "background",
            reason="Регион вне фокуса AHSTEP; документ сохранён справочно.",
            detected_region=non_target_region,
            is_non_target_region=True,
            has_low_relevance_signal=has_low_signal,
            has_federal_scope=federal_scope,
        )
    if has_low_signal and not target_match:
        return ApplicabilityDecision(
            is_applicable=False,
            recommended_action_level="irrelevant",
            reason="Тематически нерелевантная региональная мера; документ сохранён справочно.",
            detected_region=non_target_region,
            is_non_target_region=bool(non_target_region),
            has_low_relevance_signal=True,
            has_federal_scope=federal_scope,
        )
    if target_match or federal_scope:
        return ApplicabilityDecision(
            is_applicable=True,
            detected_region=target_match,
            has_federal_scope=federal_scope,
        )
    if _looks_like_regional_authority(combined):
        return ApplicabilityDecision(
            is_applicable=False,
            recommended_action_level="background",
            reason="Регион вне фокуса AHSTEP; документ сохранён справочно.",
            detected_region=non_target_region,
            is_non_target_region=True,
            has_low_relevance_signal=has_low_signal,
            has_federal_scope=federal_scope,
        )
    return ApplicabilityDecision(is_applicable=True, has_federal_scope=federal_scope)


def is_support_measure_surface(
    *,
    source_name: str | None,
    url: str | None,
    level: str | None,
    source_role: SourceRole | None,
    page_type: str | None,
) -> bool:
    combined = f"{source_name or ''} {url or ''} {level or ''}".lower()
    if source_role in {"active_support_measures", "support_documents"}:
        return True
    if "promote.budget.gov.ru" in combined:
        return True
    if level == "support_measures":
        return True
    return (page_type or "") in {
        "measure_card",
        "selection_announcement",
        "deadline_update",
    } and ("субсид" in combined or "господдерж" in combined)


def normalize_region_text(value: object) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я]+", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _target_region_marker_set() -> frozenset[str]:
    values: set[str] = set()
    for raw_value in config.AHSTEP_TARGET_REGIONS:
        normalized = normalize_region_text(raw_value)
        if not normalized:
            continue
        values.add(normalized)
        for code, markers in TARGET_REGION_CODES.items():
            if normalized == code:
                values.update(markers)
            elif normalized in markers:
                values.update(markers)
    return frozenset(values)


def _target_region_match(combined: str, *, region: str | None) -> str:
    normalized_region = normalize_region_text(region)
    if normalized_region:
        for code, markers in TARGET_REGION_CODES.items():
            if code == "federal":
                continue
            if normalized_region == code and any(
                normalize_region_text(marker) in _target_region_marker_set()
                for marker in markers
            ):
                return code
    for marker in sorted(_target_region_marker_set(), key=len, reverse=True):
        if marker and marker in combined:
            return marker
    return ""


def _has_federal_scope(combined: str, *, region: str | None) -> bool:
    if _looks_like_regional_authority(combined):
        return False
    if normalize_region_text(region) == "federal":
        return True
    return any(marker in combined for marker in FEDERAL_SCOPE_MARKERS)


def _detect_non_target_region(combined: str) -> str:
    for marker in NON_TARGET_REGION_MARKERS:
        if marker in combined:
            return marker
    if _looks_like_regional_authority(combined):
        return "региональная мера вне целевой географии"
    return ""


def _looks_like_regional_authority(combined: str) -> bool:
    if FEDERAL_AUTHORITY_RE.search(combined):
        return False
    return bool(REGIONAL_AUTHORITY_RE.search(combined))
