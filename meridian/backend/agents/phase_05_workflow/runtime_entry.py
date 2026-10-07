"""The MeridianWorkflow Runtime's one event: run or resume a Phase 5 turn.

``workflow_turn`` turns a Runtime payload into a ``WorkflowCommand`` and runs it
on this microVM. It yields a ``heartbeat`` every few seconds while the graph runs,
so the caller's 45 s read timeout never fires, then one ``result`` or one
``error``. An error carries a code instead of an exception, because exceptions
cannot cross the Runtime boundary. The payload names no pause point: only code
that builds a runner can set one.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Optional, Tuple

from backend.agents.phase_05_workflow.governed_hold import HoldOutcomeUnknown
from backend.agents.phase_05_workflow.runner import (
    WorkflowCommand,
    WorkflowConflictError,
    WorkflowRequestError,
    WorkflowRunner,
)
from backend.agents.phase_05_workflow.state import WorkflowAuthorizationError
from backend.db.journey_store import ExecutionLeaseLostError

logger = logging.getLogger(__name__)

WORKFLOW_EVENT = "workflow_turn"
MICROVM_ID = f"vm-{uuid.uuid4().hex[:12]}"
HEARTBEAT_SECONDS = 10
MODES = ("ping", "resume", "start")
TURN_KEYS = frozenset({
    "event", "mode", "thread_id", "traveler_id", "query", "travelers_count", "review_only",
})
RESULT_KEYS = (
    "activities", "packages", "response", "conversation_id", "workflow_status",
    "resumed_after_restart", "resumed_from_checkpoint", "execution_id", "worker_instance_id",
)
ERROR_CODES: Tuple[Tuple[type, str], ...] = (
    (WorkflowRequestError, "request"),
    (WorkflowAuthorizationError, "authorization"),
    (PermissionError, "authorization"),
    (WorkflowConflictError, "conflict"),
    (ExecutionLeaseLostError, "lease_lost"),
    (HoldOutcomeUnknown, "hold_unknown"),
)

RunnerFactory = Callable[[str], WorkflowRunner]


@dataclass(frozen=True)
class TravelerContext:
    """Who a workflow turn acts for.

    Attributes:
        traveler_id: The traveler whose journey the turn reads and writes.
    """

    traveler_id: str


def resolve_traveler(payload: Dict[str, Any], session_id: Optional[str]) -> TravelerContext:
    """Decide which traveler this turn acts for.

    In project A the payload's traveler is trusted. Only principals with
    InvokeAgentRuntime on this Runtime can send one: the backend's App Runner role,
    after it authenticated the caller, and the owner's admin credentials. Project B
    replaces this function with the verified JWT claim.

    Args:
        payload: The Runtime payload.
        session_id: The Runtime session id, unused until project B.

    Raises:
        WorkflowRequestError: The payload names no traveler.
    """
    return TravelerContext(traveler_id=_text(payload, "traveler_id"))


def _text(payload: Dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WorkflowRequestError(f"{key} is required")
    return value


def parse_turn(payload: Any, session_id: Optional[str]) -> Optional[WorkflowCommand]:
    """Return the command a payload asks for, or None for a ping.

    Args:
        payload: The Runtime payload, of any type.
        session_id: The Runtime session id.

    Raises:
        WorkflowRequestError: The payload is not a usable ``workflow_turn``.
    """
    if not isinstance(payload, dict) or payload.get("event") != WORKFLOW_EVENT:
        raise WorkflowRequestError(f"send event {WORKFLOW_EVENT!r}; nothing else runs here")
    unknown = sorted(set(payload) - TURN_KEYS)
    if unknown:
        raise WorkflowRequestError(f"unknown {WORKFLOW_EVENT} fields: {', '.join(unknown)}")
    mode = payload.get("mode")
    if mode not in MODES:
        raise WorkflowRequestError(f"mode must be one of {', '.join(MODES)}")
    if mode == "ping":
        return None
    review_only = payload.get("review_only", False)
    if not isinstance(review_only, bool):
        raise WorkflowRequestError("review_only must be true or false")
    return WorkflowCommand(
        query=_text(payload, "query"),
        traveler_id=resolve_traveler(payload, session_id).traveler_id,
        thread_id=_text(payload, "thread_id"),
        resume=mode == "resume",
        travelers_count=payload.get("travelers_count", 1),
        review_only=review_only,
    )


def _error(exc: BaseException) -> Dict[str, Any]:
    for kind, code in ERROR_CODES:
        if isinstance(exc, kind):
            return {"type": "error", "code": code, "message": str(exc)}
    reference = uuid.uuid4().hex[:8]
    logger.error("reference=<%s> | workflow turn failed", reference, exc_info=exc)
    return {"type": "error", "code": "internal",
            "message": f"The workflow failed. Reference {reference}."}


def default_runner(worker_id: str) -> WorkflowRunner:
    """The production runner, recording ``worker_id`` on its lease and snapshots."""
    from backend.agents.phase_05_workflow.service import build_workflow_runner

    return build_workflow_runner(worker_id=worker_id)


async def _collect(run: Optional["asyncio.Future[Any]"]) -> None:
    """Cancel an unfinished run, then retrieve its outcome so asyncio never reports it lost."""
    if run is None:
        return
    if not run.done():
        run.cancel()
    await asyncio.gather(run, return_exceptions=True)


async def workflow_turn(
    payload: Any,
    *,
    session_id: Optional[str],
    runner_factory: RunnerFactory = default_runner,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
) -> AsyncIterator[Dict[str, Any]]:
    """Run one workflow turn and yield its events.

    Args:
        payload: The Runtime payload.
        session_id: The AgentCore Runtime session id.
        runner_factory: Builds the runner for this worker id.
        heartbeat_seconds: Gap between heartbeat events while the run works.

    Yields:
        Heartbeats, then one result or one error.
    """
    worker_id = f"{session_id or 'local'}/{MICROVM_ID}"
    try:
        command = parse_turn(payload, session_id)
    except WorkflowRequestError as exc:
        yield _error(exc)
        return
    if command is None:
        yield {"type": "result",
               "state": {"workflow_status": "ready", "worker_instance_id": worker_id}}
        return
    run: Optional["asyncio.Future[Dict[str, Any]]"] = None
    try:
        run = asyncio.ensure_future(runner_factory(worker_id).run(command))
        while not run.done():
            await asyncio.wait({run}, timeout=heartbeat_seconds)
            if not run.done():
                yield {"type": "heartbeat", "worker_instance_id": worker_id}
        state = run.result()
    except asyncio.CancelledError as exc:
        if run is None or not run.cancelled():
            raise
        yield _error(exc)
        return
    except Exception as exc:  # noqa: BLE001 - every failure leaves as a coded event
        yield _error(exc)
        return
    finally:
        await _collect(run)
    yield {"type": "result", "state": {key: state.get(key) for key in RESULT_KEYS}}
