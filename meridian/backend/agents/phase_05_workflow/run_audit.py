"""One ``agent_audit_log`` row for each workflow run that ran something.

The row names the signed-in person (the verified Amazon Cognito subject), the traveler, the
workload (the Runtime role), every Gateway call the run made and how each ended
(``tool_calls``), and the error that ended the run if one did (``turn_error``). Its
``authorization_decision`` comes from ``backend.audit_vocabulary.authorization_decision``, so a
row can never say ``allow`` for a run that failed. A request that ran nothing for the caller
(malformed, or a thread another run holds) writes no row.

Two limits follow from writing the row when the run ends. A process that dies hard (a kill, an
out-of-memory stop, a container recycle) writes no row, because nothing is written ahead. And a
Gateway call running in a worker thread when the run is cancelled cannot be recorded as pending:
the row lists the calls that had finished when the cancellation reached the runner, and a thread
that finishes after that (a hold that committed at the Gateway) is missing from it. The hold's
own record in the journey holds the outcome, and the audit vocabulary has no state for a call
still running.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Coroutine, Optional, Protocol, Tuple

from backend.audit_vocabulary import (
    CLASS_NAME_LIMIT,
    ToolCall,
    authorization_decision,
    turn_error_of,
)

OPERATION = "workflow_run"
AGENT_NAME = "WorkflowRunner"
AGENT_TYPE = "booking_agent"
COGNITO = "cognito"


@dataclass(frozen=True)
class RunAuditRecord:
    """What the audit row says about one run.

    Attributes:
        traveler_id: The traveler the run acted for.
        subject: The verified Cognito subject, or None when no person signed in (``iam`` mode).
        tool_calls: One ``ToolCall`` per Gateway call, in the order the calls finished.
        turn_error: The error that ended the run, worded for a row, or None for a completed run.
        rows_returned: How many packages the run returned.
    """

    traveler_id: str
    subject: Optional[str]
    tool_calls: Tuple[ToolCall, ...]
    turn_error: Optional[str]
    rows_returned: int

    @property
    def decision(self) -> str:
        """The row's ``authorization_decision``, from the calls and the run's error."""
        return authorization_decision(self.tool_calls, turn_error=self.turn_error)


class RunAuditor(Protocol):
    """Writes the audit row for a run."""

    async def record(self, record: RunAuditRecord) -> None: ...


def turn_error_for(error: Optional[BaseException]) -> Optional[str]:
    """The row's ``turn_error``: None without an error, else the error worded for a row.

    An error whose own text cannot be read is named by its class alone.
    """
    if error is None:
        return None
    try:
        return turn_error_of(error)
    except Exception:  # noqa: BLE001 - an error's own __str__ may raise; the class still names it
        return type(error).__name__[:CLASS_NAME_LIMIT]


async def finish_write(write: Coroutine[Any, Any, None]) -> None:
    """Run an audit write to its end even if the caller is cancelled while it runs.

    The write runs as a task of its own under ``asyncio.shield``: a cancellation of the caller
    waits for it, however often it arrives, and then proceeds. Phase 4's
    ``_write_through_cancellation`` does the same for its row.
    """
    task = asyncio.create_task(write)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.wait({task})
            except asyncio.CancelledError:
                continue
        raise


class AuroraRunAuditor:
    """Writes the row through ``MemoryStore.write_audit`` inside the traveler's scope.

    Args:
        store: The memory store; defaults to the process-wide one when the row is written.
    """

    def __init__(self, store: Any = None) -> None:
        self._store = store

    async def record(self, record: RunAuditRecord) -> None:
        """Write one ``workflow_run`` row.

        Raises:
            TravelerAuthorizationError: The Runtime role has no grant for the traveler.
        """
        from backend.agentcore.identity import get_agentcore_identity
        from backend.memory.store import get_memory_store

        store = self._store or get_memory_store()
        identity = get_agentcore_identity()
        workload = identity.authorization_context()
        provider, subject = (
            (COGNITO, record.subject) if record.subject
            else (workload.provider, workload.subject_id)
        )
        async with store.db.scoped_session(
            traveler_id=record.traveler_id, agent_type=AGENT_TYPE, authorization=workload
        ) as transaction_id:
            await store.write_audit(
                agent_name=AGENT_NAME,
                operation=OPERATION,
                traveler_id=record.traveler_id,
                rls_traveler=record.traveler_id,
                rls_agent_type=AGENT_TYPE,
                iam_identity=identity.iam_identity(),
                authorization_provider=provider,
                authorization_subject=subject,
                authorization_decision=record.decision,
                rows_returned=record.rows_returned,
                transaction_id=transaction_id,
                tool_calls=list(record.tool_calls),
                turn_error=record.turn_error,
            )
