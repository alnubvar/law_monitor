from __future__ import annotations

from collections.abc import Iterable

from app.models import SourceRole

MARKET_BACKGROUND_SIGNAL = "Рыночный или отраслевой фон без прямого регуляторного сигнала."


def contains_market_background_signal(values: Iterable[str | None]) -> bool:
    marker = MARKET_BACKGROUND_SIGNAL.lower()
    return any(marker in (value or "").lower() for value in values)


def guard_news_signal_action_level(
    action_level: str | None,
    *,
    source_role: SourceRole | None,
    reason: str | None = None,
    impact: str | None = None,
    signal: str | None = None,
) -> str | None:
    if source_role != "news_signals":
        return action_level
    if not contains_market_background_signal((reason, impact, signal)):
        return action_level
    if action_level == "requires_attention":
        return "background"
    return action_level
