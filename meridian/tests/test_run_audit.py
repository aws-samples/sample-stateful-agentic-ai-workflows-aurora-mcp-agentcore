"""One audit row per workflow run: who asked, which Gateway calls ran, and how the run ended."""

import asyncio
import dataclasses
import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.agents.phase_05_workflow import governed_tools
from backend.agents.phase_05_workflow import run_audit as audit
from backend.agents.phase_05_workflow import runtime_entry as entry
from backend.agents.phase_05_workflow.runner import WorkflowConflictError
from backend.agents.phase_05_workflow.state import WorkflowAuthorizationError
from backend.audit_vocabulary import ToolCall
from backend.authorization import AuthorizationContext
from backend.db.journey_store import ExecutionLeaseLostError
from tests.jwt_support import access_token
from tests.test_governed_tools import CANDIDATES, ROWS, reply
from tests.test_workflow_runner import RECOVERY, World, command

HOLD = "create_courtesy_hold"


class Recorder:
    def __init__(self, fail=False):
        self.records, self._fail = [], fail

    async def record(self, record):
        if self._fail:
            raise RuntimeError("audit store down")
        self.records.append(record)


def audited(world, recorder, **kwargs):
    runner = world.runner(**kwargs)
    runner._auditor = recorder
    return runner


async def finish(world, recorder, thread="t1", **kwargs):
    for _ in range(5):
        state = await audited(world, recorder, **kwargs).run(
            command(RECOVERY, resume=True, thread=thread, caller_subject="sub-1"))
        if state["workflow_status"] != "paused":
            return state
    raise AssertionError("still paused")


async def test_a_paused_run_writes_one_allowed_row_naming_the_verified_subject():
    world, recorder = World(), Recorder()
    await audited(world, recorder).run(command(RECOVERY, caller_subject="sub-1"))
    [row] = recorder.records
    assert (row.traveler_id, row.subject, row.turn_error) == ("trv_x", "sub-1", None)
    assert row.tool_calls == () and row.decision == "allow"


async def test_a_run_that_holds_records_the_gateway_call_under_its_bare_name():
    world, recorder = World(), Recorder()
    await audited(world, recorder).run(command(RECOVERY, caller_subject="sub-1"))
    await finish(world, recorder)
    last = recorder.records[-1]
    assert last.tool_calls == (ToolCall(HOLD, "allowed"),)
    assert (last.decision, last.turn_error, last.rows_returned) == ("allow", None, 1)


async def test_a_hold_refused_by_policy_is_a_refused_call_and_a_deny_row():
    world, recorder = World(), Recorder()
    await audited(world, recorder, ceiling=1).run(command(RECOVERY, caller_subject="sub-1"))
    await finish(world, recorder, ceiling=1)
    last = recorder.records[-1]
    assert last.tool_calls == (ToolCall(HOLD, "refused", "policy"),)
    assert (last.decision, last.turn_error) == ("deny", None)


async def test_a_run_that_fails_is_an_error_row_and_the_error_still_propagates():
    world, recorder = World(), Recorder()
    await audited(world, recorder).run(command(RECOVERY, caller_subject="sub-1"))

    async def explode(state, config=None):
        raise RuntimeError("synthesis exploded")

    with pytest.raises(RuntimeError, match="synthesis exploded"):
        await audited(world, recorder, synthesize=explode, release=AsyncMock(return_value=True)
                      ).run(command(RECOVERY, resume=True, caller_subject="sub-1"))
    last = recorder.records[-1]
    assert last.tool_calls == (ToolCall(HOLD, "allowed"),)
    assert last.turn_error.startswith("RuntimeError: synthesis exploded")
    assert last.decision == "error"


async def test_a_cancelled_run_still_writes_its_row_and_the_cancellation_stands():
    world, recorder = World(), Recorder()
    await audited(world, recorder).run(command(RECOVERY, caller_subject="sub-1"))
    entered = asyncio.Event()

    async def hang(state, config=None):
        entered.set()
        await asyncio.sleep(30)

    run = asyncio.ensure_future(
        audited(world, recorder, synthesize=hang, release=AsyncMock(return_value=True)).run(
            command(RECOVERY, resume=True, caller_subject="sub-1")))
    await entered.wait()
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    last = recorder.records[-1]
    assert last.turn_error.startswith("CancelledError") and last.decision == "error"
    assert last.tool_calls == (ToolCall(HOLD, "allowed"),)


