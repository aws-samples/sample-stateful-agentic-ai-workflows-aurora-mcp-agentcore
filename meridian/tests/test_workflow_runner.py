"""The runner: lease, ownership, conflicts, review, resume, replay, compensation, timings."""

import asyncio
import dataclasses
import json
import logging
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from strands.storage import InMemoryStorage

from backend.agents.phase_05_workflow.governed_hold import HoldOutcomeUnknown
from backend.agents.phase_05_workflow.graph import run_task, snapshot_key
from backend.agents.phase_05_workflow.nodes import WorkflowNodes
from backend.agents.phase_05_workflow.runner import (
    WorkflowCommand,
    WorkflowConflictError,
    WorkflowRequestError,
    WorkflowRunner,
)
from backend.agents.phase_05_workflow.state import WorkflowAuthorizationError
from backend.db.journey_store import ExecutionLeaseLostError
from backend.demo_prompts import PROMPT_LADDER
from tests.phase5_support import GatewayFake, InMemoryLease, fake_availability, fake_search

CANONICAL = PROMPT_LADDER[5].works[0]
RECOVERY = "My flight was canceled. Rework my Tokyo trip and check availability."


class SnapshotAppearsOnClaim(InMemoryLease):
    """A lease whose claim lands after another request saved progress for the thread."""

    def __init__(self, storage, key, snapshot):
        super().__init__()
        self._storage, self._key, self._snapshot = storage, key, snapshot

    async def claim(self, *args, **kwargs):
        await self._storage.write(self._key, self._snapshot)
        return await super().claim(*args, **kwargs)


class ServedReads:
    """Storage whose snapshot reads return the given snapshots in order, then the last again."""

    def __init__(self, inner, snapshots):
        self._inner, self._snapshots = inner, snapshots

    async def read(self, key):
        return self._snapshots.pop(0) if len(self._snapshots) > 1 else self._snapshots[0]

    def __getattr__(self, name):
        return getattr(self._inner, name)


class World:
    """One storage and lease store shared by every 'process' in a test."""

    def __init__(self):
        self.storages = {}
        self.served = {}
        self.lease = InMemoryLease()
        self.gateway = GatewayFake()

    def storage_for(self, thread_id, traveler_id, execution_id, worker_id, on_write):
        storage = self.storages.setdefault(thread_id, InMemoryStorage())
        if thread_id in self.served:
            return ServedReads(storage, self.served[thread_id])
        return storage

    async def pause_at_review(self, thread):
        """Run a fresh recovery to its REVIEW pause."""
        await self.runner().run(command(RECOVERY, thread=thread))

    async def finish(self, thread):
        """Resume the thread until it completes."""
        for _ in range(5):
            state = await self.runner().run(command(RECOVERY, resume=True, thread=thread))
            if state["workflow_status"] != "paused":
                return
        raise AssertionError(f"Thread {thread} was still paused after 5 resumes")

    async def snapshot(self, thread):
        return await self.storages[thread].read(snapshot_key(thread))

    def serve_reads(self, thread, snapshots):
        """Make decision reads of ``thread`` return ``snapshots`` in order."""
        self.served[thread] = list(snapshots)

    def released_statuses(self, thread):
        return [e["status"] for e in self.lease.executions if e["thread_id"] == thread]

    def runner(
        self, worker_id="worker-a", *, gateway=None, release=None, synthesize=None,
        pause_after=None,
    ):
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
            pause_after=pause_after,
        )


def command(query=CANONICAL, *, resume=False, traveler="trv_x", thread="t1", **kw):
    return WorkflowCommand(
        query=query, traveler_id=traveler, thread_id=thread, resume=resume, travelers_count=2, **kw
    )


async def test_a_paused_run_releases_its_lease_as_paused():
    world = World()
    result = await world.runner().run(command())
    assert result["workflow_status"] == "paused"
    assert result["response"].startswith("Workflow paused after a saved step.")
    assert result["activities"][-1]["title"] == "Workflow paused at a saved step"
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
    assert result["response"].startswith("Continued from the saved availability step.")
    assert "checkpoint" not in result["response"].lower()
    assert result["activities"][-1]["title"] == "Workflow resumed from a saved step"
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
    with pytest.raises(WorkflowConflictError, match="already has saved progress") as raised:
        await world.runner().run(command())
    assert "checkpoint" not in str(raised.value).lower()
    assert "saved step" in str(raised.value)


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
    checkpoint_spans = [a for a in result["activities"] if a["title"].startswith("Snapshot saved: ")]
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


async def test_a_snapshot_without_a_recorded_owner_cannot_be_resumed():
    world = World()
    ownerless = run_task(
        query=CANONICAL, conversation_id="t1", journey_id="j", travelers_count=2
    )
    envelope = {
        "data": {
            "state": {
                "current_task": ownerless,
                "next_nodes_to_execute": ["availability"],
            }
        }
    }
    storage = world.storages.setdefault("t1", InMemoryStorage())
    await storage.write(snapshot_key("t1"), json.dumps(envelope).encode())
    with pytest.raises(WorkflowAuthorizationError, match="no recorded owner"):
        await world.runner().run(command("Resume workflow", resume=True))
    assert world.lease.executions == []
    assert world.gateway.calls == []


