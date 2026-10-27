"""The runtime turns every gateway tool call into a trace span and enforces the hold contract."""

from __future__ import annotations

import asyncio
import json
import sys
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
sys.path.insert(0, str(RUNTIME))

from turn_trace import (  # noqa: E402
    POLICY_FOR,
    RUNTIME_GUARD,
    SPANS,
    WRITE_ALREADY_ATTEMPTED,
    TraceHooks,
    TurnContext,
    friendly_denial,
    short,
)

from backend import audit_vocabulary  # noqa: E402
from backend.agentcore.runtime import AgentCoreRuntimeAdapter  # noqa: E402
from backend.agents.phase_04_production.audit_outcome import (  # noqa: E402
    audit_from_decision,
    read_turn,
)
from backend.audit_vocabulary import ToolCall  # noqa: E402
from tests.runtime_support import load_runtime_main  # noqa: E402


@pytest.mark.parametrize("message", [
    "AccessDeniedException: not authorized to invoke this gateway",
    "The target workload was denied access to this traveler",
    "An upstream policy service is unavailable",
])
def test_non_policy_errors_never_become_cedar_denials(message):
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn())
    event = _event("MeridianHolds___create_courtesy_hold", {"packageId": "CTY-002"})
    hooks.before(event)
    event.result = {"status": "error", "content": [{"text": message}]}
    hooks.after(event)
    items = _drain(queue)
    result = [span for kind, span in items if kind == "activity"][-1]
    assert result["telemetry"]["status"] == "error"
    assert not any(field["label"] == "cedar_decision" for field in result["telemetry"]["fields"])
    assert [span for kind, span in items if kind == "hold"][0]["policyDecision"] is None


def _turn(**overrides):
    base = dict(
        traveler_id="trv_meridian_demo",
        conversation_id="conv-1",
        hold_confirmed=False,
        budget_ceiling_cents=400000,
        gateway_id="gw-1",
        policy_engine_id="pe-1",
    )
    base.update(overrides)
    return TurnContext(**base)


def _event(name, inputs, tool_use_id="t1", result=None):
    return SimpleNamespace(
        tool_use={"name": name, "toolUseId": tool_use_id, "input": inputs},
        cancel_tool=False,
        result=result,
    )


def _drain(queue):
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def test_short_strips_the_target_prefix():
    assert short("MeridianHolds___create_courtesy_hold") == "create_courtesy_hold"
    assert short("semantic_trip_search") == "semantic_trip_search"


def test_search_call_emits_a_tool_span_with_arguments():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn())
    hooks.before(
        _event("SemanticTripSearchLambda___semantic_trip_search", {"query": "Tokyo", "limit": 5})
    )
    kind, span = _drain(queue)[0]
    assert kind == "activity"
    assert span["activity_type"] == "search"
    assert span["telemetry"]["category"] == "gateway"
    assert span["telemetry"]["status"] == "ok"
    labels = {field["label"]: field["value"] for field in span["telemetry"]["fields"]}
    assert labels["tool"] == "SemanticTripSearchLambda___semantic_trip_search"
    assert labels["auth"] == "SigV4"
    assert labels["policy_engine"] == "pe-1"


def test_hold_arguments_are_pinned_to_the_turn_not_the_model():
    hooks = TraceHooks(asyncio.Queue(), _turn(hold_confirmed=False, budget_ceiling_cents=350000))
    event = _event(
        "MeridianHolds___create_courtesy_hold",
        {
            "travelerId": "trv_someone_else",
            "packageId": "CTY-002",
            "duration": "7 nights",
            "travelers": 2,
            "unitPriceCents": 250000,
            "totalCents": 500000,
            "holdMinutes": 720,
            "travelerConfirmed": True,
            "budgetCeilingCents": 9999999,
            "journeyRef": "concierge:conv-1",
        },
    )
    hooks.before(event)
    assert event.cancel_tool is False
    assert event.tool_use["input"]["travelerId"] == "trv_meridian_demo"
    assert event.tool_use["input"]["travelerConfirmed"] is False
    assert event.tool_use["input"]["budgetCeilingCents"] == 350000
    assert event.tool_use["input"]["journeyRef"] == "concierge:conv-1"


