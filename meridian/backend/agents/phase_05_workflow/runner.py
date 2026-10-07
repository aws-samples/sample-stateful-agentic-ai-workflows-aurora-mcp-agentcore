"""Run the Phase 5 graph once: claim the lease, run or resume, release it.

The runner owns what a single invocation needs that the snapshot does not:
who is asking, which execution holds the lease, and how the outcome is
reported. It raises domain errors, never HTTP ones, because the same code runs
inside AgentCore Runtime.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Set

from strands.session import SnapshotSessionManager

from backend.agents.phase_05_workflow.governed_hold import HoldOutcomeUnknown
from backend.agents.phase_05_workflow.graph import (
    CONFIRM_INTERRUPT,
    REVIEW_INTERRUPT,
    ResumableStorage,
    RunContext,
    build_graph,
    fold_state,
    next_nodes,
    node_spans,
    pending_interrupts,
    run_task,
    snapshot_key,
)
from backend.agents.phase_05_workflow.nodes import WorkflowNodes
from backend.agents.phase_05_workflow.state import (
    SNAPSHOT_STORE,
    WorkflowAuthorizationError,
    activity,
    hold_key,
)
from backend.db.journey_store import ExecutionClaim, ExecutionLeaseLostError

logger = logging.getLogger(__name__)

LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 10
WORKER_ID = f"worker-{uuid.uuid4().hex[:8]}"
CHECKPOINT_PREFIX = "Checkpoint · "
CONSENT_INTERRUPTS = frozenset({REVIEW_INTERRUPT, CONFIRM_INTERRUPT})

StorageFactory = Callable[[str, str, Optional[str], str, Callable[[int], None]], Any]
AfterPause = Callable[[Dict[str, Any], ExecutionClaim], Awaitable[None]]


class WorkflowConflictError(RuntimeError):
    """The request conflicts with the thread's saved progress or live owner."""


@dataclass(frozen=True)
class WorkflowCommand:
    """One Phase 5 request.

    Attributes:
        query: The traveler's words.
        traveler_id: The authenticated traveler.
        thread_id: The workflow thread, also the snapshot session.
        resume: The traveler resumed the saved plan; this confirms the hold.
        travelers_count: Party size for a fresh run; a resume keeps the saved one.
        review_only: Pause after search for review.
        pause_after: Pause after this node; used by the scripted proofs.
    """

    query: str
    traveler_id: str
    thread_id: str
    resume: bool = False
    travelers_count: int = 1
    review_only: bool = False
    pause_after: Optional[str] = None


class LeaseStore(Protocol):
    """Journey binding and the per-thread execution lease."""

    async def ensure_journey(self, traveler_id: str, thread_id: str) -> str: ...

    async def claim(
        self, traveler_id: str, journey_id: str, thread_id: str, worker_id: str, lease_seconds: int
    ) -> ExecutionClaim: ...

    async def renew(self, traveler_id: str, execution_id: str, lease_seconds: int) -> bool: ...

    async def release(
        self, traveler_id: str, journey_id: str, execution_id: str, status: str
    ) -> None: ...

    async def previous_worker(
        self, traveler_id: str, thread_id: str, execution_id: str
    ) -> Optional[str]: ...


def _validate(command: WorkflowCommand) -> None:
    count = command.travelers_count
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 20:
        raise ValueError("travelers_count must be an integer between 1 and 20")
    if not command.thread_id:
        raise ValueError("thread_id is required")


def _owner(prior: Optional[Dict[str, Any]]) -> str:
    task = prior["data"]["state"].get("current_task") if prior else None
    return str(json.loads(task).get("traveler_id") or "") if isinstance(task, str) else ""


def _authorize(prior: Optional[Dict[str, Any]], command: WorkflowCommand) -> None:
    """Refuse another traveler's thread, re-checked on every resume."""
    if prior is None:
        return
    owner = _owner(prior)
    if not owner:
        raise WorkflowAuthorizationError(
            f"Thread {command.thread_id} has no recorded owner and cannot be resumed."
        )
    if owner != command.traveler_id:
        raise WorkflowAuthorizationError(f"Thread {command.thread_id} belongs to another traveler.")


