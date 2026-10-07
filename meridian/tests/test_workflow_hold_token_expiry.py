"""An expired token stops the hold cleanly; a fresh one resumes the same saved hold."""

from unittest.mock import AsyncMock

import pytest

from backend.agentcore.errors import CallerTokenExpired
from backend.agents.phase_05_workflow.governed_hold import HoldOutcomeUnknown
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