def test_successful_hold_emits_hold_and_result_span():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn(hold_confirmed=True))
    event = _event("MeridianHolds___create_courtesy_hold", {"packageId": "CTY-002"})
    hooks.before(event)
    payload = {
        "hold": {
            "bookingId": "HLD-1",
            "status": "held",
            "replayed": False,
            "expiresAt": "2026-09-11T00:00:00+00:00",
            "seatsRemaining": 3,
        },
        "summary": "Held CTY-002 for 2 travelers",
        "governance": {"subject": "AROA1", "decision": "allow"},
    }
    event.result = {"status": "success", "content": [{"text": json.dumps(payload)}]}
    hooks.after(event)
    items = _drain(queue)
    assert [kind for kind, _ in items].count("hold") == 1
    assert hooks.hold["bookingId"] == "HLD-1"
    result = [span for kind, span in items if kind == "activity"][-1]
    labels = {field["label"]: field["value"] for field in result["telemetry"]["fields"]}
    assert labels["workload"] == "AROA1"
    assert labels["traveler_grant"] == "allow"


def test_search_result_publishes_packages():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn())
    event = _event("SemanticTripSearchLambda___semantic_trip_search", {"query": "Tokyo"})
    hooks.before(event)
    event.result = {
        "status": "success",
        "content": [{"text": json.dumps({"packages": [{"package_id": "CTY-002"}]})}],
    }
    hooks.after(event)
    kinds = dict(_drain(queue))
    assert kinds["packages"] == [{"package_id": "CTY-002"}]
    assert hooks.packages[0]["package_id"] == "CTY-002"


def test_policy_denial_is_a_denied_security_span_and_a_refused_hold():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn())
    event = _event("MeridianHolds___create_courtesy_hold", {"packageId": "CTY-002"})
    hooks.before(event)
    event.result = {
        "status": "error",
        "content": [{
            "text": (
                "Tool Execution Denied: [Policy evaluation denied due to "
                "meridian_hold_governance-abc12de_]"
            )
        }],
    }
    hooks.after(event)
    items = _drain(queue)
    result = [span for kind, span in items if kind == "activity"][-1]
    assert result["telemetry"]["status"] == "denied"
    assert result["telemetry"]["category"] == "security"
    assert result["title"] == "Hold refused by Cedar policy"
    assert "meridian_hold_governance" in result["details"]
    refused = [span for kind, span in items if kind == "hold"][0]
    assert refused["policyDecision"] == "deny"
    assert hooks.hold is None


def test_default_deny_names_the_hold_conditions_the_arguments_failed():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn(hold_confirmed=False, budget_ceiling_cents=300000))
    event = _event(
        "MeridianHolds___create_courtesy_hold",
        {"packageId": "CTY-002", "travelers": 2, "holdMinutes": 720, "totalCents": 500000},
    )
    hooks.before(event)
    event.result = {
        "status": "error",
        "content": [{"text": "Tool Execution Denied: [No policy applies to the request (denied by default).]"}],
    }
    hooks.after(event)
    items = _drain(queue)
    result = [span for kind, span in items if kind == "activity"][-1]
    assert result["telemetry"]["status"] == "denied"
    assert "has not confirmed" in result["details"]
    assert "$5,000.00 exceeds the saved budget ceiling $3,000.00" in result["details"]
    refused = [span for kind, span in items if kind == "hold"][0]
    assert refused["policyDecision"] == "deny"
    # The model receives the explained decision, not the raw gateway text.
    assert event.result["status"] == "error"
    assert event.result["content"][0]["text"] == result["details"]


def test_strands_wrapped_denial_is_still_a_cedar_denial():
    # Strands reports an MCP tool exception as "Tool execution failed: <message>".
    # The gateway's denial arrives behind that prefix on the deployed runtime, and
    # it was being rendered as a generic tool failure with no policy decision.
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn(hold_confirmed=False, budget_ceiling_cents=640000))
    event = _event(
        "MeridianHolds___create_courtesy_hold",
        {"packageId": "CTY-002", "travelers": 8, "holdMinutes": 720, "totalCents": 1999200},
    )
    hooks.before(event)
    event.result = {
        "status": "error",
        "content": [{
            "text": (
                "Tool execution failed: Tool Execution Denied: Tool call not allowed due to "
                "policy enforcement [No policy applies to the request (denied by default).]"
            )
        }],
    }
    hooks.after(event)
    items = _drain(queue)
    result = [span for kind, span in items if kind == "activity"][-1]
    assert result["telemetry"]["status"] == "denied"
    assert result["title"] == "Hold refused by Cedar policy"
    assert "denied it by default" in result["details"]
    refused = [span for kind, span in items if kind == "hold"][0]
    assert refused["policyDecision"] == "deny"
    assert hooks.hold is None


