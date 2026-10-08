"""The stop-and-resume proof's checks fail on the evidence that would make it a lie."""

import pytest

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


class FakeClient:
    def __init__(self, thread_count):
        self.thread_count = thread_count
        self.statements = []

    async def execute(self, sql, params=(), **_kwargs):
        self.statements.append(sql)
        if "FROM journey_threads WHERE thread_id" in sql:
            return [{"journey_id": "jrn-1"}]
        if "COUNT(*) AS n FROM journey_threads" in sql:
            return [{"n": self.thread_count}]
        return []


async def test_the_clean_up_refuses_to_purge_a_journey_that_has_other_threads():
    client = FakeClient(thread_count=2)

    with pytest.raises(RuntimeError, match="other threads"):
        await proof._purge_run(client, "phase5-proof-abcd1234")

    assert not any(sql.lstrip().startswith("DELETE") for sql in client.statements)


async def test_the_clean_up_refuses_a_thread_the_proof_did_not_create():
    client = FakeClient(thread_count=1)

    with pytest.raises(RuntimeError, match="not created by this proof"):
        await proof._purge_run(client, "jordan-real-thread")

    assert client.statements == []
