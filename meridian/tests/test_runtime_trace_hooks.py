"""The runtime turns every gateway tool call into a trace span and enforces the hold contract."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
sys.path.insert(0, str(RUNTIME))

from turn_trace import TraceHooks, TurnContext, friendly_denial, short  # noqa: E402


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
    assert "already decided" in retry.cancel_tool
    assert "t2" not in hooks.started
    spans_before = len(_drain(queue))
    hooks.after(retry)
    assert len(_drain(queue)) == 0 and spans_before >= 2


def test_friendly_denial_names_the_policy_or_the_default_deny():
    named = friendly_denial(
        "x [Policy evaluation denied due to meridian_hold_requires_confirmation-k2j_]"
    )
    assert named.startswith("Refused by Cedar policy meridian_hold_requires_confirmation")
    assert "denied it by default" in friendly_denial(
        "[No policy applies to the request (denied by default).]"
    )