def test_lambda_business_error_is_a_failed_span_not_a_denial():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn(hold_confirmed=True))
    event = _event("MeridianHolds___create_courtesy_hold", {"packageId": "CTY-002"})
    hooks.before(event)
    event.result = {
        "status": "success",
        "content": [{"text": json.dumps({"error": "insufficient_inventory"})}],
    }
    hooks.after(event)
    items = _drain(queue)
    result = [span for kind, span in items if kind == "activity"][-1]
    assert result["telemetry"]["status"] == "error"
    assert result["details"] == "insufficient_inventory"
    refused = [span for kind, span in items if kind == "hold"][0]
    assert refused["policyDecision"] is None


def test_a_second_hold_attempt_after_a_decision_is_cancelled_not_retried():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn())
    first = _event("MeridianHolds___create_courtesy_hold", {"packageId": "CTY-002"}, "t1")
    hooks.before(first)
    first.result = {
        "status": "error",
        "content": [{"text": "[No policy applies to the request (denied by default).]"}],
    }
    hooks.after(first)
    assert hooks.hold_settled is True
    retry = _event("MeridianHolds___create_courtesy_hold", {"packageId": "CTY-002"}, "t2")
    hooks.before(retry)
    assert "already attempted" in retry.cancel_tool
    assert "t2" not in hooks.started
    spans_before = len(_drain(queue))
    hooks.after(retry)
    assert len(_drain(queue)) == 0 and spans_before >= 2


def _labels(span):
    return {field["label"]: field["value"] for field in span["telemetry"]["fields"]}


def _call_spans(name, inputs, result, tool_use_id="call-7", **turn):
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn(**turn))
    event = _event(name, inputs, tool_use_id)
    hooks.before(event)
    event.result = result
    hooks.after(event)
    return [span for kind, span in _drain(queue) if kind == "activity"]


@pytest.mark.parametrize("result", [
    {"status": "success", "content": [{"text": json.dumps({"package": {}, "summary": "ok"})}]},
    {"status": "success", "content": [{"text": json.dumps({"error": "Unknown package X"})}]},
    {"status": "error", "content": [{"text": "Tool Execution Denied: no policy applies"}]},
    {"status": "error", "content": [{"text": "Identity Check Failed: the request carries no "
                                             "bearer token."}]},
])
def test_every_span_of_one_call_carries_the_call_id(result):
    """Parallel calls report results in completion order; the call id pairs each with its call."""
    spans = _call_spans("MeridianHolds___get_package_details", {"packageId": "X"}, result)
    assert len(spans) == 2
    assert [_labels(span)["call_id"] for span in spans] == ["call-7", "call-7"]


def test_a_grant_refusal_span_carries_the_lambdas_decision_and_deny_row():
    payload = {"error": "traveler_not_authorized",
               "governance": {"decision": "deny", "subject": "AROA1",
                              "access_audit_id": "authz_0123456789ab"}}
    spans = _call_spans("MeridianHolds___create_courtesy_hold", {"packageId": "CTY-002"},
                        {"status": "success", "content": [{"text": json.dumps(payload)}]},
                        hold_confirmed=True)
    labels = _labels(spans[-1])
    assert spans[-1]["telemetry"]["status"] == "error"
    assert labels["gateway_error"] == "traveler_not_authorized"
    assert labels["traveler_grant"] == "deny"
    assert labels["access_audit_id"] == "authz_0123456789ab"


def test_a_business_refusal_span_carries_no_grant_fields():
    spans = _call_spans(
        "MeridianHolds___create_courtesy_hold", {"packageId": "CTY-002"},
        {"status": "success", "content": [{"text": json.dumps({"error": "hold_expired"})}]})
    assert not {"traveler_grant", "access_audit_id"} & set(_labels(spans[-1]))


