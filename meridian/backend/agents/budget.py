"""Derive the hold budget ceiling the gateway policy compares against.

The ceiling is an integer number of cents because Cedar has no floating point
type. It comes from the traveler's saved budget fact in Aurora, read under RLS
by the backend before the runtime turn, so the model never chooses it.

Shared by the Phase 4 concierge and the Phase 5 workflow, and kept outside
the Phase 4 package so importing it does not load that agent stack.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping

DEFAULT_ENV = "MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS"
DEFAULT_CENTS = 400000
# Jordan's seeded facts carry budget_cap ("$3,200") and per_person_range
# ("Prefers $2k-3.5k per person"). The first key present in this order decides; a lower
# key never overrides it, even when the first one cannot be read.
BUDGET_KEYS = ("budget_cap", "budget", "budget_ceiling", "budget_range", "per_person_range")
NUMBER = re.compile(r"(\d+(?:\.\d+)?)(k\b)?", re.I)
THOUSANDS_COMMA = re.compile(r"(?<=\d),(?=\d{3}\b)")
US_PREFIX = re.compile(r"(?<![A-Za-z])US\s?\$", re.I)
# Another currency, written as a symbol, an ISO code or a word, makes a budget unclear.
# "won" and "real" are also English words, so they count only straight after a figure.
OTHER_CURRENCY = re.compile(
    r"[A-Za-z]\$|[\u20a0-\u20bf\u00a2-\u00a5]"
    r"|\b(?:EUR|GBP|JPY|CNY|CAD|AUD|NZD|CHF|INR|MXN|BRL|SGD|HKD|KRW|SEK|NOK|DKK|AED|ZAR"
    r"|PLN|THB|TWD|CZK|HUF|ILS|IDR|MYR|VND|PHP)\b"
    r"|\b(?:euros?|pounds?|sterling|yen|yuan|renminbi|rupees?|rupiah|pesos?|baht|francs?"
    r"|reais|rand|liras?|lire|krona|kronor|krone|kroner|kronur|zlotys?|zloty|forints?"
    r"|shekels?|dirhams?|ringgit|dong|rubles?|roubles?|dinars?|riyals?|rials?"
    r"|(?:canadian|australian|new zealand|hong kong|singapore|singaporean|taiwan|taiwanese"
    r"|mexican|nz|hk)\s+dollars?)\b"
    r"|(?:(?<=\d)|(?<=\dk))\s*(?:won|reals?)\b",
    re.I,
)
# ".5k" is not read as 5k, and nothing is read from a figure that no budget could be.
LEADING_DOT = re.compile(r"(?<!\d)\.\d")
MAX_BUDGET_USD = 10_000_000
USD_SUFFIX = re.compile(r"\s*USD\b", re.I)
USD_BEFORE = re.compile(r"(?<![A-Za-z])USD\s*$", re.I)
RANGE_JOIN = re.compile(
    r"\s*(?:USD)?\s*(?:-|\u2013|\u2014|to)\s*\$?\s*(?:USD)?\s*", re.I
)
# A saved budget is one dollar amount or one range: marked amounts ($, US$, USD) win over bare
# figures; a single bare figure or range counts as dollars. Anything else is unclear.

def default_budget_cents(env: Mapping[str, str | None] | None = None) -> int:
    """Validate the whole-trip default shared by release configuration and callers."""
    value = (os.environ if env is None else env).get(DEFAULT_ENV, str(DEFAULT_CENTS))
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise ValueError(f"{DEFAULT_ENV} must be a positive integer number of cents")
    ceiling = int(value)
    if not 0 < ceiling <= 2**53 - 1:
        raise ValueError(f"{DEFAULT_ENV} must be between 1 and {2**53 - 1} cents")
    return ceiling


def _is_marked(text: str, match: re.Match) -> bool:
    """Whether a figure carries ``$`` or ``USD`` before or after it."""
    before = text[:match.start()]
    return (
        before.rstrip().endswith("$")
        or USD_BEFORE.search(before) is not None
        or USD_SUFFIX.match(text, match.end()) is not None
    )


def _spread_marks(marked: list[bool], joined: list[bool]) -> list[bool]:
    """A figure joined to a marked one is marked too: the later figure of ``$100 to 200``."""
    spread = list(marked)
    for i in range(1, len(spread)):
        spread[i] = spread[i] or (joined[i - 1] and spread[i - 1])
    for i in reversed(range(len(spread) - 1)):
        spread[i] = spread[i] or (joined[i] and spread[i + 1])
    return spread


def parse_usd_amounts(text: str) -> list[float] | None:
    """The one US dollar amount or one joined range in ``text``, or None when it is not clear.

    Figures that carry ``$``, ``US$`` or ``USD`` (before or after) are the only ones used when
    any exist. Otherwise a single bare figure, or a single bare joined range, counts as dollars.
    A range is two figures joined by ``-``, an en dash or ``to``. A ``k`` suffix means thousands.
    More than one amount or range, a figure of zero, another currency (a symbol, an ISO code
    or a word such as ``yen``), a figure written ``.5k``, a figure above ``MAX_BUDGET_USD`` or
    one too large to be finite, or no figure is None.

    Returns:
        ``[amount]`` or ``[low, high]`` in dollars, or None.
    """
    text = US_PREFIX.sub("$", THOUSANDS_COMMA.sub("", text))
    found = list(NUMBER.finditer(text))
    if not found or OTHER_CURRENCY.search(text) or LEADING_DOT.search(text):
        return None
    joined = [
        RANGE_JOIN.fullmatch(text[a.end():b.start()]) is not None
        for a, b in zip(found, found[1:], strict=False)
    ]
    marked = [_is_marked(text, match) for match in found]
    chosen = _spread_marks(marked, joined) if any(marked) else [True] * len(found)
    picked = [i for i, keep in enumerate(chosen) if keep]
    amounts = [
        float(found[i].group(1)) * (1000 if found[i].group(2) else 1) for i in picked
    ]
    one_range = len(picked) == 2 and picked[1] == picked[0] + 1 and joined[picked[0]]
    if min(amounts) <= 0 or not (len(picked) == 1 or one_range):
        return None
    if not all(math.isfinite(amount) and amount <= MAX_BUDGET_USD for amount in amounts):
        return None
    return amounts


def _first_budget_text(facts: list[dict]) -> str | None:
    """The value of the highest-priority budget fact present, or None when there is none."""
    by_key: dict[str, str] = {}
    for fact in facts:
        by_key.setdefault(str(fact.get("key", "")).lower(), str(fact.get("value", "")))
    return next((by_key[key] for key in BUDGET_KEYS if key in by_key), None)


def _cap_cents(text: str) -> int | None:
    """The largest US dollar figure in ``text`` in cents, or None when it is not clear.

    A non-finite figure is not clear, so no caller can raise on it.
    """
    amounts = parse_usd_amounts(text)
    if not amounts or not math.isfinite(max(amounts)):
        return None
    return int(round(max(amounts) * 100))


def budget_is_unclear(facts: list[dict]) -> bool:
    """Whether the first budget fact present has no unambiguous US dollar amount.

    A lower-priority fact or the default never stands in for it.
    """
    text = _first_budget_text(facts)
    return text is not None and _cap_cents(text) is None


def budget_ceiling_per_traveler_cents(facts: list[dict]) -> int | None:
    """Return the saved per-traveler cap in cents, or None when no cap can be read.

    This is the figure the traveler actually saved. The party ceiling the policy
    judges is this times the party size, so any surface that shows a budget can
    show the same basis the gateway used instead of a second, unrelated number.

    Args:
        facts: Aurora preference facts as ``{"key": ..., "value": ...}`` dicts, highest
            confidence first. When rows share a key, the first one wins.

    Returns:
        The largest US dollar figure in the first budget fact present, in cents. ``None`` when
        the traveler has saved no budget fact, or when the first one has no unambiguous US
        dollar amount; :func:`budget_is_unclear` tells the two apart.
    """
    text = _first_budget_text(facts)
    return _cap_cents(text) if text is not None else None


def budget_ceiling_from_facts(facts: list[dict], travelers: int) -> int:
    """Return the per-trip ceiling in cents from the traveler's saved budget fact.

    Args:
        facts: Aurora preference facts as ``{"key": ..., "value": ...}`` dicts.
        travelers: Party size; the saved budget is per person.

    Returns:
        The largest US dollar figure in the budget fact, per person, times the party size,
        in cents. ``0`` when the first budget fact present is unclear: every priced hold
        is above it, so the gateway policy authorizes nothing. Falls back to
        ``MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS``, a whole-trip default that is not multiplied
        by the party, only when no budget fact is saved at all.
    """
    if budget_is_unclear(facts):
        return 0
    per_traveler = budget_ceiling_per_traveler_cents(facts)
    if per_traveler is not None:
        return per_traveler * max(1, int(travelers))
    return default_budget_cents()