async def test_a_thread_bound_to_another_travelers_journey_is_refused_without_a_snapshot():
    world = World()
    world.lease.traveler_threads["t1"] = "trv_other"
    with pytest.raises(WorkflowAuthorizationError, match="another journey"):
        await world.runner().run(command())
    assert world.lease.executions == []
    assert world.gateway.calls == []


async def test_progress_saved_between_the_read_and_the_claim_conflicts_and_fails_the_claim():
    world = World()
    await world.runner().run(command(thread="donor"))
    donor = world.storages["donor"]
    snapshot = await donor.read(snapshot_key("donor"))
    storage = world.storages.setdefault("t1", InMemoryStorage())
    world.lease = SnapshotAppearsOnClaim(storage, snapshot_key("t1"), snapshot)
    with pytest.raises(WorkflowConflictError, match="now has saved progress"):
        await world.runner().run(command())
    assert [e["status"] for e in world.lease.executions] == ["failed"]
    assert world.gateway.calls == []


async def test_a_resume_keeps_the_saved_party_size():
    world = World()
    start = WorkflowCommand(
        query=RECOVERY, traveler_id="trv_x", thread_id="t1", travelers_count=3
    )
    await world.runner().run(start)
    resume = WorkflowCommand(
        query="Resume workflow", traveler_id="trv_x", thread_id="t1", resume=True,
        travelers_count=1,
    )
    await world.runner("worker-b").run(resume)
    assert [call["travelers"] for call in world.gateway.calls] == [3]


async def test_resuming_a_pause_after_pause_is_not_consent_to_the_hold():
    """Owner requirement: only an answered review or confirmation lets a hold through."""
    world = World()
    paused = await world.runner(pause_after="classify").run(command(RECOVERY))
    assert paused["workflow_status"] == "paused"
    assert world.gateway.calls == []

    stopped = await world.runner().run(command(RECOVERY, resume=True))
    assert stopped["workflow_status"] == "paused"
    assert "prepare_hold" in stopped["response"]
    assert world.gateway.calls == []

    placed = await world.runner().run(command(RECOVERY, resume=True))
    assert placed["workflow_status"] == "resumed"
    assert len(world.gateway.calls) == 1
    assert world.gateway.calls[0]["travelerConfirmed"] is True


async def test_a_pause_after_pause_taken_after_confirm_resumes_without_asking_again():
    """The kill-and-resume path: CONFIRM, then pause_after=hold, then a plain resume."""
    world = World()
    await world.runner().run(command(RECOVERY))
    held = await world.runner(pause_after="hold").run(command(RECOVERY, resume=True))
    assert held["workflow_status"] == "paused"
    assert len(world.gateway.calls) == 1

    done = await world.runner().run(command(RECOVERY, resume=True))
    assert done["workflow_status"] == "resumed"
    assert len(world.gateway.calls) == 1
    assert done["hold_id"] == world.gateway.calls[0]["bookingId"]


class LeaseLostAfterFirstWrite:
    """A storage whose second snapshot write finds that another worker took the thread."""

    def __init__(self, inner):
        self._inner, self._writes = inner, 0

    async def write(self, key, data):
        self._writes += 1
        if self._writes > 1:
            raise ExecutionLeaseLostError("exe_t1_1 no longer runs thread t1")
        await self._inner.write(key, data)

    def __getattr__(self, name):
        return getattr(self._inner, name)


async def test_a_snapshot_write_refused_for_a_lost_lease_stops_the_run_as_lease_loss():
    world = World()
    ran, released = [], []

    async def release(state, *, expected_hold_id=None):
        released.append(expected_hold_id)
        return True

    runner = world.runner(release=release)
    for name in ("classify", "search", "availability", "prepare_hold", "hold", "synthesize"):
        original = getattr(runner._nodes, name)

        async def spy(state, config=None, _original=original, _name=name):
            ran.append(_name)
            return await _original(state, config)

        setattr(runner._nodes, name, spy)
    runner._storage_for = lambda *args: LeaseLostAfterFirstWrite(
        world.storages.setdefault("t1", InMemoryStorage())
    )
    with pytest.raises(ExecutionLeaseLostError, match="no longer runs thread t1"):
        await runner.run(command(RECOVERY))
    assert ran == ["classify", "search"]
    assert world.gateway.calls == []
    assert released == []
    assert [e["status"] for e in world.lease.executions] == ["failed"]


@pytest.mark.parametrize("thread", ["a/b", "../x", "..", ".", "   "])
async def test_a_thread_id_strands_rejects_is_refused_before_any_claim(thread):
    world = World()
    with pytest.raises(WorkflowRequestError, match="thread_id"):
        await world.runner().run(command(thread=thread))
    assert world.lease.traveler_threads == {}
    assert world.lease.executions == []
    assert world.storages == {}


