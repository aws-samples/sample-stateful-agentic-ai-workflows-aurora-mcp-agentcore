"""The stop-and-resume proof's checks fail on the evidence that would make it a lie."""

import json
import re

import pytest

from scripts import governed_release
from scripts import stop_and_resume_proof as proof


def doc(workers, statuses, *, stops=1, restarted=True, status="resumed", during="waiting",
        last_step=None):
    return {
        "executions": {"status": "observed", "items": [
            {"worker_id": w, "status": s, "runtime_session_id": w.split("/")[0],
             "microvm_id": w.split("/")[1]} for w, s in zip(workers, statuses, strict=True)]},
        "session_stops": {"status": "observed", "items": [
            {"outcome": "stopped", "stopped_during": during, "last_step": last_step}
        ] * stops},
        "workflow": {"status": "observed", "workflow_status": status,
                     "resumed_after_restart": restarted},
    }


HELD = [{"booking_id": "b1", "status": "held", "hold_request_id": "hr-1"}]
WORKERS = ["rt-wf-a/vm-aaaaaaaaaaaa", "rt-wf-a/vm-bbbbbbbbbbbb"]
GOOD = doc(WORKERS, ["paused", "succeeded"])
RUN_WORKERS = ["rt-wf-a/vm-aaaaaaaaaaaa", "rt-wf-a/vm-bbbbbbbbbbbb", "rt-wf-a/vm-cccccccccccc"]
RUN_STATUSES = ["paused", "abandoned", "succeeded"]
RUNNING = doc(RUN_WORKERS, RUN_STATUSES, during="running", last_step="search")


def check_running(document, holds=HELD, saved="hr-1"):
    return proof.check_restart(document, holds, "running", saved_hold_request_id=saved)


def test_a_real_restart_passes():
    assert proof.check_restart(GOOD, HELD, "waiting") == []


def test_waiting_is_the_default_mode():
    assert proof.check_restart(GOOD, HELD) == []


def test_the_same_microvm_is_not_a_restart():
    same = doc(["rt-wf-a/vm-aaaaaaaaaaaa", "rt-wf-a/vm-aaaaaaaaaaaa"], ["paused", "succeeded"])
    assert any("microVM" in f for f in proof.check_restart(same, HELD))


def test_two_holds_or_none_fail():
    assert any("hold" in f for f in proof.check_restart(GOOD, HELD * 2))
    assert any("hold" in f for f in proof.check_restart(GOOD, []))


def test_a_missing_stop_record_fails():
    assert any("stop" in f for f in proof.check_restart(doc(WORKERS, ["paused", "succeeded"],
                                                            stops=0), HELD))


def test_a_different_session_fails():
    other = doc(["rt-wf-a/vm-aaaaaaaaaaaa", "rt-wf-b/vm-bbbbbbbbbbbb"], ["paused", "succeeded"])
    assert any("session" in f for f in proof.check_restart(other, HELD))


def test_wrong_statuses_or_workflow_state_fail():
    bad = doc(WORKERS, ["paused", "failed"])
    assert any("statuses" in f for f in proof.check_restart(bad, HELD))
    assert any("resumed_after_restart" in f for f in proof.check_restart(
        doc(WORKERS, ["paused", "succeeded"], restarted=False), HELD))
    assert any("workflow_status" in f for f in proof.check_restart(
        doc(WORKERS, ["paused", "succeeded"], status="paused"), HELD))


def test_a_waiting_stop_must_say_waiting():
    assert any("stopped_during" in f for f in proof.check_restart(
        doc(WORKERS, ["paused", "succeeded"], during="running"), HELD))


def test_a_real_running_stop_passes():
    assert check_running(RUNNING) == []


def test_running_needs_paused_abandoned_succeeded():
    bad = doc(RUN_WORKERS, ["paused", "failed", "succeeded"], during="running", last_step="x")
    assert any("statuses" in f for f in check_running(bad))
    assert any("statuses" in f for f in check_running(GOOD))


def test_running_final_worker_must_be_new():
    reused = doc(["rt-wf-a/vm-aaaaaaaaaaaa", "rt-wf-a/vm-bbbbbbbbbbbb", "rt-wf-a/vm-bbbbbbbbbbbb"],
                 RUN_STATUSES, during="running", last_step="search")
    assert any("microVM" in f for f in check_running(reused))


def test_running_stop_row_must_say_running_with_a_last_step():
    waiting = doc(RUN_WORKERS, RUN_STATUSES, during="waiting", last_step="search")
    assert any("stopped_during" in f for f in check_running(waiting))
    no_step = doc(RUN_WORKERS, RUN_STATUSES, during="running", last_step=None)
    assert any("last_step" in f for f in check_running(no_step))


