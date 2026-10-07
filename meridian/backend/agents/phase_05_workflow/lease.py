"""The workflow's journey binding and execution lease, in short scoped transactions.

Each call opens its own traveler-scoped session, so the grant check and RLS
apply to every lease statement, and a heartbeat never holds a transaction open
across workflow steps.
"""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from backend.agentcore.identity import get_agentcore_identity
from backend.agents.phase_05_workflow.state import SNAPSHOT_STORE
from backend.db.journey_store import (
    ExecutionClaim,
    ScopedDb,
    claim_execution,
    ensure_journey,
    release_execution,
    renew_lease,
)

PREVIOUS_WORKER_SQL = """
SELECT worker_id FROM journey_executions
 WHERE thread_id = %s AND execution_id <> %s
 ORDER BY attempt DESC LIMIT 1
"""
JOURNEY_RUNNING_SQL = """
UPDATE journeys SET status = 'running', updated_at = CURRENT_TIMESTAMP WHERE journey_id = %s
"""
JOURNEY_SETTLED_SQL = """
UPDATE journeys SET status = %s, updated_at = CURRENT_TIMESTAMP
 WHERE journey_id = %s
   AND NOT EXISTS (SELECT 1 FROM journey_executions WHERE journey_id = %s AND status = 'running')
"""


class AuroraLeaseStore:
    """``LeaseStore`` over ``journey_store``, one scoped session per call."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @asynccontextmanager
    async def _scoped(self, traveler_id: str) -> AsyncIterator[ScopedDb]:
        async with self._client.scoped_session(
            traveler_id=traveler_id,
            agent_type="booking_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as tx:
            yield ScopedDb(self._client, tx)

    async def ensure_journey(self, traveler_id: str, thread_id: str) -> str:
        """Bind the thread to the traveler's journey, creating one if needed."""
        async with self._scoped(traveler_id) as db:
            return await ensure_journey(db, traveler_id, thread_id, SNAPSHOT_STORE)

    async def claim(
        self, traveler_id: str, journey_id: str, thread_id: str, worker_id: str, lease_seconds: int
    ) -> ExecutionClaim:
        """Claim the thread's single running slot and mark the journey running."""
        async with self._scoped(traveler_id) as db:
            claim = await claim_execution(db, journey_id, thread_id, worker_id, lease_seconds)
            if claim.claimed:
                await db.execute(JOURNEY_RUNNING_SQL, (journey_id,))
            return claim

    async def renew(self, traveler_id: str, execution_id: str, lease_seconds: int) -> bool:
        """Extend the lease; False means another worker took the thread."""
        async with self._scoped(traveler_id) as db:
            return await renew_lease(db, execution_id, lease_seconds)

    async def release(
        self, traveler_id: str, journey_id: str, execution_id: str, status: str
    ) -> None:
        """Close the execution and settle the journey once nothing else runs."""
        async with self._scoped(traveler_id) as db:
            await release_execution(db, execution_id, status)
            await db.execute(JOURNEY_SETTLED_SQL, (status, journey_id, journey_id))

    async def previous_worker(
        self, traveler_id: str, thread_id: str, execution_id: str
    ) -> Optional[str]:
        """The worker of the thread's latest other execution."""
        async with self._scoped(traveler_id) as db:
            rows = await db.execute(PREVIOUS_WORKER_SQL, (thread_id, execution_id))
            return str(rows[0]["worker_id"]) if rows else None
