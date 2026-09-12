"""Place the workflow's courtesy hold through the governed gateway tool.

Phase 5 used to call the ``create_courtesy_hold`` SQL function directly from
the LangGraph node. It now asks AgentCore Gateway for the same hold the Phase 4
agent uses, so Cedar sees every hold the application places and there is one
door for business effects. The worker keeps its own lease check; it also passes
its execution id so the Lambda verifies the lease again inside the write
transaction, and it passes the checkpointed request and booking ids so a
replacement worker replays the same booking with the original expiry.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

HOLD_TOOL = "MeridianHolds___create_courtesy_hold"
# Gateway's Policy denial envelope. IAM and target authorization errors are
# different boundaries and must never be presented as a Cedar decision.
DENIAL = re.compile(r"^(?:AuthorizeActionException\s*-\s*)?Tool Execution Denied:", re.I)


@dataclass(frozen=True)
class GovernedHold:
    """The gateway's answer to one hold call."""

    hold: Optional[Dict[str, Any]]
    governance: Dict[str, Any]
    error: Optional[str]
    policy_decision: Optional[str]
    raw_error: Optional[str]

    @property
    def placed(self) -> bool:
        return self.hold is not None


def hold_arguments(
    intent: Dict[str, Any], *, traveler_id: str, journey_ref: str,
    budget_ceiling_cents: int, hold_minutes: int, execution_id: Optional[str],
) -> Dict[str, Any]:
    """Tool arguments from the checkpointed hold intent.

    The confirmation flag is set by the backend from the traveler's own request
    that started or resumed the workflow, never from a model.
    """
    unit_price_cents = int(round(float(intent["unit_price"]) * 100))
    quantity = int(intent["quantity"])
    args = {
        "travelerId": traveler_id,
        "packageId": str(intent["package_id"]),
        "duration": str(intent["duration"]),
        "travelers": quantity,
        "unitPriceCents": unit_price_cents,
        "totalCents": unit_price_cents * quantity,
        "holdMinutes": int(hold_minutes),
        "travelerConfirmed": True,
        "budgetCeilingCents": int(budget_ceiling_cents),
        "journeyRef": journey_ref,
        "holdRequestId": str(intent["hold_request_id"]),
        "bookingId": str(intent["booking_id"]),
    }
    if execution_id:
        args["executionId"] = str(execution_id)
    return args


def _text(response: Dict[str, Any]) -> str:
    result = response.get("result") or {}
    return "".join(
        block.get("text", "")
        for block in (result.get("content") or [])
        if isinstance(block, dict)
    )


def place_governed_hold(call_tool: Callable[[str, Dict[str, Any]], Dict[str, Any]],
                        arguments: Dict[str, Any]) -> GovernedHold:
    """Call the gateway hold tool and normalise what came back.

    Args:
        call_tool: ``(tool_name, arguments) -> MCP tools/call response`` (JSON-RPC dict).
        arguments: From :func:`hold_arguments`.

    Returns:
        A GovernedHold. ``error`` is the Lambda's named business error, the Cedar
        denial text, or the transport error; ``policy_decision`` is ``"deny"`` when
        the gateway refused the call and ``"allow"`` when the Lambda ran.
    """
    response = call_tool(HOLD_TOOL, arguments)
    rpc_error = response.get("error")
    result = response.get("result") or {}
    text = _text(response)
    if rpc_error or result.get("isError"):
        message = text or json.dumps(rpc_error or result)[:300]
        denied = bool(result.get("isError") and DENIAL.match(message.strip()))
        return GovernedHold(None, {}, message, "deny" if denied else None, message)
    try:
        payload = json.loads(text) if text else {}
    except ValueError:
        return GovernedHold(None, {}, f"unreadable gateway result: {text[:200]}", None, text)
    if "error" in payload:
        return GovernedHold(None, payload.get("governance") or {}, str(payload["error"]), "allow",
                            str(payload["error"]))
    return GovernedHold(payload.get("hold"), payload.get("governance") or {}, None, "allow", None)