def test_running_hold_must_carry_the_saved_hold_request_id():
    assert any("hold_request_id" in f for f in check_running(RUNNING, saved="hr-other"))
    assert any("hold_request_id" in f for f in check_running(RUNNING, saved=None))


def test_running_two_holds_fail():
    assert any("hold" in f for f in check_running(RUNNING, HELD * 2))


def test_running_in_a_different_session_fails():
    other = doc(["rt-wf-a/vm-aaaaaaaaaaaa", "rt-wf-a/vm-bbbbbbbbbbbb", "rt-wf-b/vm-cccccccccccc"],
                RUN_STATUSES, during="running", last_step="search")
    assert any("session" in f for f in check_running(other))


def test_running_needs_resumed_after_restart():
    not_restarted = doc(RUN_WORKERS, RUN_STATUSES, during="running", last_step="search",
                        restarted=False)
    assert any("resumed_after_restart" in f for f in check_running(not_restarted))


def test_a_waiting_hold_must_carry_a_captured_intent_id():
    assert proof.check_restart(GOOD, HELD, "waiting", saved_hold_request_id="hr-1") == []
    assert any("hold_request_id" in f for f in proof.check_restart(
        GOOD, HELD, "waiting", saved_hold_request_id="hr-other"))


RUN = "phase5-proof-0123456789ab"
OLDER = "phase5-proof-feedfacecafe"
SECRET = "token-abc123"
JORDAN = "trv_meridian_demo"


class ReleaseAurora:
    """The run's holds, with FORCE RLS on bookings: a hold is visible only when pinned."""

    def __init__(self, *, threads=None, holds=None, read_fails=(), owner=JORDAN):
        self.threads = threads or {RUN: "jrn-run"}
        self.holds = holds or [{"booking_id": "bk-run", "hold_request_id": "hrq-run",
                                "journey_id": "jrn-run"}]
        self.active = {hold["booking_id"]: True for hold in self.holds}
        self.read_fails, self.owner = set(read_fails), owner
        self.pinned: dict[str, dict] = {}
        self.statements: list[tuple[str, tuple]] = []

    def begin_transaction(self):
        self.pinned[f"tx{len(self.pinned)}"] = {}
        return list(self.pinned)[-1]

    def commit_transaction(self, tx):
        pass

    def rollback_transaction(self, tx):
        pass

    async def execute(self, sql, params=(), transaction_id=None, **_kwargs):
        self.statements.append((sql, tuple(params)))
        if "set_config" in sql:
            names = re.findall(r"set_config\('([\w.]+)'", sql)
            self.pinned[transaction_id].update(zip(names, params, strict=True))
            return [{}]
        if sql == governed_release.RUN_HOLDS_SQL:
            journey = self.threads.get(params[0])
            return [dict(hold) for hold in self.holds if hold["journey_id"] == journey]
        if sql == governed_release.THREADS_OF_JOURNEY_SQL:
            return [{"n": sum(1 for j in self.threads.values() if j == params[0])}]
        if sql == governed_release.ACTIVE_SQL:
            return self.state(params[0], transaction_id)
        raise AssertionError(f"unexpected statement: {sql}")

    def state(self, booking, tx):
        if booking in self.read_fails:
            raise ConnectionError(SECRET)
        scope = self.pinned.get(tx) or {}
        if (scope.get("app.current_traveler_id"), scope.get("app.agent_type")) != (
                self.owner, "booking_agent"):
            return []
        return [{"active": self.active[booking]}]


class Gateway:
    """The governed release tool; per booking it succeeds, loses a reply or refuses."""

    def __init__(self, db, **behaviour):
        self.db, self.behaviour, self.calls = db, behaviour, []

    def call_tool(self, name, args):
        booking = next(h["booking_id"] for h in self.db.holds
                       if h["hold_request_id"] == args["holdRequestId"])
        self.calls.append((name, booking, dict(args)))
        mode = self.behaviour.get(booking, "ok")
        first = sum(1 for _n, b, _a in self.calls if b == booking) == 1
        if mode == "lost-after-commit" and first:
            self.db.active[booking] = False
            raise TimeoutError(SECRET)
        if mode == "always-lost" or (mode == "lost-before-commit" and first):
            raise TimeoutError(SECRET)
        if mode == "refused":
            body = json.dumps({"error": "hold_not_found"})
        else:
            self.db.active[booking] = False
            body = json.dumps({"release": {"status": "released", "replayed": False}})
        return {"result": {"content": [{"type": "text", "text": body}]}}

    def booked(self):
        return [booking for _name, booking, _args in self.calls]


