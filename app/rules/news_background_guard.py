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
    "рэнкинг",
    "индекс",
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
WEAK_MEETING_PATTERNS = (
    "совещан",
    "встреч",
    "заседан",
    "форум",
    "сесс",
    "обсуд",
    "переговор",
)
EVENT_ANNOUNCEMENT_PATTERNS = (
    "прямая трансляц",
    "трансляц",
    "регистрац",
    "анонс",
    "состоится",
    "пройдет",
    "пройдёт",
    "запланирован",
    "приглаша",
)
CONCRETE_POLICY_OUTCOME_PATTERNS = (
    "утверд",
    "принял",
    "принята",
    "принято",
    "внесен",
    "внесён",
    "внесли",
    "изменил",
    "изменен",
    "изменён",
    "запуст",
    "поруч",
    "решил",
    "решени",
    "постановлен",
    "приказ",
    "пошлин",
    "квот",
    "ограничен",
    "запрет",
    "субсид",
    "господдерж",
    "льготн",
    "компенсац",
    "регламент",
    "порядок",
    "правил",
)
WEAK_INFRASTRUCTURE_PATTERNS = (
    "мост",
    "путепровод",
    "дорог",
    "развяз",
    "железнодорож",
    "пассажир",
    "вокзал",
    "аэропорт",
    "инфраструктур",
    "реконструкц",
    "строительств",
)
GENERIC_EXPORT_PRICE_PATTERNS = (
    "цены",
    "стоимост",
    "котиров",
    "бирж",
    "подорож",
    "подешев",
    "вырос",
    "сниз",
    "обзор",
    "прогноз",
    "статист",
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
GOVERNMENT_ACTION_ENTITY_PATTERNS = (
    "правительств",
    "кабмин",
    "минсельхоз",
)
GOVERNMENT_ACTION_VERB_PATTERNS = (
    "утверд",
    "поруч",
    "измен",
    "запуст",
    "расшир",
    "ввел",
    "введ",
    "скоррект",
    "одобр",
)
WATCHLIST_VISIBLE_CONTEXT_PATTERNS = (
    "апк",
    "сельск",
    "аграр",
    "растениевод",
    "животновод",
    "продовольств",
    "зерн",
    "пшениц",
)
EXPORT_ACTION_PATTERNS = (
    "пошлин",
    "пошлина",
    "квот",
    "ограничен",
    "запрет",
    "тамож",
    "вывоз",
    "срок",
    "дедлайн",
    "поддержк",
    "субсид",
)
NEGATED_GR_SIGNAL_PATTERNS = (
    r"без(?:\s+\w+){0,4}\s+решени\w*\s+правительств",
    r"без(?:\s+\w+){0,4}\s+господдерж",
    r"без(?:\s+\w+){0,4}\s+субсид",
    r"без(?:\s+\w+){0,4}\s+пошлин",
    r"без(?:\s+\w+){0,4}\s+квот",
    r"без(?:\s+\w+){0,4}\s+ограничен",
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
PRELIMINARY_POLICY_VERB_MARKERS = (
    "рассматривает",
    "рассматривают",
    "рассматривается",
    "прорабатывает",
    "прорабатывают",
    "прорабатывается",
    "планирует",
    "планируют",
    "планируется",
    "обсуждает",
    "обсуждают",
    "обсуждается",
)
BINDING_POLICY_OUTCOME_MARKERS = (
    "утвердил",
    "утвердили",
    "утвержден",
    "утверждён",
    "принял",
    "приняли",
    "принято",
    "вступает в силу",
    "вступил в силу",
    "постановление",
    "приказ",
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
    if action_level in {"requires_attention", "watchlist"} and _should_downgrade_event_announcement(
        title=title,
        summary=summary,
        raw_text=raw_text,
        reason=reason,
        impact=impact,
        signal=signal,
    ):
        return "background"
    if action_level == "requires_attention" and _should_cap_preliminary_federal_export_duty_signal(
        title=title,
        summary=summary,
        raw_text=raw_text,
        reason=reason,
        impact=impact,
        signal=signal,
    ):
        return "watchlist"
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
    if _looks_event_announcement_without_outcome(signal_text):
        return True
    if any(re.search(pattern, signal_text) for pattern in NEGATED_GR_SIGNAL_PATTERNS):
        return any(pattern in noise_text for pattern in WATCHLIST_NOISE_PATTERNS)
    if _has_explicit_visible_gr_signal(signal_text):
        return False
    if _looks_generic_export_or_price_story(signal_text):
        return True
    if _looks_weak_meeting_or_infrastructure_story(signal_text):
        return True
    if any(pattern in signal_text for pattern in WATCHLIST_GR_SIGNAL_PATTERNS):
        return False
    return any(pattern in noise_text for pattern in WATCHLIST_NOISE_PATTERNS)


def _should_downgrade_event_announcement(
    *,
    title: str | None,
    summary: str | None,
    raw_text: str | None,
    reason: str | None,
    impact: str | None,
    signal: str | None,
) -> bool:
    text = " ".join(
        part.lower()
        for part in (
            title,
            summary,
            (raw_text or "")[:2000],
        )
        if part
    )
    return _looks_event_announcement_without_outcome(text)


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


def _should_cap_preliminary_federal_export_duty_signal(
    *,
    title: str | None,
    summary: str | None,
    raw_text: str | None,
    reason: str | None,
    impact: str | None,
    signal: str | None,
) -> bool:
    text = " ".join(
        part.lower()
        for part in (
            title,
            summary,
            reason,
            impact,
            signal,
            (raw_text or "")[:3000],
        )
        if part
    )
    if not text:
        return False
    if any(marker in text for marker in BINDING_POLICY_OUTCOME_MARKERS):
        return False
    if not any(marker in text for marker in PRELIMINARY_POLICY_VERB_MARKERS):
        return False
    has_federal_context = (
        "минсельхоз" in text
        or "сельского хозяйства рф" in text
        or "российской федерации" in text
        or " рф " in f" {text} "
        or "россии" in text
        or "российск" in text
    )
    if not has_federal_context:
        return False
    return all(
        any(marker in text for marker in marker_group)
        for marker_group in (
            ("экспортн", "экспорт"),
            ("пошлин",),
            ("зерн", "пшениц", "ячмен", "кукуруз"),
            ("скидк", "биржев", "организованн", "торг"),
        )
    )


def _has_explicit_visible_gr_signal(text: str) -> bool:
    if _has_strong_watchlist_gr_signal(text):
        return True
    has_visible_context = any(pattern in text for pattern in WATCHLIST_VISIBLE_CONTEXT_PATTERNS)
    has_export_action = any(pattern in text for pattern in EXPORT_ACTION_PATTERNS)
    return has_visible_context and has_export_action


def _looks_generic_export_or_price_story(text: str) -> bool:
    if "экспорт" not in text:
        return False
    if any(pattern in text for pattern in EXPORT_ACTION_PATTERNS):
        return False
    return any(pattern in text for pattern in GENERIC_EXPORT_PRICE_PATTERNS)


def _looks_weak_meeting_or_infrastructure_story(text: str) -> bool:
    has_weak_meeting = any(pattern in text for pattern in WEAK_MEETING_PATTERNS)
    has_weak_infra = any(pattern in text for pattern in WEAK_INFRASTRUCTURE_PATTERNS)
    if not has_weak_meeting and not has_weak_infra:
        return False
    if any(pattern in text for pattern in WATCHLIST_VISIBLE_CONTEXT_PATTERNS):
        return False
    if any(pattern in text for pattern in WATCHLIST_GR_SIGNAL_PATTERNS):
        return False
    return True


def _looks_event_announcement_without_outcome(text: str) -> bool:
    if not any(pattern in text for pattern in EVENT_ANNOUNCEMENT_PATTERNS):
        return False
    return not any(pattern in text for pattern in CONCRETE_POLICY_OUTCOME_PATTERNS)


def _has_strong_watchlist_gr_signal(text: str) -> bool:
    if any(pattern in text for pattern in WATCHLIST_GR_SIGNAL_PATTERNS):
        return True
    has_entity = any(pattern in text for pattern in GOVERNMENT_ACTION_ENTITY_PATTERNS)
    has_action_verb = any(pattern in text for pattern in GOVERNMENT_ACTION_VERB_PATTERNS)
    return has_entity and has_action_verb