def test_a_cancelled_second_hold_is_a_span_naming_the_runtime_guard():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn())
    first = _event("MeridianHolds___create_courtesy_hold", {"packageId": "CTY-002"}, "t1")
    hooks.before(first)
    first.result = {"status": "success",
                    "content": [{"text": json.dumps({"error": "insufficient_inventory"})}]}
    hooks.after(first)
    _drain(queue)

    retry = _event("MeridianHolds___create_courtesy_hold", {"packageId": "TKY-003"}, "t2")
    hooks.before(retry)

    (kind, span), = _drain(queue)
    labels = _labels(span)
    assert kind == "activity" and retry.cancel_tool
    assert labels["call_id"] == "t2"
    assert labels["tool"] == "MeridianHolds___create_courtesy_hold"
    assert (labels["refused_by"], labels["refusal_code"]) == (
        "runtime_guard", "write_already_attempted")
    assert span["telemetry"]["category"] == "runtime"


def test_after_a_transport_failure_the_guard_says_attempted_not_decided():
    """A failed first call settles the write, but the Gateway may have decided nothing."""
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn())
    first = _event("MeridianHolds___create_courtesy_hold", {"packageId": "CTY-002"}, "t1")
    hooks.before(first)
    first.result = {"status": "error",
                    "content": [{"text": "ConnectionResetError: the connection was reset"}]}
    hooks.after(first)
    _drain(queue)

    retry = _event("MeridianHolds___create_courtesy_hold", {"packageId": "CTY-002"}, "t2")
    hooks.before(retry)

    (_, span), = _drain(queue)
    assert span["title"] == "Second hold not sent: already attempted on this turn"
    assert span["details"] == (
        "This hold was already attempted on this turn, so the Runtime did not send the call again.")
    assert retry.cancel_tool.startswith("This hold was already attempted on this turn.")
    assert "decided" not in " ".join([span["title"], span["details"], retry.cancel_tool])


def test_the_guard_uses_the_audit_vocabularys_words():
    assert (RUNTIME_GUARD, WRITE_ALREADY_ATTEMPTED) == (
        audit_vocabulary.RUNTIME_GUARD, audit_vocabulary.WRITE_ALREADY_ATTEMPTED)


# ------------------------------------- the Runtime's real spans, read by the backend's reader
# The two sides share field labels (call_id, traveler_grant, access_audit_id, refused_by,
# refusal_code, gateway_error) but no code. These turns run the real TraceHooks, the Runtime's
# own event shaping and the backend adapter's stream reader, so a label renamed on one side
# fails here even when that side's own tests were renamed with it.

SEARCH_TOOL = "SemanticTripSearchLambda___semantic_trip_search"
DETAILS_TOOL = "MeridianHolds___get_package_details"
HOLD_TOOL = "MeridianHolds___create_courtesy_hold"
DEFAULT_DENY = ("Tool execution failed: Tool Execution Denied: Tool call not allowed due to "
                "policy enforcement [No policy applies to the request (denied by default).]")
GRANT_DENY = {"error": "traveler_not_authorized",
              "governance": {"decision": "deny", "subject": "AROA1",
                             "access_audit_id": "authz_0123456789ab"}}


def _answer(payload):
    return {"status": "success", "content": [{"text": json.dumps(payload)}]}


CONTRACT_TURNS = {
    "allowed, error, sold out, then the guard": (
        [(SEARCH_TOOL, _answer({"packages": [{"package_id": "CTY-002"}], "summary": "1 trip"})),
         (DETAILS_TOOL, _answer({"error": "Unknown package TKY-999"})),
         (HOLD_TOOL, _answer({"error": "insufficient_inventory"})),
         (HOLD_TOOL, None)],
        [ToolCall("semantic_trip_search", "allowed"),
         ToolCall("get_package_details", "error"),
         ToolCall("create_courtesy_hold", "refused", "business_rule", "insufficient_inventory"),
         ToolCall("create_courtesy_hold", "refused", "runtime_guard", "write_already_attempted")],
    ),
    "cedar denial": (
        [(HOLD_TOOL, {"status": "error", "content": [{"text": DEFAULT_DENY}]})],
        [ToolCall("create_courtesy_hold", "refused", "policy")],
    ),
    "workload grant": (
        [(HOLD_TOOL, _answer(GRANT_DENY))],
        [ToolCall("create_courtesy_hold", "refused", "workload_grant", "traveler_not_authorized",
                  "authz_0123456789ab")],
    ),
}