@pytest.fixture
def gateway(monkeypatch):
    def install(db, **behaviour):
        made = Gateway(db, **behaviour)
        monkeypatch.setattr("backend.agentcore.gateway.get_agentcore_gateway", lambda: made)
        return made
    return install


def _no_writes(db):
    return not any(sql.lstrip().upper().startswith(("DELETE", "UPDATE", "INSERT"))
                   for sql, _params in db.statements)


async def test_the_run_hold_is_released_through_the_gateway_and_nothing_is_deleted(gateway):
    from backend.agents.phase_05_workflow.governed_hold import RELEASE_TOOL

    db = ReleaseAurora()
    made = gateway(db)

    assert await proof.release_run(db, RUN) == []

    assert made.calls == [(RELEASE_TOOL, "bk-run", {
        "travelerId": JORDAN, "holdRequestId": "hrq-run", "journeyId": "jrn-run"})]
    assert db.active["bk-run"] is False and _no_writes(db)


async def test_a_lost_reply_that_committed_is_read_back_and_not_repeated(gateway):
    db = ReleaseAurora()
    made = gateway(db, **{"bk-run": "lost-after-commit"})

    assert await proof.release_run(db, RUN) == []
    assert made.booked() == ["bk-run"]


async def test_a_lost_reply_that_did_not_commit_is_tried_once_more(gateway):
    db = ReleaseAurora()
    made = gateway(db, **{"bk-run": "lost-before-commit"})

    assert await proof.release_run(db, RUN) == []
    assert made.booked() == ["bk-run", "bk-run"] and db.active["bk-run"] is False


async def test_a_release_that_keeps_failing_stops_at_two_calls_and_is_reported(gateway):
    db = ReleaseAurora()
    made = gateway(db, **{"bk-run": "refused"})

    problems = await proof.release_run(db, RUN)

    assert made.booked() == ["bk-run", "bk-run"] and db.active["bk-run"] is True
    assert len(problems) == 1 and "booking bk-run" in problems[0]
    assert "release not confirmed" in problems[0] and SECRET not in problems[0]


async def test_a_lost_reply_is_not_retried_when_the_read_back_fails(gateway):
    db = ReleaseAurora(read_fails={"bk-run"})
    made = gateway(db, **{"bk-run": "always-lost"})

    problems = await proof.release_run(db, RUN)

    assert made.booked() == ["bk-run"]
    assert problems == ["booking bk-run still held or unknown (read-back failed "
                        "(ConnectionError); release reply lost (TimeoutError))"]


async def test_a_booking_the_pinned_scope_cannot_see_is_never_taken_for_released(gateway):
    db = ReleaseAurora(owner="trv_someone_else")
    made = gateway(db)

    problems = await proof.release_run(db, RUN)

    assert made.booked() == ["bk-run"]
    assert problems == ["booking bk-run still held or unknown (booking row not visible or its "
                        "state unknown)"]


async def test_an_older_proof_thread_is_left_alone(gateway):
    db = ReleaseAurora(
        threads={RUN: "jrn-run", OLDER: "jrn-old"},
        holds=[{"booking_id": "bk-run", "hold_request_id": "hrq-run", "journey_id": "jrn-run"},
               {"booking_id": "bk-old", "hold_request_id": "hrq-old", "journey_id": "jrn-old"}])
    made = gateway(db)

    assert await proof.release_run(db, RUN) == []

    assert made.booked() == ["bk-run"] and db.active["bk-old"] is True
    touched = {value for _sql, params in db.statements for value in params}
    assert not touched & {OLDER, "jrn-old", "bk-old"} and _no_writes(db)


async def test_a_journey_with_another_thread_is_not_released(gateway):
    db = ReleaseAurora(threads={RUN: "jrn-run", "jordan-real-thread": "jrn-run"})
    made = gateway(db)

    problems = await proof.release_run(db, RUN)

    assert made.calls == [] and db.active["bk-run"] is True
    assert problems == ["journey jrn-run has other threads; its holds were not released"]


async def test_a_thread_the_proof_did_not_create_is_refused_without_a_statement(gateway):
    db = ReleaseAurora()
    made = gateway(db)

    problems = await proof.release_run(db, "jordan-real-thread")

    assert db.statements == [] and made.calls == []
    assert "not a thread this proof creates" in problems[0]


async def test_no_gateway_reports_every_hold_without_its_error_text(gateway, monkeypatch):
    def broken():
        raise RuntimeError(SECRET)
    monkeypatch.setattr("backend.agentcore.gateway.get_agentcore_gateway", broken)
    db = ReleaseAurora()

    problems = await proof.release_run(db, RUN)

    assert problems == ["booking bk-run not released: no governed release possible "
                        "(RuntimeError)"]


