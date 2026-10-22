"""The traveler chooses which reviewed alternative the recovery holds.

The graph, its interrupts and Strands' snapshot manager are real. Only retrieval, the
Gateway and the lease store are replaced, as in the other runner tests.
"""

import json
from unittest.mock import AsyncMock

import pytest
from strands.storage import InMemoryStorage

from backend.agents.phase_05_workflow.graph import snapshot_key
from backend.agents.phase_05_workflow.nodes import WorkflowNodes
from backend.agents.phase_05_workflow.runner import (
    WorkflowCommand,
    WorkflowConflictError,
    WorkflowRequestError,
    WorkflowRunner,
)
from backend.demo_prompts import PROMPT_LADDER
from tests.phase5_support import GatewayFake, InMemoryLease

RECOVERY = "My flight was canceled. Rework my Tokyo trip and check availability."
# The presenter's prompt pauses for review right after search, before availability runs.
CANONICAL = PROMPT_LADDER[5].works[0]
RANKED = [
    {"product_id": "pkg_tokyo", "name": "Tokyo", "price": 100,
     "available_sizes": ["7 nights"], "availability": {"7 nights": 10}},
    {"product_id": "pkg_kyoto", "name": "Kyoto", "price": 120,
     "available_sizes": ["3 nights", "5 nights"], "availability": {"3 nights": 1, "5 nights": 6}},
    {"product_id": "pkg_osaka", "name": "Osaka", "price": 90,
     "available_sizes": ["4 nights"], "availability": {"4 nights": 3}},
    {"product_id": "pkg_hakone", "name": "Hakone", "price": 80,
     "available_sizes": ["2 nights"], "availability": {"2 nights": 8}},
]


async def ranked_search(query, limit=5):
    return [dict(package) for package in RANKED], []


async def ranked_availability(query, package_id=None):
    package = next(p for p in RANKED if p["product_id"] == package_id)
    return [dict(package)], [], ""


class World:
    def __init__(self):
        self.storage = InMemoryStorage()
        self.lease = InMemoryLease()
        self.gateway = GatewayFake()

    def runner(self, worker_id="worker-a"):
        nodes = WorkflowNodes(ranked_search, ranked_availability, gateway_call=self.gateway)
        nodes._prepare_governed_hold = AsyncMock(return_value=("jrn_test", 900000))
        nodes._committed_hold = self.gateway.committed_hold
        nodes._booking_status = AsyncMock(return_value=None)
        return WorkflowRunner(
            nodes, storage_for=lambda *args: self.storage, lease=self.lease,
            worker_id=worker_id, heartbeat_seconds=1,
        )

    async def run(self, *, resume=False, chosen=None, worker_id="worker-a", query=RECOVERY):
        return await self.runner(worker_id).run(WorkflowCommand(
            query=query, traveler_id="trv_x", thread_id="t1", resume=resume,
            travelers_count=2, selected_package_id=chosen,
        ))

    async def saved_task(self):
        snapshot = json.loads(await self.storage.read(snapshot_key("t1")))
        return json.loads(snapshot["data"]["state"]["current_task"])


async def test_resuming_with_a_chosen_alternative_holds_that_package_not_the_top_one():
    world = World()
    assert (await world.run())["workflow_status"] == "paused"
    result = await world.run(resume=True, chosen="pkg_kyoto")
    assert result["workflow_status"] == "resumed"
    [call] = world.gateway.calls
    assert (call["packageId"], call["duration"], call["travelers"]) == ("pkg_kyoto", "5 nights", 2)
    assert call["unitPriceCents"] == 12000
    assert result["hold_package"] == "pkg_kyoto"
    assert result["plan_package_id"] == "pkg_kyoto", "the plan is the held choice, not rank one"
    assert [p["product_id"] for p in result["packages"]][:2] == ["pkg_tokyo", "pkg_kyoto"]
    assert (await world.saved_task())["selected_package"] == "pkg_kyoto"