def _sse(events):
    """Each event as the AgentCore app frames a string the Runtime yields: JSON inside JSON."""
    return "".join(f"data: {json.dumps(json.dumps(event))}\n\n" for event in events).encode()


def _backend_decision(runtime_events):
    """The decision the backend adapter reads from the Runtime's stream."""
    result = {"type": "result", "message": "Done.", "recommended_package_ids": [],
              "follow_ups": [], "hold": None, "trace_id": None, "usage": {}, "elapsed_ms": 1}
    body = MagicMock()
    body.read.side_effect = [_sse([*runtime_events, result]), b""]
    adapter = AgentCoreRuntimeAdapter(
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/x",
        region="us-east-1")
    adapter._client = MagicMock()
    adapter._client.invoke_agent_runtime.return_value = {
        "response": body, "ResponseMetadata": {"RequestId": "r"}}
    return adapter.invoke_turn("conv-1", "trv_meridian_demo", "Hold it", "",
                               budget_ceiling_cents=400000, travelers_count=2)


def _traced_turn(monkeypatch, calls):
    """Run the calls through the real hooks and Runtime events; return the backend's decision."""
    runtime = load_runtime_main(monkeypatch, "meridian_runtime_contract")
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn())
    for number, (name, result) in enumerate(calls, start=1):
        event = _event(name, {"packageId": "CTY-002"}, f"tooluse-{number}")
        hooks.before(event)
        event.result = result or {"status": "error", "content": [{"text": event.cancel_tool}]}
        hooks.after(event)
    return _backend_decision(list(runtime.drain(queue)))


@pytest.mark.parametrize(("calls", "expected"), CONTRACT_TURNS.values(), ids=CONTRACT_TURNS)
def test_the_backend_reads_each_call_the_runtime_traced(monkeypatch, calls, expected):
    audit = audit_from_decision(_traced_turn(monkeypatch, calls))

    assert audit.tool_calls == expected


# How the trace titles and marks the span that settled each call, read as the row reads it.
TRACED_TITLES = {
    "allowed, error, sold out, then the guard": [
        ("semantic_trip_search: result", "ok"), ("Tool call failed", "error"),
        ("Hold refused: business rule", "refused"), ("Hold refused: runtime guard", "refused")],
    "cedar denial": [("Hold refused by Cedar policy", "denied")],
    "workload grant": [("Hold refused: workload grant", "refused")],
}


@pytest.mark.parametrize(("calls", "expected"),
                         [(CONTRACT_TURNS[turn][0], TRACED_TITLES[turn]) for turn in TRACED_TITLES],
                         ids=TRACED_TITLES)
def test_the_trace_titles_each_call_from_the_rows_reading(monkeypatch, calls, expected):
    decision = _traced_turn(monkeypatch, calls)

    traced = read_turn(decision).spans

    assert [(span["title"], span["telemetry"]["status"]) for span in traced
            if not span["title"].startswith("AgentCore Gateway: tools/call")] == expected


AGENTCORE = RUNTIME.parents[1] / "agentcore"


def gateway_tools() -> set[str]:
    """Every tool the Gateway serves the Runtime, from the tracked config and its tool schemas."""
    config = json.loads((AGENTCORE / "agentcore.template.json").read_text())
    tools = set()
    for gateway in config["agentCoreGateways"]:
        for target in gateway["targets"]:
            tools |= {tool["name"] for tool in target.get("toolDefinitions", [])}
            schema = (target.get("lambdaFunctionArn") or {}).get("toolSchemaFile")
            if schema:
                path = (AGENTCORE.parent / schema).resolve()
                tools |= {tool["name"] for tool in json.loads(path.read_text())}
    return tools


def test_the_config_names_the_gateway_tools():
    assert {"semantic_trip_search", "get_package_details", "create_courtesy_hold",
            "confirm_booking"} <= gateway_tools()


