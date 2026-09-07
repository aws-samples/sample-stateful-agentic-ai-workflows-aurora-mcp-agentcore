"""Track HTTP workflow executions in Aurora, using short scoped transactions."""
import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import HTTPException

from backend.agentcore.identity import get_agentcore_identity
from backend.db.journey_store import ScopedDb, ensure_journey, claim_execution, renew_lease, release_execution
from backend.db.rds_data_client import get_rds_data_client
from backend.agents.orchestration_05.workflow import WORKER_INSTANCE_ID

LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 10


async def run_http_workflow(workflow, query, traveler_id, conversation_id, *, resume=False, travelers_count=1, after_pause=None):
    """Claim before invocation; stop on lease loss and record every terminal outcome."""
    thread_id = conversation_id or f"phase5-{uuid.uuid4().hex[:12]}"
    backend = await workflow._ensure_checkpoint_backend()
    prior = await workflow.graph.aget_state({"configurable": {"thread_id": thread_id}})
    if resume or prior.values:
        workflow._authorize_thread(prior, thread_id, traveler_id)
    if resume and not prior.next:
        raise HTTPException(409, "This workflow has no pending checkpoint to resume.")
    client = get_rds_data_client()

    @asynccontextmanager
    async def scoped():
        async with client.scoped_session(
            traveler_id=traveler_id, agent_type="booking_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as tx:
            yield ScopedDb(client, tx)

    async with scoped() as db:
        try:
            journey_id = await ensure_journey(db, traveler_id, thread_id, backend.kind)
        except PermissionError as exc:
            raise HTTPException(403, "This workflow thread belongs to another journey.") from exc
        claim = await claim_execution(db, journey_id, thread_id, WORKER_INSTANCE_ID, LEASE_SECONDS)
        if not claim.claimed:
            raise HTTPException(409, "This recovery is already running. Re-read its progress before resuming.")
        await db.execute("UPDATE journeys SET status = 'running', updated_at = CURRENT_TIMESTAMP WHERE journey_id = %s", (journey_id,))

    async def heartbeat():
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            async with asyncio.timeout(HEARTBEAT_SECONDS * 2):
                async with scoped() as db:
                    if not await renew_lease(db, claim.execution_id, LEASE_SECONDS):
                        raise HTTPException(409, "The recovery worker lost its lease. Re-read the saved journey.")

    async def invoke():
        result = await workflow.run(
            query, traveler_id, thread_id, resume=resume, travelers_count=travelers_count,
            journey_id=journey_id, execution_id=claim.execution_id,
        )
        # The CLI kill demonstration can keep a paused worker alive until
        # SIGKILL. It uses the same claim, heartbeat, and release path as HTTP.
        if after_pause and result.get("workflow_status") == "paused":
            await after_pause(result, claim)
        return result

    work = asyncio.create_task(invoke())
    pulse = asyncio.create_task(heartbeat())
    terminal = "failed"
    try:
        done, _ = await asyncio.wait((work, pulse), return_when=asyncio.FIRST_COMPLETED)
        if pulse in done:
            await pulse  # Propagate lease loss; finally cancels the workflow.
        result = await work
        terminal = "paused" if result.get("workflow_status") == "paused" else "succeeded"
        return result
    finally:
        for task in (work, pulse):
            if not task.done():
                task.cancel()
        await asyncio.gather(work, pulse, return_exceptions=True)
        # A dead process cannot run finally: its lease expires and the next
        # claimant marks it abandoned. A normal pause releases immediately.
        async with scoped() as db:
            await release_execution(db, claim.execution_id, terminal)
            await db.execute(
                "UPDATE journeys SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE journey_id = %s "
                "AND NOT EXISTS (SELECT 1 FROM journey_executions WHERE journey_id = %s AND status = 'running')",
                (terminal, journey_id, journey_id),
            )
