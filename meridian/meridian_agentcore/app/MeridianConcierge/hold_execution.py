"""Confirmed writes are executed by the platform, not proposed by the model.

When the traveler clicks Hold or Confirm, the terms are already exact and the
confirmation is already given. Leaving that call to the model would make a
confirmed action depend on the model's willingness, and a model that remembers
an earlier refusal in the same conversation will decline to try again. So the
runtime places the call itself, through the same gateway tool, with the same
pinned arguments and the same Cedar decision, and the model only narrates the
outcome.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

HOLD_TOOL = "MeridianHolds___create_courtesy_hold"
BOOKING_TOOL = "MeridianHolds___confirm_booking"
HOLD_MINUTES = 720


def confirmed_hold_arguments(hold_target: dict) -> dict:
    """Tool arguments for the exact terms the traveler confirmed with the Hold button."""
    travelers = int(hold_target["travelers"])
    unit_price_cents = int(hold_target["unit_price_cents"])
    return {
        "packageId": str(hold_target["package_id"]),
        "duration": str(hold_target["duration"]),
        "travelers": travelers,
        "unitPriceCents": unit_price_cents,
        "totalCents": unit_price_cents * travelers,
        "holdMinutes": HOLD_MINUTES,
    }


def confirmed_booking_arguments(booking_target: dict) -> dict:
    """Tool arguments for the held booking the traveler confirmed with the Confirm button."""
    return {
        "bookingId": str(booking_target["booking_id"]),
        "totalCents": int(booking_target["total_cents"]),
    }


def _platform_call(hooks, call_tool, tool_name: str, arguments: dict, prefix: str):
    """Run one governed tool call through the hooks; return the event and any cancellation."""
    event = SimpleNamespace(
        tool_use={
            "name": tool_name,
            "toolUseId": f"{prefix}-{uuid.uuid4().hex[:12]}",
            "input": arguments,
        },
        cancel_tool=False,
        result=None,
    )
    hooks.before(event)
    if event.cancel_tool:
        return event, str(event.cancel_tool)
    event.result = call_tool(event.tool_use["toolUseId"], tool_name, event.tool_use["input"])
    hooks.after(event)
    return event, None


def _result_text(event) -> str:
    content = event.result.get("content") if isinstance(event.result, dict) else None
    return " ".join(
        block.get("text", "") for block in (content or []) if isinstance(block, dict)
    ).strip()


def execute_confirmed_hold(hooks, call_tool, hold_target: dict, tool_name: str = HOLD_TOOL) -> str:
    """Call the governed hold tool once with pinned arguments and return the explained outcome.

    Args:
        hooks: The turn's TraceHooks; they pin the traveler, confirmation, ceiling and
            journey reference onto the arguments and emit the spans and hold events.
        call_tool: ``(tool_use_id, name, arguments) -> tool result dict`` with ``status``
            and ``content``; the Strands MCP client's ``call_tool_sync`` fits.
        hold_target: ``{"package_id", "duration", "travelers", "unit_price_cents"}``.
        tool_name: The gateway tool name, target-prefixed.

    Returns:
        The one-line summary the model narrates: the hold, or the explained refusal.
    """
    event, cancelled = _platform_call(
        hooks, call_tool, tool_name, confirmed_hold_arguments(hold_target), "hold"
    )
    if cancelled:
        return cancelled
    if hooks.hold:
        hold = hooks.hold
        return (
            f"Hold {hold.get('bookingId')} is {hold.get('status')} for {hold.get('packageId')} "
            f"({hold.get('duration')}, {hold.get('travelers')} traveler(s)), total "
            f"${hold.get('totalAmount')}, expires {hold.get('expiresAt')}, "
            f"{hold.get('seatsRemaining')} places remaining."
        )
    return _result_text(event) or "The gateway did not place the hold and gave no reason."


def execute_confirmed_booking(
    hooks, call_tool, booking_target: dict, tool_name: str = BOOKING_TOOL
) -> str:
    """Call the governed confirm tool once with pinned arguments and return the explained outcome.

    Args:
        hooks: The turn's TraceHooks, as for ``execute_confirmed_hold``.
        call_tool: As for ``execute_confirmed_hold``.
        booking_target: ``{"booking_id", "total_cents", "package_id", "duration", "travelers"}``;
            only the id and the total go to the tool, the rest describes the booking.
        tool_name: The gateway tool name, target-prefixed.

    Returns:
        The one-line summary the model narrates: the confirmation, or the explained refusal.
    """
    event, cancelled = _platform_call(
        hooks, call_tool, tool_name, confirmed_booking_arguments(booking_target), "booking"
    )
    if cancelled:
        return cancelled
    if hooks.booking:
        booking = hooks.booking
        return (
            f"Booking {booking.get('bookingId')} is {booking.get('status')} for "
            f"{booking.get('packageId')} ({booking.get('duration')}, "
            f"{booking.get('travelers')} traveler(s)), total ${booking.get('totalAmount')}, "
            f"confirmed {booking.get('confirmedAt')}. Catalog inventory is booked in Meridian's "
            "database; no supplier was contacted and no payment was taken."
        )
    return _result_text(event) or "The gateway did not confirm the booking and gave no reason."
