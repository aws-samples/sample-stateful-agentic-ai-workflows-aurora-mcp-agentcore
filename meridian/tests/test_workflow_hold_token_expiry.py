"""An expired token stops the hold cleanly; a fresh one resumes the same saved hold."""

from unittest.mock import AsyncMock

import pytest

from backend.agentcore.caller_claims import CallerClaimsError
from backend.agentcore.errors import CallerTokenExpired, CallerTokenMissing
from backend.agents.phase_05_workflow.governed_hold import HoldOutcomeUnknown
from backend.agents.phase_05_workflow.governed_tools import GatewayToolError
from backend.agents.phase_05_workflow.nodes import WorkflowNodes
from tests.phase5_support import fake_availability, fake_search
# The in-memory world the runner tests share: one storage and lease store for several workers.
from tests.test_workflow_nodes import HOLD_CONFIG, _hold_state
from tests.test_workflow_runner import RECOVERY, World, command


def expired(name, arguments):
    raise CallerTokenExpired("expired")


async def test_the_hold_node_lets_an_expired_token_through_instead_of_calling_it_unknown():
    nodes = WorkflowNodes(fake_search, fake_availability, gateway_call=expired)
    nodes._prepare_governed_hold = AsyncMock(return_value=("jrn_test", 400000))
    nodes._committed_hold = AsyncMock(return_value=None)
    nodes._booking_status = AsyncMock(return_value=None)
    with pytest.raises(CallerTokenExpired) as raised:
        await nodes.hold(_hold_state(), HOLD_CONFIG)
    assert not isinstance(raised.value, HoldOutcomeUnknown)


async def test_a_run_stopped_by_an_expired_token_resumes_with_a_fresh_one_and_holds_once():
    world = World()
    await world.runner("worker-a").run(command(RECOVERY))

    with pytest.raises(CallerTokenExpired):
        await world.runner("worker-a", gateway=expired).run(command(RECOVERY, resume=True))
    assert world.gateway.calls == []
    result = await world.runner("worker-b").run(command(RECOVERY, resume=True))
    assert result["workflow_status"] == "resumed"
    assert len({call["holdRequestId"] for call in world.gateway.calls}) == 1
    assert [e["status"] for e in world.lease.executions] == ["paused", "failed", "succeeded"]


@pytest.mark.parametrize("error", [CallerTokenMissing("none"), CallerClaimsError("malformed")])
async def test_a_token_refused_before_any_send_is_a_clean_failure_not_an_unknown_outcome(error):
    def refuse(name, arguments):
        raise error

    nodes = WorkflowNodes(fake_search, fake_availability, gateway_call=refuse)
    nodes._prepare_governed_hold = AsyncMock(return_value=("jrn_test", 400000))
    nodes._committed_hold = AsyncMock(return_value=None)
    nodes._booking_status = AsyncMock(return_value=None)
    with pytest.raises(type(error)) as raised:
        await nodes.hold(_hold_state(), HOLD_CONFIG)
    assert not isinstance(raised.value, HoldOutcomeUnknown)


@pytest.mark.parametrize("code", ["unauthorized", "throttled"])
async def test_a_clear_gateway_rejection_is_not_an_unknown_hold_outcome(code):
    def reject(name, arguments):
        raise GatewayToolError(name, "the Gateway rejected the caller's credentials", code=code)

    nodes = WorkflowNodes(fake_search, fake_availability, gateway_call=reject)
    nodes._prepare_governed_hold = AsyncMock(return_value=("jrn_test", 400000))
    nodes._committed_hold = AsyncMock(return_value=None)
    nodes._booking_status = AsyncMock(return_value=None)
    with pytest.raises(GatewayToolError) as raised:
        await nodes.hold(_hold_state(), HOLD_CONFIG)
    assert raised.value.code == code


@pytest.mark.parametrize("code", ["timeout", "rejected"])
async def test_a_gateway_failure_that_may_have_run_the_tool_is_an_unknown_hold_outcome(code):
    def time_out(name, arguments):
        raise GatewayToolError(name, "the Gateway call failed", code=code)

    nodes = WorkflowNodes(fake_search, fake_availability, gateway_call=time_out)
    nodes._prepare_governed_hold = AsyncMock(return_value=("jrn_test", 400000))
    nodes._committed_hold = AsyncMock(return_value=None)
    nodes._booking_status = AsyncMock(return_value=None)
    with pytest.raises(HoldOutcomeUnknown):
        await nodes.hold(_hold_state(), HOLD_CONFIG)