async def test_a_hold_answering_journey_not_owned_is_a_database_guard_refusal_and_a_lost_lease():
    world, recorder = World(), Recorder()
    await audited(world, recorder).run(command(RECOVERY, caller_subject="sub-1"))

    def not_owned(name, arguments):
        body = json.dumps({"error": "journey_not_owned"})
        return {"result": {"content": [{"type": "text", "text": body}]}}

    with pytest.raises(ExecutionLeaseLostError):
        await audited(world, recorder, gateway=not_owned).run(
            command(RECOVERY, resume=True, caller_subject="sub-1"))
    last = recorder.records[-1]
    assert last.tool_calls == (ToolCall(HOLD, "refused", "database_guard", "journey_not_owned"),)
    assert last.turn_error.startswith("ExecutionLeaseLostError") and last.decision == "deny"


async def test_another_travelers_thread_writes_a_row_for_the_caller_with_no_calls():
    world, recorder = World(), Recorder()
    await audited(world, recorder).run(command(RECOVERY))
    with pytest.raises(WorkflowAuthorizationError):
        await audited(world, recorder).run(
            command(RECOVERY, resume=True, traveler="trv_other", caller_subject="sub-2"))
    row = recorder.records[-1]
    assert (row.traveler_id, row.subject, row.tool_calls) == ("trv_other", "sub-2", ())
    assert row.turn_error.startswith("WorkflowAuthorizationError:") and row.decision == "error"


async def test_the_production_search_path_puts_its_gateway_call_in_the_row(monkeypatch):
    def answer(tool, arguments, *, caller_token):
        return reply({"packages": ROWS, "candidates": CANDIDATES, "reranked": True})

    monkeypatch.setattr(governed_tools, "invoke_gateway_tool", answer)
    world, recorder = World(), Recorder()
    runner = audited(world, recorder)
    runner._nodes.search_fn = governed_tools.gateway_search
    await runner.run(command(RECOVERY, caller_subject="sub-1"))
    [row] = recorder.records
    assert row.tool_calls == (ToolCall("hybrid_trip_search", "allowed"),)


async def test_a_cancellation_while_a_finished_runs_row_is_written_does_not_lose_it():
    world = World()
    started, finished = asyncio.Event(), []

    class Slow:
        async def record(self, record):
            started.set()
            await asyncio.sleep(0.05)
            finished.append(record)

    run = asyncio.ensure_future(audited(world, Slow()).run(
        command(RECOVERY, caller_subject="sub-1")))
    await started.wait()
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert len(finished) == 1 and finished[0].turn_error is None


async def test_a_hung_audit_write_is_cut_off_logged_and_the_runs_result_stands(
    monkeypatch, caplog
):
    from backend.agents.phase_05_workflow import runner as runner_module

    monkeypatch.setattr(runner_module, "AUDIT_WRITE_SECONDS", 0.05)

    class Hung:
        async def record(self, record):
            await asyncio.sleep(30)

    with caplog.at_level(logging.ERROR):
        result = await asyncio.wait_for(
            audited(World(), Hung()).run(command(RECOVERY)), timeout=5)
    assert result["workflow_status"] == "paused"
    assert "audit row could not be written" in caplog.text


async def test_a_bad_request_and_a_busy_thread_write_no_row():
    world, recorder = World(), Recorder()
    with pytest.raises(ValueError, match="travelers_count"):
        await audited(world, recorder).run(
            dataclasses.replace(command(RECOVERY), travelers_count=0))
    world.lease.busy = True
    with pytest.raises(WorkflowConflictError):
        await audited(world, recorder).run(command(RECOVERY))
    assert recorder.records == []


