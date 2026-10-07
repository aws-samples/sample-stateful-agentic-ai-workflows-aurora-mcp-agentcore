"""The Phase 5 runner as the app, the scripts and A2's Runtime build it."""

from typing import Any, Callable, Dict, Optional

from backend.agents.phase_05_workflow.lease import AuroraLeaseStore
from backend.agents.phase_05_workflow.nodes import GatewayCall, WorkflowNodes
from backend.agents.phase_05_workflow.runner import (
    HEARTBEAT_SECONDS,
    LEASE_SECONDS,
    WORKER_ID,
    StorageFactory,
    WorkflowRunner,
)
from backend.agents.phase_05_workflow.snapshot_storage import AuroraSnapshotStorage
from backend.agents.phase_05_workflow.state import SNAPSHOT_STORE


def aurora_storage_factory(client: Any) -> StorageFactory:
    """Build each run's snapshot storage over one Data API client."""

    def build(
        thread_id: str,
        traveler_id: str,
        execution_id: Optional[str],
        worker_id: str,
        on_write: Callable[[int], None],
    ) -> AuroraSnapshotStorage:
        return AuroraSnapshotStorage(
            client,
            session_id=thread_id,
            traveler_id=traveler_id,
            execution_id=execution_id,
            worker_id=worker_id,
            on_write=on_write,
        )

    return build


def build_workflow_runner(
    *,
    client: Any = None,
    gateway_call: Optional[GatewayCall] = None,
    lease_seconds: int = LEASE_SECONDS,
    heartbeat_seconds: int = HEARTBEAT_SECONDS,
    search_fn: Any = None,
    availability_fn: Any = None,
    memory_recall_fn: Any = None,
    worker_id: str = WORKER_ID,
    pause_after: Optional[str] = None,
) -> WorkflowRunner:
    """The production runner: real retrieval, the configured gateway, Aurora state."""
    from backend.agents.phase_05_workflow.memory_recall import workflow_memory_recall
    from backend.db.rds_data_client import get_rds_data_client
    from backend.retrieval.availability import retrieval_availability_search
    from backend.retrieval.hybrid import retrieval_search

    client = client or get_rds_data_client()
    nodes = WorkflowNodes(
        search_fn or retrieval_search,
        availability_fn or retrieval_availability_search,
        memory_recall_fn or workflow_memory_recall,
        gateway_call=gateway_call,
    )
    return WorkflowRunner(
        nodes,
        storage_for=aurora_storage_factory(client),
        lease=AuroraLeaseStore(client),
        lease_seconds=lease_seconds,
        heartbeat_seconds=heartbeat_seconds,
        worker_id=worker_id,
        pause_after=pause_after,
    )


def workflow_store_status() -> Dict[str, Any]:
    """What health and receipts report about workflow durability.

    This is a static description of the configured store. The health probe, not this
    function, checks that the ``workflow_snapshots`` table exists.
    """
    return {
        "kind": SNAPSHOT_STORE,
        "durable": True,
        "required": True,
        "configured": True,
        "error": None,
    }
