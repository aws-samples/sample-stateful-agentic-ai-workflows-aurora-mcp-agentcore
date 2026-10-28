"""The workflow's hold goes through the gateway tool with its checkpointed identity."""

from __future__ import annotations

import json
import pytest

from backend.agents.phase_05_workflow.governed_hold import (
    HOLD_TOOL,
    LEASE_LOST_ERRORS,
    RELEASE_TOOL,
    hold_arguments,
    place_governed_hold,
    release_arguments,
    release_governed_hold,
    target_error,
)
from backend.agents.phase_05_workflow.governed_tools import GatewayToolError

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
        traveler_confirmed=True,
    )
    assert args["holdRequestId"] == "hrq_abc123"
    assert args["bookingId"] == "HLD-ABC123"
    assert args["executionId"] == "exe_1"
    assert args["journeyRef"] == "phase5-thread"
    assert args["unitPriceCents"] == 194900 and args["totalCents"] == 389800
    assert args["holdMinutes"] == 15 and args["travelerConfirmed"] is True
    assert "executionId" not in hold_arguments(
        INTENT, traveler_id="t", journey_ref="j", budget_ceiling_cents=1, hold_minutes=15,
        execution_id=None, traveler_confirmed=True,
    )


def test_confirmation_is_whatever_the_caller_proves_never_a_default():
    unconfirmed = hold_arguments(
        INTENT, traveler_id="t", journey_ref="j", budget_ceiling_cents=1, hold_minutes=15,
        execution_id=None, traveler_confirmed=False,
    )
    assert unconfirmed["travelerConfirmed"] is False
    with pytest.raises(TypeError):
        hold_arguments(  # type: ignore[call-arg]
            INTENT, traveler_id="t", journey_ref="j", budget_ceiling_cents=1,
            hold_minutes=15, execution_id=None,
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
    assert outcome.error == "the Gateway policy denied the call"


def test_a_business_refusal_from_the_lambda_keeps_its_name():
    outcome = place_governed_hold(
        lambda *_: _response({"error": "insufficient_inventory"}), {"packageId": "TKY-003"}
    )
    assert not outcome.placed
    assert outcome.error == "insufficient_inventory"
    assert outcome.policy_decision == "allow"


def test_a_transport_error_is_reported_with_a_fixed_phrase():
    outcome = place_governed_hold(
        lambda *_: _response(error={"code": -32000, "message": "Gateway HTTP 503"}),
        {"packageId": "TKY-003"},
    )
    assert not outcome.placed and outcome.policy_decision is None
    assert outcome.error == "the Gateway answered with an error"


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


@pytest.mark.parametrize("code,message,expected", [
    (-32002, "Tool Execution Denied: Tool call not allowed due to policy enforcement "
     "[No policy applies to the request (denied by default).]", "deny"),
    (-32002, "AccessDeniedException: not authorized to invoke this gateway", None),
    (-32000, "Tool Execution Denied: proxy error, not the Gateway policy envelope", None),
])
def test_jsonrpc_denial_requires_both_the_gateway_code_and_message(code, message, expected):
    outcome = place_governed_hold(
        lambda *_: _response(error={"code": code, "message": message}), {},
    )
    assert outcome.policy_decision == expected
    assert not outcome.placed


def test_a_cedar_denial_is_attributed_to_the_policy():
    outcome = place_governed_hold(
        lambda *_: _response(text="Tool Execution Denied: [denied by default]", is_error=True), {}
    )
    assert (outcome.policy_decision, outcome.refused_by) == ("deny", "policy")


def test_an_identity_refusal_is_not_a_policy_decision():
    outcome = place_governed_hold(
        lambda *_: _response(
            text="Identity Check Failed: the request carries no bearer token.", is_error=True
        ), {}
    )
    assert (outcome.policy_decision, outcome.refused_by) == (None, "identity_check")
    assert not outcome.placed


def test_an_identity_prefix_outside_the_gateway_envelope_is_not_a_refusal():
    outcome = place_governed_hold(
        lambda *_: _response(error={"code": -32000, "message": "Identity Check Failed: proxy"}), {}
    )
    assert (outcome.policy_decision, outcome.refused_by) == (None, None)


def test_release_arguments_are_the_three_identities_and_the_execution_when_given():
    assert release_arguments(traveler_id="t", hold_request_id="h", journey_id="j") == {
        "travelerId": "t", "holdRequestId": "h", "journeyId": "j"}
    assert release_arguments(
        traveler_id="t", hold_request_id="h", journey_id="j", execution_id="exe_1"
    ) == {"travelerId": "t", "holdRequestId": "h", "journeyId": "j", "executionId": "exe_1"}


@pytest.mark.parametrize("code", sorted(LEASE_LOST_ERRORS))
def test_a_lease_lost_answer_is_never_a_release(code):
    outcome = release_governed_hold(lambda *_: _response({"error": code}), {})
    assert (outcome.released, outcome.error, outcome.lease_lost) == (False, code, True)


@pytest.mark.parametrize("code", [["execution_lease_lost"], {"code": "x"}, 7])
def test_a_non_string_error_value_is_a_fixed_phrase_and_not_a_lost_lease(code):
    outcome = release_governed_hold(lambda *_: _response({"error": code}), {})
    assert outcome.released is False and outcome.lease_lost is False
    assert outcome.error == target_error(code)


def test_other_failures_are_not_a_lost_lease():
    outcome = release_governed_hold(lambda *_: _response({"error": "release_failed"}), {})
    assert outcome.released is False and outcome.lease_lost is False


@pytest.mark.parametrize(("payload", "replayed"), [
    ({"release": {"status": "released", "replayed": False}}, False),
    ({"release": {"status": "released", "replayed": True}}, True),
])
def test_a_release_receipt_is_a_release(payload, replayed):
    calls = []

    def call_tool(name, arguments):
        calls.append(name)
        return _response(payload)

    outcome = release_governed_hold(call_tool, {})
    assert calls == [RELEASE_TOOL]
    assert (outcome.released, outcome.replayed, outcome.error) == (True, replayed, None)


@pytest.mark.parametrize(("reply", "error", "refused_by"), [
    (_response(text="Tool Execution Denied: no policy", is_error=True),
     "the Gateway policy denied the call", "policy"),
    (_response(text="Identity Check Failed: the request carries no bearer token.",
               is_error=True),
     "the Gateway identity check refused the call (no_token)", "identity_check"),
    (_response({"error": "hold_not_found"}), "hold_not_found", None),
    (_response({"error": "traveler_not_authorized"}), "traveler_not_authorized", None),
    (_response({"error": "booking_not_held"}), "booking_not_held", None),
    (_response({"error": "release_failed"}), "the hold target returned an error", None),
    (_response({"error": "arn:aws:iam::123456789012:role/x"}),
     "the hold target returned an error", None),
    (_response(text="not json"), "the Gateway result could not be read", None),
    (_response(text="[1]"), "the Gateway result could not be read", None),
    (_response({"release": {"status": "held"}}), "the Gateway returned no release receipt", None),
    (_response({"release": "released"}), "the Gateway returned no release receipt", None),
    (_response({}), "the Gateway returned no release receipt", None),
])
def test_anything_but_a_release_receipt_is_not_released(reply, error, refused_by):
    outcome = release_governed_hold(lambda *_: reply, {})
    assert (outcome.released, outcome.error, outcome.refused_by) == (False, error, refused_by)


def test_a_null_error_is_not_an_error():
    reply = _response({"error": None, "release": {"status": "released", "replayed": False}})
    assert release_governed_hold(lambda *_: reply, {}).released is True


def test_gateway_text_goes_to_a_masked_log_and_not_to_the_outcome(caplog):
    sent = "Tool Execution Denied: arn:aws:iam::123456789012:role/x Bearer abc.def.ghi"
    with caplog.at_level("WARNING"):
        outcome = release_governed_hold(lambda *_: _response(text=sent, is_error=True), {})
    assert "123456789012" not in str(outcome) and "abc.def.ghi" not in str(outcome)
    assert "Bearer <token>" in caplog.text and "abc.def.ghi" not in caplog.text
    assert "123456789012" not in caplog.text


def test_a_malformed_envelope_is_not_a_release():
    outcome = release_governed_hold(lambda *_: "not a response", {})
    assert not outcome.released and outcome.error == "the Gateway answered with an error"


def test_a_transport_failure_propagates_with_its_code():
    def call_tool(name, arguments):
        raise GatewayToolError(name, "the Gateway timed out", code="timeout")

    with pytest.raises(GatewayToolError) as raised:
        release_governed_hold(call_tool, {})
    assert raised.value.code == "timeout"
