"""The presenter command must fail when its restart claim is not proved."""
import asyncio
from types import SimpleNamespace

import pytest

from scripts.kill_and_resume_demo import _verify_replacement, _wait_for_pause


@pytest.mark.parametrize("state,workers", [
    ({"workflow_status": "failed", "resumed_after_restart": True}, ["first", "second"]),
    ({"workflow_status": "resumed", "resumed_after_restart": False}, ["first", "second"]),
    ({"workflow_status": "resumed", "resumed_after_restart": True}, ["first", "first"]),
])
def test_restart_proof_rejects_false_success(state, workers):
    executions = [{"worker_id": worker, "status": "succeeded"} for worker in workers]
    with pytest.raises(AssertionError):
        _verify_replacement(state, executions, "first")


def test_restart_proof_requires_successful_persisted_replacement():
    state = {"workflow_status": "resumed", "resumed_after_restart": True}
    executions = [{"worker_id": "first", "status": "abandoned"},
                  {"worker_id": "second", "status": "succeeded"}]
    _verify_replacement(state, executions, "first")
    executions[-1]["status"] = "failed"
    with pytest.raises(AssertionError):
        _verify_replacement(state, executions, "first")


@pytest.mark.asyncio
async def test_worker_exit_before_pause_is_a_failure():
    output = asyncio.StreamReader()
    output.feed_eof()
    with pytest.raises(RuntimeError, match="before reaching"):
        await _wait_for_pause(SimpleNamespace(stdout=output, pid=123))


@pytest.mark.asyncio
async def test_silent_worker_can_be_timed_out_without_blocking_cleanup():
    output = asyncio.StreamReader()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(_wait_for_pause(SimpleNamespace(stdout=output, pid=123)), .01)