def _check_against(prior: Optional[Dict[str, Any]], command: WorkflowCommand) -> None:
    if prior is not None and not command.resume:
        raise WorkflowConflictError(
            "This recovery already has saved progress. Read its journey and resume the same "
            "checkpoint, or start a new recovery."
        )
    if command.resume and not next_nodes(prior):
        raise WorkflowConflictError("This workflow has no pending checkpoint to resume.")


def _traveler_confirmed(prior: Optional[Dict[str, Any]], command: WorkflowCommand) -> bool:
    """A resume confirms the hold only when the traveler answered a review or confirmation.

    That is: the saved run is waiting on a ``REVIEW_INTERRUPT`` or
    ``CONFIRM_INTERRUPT``, or it already got past ``prepare_hold``, which only
    runs after a confirmation. A ``pause_after`` pause is not an answer, and a
    run that crashed before any review was shown confirms nothing, so the gate
    stops it at ``prepare_hold`` and the traveler sees the plan first.
    """
    if not command.resume or prior is None:
        return False
    completed = set(prior["data"]["state"].get("completed_nodes") or [])
    return (
        any(item.get("name") in CONSENT_INTERRUPTS for item in pending_interrupts(prior))
        or _answered_review(prior)
        or "prepare_hold" in completed
    )


def _answered_review(prior: Dict[str, Any]) -> bool:
    """A worker died after the traveler answered a review or confirmation.

    Strands saved the answer with the interrupt state still activated.
    """
    internal = prior["data"]["state"].get("_internal_state") or {}
    interrupt_state = internal.get("interrupt_state") or {}
    interrupts = (interrupt_state.get("interrupts") or {}).values()
    return bool(interrupt_state.get("activated")) and any(
        item.get("name") in CONSENT_INTERRUPTS and item.get("response") is not None
        for item in interrupts
    )


def _timed(
    graph_state: Any, completed_before: Set[str], write_ms: List[int]
) -> List[Dict[str, Any]]:
    """Every span, with this invocation's measured snapshot writes attached in node order."""
    timings = iter(write_ms)
    activities: List[Dict[str, Any]] = []
    for node_id, spans in node_spans(graph_state):
        took = next(timings, None) if node_id not in completed_before else None
        for span in spans:
            if (
                took is not None
                and str(span.get("title", "")).startswith(CHECKPOINT_PREFIX)
                and span.get("execution_time_ms") is None
            ):
                span = {**span, "execution_time_ms": took}
            activities.append(span)
    return activities


def _status_span(
    title: str, details: str, status: str, fields: List[Dict[str, str]]
) -> Dict[str, Any]:
    return activity(
        "result",
        title,
        details=details,
        telemetry={
            "category": "orchestration",
            "component": "Strands Graph",
            "status": status,
            "fields": [
                {"label": "checkpoint_durable", "value": "true"},
                *fields,
                {"label": "checkpointer", "value": SNAPSHOT_STORE},
                {"label": "durability", "value": "Aurora"},
            ],
        },
    )