@pytest.mark.parametrize("tool", sorted(gateway_tools()))
def test_every_gateway_tool_has_a_span_and_a_policy(tool):
    """A tool without a span would run unseen: missing from tool_calls, a denial read as allowed."""
    assert tool in SPANS, f"{tool} has no SPANS entry in turn_trace.py"
    assert tool in POLICY_FOR, f"{tool} has no POLICY_FOR entry in turn_trace.py"


def test_friendly_denial_names_the_policy_or_the_default_deny():
    named = friendly_denial(
        "x [Policy evaluation denied due to meridian_hold_requires_confirmation-k2j_]"
    )
    assert named.startswith("Refused by Cedar policy meridian_hold_requires_confirmation")
    assert "denied it by default" in friendly_denial(
        "[No policy applies to the request (denied by default).]"
    )


def test_booking_arguments_are_pinned_to_the_turn_not_the_model():
    hooks = TraceHooks(
        asyncio.Queue(), _turn(booking_confirmed=False, budget_ceiling_cents=350000)
    )
    event = _event(
        "MeridianHolds___confirm_booking",
        {
            "travelerId": "trv_someone_else",
            "bookingId": "HLD-1",
            "totalCents": 500000,
            "travelerConfirmed": True,
            "budgetCeilingCents": 9999999,
            "journeyRef": "concierge:other",
        },
    )
    hooks.before(event)
    assert event.cancel_tool is False
    args = event.tool_use["input"]
    assert args["travelerId"] == "trv_meridian_demo"
    assert args["travelerConfirmed"] is False
    assert args["budgetCeilingCents"] == 350000
    assert args["journeyRef"] == "concierge:conv-1"
    labels = {f["label"]: f["value"] for f in _drain(hooks.queue)[0][1]["telemetry"]["fields"]}
    assert labels["cedar_policy"] == "meridian_booking_governance"


def test_successful_confirmation_emits_booking_and_result_span():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn(booking_confirmed=True))
    event = _event("MeridianHolds___confirm_booking", {"bookingId": "HLD-1", "totalCents": 1})
    hooks.before(event)
    payload = {
        "booking": {"bookingId": "HLD-1", "status": "confirmed", "replayed": False,
                    "confirmedAt": "2026-09-10 13:00:00+00", "totalAmount": "5000.00"},
        "summary": "Confirmed booking HLD-1",
        "governance": {"subject": "AROA1", "decision": "allow"},
    }
    event.result = {"status": "success", "content": [{"text": json.dumps(payload)}]}
    hooks.after(event)
    items = _drain(queue)
    booking = [item for kind, item in items if kind == "booking"][0]
    assert booking["policyDecision"] == "allow" and booking["booking"]["status"] == "confirmed"
    assert hooks.booking["bookingId"] == "HLD-1" and hooks.booking_settled is True
    assert hooks.hold is None and hooks.hold_settled is False
    result = [span for kind, span in items if kind == "activity"][-1]
    labels = {field["label"]: field["value"] for field in result["telemetry"]["fields"]}
    assert labels["cedar_decision"] == "allow"
    assert labels["cedar_policy"] == "meridian_booking_governance"


def test_default_deny_names_the_booking_conditions_the_arguments_failed():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn(booking_confirmed=False, budget_ceiling_cents=300000))
    event = _event("MeridianHolds___confirm_booking", {"bookingId": "HLD-1", "totalCents": 500000})
    hooks.before(event)
    event.result = {
        "status": "error",
        "content": [{"text": "Tool Execution Denied: [No policy applies to the request (denied by default).]"}],
    }
    hooks.after(event)
    items = _drain(queue)
    result = [span for kind, span in items if kind == "activity"][-1]
    assert result["title"] == "Booking refused by Cedar policy"
    assert result["details"].startswith("No Cedar policy permits this booking")
    assert "has not confirmed this booking" in result["details"]
    assert "12 hours" not in result["details"]
    assert "$5,000.00 exceeds the saved budget ceiling $3,000.00" in result["details"]
    refused = [item for kind, item in items if kind == "booking"][0]
    assert refused["policyDecision"] == "deny" and refused["booking"] is None
    retry = _event("MeridianHolds___confirm_booking", {"bookingId": "HLD-1"}, "t2")
    hooks.before(retry)
    assert "booking was already attempted" in retry.cancel_tool
