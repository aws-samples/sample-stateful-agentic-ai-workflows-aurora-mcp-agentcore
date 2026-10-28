"""A failed run gives its hold back through the Gateway, never with SQL of its own."""

import logging

import pytest

from backend.agentcore.errors import CallerTokenExpired
from backend.agents.phase_05_workflow.governed_hold import RELEASE_TOOL
from backend.agents.phase_05_workflow.governed_tools import GatewayToolError, tool_log_scope
from backend.agents.phase_05_workflow.nodes import (
    RELEASE_LEASE_LOST,
    RELEASE_NO_REQUEST,
    RELEASE_NOT_SENT,
    RELEASE_UNCONFIRMED,

    WorkflowNodes,
)
from backend.agents.phase_05_workflow.runtime_entry import _error
from backend.audit_vocabulary import ToolCall
from tests.phase5_support import GatewayFake, fake_availability, fake_search
from tests.test_workflow_runner import RECOVERY, World, command


@pytest.fixture(autouse=True)
def no_direct_aurora(monkeypatch):
    def forbidden():
        pytest.fail("compensation must not reach Aurora with SQL of its own")

    monkeypatch.setattr("backend.db.rds_data_client.get_rds_data_client", forbidden)


async def explode(state, config=None):
    raise RuntimeError("synthesis exploded")


async def _fail_after_the_hold(world):
    await world.runner().run(command(RECOVERY))
    with pytest.raises(RuntimeError, match="synthesis exploded") as failure:
        await world.runner(synthesize=explode).run(command(RECOVERY, resume=True))
    return failure.value


async def test_a_failed_run_releases_its_hold_through_the_gateway():
    world = World()
    await _fail_after_the_hold(world)
    [held] = [c for c in world.gateway.calls if c["tool"] != RELEASE_TOOL]
    [release] = world.gateway.releases
    assert release.pop("executionId").startswith("exe_t1_")
    assert release == {
        "travelerId": "trv_x", "holdRequestId": held["holdRequestId"], "journeyId": "jrn_test"
    }
    assert world.gateway.receipts[held["holdRequestId"]]["status"] == "released"


async def test_a_lapsed_lease_leaves_the_hold_and_says_so():
    world = World()
    world.gateway.lapsed_leases = True
    await world.runner().run(command(RECOVERY))
    with pytest.raises(RuntimeError, match="synthesis exploded") as failure:
        await world.runner(synthesize=explode).run(command(RECOVERY, resume=True))
    [held] = [c for c in world.gateway.calls if c["tool"] != RELEASE_TOOL]
    assert len(world.gateway.releases) == 1
    assert world.gateway.receipts[held["holdRequestId"]]["status"] == "held"
    assert failure.value.hold_release == RELEASE_LEASE_LOST
    event = _error(failure.value)
    assert event["hold_release"] == RELEASE_LEASE_LOST
    assert event["code"] == "internal"


async def test_a_refused_release_never_hides_the_error_that_caused_it():
    world = World()
    world.gateway.refuse_release = True
    failed = await _fail_after_the_hold(world)
    [held] = [c for c in world.gateway.calls if c["tool"] != RELEASE_TOOL]
    assert len(world.gateway.releases) == 1
    assert world.gateway.receipts[held["holdRequestId"]]["status"] == "held"
    assert failed.hold_release == "hold not released: the Gateway policy denied the call"


def _nodes(gateway):
    return WorkflowNodes(fake_search, fake_availability, gateway_call=gateway)


STATE = {
    "traveler_id": "trv_x", "hold_id": "bk_1", "journey_id": "jrn_1",
    "hold_intent": {"hold_request_id": "hrq_1"},
}


async def test_the_release_names_the_saved_request_and_logs_the_tool():
    gateway = GatewayFake()
    gateway.receipts["hrq_1"] = {"bookingId": "bk_1", "status": "held"}
    with tool_log_scope() as called:
        assert await _nodes(gateway).release_hold(STATE, expected_hold_id="bk_1") is True
    assert called == [ToolCall("release_hold", "allowed")]
    assert gateway.releases == [
        {"travelerId": "trv_x", "holdRequestId": "hrq_1", "journeyId": "jrn_1"}]


async def test_a_snapshot_without_the_request_id_skips_the_call_and_never_sends_the_booking_id():
    gateway = GatewayFake()
    gateway.receipts["bk_1"] = {"bookingId": "bk_1", "status": "held"}
    state = {k: v for k, v in STATE.items() if k != "hold_intent"}
    nodes = _nodes(gateway)
    assert await nodes.release_hold(state, expected_hold_id="bk_1") is False
    assert gateway.releases == [] and gateway.receipts["bk_1"]["status"] == "held"
    assert nodes.release_note == RELEASE_NO_REQUEST
    assert nodes.release_note not in (RELEASE_NOT_SENT, RELEASE_UNCONFIRMED, RELEASE_LEASE_LOST)


@pytest.mark.parametrize(("state", "expected"), [
    ({**STATE, "hold_id": "bk_other"}, "bk_1"),
    ({k: v for k, v in STATE.items() if k != "journey_id"}, "bk_1"),
    ({k: v for k, v in STATE.items() if k != "hold_id"}, None),
    ({k: v for k, v in STATE.items() if k != "traveler_id"}, "bk_1"),
])
async def test_a_release_that_cannot_be_named_calls_nothing(state, expected):
    gateway = GatewayFake()
    assert await _nodes(gateway).release_hold(state, expected_hold_id=expected) is False
    assert gateway.calls == []


