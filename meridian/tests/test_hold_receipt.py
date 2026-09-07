"""Readback must report the persisted TTL, including after a node replay.

These tests use an in-memory stand-in for the database, with no AWS calls.
"""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from backend.agents.orchestration_05.workflow import OrchestrationAgent
from backend.db.journey_document import _hold, _pending_decision, _recommendations, _selected_plan


CHECKPOINT = {"status": "committed", "thread_id": "thread-test", "checkpoint_id": "cp-test"}
HOLD_INTENT = {"hold_request_id": "request-test", "package_id": "PKG-1"}


@pytest.mark.parametrize("completed", [
    {"hold_id": "booking-test"},
    {"workflow_status": "complete"},
    {"workflow_status": "resumed"},
])
def test_completed_intent_is_not_a_pending_decision(completed):
    result = _pending_decision(CHECKPOINT, {"hold_intent": HOLD_INTENT, **completed})
    assert result["status"] == "unavailable"


def test_prepared_intent_remains_pending_and_identifies_selected_package():
    values = {"hold_intent": HOLD_INTENT, "workflow_status": "paused"}
    decision = _pending_decision(CHECKPOINT, values)
    assert decision["status"] == "observed"
    assert decision["hold_request_id"] == "request-test"
    selection = _selected_plan(CHECKPOINT, values)
    assert selection["package_id"] == "PKG-1"
    assert selection["source"].endswith("#channel:hold_intent")


def test_completed_hold_keeps_its_selection_without_a_pending_decision():
    values = {"hold_id": "booking-test", "hold_package": "PKG-1", "hold_intent": HOLD_INTENT}
    assert _pending_decision(CHECKPOINT, values)["status"] == "unavailable"
    selection = _selected_plan(CHECKPOINT, values)
    assert selection["package_id"] == "PKG-1"
    assert selection["source"].endswith("#channel:hold_package")


def test_legacy_selection_is_preserved_but_recommendations_are_not_a_selection():
    selection = _selected_plan(CHECKPOINT, {"selected_package": "PKG-1"})
    assert selection["package_id"] == "PKG-1"
    assert selection["source"].endswith("#channel:selected_package")
    assert _selected_plan(CHECKPOINT, {"packages": [{"product_id": "PKG-1"}]})["status"] == "unavailable"


@pytest.mark.parametrize("channel", ["packages", "recommendations"])
def test_recommendation_evidence_cites_its_actual_checkpoint_channel(channel):
    packages = [{"product_id": "PKG-1"}]
    result = _recommendations(CHECKPOINT, {channel: packages})
    assert result["items"] == packages
    assert result["source"].endswith(f"#channel:{channel}")


@pytest.mark.parametrize("replayed", [False, True])
def test_hold_reports_saved_receipt_even_when_it_is_old(monkeypatch, replayed):
    calls = []
    receipt = {
        "status": "held",
        "created_at": "2026-09-06 12:00:00+00",
        "hold_expires_at": "2026-09-06 12:15:00+00",
        "observed_at": "2026-09-06 12:20:00+00",
    }

    @asynccontextmanager
    async def scoped_session(**kwargs):
        assert kwargs["traveler_id"] == "traveler-test"
        yield "transaction-test"

    async def execute(sql, params, **kwargs):
        calls.append((sql, params))
        assert kwargs["transaction_id"] == "transaction-test"
        if "create_courtesy_hold" in sql:
            return [{"booking_id": "persisted-booking", "status": "held", "replayed": replayed,
                     "seats_remaining": None if replayed else 5}]
        assert "FROM bookings" in sql
        assert params == ("persisted-booking", "traveler-test")
        return [receipt]

    monkeypatch.setattr("backend.db.rds_data_client.get_rds_data_client", lambda: SimpleNamespace(scoped_session=scoped_session, execute=execute))
    monkeypatch.setattr("backend.agentcore.identity.get_agentcore_identity", lambda: SimpleNamespace(authorization_context=lambda: {}))
    agent = OrchestrationAgent.__new__(OrchestrationAgent)
    agent.checkpointer_kind = "unit-test"
    agent.checkpointer_durable = False
    result = asyncio.run(agent._node_hold({
        "traveler_id": "traveler-test", "conversation_id": "thread-test", "journey_id": "journey-test",
        "packages": [{"product_id": "PKG-1", "price": 100, "available_sizes": ["2 nights"]}],
        "hold_intent": {"package_id": "PKG-1", "duration": "2 nights", "quantity": 2, "unit_price": 100, "hold_request_id": "request-test", "fingerprint": "test"},
    }))
    assert result["hold_id"] == "persisted-booking"
    assert result["hold_expires_at"] == receipt["hold_expires_at"]
    assert result["hold_created_at"] == receipt["created_at"]
    assert result["hold_observed_at"] == receipt["observed_at"]
    assert result["hold_seats_remaining"] == (None if replayed else 5)
    fields = {f["label"]: f["value"] for f in result["activities"][0]["telemetry"]["fields"]}
    assert fields["expires_at"] == receipt["hold_expires_at"]
    assert fields["replayed"] == ("yes" if replayed else "no")


def test_evidence_document_exposes_the_booking_timestamps():
    async def query(sql, params):
        assert params == ("journey-test",)
        return [{"status": "held", "hold_request_id": "req-1", "booking_id": "booking-1", "execution_id": "exec-1",
                 "hold_created_at": "2026-09-06 12:00:00+00", "hold_expires_at": "2026-09-06 12:15:00+00",
                 "observed_at": "2026-09-06 12:05:00+00", "package_id": "PKG-1", "duration": "2 nights", "travelers_count": 2}]
    hold = asyncio.run(_hold(query, "journey-test"))
    assert hold["hold_created_at"] == "2026-09-06 12:00:00+00"
    assert hold["hold_expires_at"] == "2026-09-06 12:15:00+00"
    assert hold["observed_at"] == "2026-09-06 12:05:00+00"
    assert hold["created_by_execution_id"] == "exec-1"
