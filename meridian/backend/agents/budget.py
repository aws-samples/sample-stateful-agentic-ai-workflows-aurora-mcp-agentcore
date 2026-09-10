"""Derive the hold budget ceiling the gateway policy compares against.

The ceiling is an integer number of cents because Cedar has no floating point
type. It comes from the traveler's saved budget fact in Aurora, read under RLS
by the backend before the runtime turn, so the model never chooses it.

Shared by the Phase 4 concierge and the Phase 5 workflow, and kept outside
the Phase 4 package so importing it does not load that agent stack.
"""

from __future__ import annotations

import os
import re

DEFAULT_ENV = "MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS"
DEFAULT_CENTS = 400000
# Alex's seeded facts carry budget_cap ("$3,200") and per_person_range
# ("Prefers $2k-3.5k per person"). The cap wins when both are present.
BUDGET_KEYS = ("budget_cap", "budget", "budget_ceiling", "budget_range", "per_person_range")
AMOUNT = re.compile(r"\$?\s*(\d+(?:\.\d+)?)\s*(k)?", re.I)


def _dollars(text: str) -> list[float]:
    values = []
    for number, thousands in AMOUNT.findall(text.replace(",", "")):
        value = float(number) * (1000 if thousands else 1)
        if value >= 100:
            values.append(value)
    return values


def budget_ceiling_from_facts(facts: list[dict], travelers: int) -> int:
    """Return the per-trip ceiling in cents from the traveler's saved budget fact.

    Args:
        facts: Aurora preference facts as ``{"key": ..., "value": ...}`` dicts.
        travelers: Party size; the saved budget is per person.

    Returns:
        The largest dollar figure in the budget fact, per person, times the party
        size, in cents. Falls back to ``MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS``.
    """
    by_key = {str(fact.get("key", "")).lower(): str(fact.get("value", "")) for fact in facts}
    for key in BUDGET_KEYS:
        amounts = _dollars(by_key.get(key, ""))
        if amounts:
            return int(round(max(amounts) * 100)) * max(1, int(travelers))
    return int(os.getenv(DEFAULT_ENV, str(DEFAULT_CENTS)))
