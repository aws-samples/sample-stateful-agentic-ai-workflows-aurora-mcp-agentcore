"""The Runtime entry turns one payload into heartbeats and one coded outcome."""

import asyncio
import gc
import json
import re

import pytest

from backend.agents.phase_05_workflow import runtime_entry as entry
from backend.authorization import AuthorizationDecision, TravelerAuthorizationError
from backend.agents.phase_05_workflow.governed_hold import HoldOutcomeUnknown
from backend.agents.phase_05_workflow.runner import WorkflowConflictError, WorkflowRequestError
from backend.agents.phase_05_workflow.state import WorkflowAuthorizationError
from backend.db.journey_store import ExecutionLeaseLostError

DENIED = AuthorizationDecision(False, "deny", "trv_meridian_demo", "aws_iam", "role", "role")
START = {"event": "workflow_turn", "mode": "start", "thread_id": "phase5-abc",
         "traveler_id": "trv_meridian_demo", "query": "My flight was canceled.",
         "travelers_count": 2, "review_only": False}


class Runner:
    def __init__(self, result=None, error=None, delay=0.0):
        self.commands, self._result, self._error, self._delay = [], result, error, delay
        self.cancelled = False

    async def run(self, command):
        self.commands.append(command)
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self._error:
            raise self._error
        return self._result


async def events(payload, runner, *, heartbeat=10.0, session="rt-wf-x-" + "0" * 32):
    return [e async for e in entry.workflow_turn(payload, session_id=session,
                                                 runner_factory=lambda wid: runner,
                                                 heartbeat_seconds=heartbeat)]


async def test_a_start_builds_the_command_and_returns_the_public_state():
    state = {key: f"v-{key}" for key in entry.RESULT_KEYS} | {"hold_intent": {"secret": 1}}
    runner = Runner(result=state)
    out = await events(START, runner)
    assert out == [{"type": "result", "state": {k: f"v-{k}" for k in entry.RESULT_KEYS}}]
    command = runner.commands[0]
    assert (command.thread_id, command.traveler_id, command.resume, command.travelers_count,
            command.review_only) == ("phase5-abc", "trv_meridian_demo", False, 2, False)
    json.dumps(out)


async def test_a_resume_sets_resume():
    runner = Runner(result={})
    await events({**START, "mode": "resume"}, runner)
    assert runner.commands[0].resume is True


@pytest.mark.parametrize("payload", [
    {**START, "event": "concierge_turn"},
    {**START, "pause_after": "hold"},
    {**START, "mode": "stop"},
    {**START, "traveler_id": ""},
    {**START, "query": "  "},
    {**START, "review_only": "yes"},
    "not a dict",
])
async def test_an_unusable_payload_is_a_request_error_and_runs_nothing(payload):
    runner = Runner(result={})
    out = await events(payload, runner)
    assert [e["type"] for e in out] == ["error"] and out[0]["code"] == "request"
    assert runner.commands == []


async def test_ping_answers_without_running_the_workflow():
    runner = Runner(result={})
    out = await events({"event": "workflow_turn", "mode": "ping"}, runner, session="rt-wf-s")
    worker = f"rt-wf-s/{entry.MICROVM_ID}"
    assert out == [{"type": "result",
                    "state": {"workflow_status": "ready", "worker_instance_id": worker}}]
    assert runner.commands == []


async def test_heartbeats_flow_while_the_run_works():
    out = await events(START, Runner(result={}, delay=0.5), heartbeat=0.1)
    assert [e["type"] for e in out][:3] == ["heartbeat"] * 3
    assert out[-1]["type"] == "result"


@pytest.mark.parametrize(("error", "code"), [
    (WorkflowRequestError("bad"), "request"),
    (WorkflowAuthorizationError("not yours"), "authorization"),
    (TravelerAuthorizationError(DENIED), "authorization"),
    (PermissionError("denied"), "authorization"),
    (WorkflowConflictError("busy"), "conflict"),
    (ExecutionLeaseLostError("lost"), "lease_lost"),
    (HoldOutcomeUnknown("unknown"), "hold_unknown"),
])
async def test_domain_errors_leave_as_codes(error, code):
    out = await events(START, Runner(error=error))
    assert out == [{"type": "error", "code": code, "message": str(error)}]


async def test_an_unexpected_failure_hides_its_detail_behind_a_reference():
    out = await events(START, Runner(error=RuntimeError("db password is hunter2")))
    assert out[0]["code"] == "internal" and "hunter2" not in out[0]["message"]
    assert "Reference" in out[0]["message"]


async def test_closing_the_stream_cancels_the_run():
    runner = Runner(result={}, delay=5)
    stream = entry.workflow_turn(START, session_id="s", runner_factory=lambda wid: runner,
                                 heartbeat_seconds=0.05)
    assert (await anext(stream))["type"] == "heartbeat"
    await stream.aclose()
    assert runner.cancelled is True


async def test_the_worker_id_names_the_session_and_this_microvm():
    seen = []

    def factory(worker_id):
        seen.append(worker_id)
        return Runner(result={})

    stream = entry.workflow_turn(START, session_id="rt-wf-s", runner_factory=factory,
                                 heartbeat_seconds=10)
    await stream.__anext__()
    await stream.aclose()
    assert re.fullmatch(r"vm-[0-9a-f]{12}", entry.MICROVM_ID)
    assert seen == [f"rt-wf-s/{entry.MICROVM_ID}"] and len(seen[0]) <= 128


async def test_a_factory_failure_is_one_internal_event():
    def factory(worker_id):
        raise RuntimeError("secret detail")

    out = [e async for e in entry.workflow_turn(START, session_id="s", runner_factory=factory,
                                                 heartbeat_seconds=10)]
    assert len(out) == 1 and out[0]["code"] == "internal"
    assert "secret detail" not in out[0]["message"]


async def test_a_run_that_fails_after_the_last_heartbeat_is_still_collected():
    loop = asyncio.get_running_loop()
    reported = []
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    runner = Runner(error=RuntimeError("late"), delay=0.15)
    stream = entry.workflow_turn(START, session_id="s", runner_factory=lambda wid: runner,
                                 heartbeat_seconds=0.05)
    assert (await anext(stream))["type"] == "heartbeat"
    await asyncio.sleep(0.2)
    await stream.aclose()
    del stream
    gc.collect()
    await asyncio.sleep(0)
    loop.set_exception_handler(None)
    assert [c for c in reported if "never retrieved" in c.get("message", "")] == []


async def test_a_run_that_cancels_itself_is_an_internal_event():
    class SelfCancelling:
        async def run(self, command):
            raise asyncio.CancelledError

    out = [e async for e in entry.workflow_turn(START, session_id="s",
                                                 runner_factory=lambda wid: SelfCancelling(),
                                                 heartbeat_seconds=10)]
    assert [e["code"] for e in out] == ["internal"]