class WorkflowRunner:
    """Claim, run or resume, and release one Phase 5 execution.

    Args:
        nodes: The graph's steps.
        storage_for: Builds the snapshot storage for a thread and execution.
        lease: Journey binding and the execution lease.
        worker_id: This worker's identity, recorded on the lease and every snapshot.
        lease_seconds: How long a claim survives without a heartbeat.
        heartbeat_seconds: How often the lease is renewed while the graph runs.
    """

    def __init__(
        self,
        nodes: WorkflowNodes,
        *,
        storage_for: StorageFactory,
        lease: LeaseStore,
        worker_id: str = WORKER_ID,
        lease_seconds: int = LEASE_SECONDS,
        heartbeat_seconds: int = HEARTBEAT_SECONDS,
    ) -> None:
        self._nodes = nodes
        self._storage_for = storage_for
        self._lease = lease
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds

    async def run(
        self, command: WorkflowCommand, *, after_pause: Optional[AfterPause] = None
    ) -> Dict[str, Any]:
        """Run a fresh request or resume the saved one.

        Raises:
            ValueError: Invalid party size or thread.
            WorkflowAuthorizationError: The thread belongs to another traveler.
            WorkflowConflictError: Saved progress, nothing to resume, or a live owner.
            ExecutionLeaseLostError: Another worker took the thread mid-run.
            HoldOutcomeUnknown: The hold's outcome must be re-read before retrying.
        """
        _validate(command)
        prior = await self._read_snapshot(command)
        _authorize(prior, command)
        _check_against(prior, command)
        try:
            journey_id = await self._lease.ensure_journey(command.traveler_id, command.thread_id)
        except PermissionError as exc:
            raise WorkflowAuthorizationError(
                "This workflow thread belongs to another journey."
            ) from exc
        claim = await self._lease.claim(
            command.traveler_id, journey_id, command.thread_id, self._worker_id, self._lease_seconds
        )
        if not claim.claimed:
            raise WorkflowConflictError(
                "This recovery is already running. Re-read its progress before resuming."
            )
        return await self._run_claimed(command, journey_id, claim, prior, after_pause)

    async def _read_snapshot(self, command: WorkflowCommand) -> Optional[Dict[str, Any]]:
        # Decisions read the snapshot as saved; the graph itself reads it through
        # ResumableStorage. An answered-but-still-activated review is consent.
        storage = self._storage_for(
            command.thread_id, command.traveler_id, None, self._worker_id, lambda _ms: None
        )
        raw = await storage.read(snapshot_key(command.thread_id))
        return json.loads(raw) if raw else None

    async def _run_claimed(self, command, journey_id, claim, prior, after_pause) -> Dict[str, Any]:
        terminal = "failed"
        work = asyncio.create_task(self._execute(command, journey_id, claim, prior, after_pause))
        pulse = asyncio.create_task(self._heartbeat(command, claim))
        try:
            done, _ = await asyncio.wait((work, pulse), return_when=asyncio.FIRST_COMPLETED)
            if pulse in done:
                await pulse
            result = await work
            terminal = "paused" if result["workflow_status"] == "paused" else "succeeded"
            return result
        finally:
            for task in (work, pulse):
                if not task.done():
                    task.cancel()
            await asyncio.gather(work, pulse, return_exceptions=True)
            # A dead process cannot run this: its lease expires and the next
            # claimant marks it abandoned. A pause releases immediately.
            await self._lease.release(command.traveler_id, journey_id, claim.execution_id, terminal)

    async def _heartbeat(self, command: WorkflowCommand, claim: ExecutionClaim) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            async with asyncio.timeout(self._heartbeat_seconds * 2):
                if not await self._lease.renew(
                    command.traveler_id, claim.execution_id, self._lease_seconds
                ):
                    raise ExecutionLeaseLostError(
                        "The recovery worker lost its lease. Re-read the saved journey."
                    )

    async def _execute(self, command, journey_id, claim, prior, after_pause) -> Dict[str, Any]:
        if not command.resume and await self._read_snapshot(command) is not None:
            # Another request completed between the first read and this claim.
            raise WorkflowConflictError(
                "This recovery now has saved progress. Read its journey before resuming."
            )
        write_ms: List[int] = []
        storage = ResumableStorage(
            self._storage_for(
                command.thread_id,
                command.traveler_id,
                claim.execution_id,
                self._worker_id,
                write_ms.append,
            )
        )
        session = SnapshotSessionManager(
            command.thread_id, storage=storage, multi_agent_save_latest_on="node"
        )
        run = RunContext(
            thread_id=command.thread_id,
            execution_id=claim.execution_id,
            traveler_confirmed=_traveler_confirmed(prior, command),
            review_requested=command.review_only,
            pause_after=command.pause_after,
        )
        graph = build_graph(self._nodes, run, session)
        completed_before = set(
            (prior or {}).get("data", {}).get("state", {}).get("completed_nodes") or []
        )
        try:
            await graph.invoke_async(self._task(command, journey_id, prior))
        except (ExecutionLeaseLostError, HoldOutcomeUnknown):
            raise
        except Exception:
            await self._compensate(graph, command.thread_id)
            raise
        previous = (
            await self._lease.previous_worker(
                command.traveler_id, command.thread_id, claim.execution_id
            )
            if command.resume
            else None
        )
        result = self._outcome(
            command, graph, prior, previous, claim, _timed(graph.state, completed_before, write_ms)
        )
        if after_pause is not None and result["workflow_status"] == "paused":
            await after_pause(result, claim)
        return result

    @staticmethod
    def _task(command: WorkflowCommand, journey_id: str, prior: Optional[Dict[str, Any]]) -> Any:
        pending = pending_interrupts(prior) if command.resume else []
        if pending:
            return [
                {"interruptResponse": {"interruptId": item["id"], "response": "approved"}}
                for item in pending
            ]
        if command.resume:
            return prior["data"]["state"]["current_task"]
        return run_task(
            query=command.query,
            traveler_id=command.traveler_id,
            conversation_id=command.thread_id,
            journey_id=journey_id,
            travelers_count=command.travelers_count,
        )

    async def _compensate(self, graph: Any, thread_id: str) -> None:
        """Give back a hold this run committed before it failed."""
        state = fold_state(graph.state)
        intent = state.get("hold_intent") or {}
        package, duration = state.get("hold_package"), state.get("hold_duration")
        expected = intent.get("booking_id") or (
            hold_key(thread_id, str(package), str(duration)) if package and duration else None
        )
        await self._nodes.release_hold(state, expected_hold_id=expected)

    def _outcome(self, command, graph, prior, previous_worker, claim, activities) -> Dict[str, Any]:
        state = fold_state(graph.state)
        thread_id = command.thread_id
        base = {
            **state,
            "activities": activities,
            "conversation_id": thread_id,
            "execution_id": claim.execution_id,
            "worker_instance_id": self._worker_id,
        }
        finished = "synthesize" in {node.node_id for node in graph.state.completed_nodes}
        if not finished:
            pending = sorted(node.node_id for node in graph.state.interrupted_nodes)
            return {
                **base,
                "workflow_status": "paused",
                "resumed_after_restart": False,
                "resumed_from_checkpoint": None,
                "activities": activities + [_paused_span(thread_id, pending)],
                "response": (
                    f"Workflow paused after a committed checkpoint. Resume thread "
                    f"{thread_id} to continue with {', '.join(pending)}."
                ),
            }
        if not command.resume:
            return {
                **base,
                "workflow_status": "complete",
                "resumed_after_restart": False,
                "resumed_from_checkpoint": None,
            }
        restarted = bool(previous_worker and previous_worker != self._worker_id)
        resumed_nodes = next_nodes(prior)
        return {
            **base,
            "workflow_status": "resumed",
            "resumed_after_restart": restarted,
            "resumed_from_checkpoint": prior.get("created_at"),
            "activities": activities + [_resumed_span(thread_id, resumed_nodes, restarted)],
            "response": (
                f"Continued from the saved {', '.join(resumed_nodes)} checkpoint. "
                "The summary below describes the whole journey.\n\n" + state.get("response", "")
            ),
        }


def _paused_span(thread_id: str, pending: List[str]) -> Dict[str, Any]:
    return _status_span(
        "Workflow paused at checkpoint",
        f"thread_id={thread_id}, next={', '.join(pending)}, store={SNAPSHOT_STORE}",
        "held",
        [
            {"label": "thread_id", "value": thread_id},
            {"label": "next_node", "value": ", ".join(pending)},
        ],
    )


def _resumed_span(thread_id: str, resumed: List[str], restarted: bool) -> Dict[str, Any]:
    observed = "observed" if restarted else "not observed"
    return _status_span(
        "Workflow resumed from checkpoint",
        (
            f"thread_id={thread_id}, resumed={', '.join(resumed)}, "
            f"store={SNAPSHOT_STORE}, worker_restart={observed}"
        ),
        "ok",
        [
            {"label": "thread_id", "value": thread_id},
            {"label": "resumed_nodes", "value": ", ".join(resumed)},
            {"label": "worker_restart", "value": observed},
        ],
    )
