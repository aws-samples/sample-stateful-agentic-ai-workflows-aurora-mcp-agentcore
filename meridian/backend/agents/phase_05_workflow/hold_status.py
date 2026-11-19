"""The one rule for the status a traveler is told about a hold."""

from __future__ import annotations

from typing import Optional


def effective_hold_status(status: Optional[str], *, lapsed: bool) -> Optional[str]:
    """The status a traveler is told: a stored ``held`` whose expiry has passed is ``expired``.

    Reconciliation, the final read, synthesis and the journey document all use this rule, so
    what they report agrees with confirmation, which refuses a lapsed hold.
    """
    if status == "held" and lapsed:
        return "expired"
    return status