async def test_a_choice_at_the_review_after_search_is_checked_and_held():
    world = World()
    paused = await world.run(query=CANONICAL)
    assert paused["workflow_status"] == "paused"
    assert "Workflow node: availability" not in {a["title"] for a in paused["activities"]}
    result = await world.run(resume=True, chosen="pkg_osaka", query=CANONICAL)
    assert result["workflow_status"] == "resumed"
    assert [(c["packageId"], c["duration"]) for c in world.gateway.calls] == [
        ("pkg_osaka", "4 nights")
    ]


async def test_resuming_without_a_choice_holds_the_top_ranked_package():
    world = World()
    paused = await world.run()
    assert paused["plan_package_id"] is None, "nothing is chosen or held while waiting"
    result = await world.run(resume=True)
    assert result["plan_package_id"] == "pkg_tokyo"
    assert [call["packageId"] for call in world.gateway.calls] == ["pkg_tokyo"]
    assert "selected_package" not in await world.saved_task()


async def _crash(state, config=None):
    raise RuntimeError("worker died")


async def test_the_choice_is_saved_before_any_step_so_a_crash_resume_keeps_it():
    world = World()
    await world.run()
    crashing = world.runner()
    crashing._nodes.prepare_hold = _crash
    with pytest.raises(RuntimeError, match="worker died"):
        await crashing.run(WorkflowCommand(
            query=RECOVERY, traveler_id="trv_x", thread_id="t1", resume=True,
            travelers_count=2, selected_package_id="pkg_osaka",
        ))
    assert (await world.saved_task())["selected_package"] == "pkg_osaka"
    assert world.gateway.calls == []

    await world.run(resume=True, worker_id="worker-b")
    assert [call["packageId"] for call in world.gateway.calls] == ["pkg_osaka"]


@pytest.mark.parametrize("chosen", ["pkg_hakone", "pkg_unknown", ""])
async def test_a_choice_outside_the_reviewed_shortlist_is_refused_before_any_work(chosen):
    world = World()
    await world.run()
    executions = len(world.lease.executions)
    with pytest.raises(WorkflowRequestError, match="pkg_tokyo, pkg_kyoto, pkg_osaka"):
        await world.run(resume=True, chosen=chosen)
    assert len(world.lease.executions) == executions, "refused before claiming the thread"
    assert world.gateway.calls == []
    assert "selected_package" not in await world.saved_task()


async def test_a_choice_on_a_fresh_start_is_refused():
    with pytest.raises(WorkflowRequestError, match="resum"):
        await World().run(chosen="pkg_kyoto")


async def test_once_the_run_went_past_the_review_the_choice_cannot_change():
    world = World()
    await world.run()
    crashing = world.runner()
    crashing._nodes.hold = _crash
    with pytest.raises(RuntimeError, match="worker died"):
        await crashing.run(WorkflowCommand(
            query=RECOVERY, traveler_id="trv_x", thread_id="t1", resume=True,
            travelers_count=2, selected_package_id="pkg_kyoto",
        ))
    with pytest.raises(WorkflowConflictError, match="continues with pkg_kyoto"):
        await world.run(resume=True, chosen="pkg_osaka")
    same = await world.run(resume=True, chosen="pkg_kyoto")
    assert same["hold_package"] == "pkg_kyoto"
    assert [call["packageId"] for call in world.gateway.calls] == ["pkg_kyoto"]


async def test_a_run_that_went_past_the_review_without_a_choice_keeps_the_top_package():
    world = World()
    await world.run()
    crashing = world.runner()
    crashing._nodes.hold = _crash
    with pytest.raises(RuntimeError, match="worker died"):
        await crashing.run(WorkflowCommand(
            query=RECOVERY, traveler_id="trv_x", thread_id="t1", resume=True, travelers_count=2,
        ))
    with pytest.raises(WorkflowConflictError, match="its top-ranked package"):
        await world.run(resume=True, chosen="pkg_kyoto")
