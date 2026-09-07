"""HTTP lifecycle tests: no database, network, or real hold operations."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from backend.agents.orchestration_05 import execution as module


@pytest.fixture
def lifecycle(monkeypatch):
    @asynccontextmanager
    async def scoped_session(**kwargs):
        yield "tx"

    client = SimpleNamespace(scoped_session=scoped_session, execute=AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "get_rds_data_client", lambda: client)
    monkeypatch.setattr(module, "get_agentcore_identity", lambda: SimpleNamespace(authorization_context=lambda: None))
    monkeypatch.setattr(module, "ensure_journey", AsyncMock(return_value="journey"))
    monkeypatch.setattr(module, "claim_execution", AsyncMock(return_value=SimpleNamespace(claimed=True, execution_id="execution")))
    monkeypatch.setattr(module, "release_execution", AsyncMock())
    monkeypatch.setattr(module, "renew_lease", AsyncMock(return_value=True))
    workflow = SimpleNamespace(
        _ensure_checkpoint_backend=AsyncMock(return_value=SimpleNamespace(kind="AuroraDataApiSaver")),
        graph=SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values={}, next=()))),
        run=AsyncMock(return_value={"workflow_status": "paused"}),
    )
    return workflow


@pytest.mark.asyncio
async def test_http_claims_before_run_and_releases_at_pause(lifecycle):
    async def run(*args, **kwargs):
        module.claim_execution.assert_awaited_once()
        assert kwargs["journey_id"] == "journey"
        assert kwargs["execution_id"] == "execution"
        return {"workflow_status": "paused"}
    lifecycle.run.side_effect = run
    await module.run_http_workflow(lifecycle, "Tokyo", "alice", "thread")
    assert module.release_execution.call_args.args[1:] == ("execution", "paused")


@pytest.mark.asyncio
async def test_busy_thread_is_rejected_before_work(lifecycle):
    module.claim_execution.return_value = SimpleNamespace(claimed=False)
    with pytest.raises(HTTPException) as exc:
        await module.run_http_workflow(lifecycle, "Tokyo", "alice", "thread")
    assert exc.value.status_code == 409
    lifecycle.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_lease_loss_cancels_work_and_records_failure(lifecycle, monkeypatch):
    stopped = asyncio.Event()
    async def running(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    lifecycle.run.side_effect = running
    module.renew_lease.return_value = False
    monkeypatch.setattr(module, "HEARTBEAT_SECONDS", .01)
    with pytest.raises(HTTPException) as exc:
        await module.run_http_workflow(lifecycle, "Tokyo", "alice", "thread")
    assert exc.value.status_code == 409
    assert stopped.is_set()
    assert module.release_execution.call_args.args[1:] == ("execution", "failed")


@pytest.mark.asyncio
async def test_failed_run_releases_its_execution(lifecycle):
    lifecycle.run.side_effect = RuntimeError("model unavailable")
    with pytest.raises(RuntimeError, match="model unavailable"):
        await module.run_http_workflow(lifecycle, "Tokyo", "alice", "thread")
    assert module.release_execution.call_args.args[1:] == ("execution", "failed")


@pytest.mark.asyncio
async def test_bound_thread_without_checkpoint_still_rejects_wrong_owner(lifecycle):
    module.ensure_journey.side_effect = PermissionError('foreign binding')
    with pytest.raises(HTTPException) as exc:
        await module.run_http_workflow(lifecycle, 'Tokyo', 'bob', 'owned-thread')
    assert exc.value.status_code == 403
    lifecycle.run.assert_not_awaited()
    module.claim_execution.assert_not_awaited()
