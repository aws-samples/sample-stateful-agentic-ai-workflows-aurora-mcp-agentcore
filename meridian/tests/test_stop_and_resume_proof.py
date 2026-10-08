"""The stop-and-resume proof's checks fail on the evidence that would make it a lie."""

import re

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


THREAD = "phase5-proof-abcd1234"
JOURNEY = "jrn-1"
RUN_TABLES = ("bookings", "booking_lines", "hold_requests", "journeys", "journey_executions",
              "journey_threads", "workflow_snapshots", "workflow_session_stops")
FORCED = ("bookings", "booking_lines")


class FakeAurora:
    """A small Aurora: the run's rows, and FORCE RLS on the booking tables.

    A booking table shows and deletes rows only inside a transaction pinned to the row's traveler
    and the booking agent. Any other statement silently matches nothing, as Aurora does.
    """

    DELETE = re.compile(r"DELETE FROM (\w+) WHERE (\w+) = %s")
    COUNT = re.compile(r"SELECT COUNT\(\*\) AS n FROM (\w+) WHERE (\w+) = %s")

    def __init__(self, *, keep=(), traveler="trv_meridian_demo"):
        self.keep, self.traveler = set(keep), traveler
        self.tables = {
            "journeys": [{"journey_id": JOURNEY}],
            "journey_threads": [{"journey_id": JOURNEY, "thread_id": THREAD}],
            "hold_requests": [{"journey_id": JOURNEY, "booking_id": "bk-1"}],
            "bookings": [{"booking_id": "bk-1", "traveler_id": traveler}],
            "booking_lines": [{"booking_id": "bk-1", "traveler_id": traveler}],
            "journey_executions": [{"thread_id": THREAD}],
            "workflow_snapshots": [{"session_id": THREAD}],
            "workflow_session_stops": [{"journey_id": JOURNEY}],
        }
        self.pinned: dict[str, dict] = {}
        self.statements: list[tuple[str, str | None]] = []
        self.ended: list[str] = []

    def begin_transaction(self):
        self.pinned[f"tx{len(self.pinned)}"] = {}
        return list(self.pinned)[-1]

    def commit_transaction(self, tx):
        self.ended.append(f"commit {tx}")

    def rollback_transaction(self, tx):
        self.ended.append(f"rollback {tx}")

    def visible(self, table, row, tx):
        if table not in FORCED:
            return True
        scope = self.pinned.get(tx) or {}
        return (scope.get("app.current_traveler_id") == row.get("traveler_id")
                and scope.get("app.agent_type") == "booking_agent")

    async def execute(self, sql, params=(), transaction_id=None, **_kwargs):
        self.statements.append((sql, transaction_id))
        if "set_config" in sql:
            names = re.findall(r"set_config\('([\w.]+)'", sql)
            self.pinned[transaction_id].update(zip(names, params, strict=True))
            return [{}]
        if match := self.DELETE.search(sql):
            return self.delete(*match.groups(), params[0], transaction_id)
        if match := self.COUNT.search(sql):
            return [{"n": len(self.matching(*match.groups(), params[0], transaction_id))}]
        return self.read(sql, params)

    def matching(self, table, column, value, tx):
        return [row for row in self.tables[table]
                if row.get(column) == value and self.visible(table, row, tx)]

    def delete(self, table, column, value, tx):
        if table not in self.keep:
            doomed = self.matching(table, column, value, tx)
            self.tables[table] = [r for r in self.tables[table] if r not in doomed]
        return []

    def read(self, sql, params):
        if "SELECT journey_id FROM journey_threads" in sql:
            return [{"journey_id": r["journey_id"]} for r in self.tables["journey_threads"]
                    if r["thread_id"] == params[0]]
        if "SELECT booking_id FROM hold_requests" in sql:
            return [{"booking_id": r["booking_id"]} for r in self.tables["hold_requests"]
                    if r["journey_id"] == params[0]]
        if "FROM hold_requests hr" in sql:
            return [{"booking_id": r["booking_id"], "status": "held", "hold_request_id": "hr-1"}
                    for r in self.tables["hold_requests"] if r["journey_id"] == params[0]]
        return []

    def left(self):
        return {table: len(rows) for table, rows in self.tables.items() if rows}


async def test_the_purge_removes_every_table_including_the_row_level_secured_bookings():
    client = FakeAurora()

    await proof._purge_run(client, THREAD)

    assert client.left() == {}


async def test_the_booking_tables_are_read_and_deleted_in_a_transaction_pinned_to_the_traveler():
    client = FakeAurora()

    await proof._purge_run(client, THREAD)

    pinned = [s for s in client.statements if "set_config" in s[0]]
    assert pinned and "app.current_traveler_id" in pinned[0][0] and "app.agent_type" in pinned[0][0]
    booking_work = [tx for sql, tx in client.statements
                    if re.search(r"(DELETE FROM|FROM) (bookings|booking_lines)\b", sql)]
    assert booking_work and all(tx is not None for tx in booking_work)
    assert all(end.startswith("commit") for end in client.ended)


@pytest.mark.parametrize("table", RUN_TABLES)
async def test_a_delete_that_silently_matches_nothing_makes_the_purge_raise(table):
    client = FakeAurora(keep=[table])

    with pytest.raises(RuntimeError, match=table):
        await proof._purge_run(client, THREAD)


async def test_a_booking_row_hidden_from_the_unpinned_master_is_still_counted_and_fails():
    client = FakeAurora(traveler="trv_someone_else")

    with pytest.raises(RuntimeError, match="bookings"):
        await proof._purge_run(client, THREAD)


async def test_a_failed_pinned_statement_rolls_the_transaction_back():
    client = FakeAurora()
    original = client.delete

    def boom(table, column, value, tx):
        if table == "booking_lines":
            raise ConnectionError("data api down")
        return original(table, column, value, tx)

    client.delete = boom

    with pytest.raises(ConnectionError):
        await proof._purge_run(client, THREAD)

    assert client.ended and client.ended[-1].startswith("rollback")


@pytest.mark.parametrize(("keep", "raises"), [((), False), (("workflow_snapshots",), True)])
async def test_a_thread_with_no_journey_is_purged_and_counted_too(keep, raises):
    client = FakeAurora(keep=keep)
    client.tables["journey_threads"] = []

    if raises:
        with pytest.raises(RuntimeError, match="workflow_snapshots"):
            await proof._purge_run(client, THREAD)
    else:
        await proof._purge_run(client, THREAD)
        assert client.tables["workflow_snapshots"] == []
