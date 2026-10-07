"""The crash proofs can run their worker as the workflow login, and prove that they did."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from scripts.kill_and_resume_demo import (
    WORKFLOW_LOGIN,
    _wait_for_pause,
    require_worker_login,
    worker_env,
)
from scripts.lost_response_demo import check_worker_identity

ENVIRON = {
    "AURORA_SECRET_ARN": "arn:master",
    "AURORA_WORKFLOW_SECRET_ARN": "arn:workflow",
    "X": "1",
}


def test_default_worker_env_is_the_driver_env_unchanged():
    assert worker_env(False, ENVIRON) == ENVIRON


def test_worker_login_changes_only_the_workers_aurora_secret():
    env = worker_env(True, ENVIRON)
    assert env == {**ENVIRON, "AURORA_SECRET_ARN": "arn:workflow"}
    assert ENVIRON["AURORA_SECRET_ARN"] == "arn:master"


@pytest.mark.parametrize("workflow_arn", [None, ""])
def test_worker_login_without_a_workflow_secret_fails_fast(workflow_arn):
    environ = {k: v for k, v in ENVIRON.items() if k != "AURORA_WORKFLOW_SECRET_ARN"}
    if workflow_arn is not None:
        environ["AURORA_WORKFLOW_SECRET_ARN"] = workflow_arn
    with pytest.raises(SystemExit, match="AURORA_WORKFLOW_SECRET_ARN"):
        worker_env(True, environ)


def test_the_worker_must_report_the_workflow_login():
    require_worker_login(WORKFLOW_LOGIN)
    for seen in ("postgres", None):
        with pytest.raises(AssertionError, match=WORKFLOW_LOGIN):
            require_worker_login(seen)


def test_lost_response_reads_the_identity_event_only_when_asked():
    events = [{"event": "identity", "current_user": WORKFLOW_LOGIN}, {"event": "reply_lost"}]
    check_worker_identity(events, True)
    check_worker_identity([{"event": "reply_lost"}], False)
    with pytest.raises(AssertionError):
        check_worker_identity([{"event": "reply_lost"}], True)
    with pytest.raises(AssertionError):
        check_worker_identity([{"event": "identity", "current_user": "postgres"}], True)


def _child(*events):
    output = asyncio.StreamReader()
    for event in events:
        output.feed_data(json.dumps(event).encode() + b"\n")
    output.feed_eof()
    return SimpleNamespace(stdout=output, pid=1)


CLAIMED = {"event": "claimed", "worker_id": "w1", "attempt": 1}
PAUSED = {"event": "paused", "status": "paused"}


@pytest.mark.asyncio
async def test_kill_and_resume_accepts_a_worker_that_reports_the_login():
    identity = {"event": "identity", "current_user": WORKFLOW_LOGIN}
    assert await _wait_for_pause(_child(identity, CLAIMED, PAUSED), True) == "w1"


@pytest.mark.asyncio
@pytest.mark.parametrize("events", [
    [{"event": "identity", "current_user": "postgres"}, CLAIMED, PAUSED],
    [CLAIMED, PAUSED],
])
async def test_kill_and_resume_rejects_a_worker_running_as_anyone_else(events):
    with pytest.raises(AssertionError, match=WORKFLOW_LOGIN):
        await _wait_for_pause(_child(*events), True)