async def test_a_failed_release_does_not_hide_the_lease_loss():
    world = World()
    world.lease.renewals_left = 0

    async def release(*args, **kwargs):
        raise RuntimeError("aurora blip during release")

    world.lease.release = release

    async def slow(state, config=None):
        await asyncio.sleep(3)
        return {"response": "late", "activities": state["activities"]}

    with pytest.raises(ExecutionLeaseLostError):
        await world.runner(synthesize=slow).run(command("Find me a romantic trip to Paris"))


async def test_a_failed_release_is_logged_with_its_execution(caplog):
    world = World()

    async def release(*args, **kwargs):
        raise RuntimeError("aurora blip during release")

    world.lease.release = release
    with caplog.at_level(logging.ERROR, logger="backend.agents.phase_05_workflow.runner"):
        result = await world.runner().run(command("Find me a romantic trip to Paris"))
    assert result["workflow_status"] == "complete"
    assert any(
        "exe_t1_1" in record.getMessage() and "t1" in record.getMessage()
        for record in caplog.records
    )


class _Node:
    def __init__(self, node_id):
        self.node_id = node_id


def _unfinished_graph(*, interrupted=()):
    state = SimpleNamespace(
        completed_nodes=set(), interrupted_nodes={_Node(n) for n in interrupted},
        task="{}", execution_order=[], results={},
    )
    return SimpleNamespace(state=state)


async def test_a_graph_that_ended_unfinished_with_nothing_waiting_is_an_error():
    runner = World().runner()
    with pytest.raises(RuntimeError, match="ended without finishing"):
        runner._outcome(
            command=command(), graph=_unfinished_graph(), prior=None, previous_worker=None,
            claim=SimpleNamespace(execution_id="exe_1"), activities=[],
        )


async def test_a_graph_waiting_on_an_interrupt_is_reported_paused():
    runner = World().runner()
    result = runner._outcome(
        command=command(), graph=_unfinished_graph(interrupted=["prepare_hold"]), prior=None,
        previous_worker=None, claim=SimpleNamespace(execution_id="exe_1"), activities=[],
    )
    assert result["workflow_status"] == "paused"
    assert "continue with prepare_hold." in result["response"]


async def test_a_corrupt_current_task_means_no_recorded_owner():
    world = World()
    envelope = {"data": {"state": {
        "current_task": "{not json", "next_nodes_to_execute": ["availability"],
    }}}
    storage = world.storages.setdefault("t1", InMemoryStorage())
    await storage.write(snapshot_key("t1"), json.dumps(envelope).encode())
    with pytest.raises(WorkflowAuthorizationError, match="no recorded owner"):
        await world.runner().run(command("Resume workflow", resume=True))
    assert world.lease.executions == []
    assert world.gateway.calls == []


async def test_cancelling_the_run_inside_the_hold_fails_the_lease_and_a_resume_replays_it():
    """A cancelled run did not finish and its hold outcome is unknown, so the lease is
    released as ``failed``: that frees the thread at once and records no success. No
    compensation runs, because the hold may have committed; the resume replays it."""
    world = World()
    await world.runner("worker-a").run(command(RECOVERY))
    entered, proceed, done = threading.Event(), threading.Event(), threading.Event()

    def blocked(name, arguments):
        entered.set()
        proceed.wait(5)
        try:
            return world.gateway(name, arguments)
        finally:
            done.set()

    task = asyncio.create_task(
        world.runner("worker-a", gateway=blocked).run(command(RECOVERY, resume=True))
    )
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    proceed.set()
    await asyncio.to_thread(done.wait, 5)
    assert [e["status"] for e in world.lease.executions] == ["paused", "failed"]

    result = await world.runner("worker-b").run(command(RECOVERY, resume=True))
    assert result["workflow_status"] == "resumed"
    assert len({call["holdRequestId"] for call in world.gateway.calls}) == 1
    assert len(world.gateway.receipts) == 1
    assert result["hold_id"] == world.gateway.calls[0]["bookingId"]
    assert [e["status"] for e in world.lease.executions] == ["paused", "failed", "succeeded"]


def test_the_command_carries_no_pause_point():
    assert "pause_after" not in {f.name for f in dataclasses.fields(WorkflowCommand)}


def test_a_runner_refuses_a_pause_point_that_never_pauses():
    world = World()
    with pytest.raises(ValueError, match="synthesize"):
        WorkflowRunner(
            world.runner()._nodes, storage_for=world.storage_for, lease=InMemoryLease(),
            pause_after="synthesize",
        )


async def test_a_resume_decides_from_the_snapshot_saved_after_its_claim():
    """The first read shows a paused review; by the claim another worker finished the thread."""
    world = World()
    await world.pause_at_review("t-race")
    paused = await world.snapshot("t-race")
    await world.finish("t-race")
    finished = await world.snapshot("t-race")
    calls_before = len(world.gateway.calls)
    statuses_before = len(world.released_statuses("t-race"))
    world.serve_reads("t-race", [paused, finished])

    with pytest.raises(WorkflowConflictError, match="no pending saved step"):
        await world.runner().run(command(RECOVERY, resume=True, thread="t-race"))
    assert len(world.gateway.calls) == calls_before
    released = world.released_statuses("t-race")
    assert len(released) == statuses_before + 1
    assert released[-1] == "failed"
