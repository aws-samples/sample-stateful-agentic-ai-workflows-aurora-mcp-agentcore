"""The one-click hold goes through the runtime and the governed gateway, then persists under RLS."""

import asyncio

from backend.agents.production_04 import concierge as concierge_mod
from backend.agents.production_04.concierge import HoldTarget
from tests.test_production_transaction_boundaries import build_agent, runtime_decision

TARGET = HoldTarget(package_id="CTY-002", duration="7 nights", travelers=2, unit_price_cents=250000)


def test_confirmed_hold_is_sent_to_the_runtime_and_recorded(monkeypatch):
    events, calls = [], []
    held = {"bookingId": "HLD-1", "status": "held", "expiresAt": "2026-09-11T00:00:00+00:00",
            "seatsRemaining": 2}
    decision = runtime_decision(
        message="Held CTY-002 for two travelers.",
        packages=[],
        hold=held,
        policy_decision="allow",
        activities=[{"id": "rt-h", "timestamp": "t", "activity_type": "order",
                     "title": "AgentCore Gateway · tools/call → create_courtesy_hold"}],
    )
    agent = build_agent(events, decision, calls)
    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda **_kw: None)

    outcome = asyncio.run(agent.process_hold("trv_meridian_demo", "conv-9", TARGET))

    assert outcome.hold == held
    assert outcome.policy_decision == "allow"
    assert outcome.message == "Held CTY-002 for two travelers."
    assert outcome.conv_id == "conv-test"
    assert outcome.trace_id == "abc123"
    _args, kwargs = calls[0]
    assert kwargs["hold_confirmed"] is True
    assert kwargs["hold_target"] == {
        "package_id": "CTY-002", "duration": "7 nights", "travelers": 2, "unit_price_cents": 250000,
    }
    assert kwargs["budget_ceiling_cents"] == 640000
    assert events.index("tx-1:commit") < events.index("external:runtime") < events.index("tx-2:open")
    assert "aurora:audit:production_hold" in events
    titles = [entry.title for entry in outcome.activities]
    assert "AgentCore Gateway · tools/call → create_courtesy_hold" in titles
    assert titles[-1] == "Courtesy hold persisted"
    assert outcome.activities[-1].telemetry["status"] == "held"


def test_denied_hold_returns_the_refusal_without_a_hold(monkeypatch):
    events, calls = [], []
    decision = runtime_decision(
        message="I could not place the hold: it is over your saved budget.",
        packages=[],
        hold=None,
        hold_refused="No Cedar policy permits this hold, so the gateway denied it by default.",
        policy_decision="deny",
    )
    agent = build_agent(events, decision, calls)
    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda **_kw: None)

    outcome = asyncio.run(agent.process_hold("trv_meridian_demo", None, TARGET))

    assert outcome.hold is None
    assert outcome.policy_decision == "deny"
    assert "denied it by default" in outcome.refused
    assert outcome.activities[-1].title == "Courtesy hold not placed"
    assert outcome.activities[-1].telemetry["status"] == "denied"
    assert "aurora:audit:production_hold" in events
