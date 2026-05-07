from __future__ import annotations

from collections.abc import Iterable
import re

from app.models import SourceRole

MARKET_BACKGROUND_SIGNAL = "Рыночный или отраслевой фон без прямого регуляторного сигнала."
WATCHLIST_NOISE_PATTERNS = (
    "обзор рынка",
    "аналитик",
    "аналитика",
    "рейтинг",
    "фрахт",
    "фрахтов",
    "отгруз",
    "поставк",
    "экспорт вырос",
    "экспорт сниз",
    "рекордн",
    "совэкон",
    "русагротранс",
    "урож",
    "посевн",
    "посевн",
    "полев",
    "сев",
    "цены на зерно",
    "зерновой рынок",
    "погодн",
)
WATCHLIST_GR_SIGNAL_PATTERNS = (
    "господдерж",
    "субсид",
    "пошлин",
    "квот",
    "ограничен",
    "запрет",
    "нетариф",
    "изменены правила",
    "изменение правил",
    "изменения правил",
    "изменение порядка",
    "изменения порядка",
    "правительств",
    "кабмин",
    "минсельхоз",
    "поручени",
    "утверд",
    "запуст",
    "программ",
    "меры поддержки",
    "компенсац",
    "финансирован",
    "возмещени",
    "отбор",
    "прием заяв",
    "приём заяв",
    "заявок",
    "срок подач",
    "дедлайн",
)
NEGATED_GR_SIGNAL_PATTERNS = (
    r"без(?:\s+\w+){0,4}\s+решени\w*\s+правительств",
    r"без(?:\s+\w+){0,4}\s+господдерж",
    r"без(?:\s+\w+){0,4}\s+субсид",
    r"без(?:\s+\w+){0,4}\s+регулятор",
    r"без(?:\s+\w+){0,4}\s+регулир",
)
FOREIGN_COUNTRY_MARKERS = (
    "турци",
    "турецк",
    "алжир",
    "егип",
    "индонез",
    "индия",
    "пакистан",
    "саудов",
    "иран",
    "китай",
    "бразил",
    "аргентин",
    "сша",
    "америк",
    "евросоюз",
    "ес ",
)
FOREIGN_TRADE_MARKERS = (
    "квот",
    "пошлин",
    "импорт",
    "экспорт",
    "ввоз",
    "вывоз",
    "ограничен",
    "запрет",
    "поставк",
)
RF_RELEVANCE_MARKERS = (
    "росси",
    "рф",
    "еаэс",
    "евразий",
    "минсельхоз росс",
    "правительство рф",
    "российск",
    "экспорт из россии",
    "импорт в россию",
    "российских компани",
    "российские компании",
)


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
    title: str | None = None,
    summary: str | None = None,
    raw_text: str | None = None,
    page_type: str | None = None,
) -> str | None:
    if source_role != "news_signals":
        return action_level
    if action_level == "requires_attention" and _should_downgrade_foreign_trade_signal(
        title=title,
        summary=summary,
        raw_text=raw_text,
        reason=reason,
        impact=impact,
        signal=signal,
    ):
        return "background"
    if not contains_market_background_signal((reason, impact, signal)):
        if action_level == "watchlist" and _should_downgrade_watchlist_noise(
            title=title,
            summary=summary,
            raw_text=raw_text,
            reason=reason,
            impact=impact,
            signal=signal,
            page_type=page_type,
        ):
            return "background"
        return action_level
    if action_level in {"requires_attention", "watchlist"}:
        return "background"
    return action_level


def _should_downgrade_watchlist_noise(
    *,
    title: str | None,
    summary: str | None,
    raw_text: str | None,
    reason: str | None,
    impact: str | None,
    signal: str | None,
    page_type: str | None,
) -> bool:
    if page_type not in {None, "news_background"}:
        return False

    signal_text = " ".join(
        part.lower()
        for part in (
            title,
            summary,
            reason,
            impact,
            (raw_text or "")[:2000],
        )
        if part
    )
    noise_text = " ".join(
        part.lower()
        for part in (
            signal_text,
            signal,
        )
        if part
    )
    if not noise_text:
        return False
    if any(re.search(pattern, signal_text) for pattern in NEGATED_GR_SIGNAL_PATTERNS):
        return any(pattern in noise_text for pattern in WATCHLIST_NOISE_PATTERNS)
    if any(pattern in signal_text for pattern in WATCHLIST_GR_SIGNAL_PATTERNS):
        return False
    return any(pattern in noise_text for pattern in WATCHLIST_NOISE_PATTERNS)


def _should_downgrade_foreign_trade_signal(
    *,
    title: str | None,
    summary: str | None,
    raw_text: str | None,
    reason: str | None,
    impact: str | None,
    signal: str | None,
) -> bool:
    combined_text = " ".join(
        part.lower()
        for part in (
            title,
            summary,
            reason,
            impact,
            signal,
            (raw_text or "")[:2000],
        )
        if part
    )
    if not combined_text:
        return False
    if not any(marker in combined_text for marker in FOREIGN_COUNTRY_MARKERS):
        return False
    if not any(marker in combined_text for marker in FOREIGN_TRADE_MARKERS):
        return False
    if any(marker in combined_text for marker in RF_RELEVANCE_MARKERS):
        return False
    return True
