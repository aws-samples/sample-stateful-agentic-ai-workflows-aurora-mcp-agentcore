"""A hold past its expiry is reported as expired by reconciliation and by synthesis alike."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.agents.phase_05_workflow import nodes as nodes_module
from backend.agents.phase_05_workflow.nodes import WorkflowNodes
from tests.phase5_support import GatewayFake, fake_availability, fake_search
from tests.test_workflow_nodes import HOLD_CONFIG, PLAN, _hold_state

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
PAST = "2026-11-19 11:45:00+00"
FUTURE = "2026-11-19 12:15:00+00"


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(nodes_module, "utc_now", lambda: NOW)


@pytest.fixture
def nodes():
    gateway = GatewayFake()
    built = WorkflowNodes(fake_search, fake_availability, gateway_call=gateway)
    built._prepare_governed_hold = AsyncMock(return_value=("jrn_test", 400000))
    built._committed_hold = AsyncMock(return_value=None)
    built._booking_status = AsyncMock(return_value=None)
    return built


def _synth_state(**extra):
    return {"query": PLAN, "intent": "plan", "packages": [{"product_id": "p"}],
            "availability_checks": 1, "traveler_id": "trv_x", "activities": [],
            "hold_id": "hold_1", "hold_package": "p", "hold_duration": "7 nights", **extra}


async def test_synthesis_reports_a_persisted_held_row_past_its_expiry_as_expired(nodes):
    nodes._booking_status = AsyncMock(return_value="expired")
    out = await nodes.synthesize(_synth_state(hold_status="held", hold_expires_at=PAST))
    assert "Recorded status: expired" in out["response"]
    assert "Recorded status: held" not in out["response"]


async def test_reconciliation_and_synthesis_agree_on_the_same_lapsed_hold(nodes):
    nodes._committed_hold = AsyncMock(return_value={
        "booking_id": "hold_1", "status": "held", "expires_at": PAST,
        "created_at": "2026-11-19 11:30:00+00", "observed_at": "2026-11-19 12:00:00+00",
        "lapsed": True,
    })
    config = {"configurable": {**HOLD_CONFIG["configurable"], "traveler_confirmed": True}}
    held = await nodes.hold(_hold_state(), config)
    assert held["hold_status"] == "expired"

    nodes._booking_status = AsyncMock(return_value="expired")
    out = await nodes.synthesize(_synth_state(
        hold_status=held["hold_status"], hold_expires_at=held["hold_expires_at"]))
    assert "Recorded status: expired" in out["response"]


async def test_an_active_hold_stays_held(nodes):
    nodes._booking_status = AsyncMock(return_value="held")
    out = await nodes.synthesize(_synth_state(hold_status="held", hold_expires_at=FUTURE))
    assert "Recorded status: held" in out["response"]
    assert "expired" not in out["response"]


async def test_a_confirmed_booking_stays_confirmed_after_its_hold_window(nodes):
    nodes._booking_status = AsyncMock(return_value="confirmed")
    out = await nodes.synthesize(_synth_state(hold_status="held", hold_expires_at=PAST))
    assert "Recorded status: confirmed" in out["response"]


async def test_a_failed_readback_still_reports_a_lapsed_saved_hold_as_expired(nodes):
    nodes._booking_status = AsyncMock(side_effect=RuntimeError("aurora down"))
    out = await nodes.synthesize(_synth_state(hold_status="held", hold_expires_at=PAST))
    assert "Recorded status: expired" in out["response"]


async def test_an_aurora_held_verdict_stands_when_the_worker_clock_is_past_expiry(nodes):
    nodes._booking_status = AsyncMock(return_value="held")
    state = _synth_state(hold_status="held", hold_expires_at=PAST)
    assert await nodes._current_hold_status(state) == "held"
    out = await nodes.synthesize(state)
    assert out["hold_status"] == "held"
    assert "Recorded status: held" in out["response"]


async def test_a_saved_hold_expiring_exactly_now_reads_as_expired_when_the_readback_fails(nodes):
    nodes._booking_status = AsyncMock(side_effect=RuntimeError("aurora down"))
    out = await nodes.synthesize(
        _synth_state(hold_status="held", hold_expires_at="2026-11-19T12:00:00Z"))
    assert "Recorded status: expired" in out["response"]


@pytest.mark.parametrize("expires_at", [None, "", "not a time"])
async def test_an_unreadable_expiry_does_not_change_the_stored_status(nodes, expires_at):
    nodes._booking_status = AsyncMock(return_value="held")
    out = await nodes.synthesize(_synth_state(hold_status="held", hold_expires_at=expires_at))
    assert "Recorded status: held" in out["response"]


def test_only_a_lapsed_held_status_becomes_expired():
    from backend.agents.phase_05_workflow.nodes import effective_hold_status

    assert effective_hold_status("held", lapsed=True) == "expired"
    assert effective_hold_status("held", lapsed=False) == "held"
    assert effective_hold_status("released", lapsed=True) == "released"
    assert effective_hold_status("confirmed", lapsed=True) == "confirmed"


@pytest.mark.parametrize(("aurora", "expires_at", "recorded"), [
    ("expired", PAST, "expired"),
    ("held", FUTURE, "held"),
    ("confirmed", PAST, "confirmed"),
])
async def test_the_saved_state_records_the_status_the_message_reports(
    nodes, aurora, expires_at, recorded
):
    nodes._booking_status = AsyncMock(return_value=aurora)
    out = await nodes.synthesize(_synth_state(hold_status="held", hold_expires_at=expires_at))
    assert out["hold_status"] == recorded
    assert f"Recorded status: {recorded}" in out["response"]


async def test_a_lapsed_hold_message_says_it_lapsed_and_a_new_hold_is_needed(nodes):
    nodes._booking_status = AsyncMock(return_value="expired")
    out = await nodes.synthesize(_synth_state(hold_status="held", hold_expires_at=PAST))
    assert "lapsed" in out["response"]
    assert "Place a new hold" in out["response"]
    assert "Read the current receipt" not in out["response"]


async def test_an_active_hold_message_keeps_the_receipt_advice(nodes):
    nodes._booking_status = AsyncMock(return_value="held")
    out = await nodes.synthesize(_synth_state(hold_status="held", hold_expires_at=FUTURE))
    assert "Read the current receipt before confirming the trip." in out["response"]
    assert "lapsed" not in out["response"]


async def test_a_run_with_no_hold_saves_no_hold_status(nodes):
    out = await nodes.synthesize({
        "query": PLAN, "intent": "plan", "packages": [{"product_id": "p"}],
        "availability_checks": 1, "traveler_id": "trv_x", "activities": [],
    })
    assert "hold_status" not in out


def test_the_final_read_takes_the_lapse_from_auroras_clock():
    assert "CURRENT_TIMESTAMP" in nodes_module.BOOKING_STATUS_SQL