async def test_a_request_refused_at_the_runtime_entry_never_reaches_a_runner(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")

    def forbidden(worker_id):
        pytest.fail("a refused request must not build a runner, so it writes no audit row")

    payload = {"event": "workflow_turn", "mode": "start", "thread_id": "t", "query": "q"}
    out = [event async for event in entry.workflow_turn(
        payload, session_id="s", runner_factory=forbidden)]
    assert out[0]["type"] == "error"


async def test_an_audit_failure_is_logged_and_never_replaces_the_runs_result(caplog):
    world = World()
    with caplog.at_level(logging.ERROR):
        result = await audited(world, Recorder(fail=True)).run(command(RECOVERY))
    assert result["workflow_status"] == "paused"
    assert "audit row could not be written" in caplog.text


def test_an_error_whose_text_cannot_be_read_is_named_by_its_class_alone():
    class Odd(Exception):
        def __str__(self):
            raise RuntimeError("no text")

    assert audit.turn_error_for(Odd()) == "Odd"
    assert audit.turn_error_for(None) is None


class FakeDb:
    def __init__(self):
        self.scopes = []

    @asynccontextmanager
    async def scoped_session(self, **kwargs):
        self.scopes.append(kwargs)
        yield "tx-1"


class FakeStore:
    def __init__(self):
        self.db, self.rows = FakeDb(), []

    async def write_audit(self, **kwargs):
        self.rows.append(kwargs)
        return "aud_1"


@pytest.fixture
def identity(monkeypatch):
    workload = AuthorizationContext(
        "aws_iam", "AROA1", "arn:aws:sts::123456789012:assumed-role/rt/s")
    fake = SimpleNamespace(
        authorization_context=lambda: workload, iam_identity=lambda: workload.principal)
    monkeypatch.setattr("backend.agentcore.identity.get_agentcore_identity", lambda: fake)
    return workload


def _record(**overrides):
    base = dict(traveler_id="trv_x", subject="sub-1", tool_calls=(ToolCall(HOLD, "allowed"),),
                turn_error=None, rows_returned=2)
    return audit.RunAuditRecord(**{**base, **overrides})


async def test_the_aurora_auditor_writes_the_cognito_subject_in_the_travelers_scope(identity):
    store = FakeStore()
    await audit.AuroraRunAuditor(store=store).record(_record())
    [row] = store.rows
    assert store.db.scopes == [{
        "traveler_id": "trv_x", "agent_type": "booking_agent", "authorization": identity}]
    assert (row["operation"], row["authorization_provider"], row["authorization_subject"]) == (
        "workflow_run", "cognito", "sub-1")
    assert row["tool_calls"] == [ToolCall(HOLD, "allowed")] and row["turn_error"] is None
    assert (row["authorization_decision"], row["transaction_id"]) == ("allow", "tx-1")


async def test_without_a_signed_in_subject_the_workload_is_the_subject(identity):
    store = FakeStore()
    await audit.AuroraRunAuditor(store=store).record(_record(subject=None))
    assert (store.rows[0]["authorization_provider"], store.rows[0]["authorization_subject"]) == (
        "aws_iam", "AROA1")


@pytest.mark.parametrize(("overrides", "decision"), [
    ({"tool_calls": (ToolCall(HOLD, "refused", "policy"),)}, "deny"),
    ({"tool_calls": (ToolCall(HOLD, "refused", "business_rule", "insufficient_inventory"),)},
     "allow"),
    ({"tool_calls": (ToolCall(HOLD, "error"),)}, "error"),
    ({"turn_error": "RuntimeError: x"}, "error"),
    ({"tool_calls": (ToolCall(HOLD, "refused", "policy"),), "turn_error": "RuntimeError: x"},
     "deny"),
])
async def test_the_decision_is_the_vocabularys_never_the_runners(identity, overrides, decision):
    store = FakeStore()
    await audit.AuroraRunAuditor(store=store).record(_record(**overrides))
    assert store.rows[0]["authorization_decision"] == decision


def test_the_verified_subject_reaches_the_command_in_jwt_mode(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    token = access_token("trv_meridian_demo")
    payload = {"event": "workflow_turn", "mode": "start", "thread_id": "t", "query": "q"}
    parsed = entry.parse_turn(payload, None, {"Authorization": f"Bearer {token}"})
    assert (parsed.traveler_id, parsed.caller_subject) == ("trv_meridian_demo", "sub-test")


def test_the_iam_mode_command_has_no_subject():
    payload = {"event": "workflow_turn", "mode": "start", "thread_id": "t", "query": "q",
               "traveler_id": "trv_meridian_demo"}
    assert entry.parse_turn(payload, None).caller_subject is None


def test_the_runtime_builds_its_runner_with_the_aurora_auditor(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "backend.agents.phase_05_workflow.service.build_workflow_runner",
        lambda **kwargs: seen.update(kwargs) or "runner")
    assert entry.default_runner("w-1") == "runner"
    assert isinstance(seen["auditor"], audit.AuroraRunAuditor)