def _proof_returns(code, *, raises=None):
    async def fake_prove(client, thread, during):
        print("[BAD] the proof's own failure" if code == 1 else "RESULT: PASS (waiting)")
        if raises:
            raise raises
        return code
    return fake_prove


@pytest.fixture
def proof_run(monkeypatch):
    def install(db, prove):
        monkeypatch.setattr(proof, "get_rds_data_client", lambda: db)
        monkeypatch.setattr(proof, "_prove", prove)
    return install


async def test_a_failed_proof_prints_its_verdict_before_a_failed_cleanup(gateway, proof_run,
                                                                         capsys):
    db = ReleaseAurora()
    gateway(db, **{"bk-run": "refused"})
    proof_run(db, _proof_returns(1))

    assert await proof.run("waiting", False, RUN) == 1

    captured = capsys.readouterr()
    assert "[BAD] the proof's own failure" in captured.out
    assert "cleanup FAILED for " + RUN in captured.err


async def test_a_passing_proof_whose_hold_stays_held_exits_one(gateway, proof_run, capsys):
    db = ReleaseAurora()
    gateway(db, **{"bk-run": "refused"})
    proof_run(db, _proof_returns(0))

    assert await proof.run("waiting", False, RUN) == 1

    assert "the run's hold was not released" in capsys.readouterr().out


async def test_an_error_in_flight_survives_a_failed_cleanup(gateway, proof_run, capsys):
    db = ReleaseAurora()
    made = gateway(db, **{"bk-run": "refused"})
    proof_run(db, _proof_returns(0, raises=ValueError("the proof's own error")))

    with pytest.raises(ValueError, match="the proof's own error"):
        await proof.run("waiting", False, RUN)

    assert made.booked() == ["bk-run", "bk-run"]
    assert "cleanup FAILED for " + RUN in capsys.readouterr().err


async def test_a_passing_proof_releases_its_hold_and_exits_zero(gateway, proof_run):
    db = ReleaseAurora()
    gateway(db)
    proof_run(db, _proof_returns(0))

    assert await proof.run("waiting", False, RUN) == 0
    assert db.active["bk-run"] is False


async def test_release_only_exits_zero_only_when_nothing_is_left_held(gateway, monkeypatch):
    db = ReleaseAurora()
    monkeypatch.setattr(proof, "get_rds_data_client", lambda: db)
    gateway(db, **{"bk-run": "refused"})
    assert await proof.release_only(RUN, proof.CallLedger()) == 1

    gateway(db)
    assert await proof.release_only(RUN, proof.CallLedger()) == 0


async def test_two_processes_sharing_a_calls_file_send_two_calls_in_all(gateway, tmp_path):
    db = ReleaseAurora()
    made = gateway(db, **{"bk-run": "refused"})
    ledger = tmp_path / "calls"

    first = await proof.release_run(db, RUN, proof.CallLedger(ledger))
    second = await proof.release_run(db, RUN, proof.CallLedger(ledger))

    assert made.booked() == ["bk-run", "bk-run"]
    assert first and second and "release not confirmed" not in second[0]
    assert ledger.read_text().split() == ["bk-run", "bk-run"]


async def test_a_later_process_sends_its_one_call_only_after_a_read_back_shows_held(
        gateway, tmp_path):
    db = ReleaseAurora()
    made = gateway(db)
    ledger = tmp_path / "calls"
    ledger.write_text("bk-run\n")

    assert await proof.release_run(db, RUN, proof.CallLedger(ledger)) == []
    assert made.booked() == ["bk-run"]

    unreadable = ReleaseAurora(read_fails={"bk-run"})
    blind = gateway(unreadable)
    problems = await proof.release_run(unreadable, RUN, proof.CallLedger(ledger))
    assert blind.calls == [] and "read-back failed (ConnectionError)" in problems[0]


async def test_a_booking_whose_state_reads_null_is_never_taken_for_released(gateway):
    db = ReleaseAurora()
    db.active["bk-run"] = None
    made = gateway(db, **{"bk-run": "refused"})

    problems = await proof.release_run(db, RUN)

    assert made.booked() == ["bk-run"]
    assert problems == ["booking bk-run still held or unknown (booking row not visible or its "
                        "state unknown; release not confirmed (hold_not_found))"]


@pytest.mark.parametrize("argv", [["--release-only"], ["--thread", "jordan-real-thread"],
                                  ["--thread", "phase5-proof-XYZ"]])
def test_the_command_line_refuses_a_missing_or_foreign_thread(argv):
    with pytest.raises(SystemExit):
        proof.main(argv)
