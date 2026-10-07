"""The runner: lease, ownership, conflicts, review, resume, replay, compensation, timings."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from strands.storage import InMemoryStorage

from backend.agents.phase_05_workflow.governed_hold import HoldOutcomeUnknown
from backend.agents.phase_05_workflow.nodes import WorkflowNodes
from backend.agents.phase_05_workflow.runner import (
    WorkflowCommand,
    WorkflowConflictError,
    WorkflowRunner,
)
from backend.agents.phase_05_workflow.state import WorkflowAuthorizationError
from backend.db.journey_store import ExecutionLeaseLostError
from backend.demo_prompts import PROMPT_LADDER
from tests.phase5_support import GatewayFake, InMemoryLease, fake_availability, fake_search

CANONICAL = PROMPT_LADDER[5].works[0]
RECOVERY = "My flight was canceled. Rework my Tokyo trip and check availability."


class World:
    """One storage and lease store shared by every 'process' in a test."""

    def __init__(self):
        self.storages = {}
        self.lease = InMemoryLease()
        self.gateway = GatewayFake()
        self.writes = []

    def storage_for(self, thread_id, traveler_id, execution_id, worker_id, on_write):
        storage = self.storages.setdefault(thread_id, InMemoryStorage())
        self.writes.append(on_write)
        return storage

    def runner(self, worker_id="worker-a", *, gateway=None, release=None, synthesize=None):
        nodes = WorkflowNodes(fake_search, fake_availability, gateway_call=gateway or self.gateway)
        nodes._prepare_governed_hold = AsyncMock(return_value=("jrn_test", 400000))
        nodes._booking_status = AsyncMock(return_value=None)
        if release is not None:
            nodes.release_hold = release
        if synthesize is not None:
            nodes.synthesize = synthesize
        return WorkflowRunner(
            nodes,
            storage_for=self.storage_for,
            lease=self.lease,
            worker_id=worker_id,
            heartbeat_seconds=1,
        )


def command(query=CANONICAL, *, resume=False, traveler="trv_x", thread="t1", **kw):
    return WorkflowCommand(
        query=query, traveler_id=traveler, thread_id=thread, resume=resume, travelers_count=2, **kw
    )


async def test_a_paused_run_releases_its_lease_as_paused():
    world = World()
    result = await world.runner().run(command())
    assert result["workflow_status"] == "paused"
    assert result["response"].startswith("Workflow paused after a committed checkpoint.")
    assert result["activities"][-1]["title"] == "Workflow paused at checkpoint"
    assert [e["status"] for e in world.lease.executions] == ["paused"]
    assert world.gateway.calls == []


async def test_resume_on_another_worker_holds_once_and_reports_the_restart():
    world = World()
    await world.runner("worker-a").run(command())
    result = await world.runner("worker-b").run(command("Resume workflow", resume=True))
    assert result["workflow_status"] == "resumed"
    assert result["resumed_after_restart"] is True
    assert result["resumed_from_checkpoint"]
    assert result["hold_id"] == world.gateway.calls[0]["bookingId"]
    assert result["response"].startswith("Continued from the saved availability checkpoint.")
    assert result["activities"][-1]["title"] == "Workflow resumed from checkpoint"
    assert [e["status"] for e in world.lease.executions] == ["paused", "succeeded"]


async def test_resume_on_the_same_worker_does_not_claim_a_restart():
    world = World()
    await world.runner("worker-a").run(command())
    result = await world.runner("worker-a").run(command("Resume workflow", resume=True))
    assert result["resumed_after_restart"] is False


async def test_another_traveler_cannot_resume_or_restart_the_thread():
    world = World()
    await world.runner().run(command())
    with pytest.raises(WorkflowAuthorizationError, match="another traveler"):
        await world.runner().run(command(resume=True, traveler="trv_other"))
    with pytest.raises(WorkflowAuthorizationError):
        await world.runner().run(command(traveler="trv_other"))


async def test_saved_progress_cannot_be_overwritten_by_a_fresh_start():
    world = World()
    await world.runner().run(command())
    with pytest.raises(WorkflowConflictError, match="already has saved progress"):
        await world.runner().run(command())


async def test_a_finished_run_has_nothing_to_resume():
    world = World()
    await world.runner().run(command("Find me a romantic trip to Paris"))
    with pytest.raises(WorkflowConflictError, match="no pending"):
        await world.runner().run(command("Resume workflow", resume=True))


async def test_a_busy_thread_is_refused_before_any_work():
    world = World()
    world.lease.busy = True
    with pytest.raises(WorkflowConflictError, match="already running"):
        await world.runner().run(command())
    assert world.gateway.calls == []
    assert world.lease.executions == []
    storage = world.storages.get("t1")
    assert (
        storage is None
        or await storage.read("session/t1/scopes/multiAgent/phase5/snapshots/snapshot_latest.json")
        is None
    )


async def test_lease_loss_stops_the_run_and_records_failure():
    world = World()
    world.lease.renewals_left = 0

    async def slow(state, config=None):
        await asyncio.sleep(3)
        return {"response": "late", "activities": state["activities"]}

    with pytest.raises(ExecutionLeaseLostError):
        await world.runner(synthesize=slow).run(command("Find me a romantic trip to Paris"))
    assert [e["status"] for e in world.lease.executions] == ["failed"]


async def test_a_lost_hold_reply_resumes_the_same_request_on_a_new_worker():
    world = World()
    await world.runner("worker-a").run(command(RECOVERY))

    def lose(name, arguments):
        world.gateway(name, arguments)
        raise TimeoutError("reply lost after the business write")

    with pytest.raises(HoldOutcomeUnknown):
        await world.runner("worker-a", gateway=lose).run(command(RECOVERY, resume=True))
    result = await world.runner("worker-b").run(command(RECOVERY, resume=True))
    assert result["workflow_status"] == "resumed"
    assert len({call["holdRequestId"] for call in world.gateway.calls}) == 1
    assert result["hold_id"] == world.gateway.calls[0]["bookingId"]
    assert [e["status"] for e in world.lease.executions] == ["paused", "failed", "succeeded"]


async def test_a_resume_after_a_crash_before_review_does_not_hold():
    """Owner requirement: the resume of a run that never showed the review confirms nothing."""
    world = World()

    async def crash(state, config=None):
        raise RuntimeError("worker died in availability")

    nodes_crash = world.runner()
    nodes_crash._nodes.availability = crash
    with pytest.raises(RuntimeError, match="worker died"):
        await nodes_crash.run(command(RECOVERY))
    result = await world.runner("worker-b").run(command(RECOVERY, resume=True))
    assert result["workflow_status"] == "paused"
    assert world.gateway.calls == []


async def test_a_failure_after_the_hold_releases_only_this_runs_booking():
    world = World()
    released = []

    async def release(state, *, expected_hold_id=None):
        released.append((state.get("hold_id"), expected_hold_id))
        return True

    async def explode(state, config=None):
        raise RuntimeError("synthesis exploded")

    await world.runner().run(command(RECOVERY))
    with pytest.raises(RuntimeError, match="synthesis exploded"):
        await world.runner(release=release, synthesize=explode).run(command(RECOVERY, resume=True))
    booking = world.gateway.calls[0]["bookingId"]
    assert released == [(booking, booking)]


async def test_snapshot_spans_carry_the_measured_write_time():
    world = World()
    timings = iter([111, 222, 333, 444])

    class Timed:
        """Delegates to the shared storage and reports each write to this call's callback."""

        def __init__(self, inner, on_write):
            self._inner, self._on_write = inner, on_write

        async def write(self, key, data):
            await self._inner.write(key, data)
            self._on_write(next(timings))

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def storage_for(thread_id, traveler_id, execution_id, worker_id, on_write):
        inner = world.storages.setdefault(thread_id, InMemoryStorage())
        return Timed(inner, on_write)

    runner = world.runner()
    runner._storage_for = storage_for
    result = await runner.run(command())
    checkpoint_spans = [a for a in result["activities"] if a["title"].startswith("Checkpoint · ")]
    assert [span["execution_time_ms"] for span in checkpoint_spans] == [222]


@pytest.mark.parametrize("count", [0, 21, True, 1.5])
async def test_party_size_is_validated(count):
    world = World()
    with pytest.raises(ValueError, match="travelers_count"):
        await world.runner().run(
            WorkflowCommand(
                query=CANONICAL, traveler_id="trv_x", thread_id="t1", travelers_count=count
            )
        )
