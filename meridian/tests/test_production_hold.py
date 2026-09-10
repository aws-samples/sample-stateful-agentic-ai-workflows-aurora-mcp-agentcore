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


def test_confirmed_booking_is_sent_to_the_runtime_and_recorded(monkeypatch):
    from backend.agents.production_04.concierge import BookingTarget

    events, calls = [], []
    booked = {"bookingId": "HLD-1", "status": "confirmed", "confirmedAt": "2026-09-10 13:00:00+00",
              "totalAmount": "5000.00"}
    decision = runtime_decision(
        message="Booked CTY-002 for two travelers.",
        packages=[],
        booking=booked,
        policy_decision="allow",
        activities=[{"id": "rt-b", "timestamp": "t", "activity_type": "order",
                     "title": "AgentCore Gateway · tools/call → confirm_booking"}],
    )
    agent = build_agent(events, decision, calls)
    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda **_kw: None)
    target = BookingTarget(booking_id="HLD-1", total_cents=500000, package_id="CTY-002",
                           duration="7 nights", travelers=2)

    outcome = asyncio.run(agent.process_booking("trv_meridian_demo", "conv-9", target))

    assert outcome.booking == booked
    assert outcome.policy_decision == "allow"
    assert outcome.message == "Booked CTY-002 for two travelers."
    _args, kwargs = calls[0]
    assert kwargs["booking_confirmed"] is True
    assert "hold_confirmed" not in kwargs
    assert kwargs["booking_target"] == {
        "booking_id": "HLD-1", "total_cents": 500000, "package_id": "CTY-002",
        "duration": "7 nights", "travelers": 2,
    }
    assert kwargs["budget_ceiling_cents"] == 640000
    assert events.index("tx-1:commit") < events.index("external:runtime") < events.index("tx-2:open")
    assert "aurora:audit:production_booking" in events
    titles = [entry.title for entry in outcome.activities]
    assert "AgentCore Gateway · tools/call → confirm_booking" in titles
    assert titles[-1] == "Booking confirmed in Aurora"
    assert outcome.activities[-1].telemetry["status"] == "confirmed"


def test_denied_booking_returns_the_refusal_without_a_booking(monkeypatch):
    from backend.agents.production_04.concierge import BookingTarget

    events, calls = [], []
    decision = runtime_decision(
        message="I could not confirm the booking: it is over your saved budget.",
        packages=[],
        booking=None,
        booking_refused="No Cedar policy permits this booking, so the gateway denied it by default.",
        policy_decision="deny",
    )
    agent = build_agent(events, decision, calls)
    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda **_kw: None)
    target = BookingTarget(booking_id="HLD-1", total_cents=500000, package_id="CTY-002",
                           duration="7 nights", travelers=2)

    outcome = asyncio.run(agent.process_booking("trv_meridian_demo", None, target))

    assert outcome.booking is None
    assert outcome.policy_decision == "deny"
    assert "denied it by default" in outcome.refused
    assert outcome.activities[-1].title == "Booking not confirmed"
    assert outcome.activities[-1].telemetry["status"] == "denied"
