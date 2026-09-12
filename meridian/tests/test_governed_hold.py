"""The workflow's hold goes through the gateway tool with its checkpointed identity."""

from __future__ import annotations

import json
import pytest

from backend.agents.orchestration_05.governed_hold import (
    HOLD_TOOL,
    hold_arguments,
    place_governed_hold,
)

INTENT = {
    "hold_request_id": "hrq_abc123",
    "booking_id": "HLD-ABC123",
    "package_id": "TKY-003",
    "duration": "3 nights",
    "quantity": 2,
    "unit_price": "1949.00",
    "total_amount": "3898.00",
    "fingerprint": "f" * 64,
}


def test_arguments_carry_the_checkpointed_identity_and_the_worker_lease():
    args = hold_arguments(
        INTENT, traveler_id="trv_meridian_demo", journey_ref="phase5-thread",
        budget_ceiling_cents=640000, hold_minutes=15, execution_id="exe_1",
    )
    assert args["holdRequestId"] == "hrq_abc123"
    assert args["bookingId"] == "HLD-ABC123"
    assert args["executionId"] == "exe_1"
    assert args["journeyRef"] == "phase5-thread"
    assert args["unitPriceCents"] == 194900 and args["totalCents"] == 389800
    assert args["holdMinutes"] == 15 and args["travelerConfirmed"] is True
    assert "executionId" not in hold_arguments(
        INTENT, traveler_id="t", journey_ref="j", budget_ceiling_cents=1, hold_minutes=15,
        execution_id=None,
    )


def _response(payload=None, *, error=None, is_error=False, text=None):
    if error is not None:
        return {"jsonrpc": "2.0", "id": "x", "error": error}
    body = text if text is not None else json.dumps(payload)
    return {"jsonrpc": "2.0", "id": "x", "result": {"content": [{"type": "text", "text": body}],
                                                   "isError": is_error}}


def test_a_placed_hold_comes_back_with_its_receipt_and_governance():
    calls = []

    def call_tool(name, arguments):
        calls.append((name, arguments))
        return _response({"hold": {"bookingId": "HLD-ABC123", "status": "held", "replayed": True,
                                   "expiresAt": "2026-09-10 12:15:00+00"},
                          "governance": {"subject": "AROA1", "decision": "allow"},
                          "summary": "Replayed"})

    outcome = place_governed_hold(call_tool, {"packageId": "TKY-003"})
    assert calls[0][0] == HOLD_TOOL
    assert outcome.placed and outcome.hold["replayed"] is True
    assert outcome.governance["decision"] == "allow"
    assert outcome.policy_decision == "allow" and outcome.error is None


def test_a_cedar_denial_is_a_policy_decision_not_an_exception():
    outcome = place_governed_hold(
        lambda *_: _response(text="Tool Execution Denied: [No policy applies to the request "
                                  "(denied by default).]", is_error=True),
        {"packageId": "TKY-003"},
    )
    assert not outcome.placed
    assert outcome.policy_decision == "deny"
    assert "denied by default" in outcome.error


def test_a_business_refusal_from_the_lambda_keeps_its_name():
    outcome = place_governed_hold(
        lambda *_: _response({"error": "insufficient_inventory"}), {"packageId": "TKY-003"}
    )
    assert not outcome.placed
    assert outcome.error == "insufficient_inventory"
    assert outcome.policy_decision == "allow"


def test_a_transport_error_is_reported_as_is():
    outcome = place_governed_hold(
        lambda *_: _response(error={"code": -32000, "message": "Gateway HTTP 503"}),
        {"packageId": "TKY-003"},
    )
    assert not outcome.placed and outcome.policy_decision is None
    assert "503" in outcome.error


@pytest.mark.parametrize("message", [
    "AccessDeniedException: not authorized to invoke this gateway",
    "The Lambda's workload is not authorized for traveler",
    "An upstream policy service is unavailable",
])
def test_other_authorization_failures_are_not_cedar_decisions(message):
    outcome = place_governed_hold(
        lambda *_: _response(text=message, is_error=True), {"packageId": "TKY-003"}
    )
    assert outcome.policy_decision is None
    assert not outcome.placed


def test_documented_gateway_policy_denial_is_recognized():
    outcome = place_governed_hold(
        lambda *_: _response(
            text="AuthorizeActionException - Tool Execution Denied: Tool call not allowed "
                 "due to policy enforcement [No policy applies to the request (denied by default).]",
            is_error=True,
        ), {"packageId": "TKY-003"}
    )
    assert outcome.policy_decision == "deny"
