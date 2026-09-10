"""The confirmed hold is executed by the platform, not proposed by the model.

When the traveler clicks Hold, the terms are already exact and the confirmation
is already given. Leaving that call to the model would make a confirmed action
depend on the model's willingness, and a model that remembers an earlier
refusal in the same conversation will decline to try again. So the runtime
places the call itself, through the same gateway tool, with the same pinned
arguments and the same Cedar decision, and the model only narrates the outcome.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

HOLD_TOOL = "MeridianHolds___create_courtesy_hold"
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
    event = SimpleNamespace(
        tool_use={
            "name": tool_name,
            "toolUseId": f"hold-{uuid.uuid4().hex[:12]}",
            "input": confirmed_hold_arguments(hold_target),
        },
        cancel_tool=False,
        result=None,
    )
    hooks.before(event)
    if event.cancel_tool:
        return str(event.cancel_tool)
    event.result = call_tool(event.tool_use["toolUseId"], tool_name, event.tool_use["input"])
    hooks.after(event)
    if hooks.hold:
        hold = hooks.hold
        return (
            f"Hold {hold.get('bookingId')} is {hold.get('status')} for {hold.get('packageId')} "
            f"({hold.get('duration')}, {hold.get('travelers')} traveler(s)), total "
            f"${hold.get('totalAmount')}, expires {hold.get('expiresAt')}, "
            f"{hold.get('seatsRemaining')} places remaining."
        )
    content = event.result.get("content") if isinstance(event.result, dict) else None
    text = " ".join(
        block.get("text", "") for block in (content or []) if isinstance(block, dict)
    ).strip()
    return text or "The gateway did not place the hold and gave no reason."
