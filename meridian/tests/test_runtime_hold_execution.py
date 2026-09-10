"""A confirmed hold is placed by the platform with pinned arguments; the model only narrates."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
sys.path.insert(0, str(RUNTIME))

from hold_execution import HOLD_TOOL, confirmed_hold_arguments, execute_confirmed_hold  # noqa: E402
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
    assert "already decided" in outcome
