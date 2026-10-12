"""The crash proofs can run their worker as the workflow login, and prove that they did."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.agents.phase_05_workflow.runner import WorkflowConflictError

from scripts import kill_and_resume_proof as demo
from scripts.kill_and_resume_proof import (
    WORKFLOW_LOGIN,
    _wait_for_pause,
    parse_takeover,
    run_takeover,
    require_worker_login,
    worker_env,
)
from scripts.lost_response_proof import check_worker_identity

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


def _line(event):
    return json.dumps(event) + "\n"


IDENTITY = {"event": "identity", "current_user": WORKFLOW_LOGIN}
RESULT = {"event": "result", "state": {"workflow_status": "resumed"}}


def test_the_takeover_state_is_returned_when_it_ran_as_the_login():
    out = _line(IDENTITY) + _line(RESULT)
    assert parse_takeover(0, out, "", True) == {"workflow_status": "resumed"}


@pytest.mark.parametrize("identity", [{"event": "identity", "current_user": "postgres"}, None])
def test_a_takeover_that_did_not_run_as_the_login_is_rejected(identity):
    out = (_line(identity) if identity else "") + _line(RESULT)
    with pytest.raises(AssertionError, match=WORKFLOW_LOGIN):
        parse_takeover(0, out, "", True)


def test_the_identity_is_not_required_without_the_flag():
    assert parse_takeover(0, _line(RESULT), "", False) == RESULT["state"]


def test_a_refused_takeover_is_a_conflict_not_a_failure():
    out = _line(IDENTITY) + _line({"event": "conflict", "message": "lease held"})
    with pytest.raises(WorkflowConflictError, match="lease held"):
        parse_takeover(demo.CONFLICT_EXIT, out, "", True)


def test_a_crashed_takeover_reports_its_stderr():
    with pytest.raises(RuntimeError, match="boom"):
        parse_takeover(1, _line(IDENTITY), "boom", True)


def test_a_takeover_that_crashed_before_its_identity_reports_its_stderr():
    stderr = "Traceback\npermission denied for table workflow_session_stops"
    with pytest.raises(RuntimeError, match="permission denied") as caught:
        parse_takeover(1, "", stderr, True)
    assert "exit 1" in str(caught.value)


def test_a_crash_reports_only_the_last_twenty_stderr_lines():
    stderr = "\n".join(f"line {n}" for n in range(1, 41))
    with pytest.raises(RuntimeError) as caught:
        parse_takeover(1, "", stderr, True)
    message = str(caught.value)
    assert "line 40" in message and "line 21" in message
    assert "line 20" not in message


def test_a_clean_exit_with_no_result_is_a_failure():
    with pytest.raises(RuntimeError, match="no result"):
        parse_takeover(0, _line(IDENTITY), "", True)


@pytest.mark.asyncio
async def test_without_the_flag_the_takeover_runs_in_the_driver(monkeypatch):
    calls = []

    async def run_workflow(thread_id, *, resume):
        calls.append((thread_id, resume))
        return {"workflow_status": "resumed"}

    async def no_subprocess(*args, **kwargs):
        raise AssertionError("no subprocess without --worker-login")

    monkeypatch.setattr(demo, "_run_workflow", run_workflow)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_subprocess)
    assert await run_takeover("t1", False) == {"workflow_status": "resumed"}
    assert calls == [("t1", True)]


@pytest.mark.asyncio
async def test_with_the_flag_the_takeover_is_a_subprocess_with_the_login_secret(monkeypatch):
    seen = {}

    class Child:
        returncode = 0

        async def communicate(self):
            return (_line(IDENTITY) + _line(RESULT)).encode(), b""

    async def spawn(*args, **kwargs):
        seen["args"], seen["env"] = args, kwargs["env"]
        return Child()

    monkeypatch.setenv("AURORA_SECRET_ARN", "arn:master")
    monkeypatch.setenv("AURORA_WORKFLOW_SECRET_ARN", "arn:workflow")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert await run_takeover("t1", True) == RESULT["state"]
    assert seen["args"][-2:] == ("--worker-two", "t1")
    assert seen["env"]["AURORA_SECRET_ARN"] == "arn:workflow"
