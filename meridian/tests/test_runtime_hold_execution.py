"""A confirmed hold is placed by the platform with pinned arguments; the model only narrates."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from strands.types.exceptions import MCPClientInitializationError

from backend.audit_vocabulary import ToolCall
from tests.test_strands_hooks import Gateway, ScriptedModel, run_concierge, stored_after

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
sys.path.insert(0, str(RUNTIME))

from hold_execution import (  # noqa: E402
    BOOKING_TOOL,
    HOLD_TOOL,
    confirmed_hold_arguments,
    execute_confirmed_booking,
    execute_confirmed_hold,
)
from tool_errors import ToolSetupError  # noqa: E402
from turn_trace import TraceHooks, TurnContext  # noqa: E402

TARGET = {"package_id": "CTY-002", "duration": "5 nights", "travelers": 2, "unit_price_cents": 249900}


def _turn(**overrides):
    base = dict(
        traveler_id="trv_meridian_demo",
        conversation_id="conv-1",
        hold_confirmed=True,
        budget_ceiling_cents=640000,
        gateway_id="gw-1",
        policy_engine_id="pe-1",
    )
    base.update(overrides)
    return TurnContext(**base)


def _drain(queue):
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def test_confirmed_arguments_carry_the_exact_terms_and_the_total():
    args = confirmed_hold_arguments(TARGET)
    assert args == {
        "packageId": "CTY-002",
        "duration": "5 nights",
        "travelers": 2,
        "unitPriceCents": 249900,
        "totalCents": 499800,
        "holdMinutes": 720,
    }


def test_platform_call_is_pinned_and_a_held_result_is_narrated():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn())
    calls = []

    def call_tool(tool_use_id, name, arguments):
        calls.append((tool_use_id, name, dict(arguments)))
        return {
            "toolUseId": tool_use_id,
            "status": "success",
            "content": [{"text": json.dumps({
                "hold": {"bookingId": "HLD-9", "status": "held", "packageId": "CTY-002",
                         "duration": "5 nights", "travelers": 2, "totalAmount": "4998.00",
                         "expiresAt": "2026-09-11T00:00:00+00:00", "seatsRemaining": 8},
                "governance": {"subject": "AROA1", "decision": "allow"},
                "summary": "Held CTY-002",
            })}],
        }

    outcome = execute_confirmed_hold(hooks, call_tool, TARGET)

    assert len(calls) == 1
    tool_use_id, name, args = calls[0]
    assert name == HOLD_TOOL
    assert args["travelerConfirmed"] is True
    assert args["travelerId"] == "trv_meridian_demo"
    assert args["budgetCeilingCents"] == 640000
    assert args["journeyRef"] == "concierge:conv-1"
    assert args["totalCents"] == 499800
    assert hooks.hold["bookingId"] == "HLD-9"
    assert "HLD-9" in outcome and "8 places remaining" in outcome
    kinds = [kind for kind, _ in _drain(queue)]
    assert kinds.count("activity") == 2 and kinds.count("hold") == 1


def test_a_refused_platform_call_narrates_the_explained_denial():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn(budget_ceiling_cents=100000))

    def call_tool(tool_use_id, name, arguments):
        return {
            "toolUseId": tool_use_id,
            "status": "error",
            "content": [{"text": "Tool Execution Denied: [No policy applies to the request (denied by default).]"}],
        }

    outcome = execute_confirmed_hold(hooks, call_tool, TARGET)

    assert hooks.hold is None
    assert "denied it by default" in outcome
    assert "$4,998.00 exceeds the saved budget ceiling $1,000.00" in outcome
    hold_event = [item for kind, item in _drain(queue) if kind == "hold"][0]
    assert hold_event["policyDecision"] == "deny"


def test_a_second_platform_call_in_the_same_turn_is_refused_by_the_hooks():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn())
    hooks.hold_settled = True
    outcome = execute_confirmed_hold(hooks, lambda *_: (_ for _ in ()).throw(AssertionError("must not call")), TARGET)
    assert "hold was already attempted on this turn" in outcome


BOOKING = {"booking_id": "HLD-9", "total_cents": 499800, "package_id": "CTY-002",
           "duration": "5 nights", "travelers": 2}


def test_platform_confirmation_is_pinned_and_a_confirmed_result_is_narrated():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn(hold_confirmed=False, booking_confirmed=True))
    calls = []

    def call_tool(tool_use_id, name, arguments):
        calls.append((tool_use_id, name, dict(arguments)))
        return {
            "toolUseId": tool_use_id,
            "status": "success",
            "content": [{"text": json.dumps({
                "booking": {"bookingId": "HLD-9", "status": "confirmed", "packageId": "CTY-002",
                            "duration": "5 nights", "travelers": 2, "totalAmount": "4998.00",
                            "confirmedAt": "2026-09-10 13:00:00+00"},
                "governance": {"subject": "AROA1", "decision": "allow"},
                "summary": "Confirmed booking HLD-9",
            })}],
        }

    outcome = execute_confirmed_booking(hooks, call_tool, BOOKING)

    assert len(calls) == 1
    tool_use_id, name, args = calls[0]
    assert name == BOOKING_TOOL and tool_use_id.startswith("booking-")
    assert args == {
        "bookingId": "HLD-9", "totalCents": 499800, "travelerId": "trv_meridian_demo",
        "travelerConfirmed": True, "budgetCeilingCents": 640000, "journeyRef": "concierge:conv-1",
    }
    assert hooks.booking["bookingId"] == "HLD-9"
    assert "HLD-9 is confirmed" in outcome and "no payment was taken" in outcome
    kinds = [kind for kind, _ in _drain(queue)]
    assert kinds.count("activity") == 2 and kinds.count("booking") == 1


def test_a_refused_platform_confirmation_narrates_the_explained_denial():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn(booking_confirmed=True, budget_ceiling_cents=100000))

    def call_tool(tool_use_id, name, arguments):
        return {
            "toolUseId": tool_use_id,
            "status": "error",
            "content": [{"text": "Tool Execution Denied: [No policy applies to the request (denied by default).]"}],
        }

    outcome = execute_confirmed_booking(hooks, call_tool, BOOKING)

    assert hooks.booking is None
    assert "No Cedar policy permits this booking" in outcome
    assert "$4,998.00 exceeds the saved budget ceiling $1,000.00" in outcome
    booking_event = [item for kind, item in _drain(queue) if kind == "booking"][0]
    assert booking_event["policyDecision"] == "deny"


def test_a_business_refusal_from_aurora_is_narrated_verbatim():
    hooks = TraceHooks(asyncio.Queue(), _turn(booking_confirmed=True))

    def call_tool(tool_use_id, name, arguments):
        return {"toolUseId": tool_use_id, "status": "success",
                "content": [{"text": json.dumps({"error": "hold_expired"})}]}

    assert execute_confirmed_booking(hooks, call_tool, BOOKING) == "hold_expired"
    assert hooks.booking is None and hooks.booking_settled is True


# ------------------------------------------- a governed write that started is always traced

FAILED_TO_START = "the client session is not running"
WRITES = {
    "hold": (execute_confirmed_hold, TARGET, HOLD_TOOL),
    "booking": (execute_confirmed_booking, BOOKING, BOOKING_TOOL),
}


def _labels(span):
    return {field["label"]: field["value"] for field in span["telemetry"]["fields"]}


def _raised_by_a_platform_call(kind, failure):
    """Run the confirmed write whose call raises ``failure``, and check what it reported.

    Both spans of the call and the write's event carry ``failure``'s own text.

    Returns:
        The error that propagated.
    """
    execute, target, _tool = WRITES[kind]
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn(booking_confirmed=True))

    def call_tool(*_args):
        raise failure

    with pytest.raises(Exception) as raised:
        execute(hooks, call_tool, target)
    items = _drain(queue)
    started, failed = [item for item_kind, item in items if item_kind == "activity"]
    assert _labels(started)["call_id"] == _labels(failed)["call_id"]
    assert failed["title"] == "Tool call failed"
    assert failed["telemetry"]["status"] == "error"
    assert _labels(failed)["gateway_error"] == f"Error: {failure}"
    assert [item for item_kind, item in items if item_kind == kind] == [{
        kind: None, "refused": f"Error: {failure}", "error": f"Error: {failure}",
        "policyDecision": None,
    }]
    return raised.value


@pytest.mark.parametrize("kind", WRITES)
def test_a_platform_call_that_could_not_be_set_up_reports_the_call_and_names_the_tool(kind):
    failure = MCPClientInitializationError(FAILED_TO_START)

    raised = _raised_by_a_platform_call(kind, failure)

    assert type(raised) is ToolSetupError and raised.__cause__ is failure
    assert str(raised) == (
        f"Tool {WRITES[kind][2]} could not run (MCPClientInitializationError): {FAILED_TO_START}")


@pytest.mark.parametrize("kind", WRITES)
def test_a_platform_call_with_any_other_error_reports_the_call_and_raises_it_unchanged(kind):
    failure = RuntimeError("the gateway closed the stream")

    assert _raised_by_a_platform_call(kind, failure) is failure


CONFIRMED_WRITES = {
    "production_hold": ({"hold_confirmed": True, "hold_target": TARGET}, HOLD_TOOL),
    "production_booking": ({"booking_confirmed": True, "booking_target": BOOKING}, BOOKING_TOOL),
}


@pytest.mark.parametrize("operation", CONFIRMED_WRITES)
def test_a_confirmed_write_whose_call_raises_is_an_error_in_the_audit_row(monkeypatch, operation):
    confirmed, tool = CONFIRMED_WRITES[operation]

    def call(*_args):
        raise MCPClientInitializationError(FAILED_TO_START)

    payload = {"event": "concierge_turn", "traveler_id": "trv_hook_test",
               "conversation_id": "conv-hook", "budget_ceiling_cents": 640000, **confirmed}
    yielded, _seen = run_concierge(monkeypatch, ScriptedModel(), Gateway(call=call), payload)

    row, raised = stored_after(monkeypatch, yielded, operation)

    message = f"Tool {tool} could not run (MCPClientInitializationError): {FAILED_TO_START}"
    assert row["tool_calls"] == [ToolCall(tool.split("___")[1], "error")]
    assert row["authorization_decision"] == "error"
    assert row["turn_error"] == f"ToolSetupError: {message}"
    assert str(raised) == f"AgentCore Runtime error: {message}"


class _ResultSpy:
    """A hook provider whose ``after`` records ``event.result`` as the platform call left it."""

    def __init__(self, hooks):
        self.hooks = hooks
        self.seen = []

    def before(self, event):
        self.hooks.before(event)

    def after(self, event):
        self.seen.append(json.dumps(event.result))
        self.hooks.after(event)


@pytest.mark.parametrize("kind", WRITES)
def test_a_platform_call_error_reaches_the_trace_and_the_model_with_account_ids_masked(kind):
    execute, target, _tool = WRITES[kind]
    queue = asyncio.Queue()
    spy = _ResultSpy(TraceHooks(queue, _turn(booking_confirmed=True)))
    failure = RuntimeError("arn:aws:lambda:us-east-1:123456789012:function:x timed out")

    def call_tool(*_args):
        raise failure

    with pytest.raises(RuntimeError):
        execute(spy, call_tool, target)
    reported = json.dumps(_drain(queue))
    assert "123456789012" not in reported and "<acct>" in reported
    (handed_to_hooks,) = spy.seen
    assert "123456789012" not in handed_to_hooks and "<acct>" in handed_to_hooks