async def test_an_unknown_hold_is_not_reported_as_released():
    gateway = GatewayFake()
    assert await _nodes(gateway).release_hold(STATE, expected_hold_id="bk_1") is False


async def test_a_release_with_an_expired_token_logs_no_account_id(caplog):
    gateway = GatewayFake()
    gateway.release_error = CallerTokenExpired("token for 123456789012 expired")
    with caplog.at_level(logging.WARNING):
        assert await _nodes(gateway).release_hold(STATE, expected_hold_id="bk_1") is False
    assert "could not release hold" in caplog.text and "123456789012" not in caplog.text


async def test_the_runs_own_error_survives_a_release_that_cannot_run():
    world = World()
    world.gateway.release_error = CallerTokenExpired("expired")
    await world.runner().run(command(RECOVERY))

    async def expire(state, config=None):
        raise CallerTokenExpired("expired")

    with pytest.raises(CallerTokenExpired):
        await world.runner(synthesize=expire).run(command(RECOVERY, resume=True))
    [held] = [c for c in world.gateway.calls if c["tool"] != RELEASE_TOOL]
    assert len(world.gateway.releases) == 1
    assert world.gateway.receipts[held["holdRequestId"]]["status"] == "held"


@pytest.mark.parametrize("answer", ["lapsed_leases", "journey_guard"])
async def test_a_release_the_fence_refuses_is_not_retried_and_says_the_run_is_not_ours(answer):
    gateway = GatewayFake()
    setattr(gateway, answer, True)
    gateway.receipts["hrq_1"] = {"bookingId": "bk_1", "status": "held"}
    nodes = _nodes(gateway)
    assert await nodes.release_hold(STATE, expected_hold_id="bk_1", execution_id="exe_1") is False
    assert nodes.release_note == RELEASE_LEASE_LOST
    assert len(gateway.releases) == 1
    assert gateway.receipts["hrq_1"]["status"] == "held"


async def test_a_run_that_failed_after_its_booking_was_confirmed_keeps_the_booking():
    world = World()
    await world.runner().run(command(RECOVERY))

    async def confirm_then_explode(state, config=None):
        for receipt in world.gateway.receipts.values():
            receipt["status"] = "confirmed"
        raise RuntimeError("synthesis exploded")

    with pytest.raises(RuntimeError, match="synthesis exploded") as failure:
        await world.runner(synthesize=confirm_then_explode).run(command(RECOVERY, resume=True))
    [held] = [c for c in world.gateway.calls if c["tool"] != RELEASE_TOOL]
    assert world.gateway.receipts[held["holdRequestId"]]["status"] == "confirmed"
    assert len(world.gateway.releases) == 1
    assert world.gateway.replayed_releases == 0
    assert failure.value.hold_release == "hold not released: booking_not_held"


async def test_a_release_that_was_certainly_not_sent_says_so():
    gateway = GatewayFake()
    gateway.release_error = CallerTokenExpired("token for 123456789012 expired")
    nodes = _nodes(gateway)
    assert await nodes.release_hold(STATE, expected_hold_id="bk_1") is False
    assert nodes.release_note == RELEASE_NOT_SENT
    assert "123456789012" not in nodes.release_note


@pytest.mark.parametrize("failure", [
    GatewayToolError("MeridianHolds___release_hold", "timed out", code="timeout"),
    RuntimeError("reset by peer"),
])
async def test_a_release_whose_answer_was_lost_does_not_claim_the_hold_is_kept(failure):
    gateway = GatewayFake()
    gateway.release_error = failure
    nodes = _nodes(gateway)
    assert await nodes.release_hold(STATE, expected_hold_id="bk_1") is False
    assert nodes.release_note == RELEASE_UNCONFIRMED
    assert "not released" not in nodes.release_note


async def test_an_unreadable_release_answer_is_unconfirmed_not_refused():
    gateway = GatewayFake()
    gateway.garbled_release = True
    nodes = _nodes(gateway)
    assert await nodes.release_hold(STATE, expected_hold_id="bk_1") is False
    assert nodes.release_note.startswith("hold release unconfirmed:")


async def test_a_refused_release_says_the_hold_was_not_released():
    gateway = GatewayFake()
    gateway.refuse_release = True
    nodes = _nodes(gateway)
    assert await nodes.release_hold(STATE, expected_hold_id="bk_1") is False
    assert nodes.release_note == "hold not released: the Gateway policy denied the call"


async def test_the_compensation_run_twice_changes_nothing_the_second_time():
    gateway = GatewayFake()
    gateway.receipts["hrq_1"] = {"bookingId": "bk_1", "status": "held"}
    nodes = _nodes(gateway)
    first = await nodes.release_hold(STATE, expected_hold_id="bk_1", execution_id="exe_1")
    second = await nodes.release_hold(STATE, expected_hold_id="bk_1", execution_id="exe_1")
    assert (first, second) == (True, True)
    assert gateway.replayed_releases == 1
    assert gateway.receipts["hrq_1"]["status"] == "released"
    assert nodes.release_note is None
