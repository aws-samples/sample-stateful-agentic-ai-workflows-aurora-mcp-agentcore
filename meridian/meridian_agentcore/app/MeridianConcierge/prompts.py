"""Prompts for the Meridian concierge runtime.

The tool guidance and the governed-write rules live in the shared definition
(backend/agents/concierge_definition.py, copied here by scripts/stage_concierge_runtime.py).
This module adds what only the Runtime knows: whether the turn carries a write the traveler
confirmed.
"""

from __future__ import annotations

import json

from concierge_definition import system_prompt as shared_prompt

UNCONFIRMED = (
    "The traveler has not confirmed a hold or a booking on this turn. If they ask you to hold "
    "or book, you may attempt create_courtesy_hold or confirm_booking so the gateway policy "
    "decides, and you must then tell the traveler exactly why it was refused and that the Hold "
    "button on a trip, or the Confirm button on a held trip, is what confirms it."
)
CONFIRMED = (
    "The traveler has confirmed a hold on this turn with the Hold button, and the platform "
    "has already placed that exact call through the gateway; the gateway's decision for this "
    "turn is in the message below. You have no tools on this turn. Report the outcome to the "
    "traveler in two or three sentences: the hold id, expiry and remaining places when it was "
    "placed, or the exact reason and what would make the hold possible when it was refused. "
    "Earlier turns in this conversation do not change this turn's outcome."
)
BOOKING_CONFIRMED = (
    "The traveler has confirmed the booking of a held package on this turn with the Confirm "
    "button, and the platform has already placed that exact call through the gateway; the "
    "gateway's decision for this turn is in the message below. You have no tools on this turn. "
    "Report the outcome to the traveler in two or three sentences: the booking id, what is "
    "booked and the total when it was confirmed, or the exact reason and what would make the "
    "booking possible when it was refused. Say plainly that this books catalog inventory in "
    "Meridian's database and takes no payment. Earlier turns in this conversation do not "
    "change this turn's outcome."
)
ACTION_LABELS = {
    "hold": "a courtesy hold with the Hold button",
    "booking": "the booking of a held package with the Confirm button",
}


def turn_instructions(
    hold_confirmed: bool,
    hold_target: dict | None,
    booking_confirmed: bool = False,
    booking_target: dict | None = None,
) -> str:
    """What this turn adds to the shared prompt: whether a confirmed write was already placed."""
    if booking_confirmed and booking_target:
        return BOOKING_CONFIRMED
    if hold_confirmed and hold_target:
        return CONFIRMED
    return UNCONFIRMED


def system_prompt(
    hold_confirmed: bool,
    hold_target: dict | None,
    booking_confirmed: bool = False,
    booking_target: dict | None = None,
) -> str:
    """The full Phase 4 prompt for a turn: the shared one plus the turn's instructions."""
    return shared_prompt(4) + "\n" + turn_instructions(
        hold_confirmed, hold_target, booking_confirmed, booking_target)


def narration_prompt(
    outcome: str, target: dict | None, budget_ceiling_cents: int = 0, action: str = "hold"
) -> str:
    """The turn prompt for a confirmed hold or booking the platform already executed."""
    ceiling = (
        f"${budget_ceiling_cents / 100:,.2f} for this party"
        if budget_ceiling_cents
        else "not set"
    )
    return (
        f"The traveler confirmed {ACTION_LABELS[action]} for these terms:\n"
        + json.dumps(target or {}, ensure_ascii=False)
        + f"\n\nThe saved budget ceiling the policy used on this turn: {ceiling}. Earlier "
        "turns may have used a different ceiling; only this one applies now."
        + "\n\nThe gateway's decision on this turn:\n"
        + outcome
    )


def turn_prompt(
    message: str, memory_context: str, hold_target: dict | None, hold_confirmed: bool
) -> str:
    parts = [f"Traveler request:\n{message}"]
    if memory_context:
        parts.append(f"Authorized traveler context:\n{memory_context}")
    if hold_confirmed and hold_target:
        parts.append(
            "Hold confirmed by the traveler for exactly these terms:\n"
            + json.dumps(hold_target, ensure_ascii=False)
        )
    return "\n\n".join(parts)
