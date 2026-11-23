"""The live tests' governed-release cleanup, offline: it attempts every hold and never deletes."""

import contextlib
import json

import pytest

from tests import live_support

SECRET = "token-abc123"


class _Db:
    """Aurora as the cleanup reads it; can fail a read-back, a hold lookup or a lease closeout."""

    def __init__(self, active: dict[str, bool], *, threads=None, read_fails=(),
                 lookup_fails=(), close_fails=False, no_request=()) -> None:
        self.active, self.threads = active, threads or {}
        self.read_fails, self.lookup_fails = set(read_fails), set(lookup_fails)
        self.close_fails, self.statements = close_fails, []
        self.no_request = set(no_request)

    def begin_transaction(self):
        return "tx"

    def commit_transaction(self, tx):
        pass

    def rollback_transaction(self, tx):
        pass

    async def execute(self, sql, params=(), transaction_id=None, **_kwargs):
        self.statements.append(sql)
        if "set_config" in sql:
            return [{}]
        key = params[0]
        if "AS active FROM bookings" in sql:
            if key in self.read_fails:
                raise ConnectionError(SECRET)
            return [{"active": self.active[key]}] if key in self.active else []
        if "UPDATE journey_executions" in sql:
            if self.close_fails:
                raise RuntimeError(SECRET)
            return []
        if key in self.lookup_fails:
            raise TimeoutError(SECRET)
        if "JOIN journey_threads" in sql:
            return [_hold(b) for b in self.threads.get(key, [])]
        return [_hold(key)] if key in self.active and key not in self.no_request else []


class _Gateway:
    """The release tool; each booking can succeed, lose a reply or keep failing."""

    def __init__(self, db: _Db, behaviour=None) -> None:
        self.db, self.behaviour, self.calls = db, behaviour or {}, []

    def call_tool(self, name, args):
        booking = args["holdRequestId"].replace("hrq_", "HLD-")
        self.calls.append(booking)
        mode = self.behaviour.get(booking, "ok")
        if mode == "lost-after-commit" and self.calls.count(booking) == 1:
            self.db.active[booking] = False
            raise TimeoutError(SECRET)
        if mode == "lost-before-commit" and self.calls.count(booking) == 1:
            raise TimeoutError(SECRET)
        if mode == "always-lost":
            raise TimeoutError(SECRET)
        if mode == "refused":
            body = json.dumps({"error": "hold_not_found"})
        else:
            self.db.active[booking] = False
            body = json.dumps({"release": {"status": "released", "replayed": False}})
        return {"result": {"content": [{"type": "text", "text": body}]}}


@pytest.fixture(autouse=True)
def no_live_run_log(monkeypatch):
    """A live run's id log in the environment never receives these fake ids."""
    monkeypatch.delenv(live_support.RUN_LOG_ENV, raising=False)


@pytest.fixture
def gateway(monkeypatch):
    def install(db, **behaviour):
        made = _Gateway(db, behaviour)
        monkeypatch.setattr("backend.agentcore.gateway.get_agentcore_gateway", lambda: made)
        return made
    return install


def _hold(booking: str) -> dict:
    return {"booking_id": booking, "hold_request_id": booking.replace("HLD-", "hrq_"),
            "journey_id": "jrn_test"}


async def _release(db, *bookings):
    await live_support.release_active_holds(db, live_support.JORDAN, [_hold(b) for b in bookings])


async def test_an_active_hold_is_released_and_nothing_is_deleted(gateway):
    db = _Db({"HLD-1": True})
    made = gateway(db)
    await _release(db, "HLD-1")
    assert made.calls == ["HLD-1"] and db.active["HLD-1"] is False
    assert not any("DELETE" in sql.upper() for sql in db.statements)


async def test_an_inactive_hold_is_left_alone(gateway):
    db = _Db({"HLD-1": False})
    made = gateway(db)
    await _release(db, "HLD-1")
    assert made.calls == []


async def test_a_lost_reply_that_committed_is_read_back_and_not_repeated(gateway):
    db = _Db({"HLD-1": True})
    made = gateway(db, **{"HLD-1": "lost-after-commit"})
    await _release(db, "HLD-1")
    assert made.calls == ["HLD-1"]


async def test_a_lost_reply_that_did_not_commit_is_tried_once_more(gateway):
    db = _Db({"HLD-1": True})
    made = gateway(db, **{"HLD-1": "lost-before-commit"})
    await _release(db, "HLD-1")
    assert made.calls == ["HLD-1", "HLD-1"] and db.active["HLD-1"] is False


async def test_a_failing_first_hold_does_not_stop_the_second(gateway):
    db = _Db({"HLD-1": True, "HLD-2": True})
    made = gateway(db, **{"HLD-1": "always-lost"})
    with pytest.raises(AssertionError) as raised:
        await _release(db, "HLD-1", "HLD-2")
    assert made.calls == ["HLD-1", "HLD-1", "HLD-2"]
    assert db.active == {"HLD-1": True, "HLD-2": False}
    message = str(raised.value)
    assert "HLD-1 (release reply lost (TimeoutError))" in message
    assert "HLD-2" not in message and SECRET not in message


async def test_release_calls_stop_at_two_per_hold(gateway):
    db = _Db({"HLD-1": True})
    made = gateway(db, **{"HLD-1": "refused"})
    with pytest.raises(AssertionError, match=r"HLD-1 \(release not confirmed"):
        await _release(db, "HLD-1")
    assert made.calls.count("HLD-1") == live_support.RELEASE_CALLS_PER_HOLD == 2


async def test_an_unreadable_hold_is_released_once_and_reported(gateway):
    db = _Db({"HLD-1": True, "HLD-2": True}, read_fails={"HLD-1"})
    made = gateway(db)
    with pytest.raises(AssertionError, match=r"HLD-1 \(read-back failed \(ConnectionError\)\)"):
        await _release(db, "HLD-1", "HLD-2")
    assert made.calls == ["HLD-1", "HLD-2"] and db.active["HLD-2"] is False


async def test_a_lost_reply_is_not_retried_without_a_read_back(gateway):
    db = _Db({"HLD-1": True, "HLD-2": True}, read_fails={"HLD-1"})
    made = gateway(db, **{"HLD-1": "always-lost"})
    with pytest.raises(AssertionError) as raised:
        await _release(db, "HLD-1", "HLD-2")
    assert made.calls == ["HLD-1", "HLD-2"] and db.active["HLD-2"] is False
    message = str(raised.value)
    assert ("HLD-1 (read-back failed (ConnectionError); release reply lost (TimeoutError))"
            in message)
    assert "HLD-2" not in message and SECRET not in message


async def test_no_token_still_closes_the_leases_and_records_the_ids(gateway, monkeypatch,
                                                                    tmp_path):
    def no_token(user_key):
        raise RuntimeError(SECRET)
    monkeypatch.setattr("scripts.agentcore_caller.caller_scope", no_token)
    log = tmp_path / "ids.jsonl"
    monkeypatch.setenv(live_support.RUN_LOG_ENV, str(log))
    db = _Db({"HLD-1": True}, threads={"t1": ["HLD-1"]})
    made = gateway(db)
    problems = await live_support.cleanup_run(db, live_support.JORDAN, test="t", threads=["t1"],
                                              executions=["exe_1"])
    assert made.calls == []
    assert problems == ["governed release cleanup: HLD-1 (no governed release possible "
                        "(RuntimeError))"]
    assert any("UPDATE journey_executions" in sql for sql in db.statements)
    assert [json.loads(line).get("execution_id") for line in log.read_text().splitlines()] == [
        None, "exe_1"]


async def test_a_scope_that_fails_to_close_keeps_the_release_results(gateway, monkeypatch):
    @contextlib.contextmanager
    def breaks_on_close(user_key):
        yield
        raise RuntimeError(SECRET)
    monkeypatch.setattr("scripts.agentcore_caller.caller_scope", breaks_on_close)
    db = _Db({"HLD-1": True})
    gateway(db)
    with pytest.raises(AssertionError) as raised:
        await _release(db, "HLD-1")
    assert db.active["HLD-1"] is False
    assert str(raised.value) == ("governed release cleanup: closing the caller token scope "
                                 "failed (RuntimeError)")


async def test_a_tracked_booking_without_a_hold_request_is_reported_only_when_active(gateway):
    db = _Db({"HLD-8": False, "HLD-9": True}, no_request={"HLD-8", "HLD-9"})
    made = gateway(db)
    problems = await live_support.cleanup_run(db, live_support.JORDAN, test="t",
                                              bookings=["HLD-8", "HLD-9"])
    assert made.calls == []
    assert problems == ["HLD-9 has no hold request to release it by (still held)"]


async def test_a_run_cleans_up_after_a_log_write_fails(gateway, monkeypatch):
    def broken_log(test, **ids):
        raise OSError(SECRET)
    monkeypatch.setattr(live_support, "record_live_ids", broken_log)
    db = _Db({"HLD-1": True, "HLD-2": True}, threads={"t1": ["HLD-1", "HLD-2"]})
    gateway(db)
    problems = await live_support.cleanup_run(db, live_support.JORDAN, test="t", threads=["t1"],
                                              executions=["exe_1"])
    assert db.active == {"HLD-1": False, "HLD-2": False}
    assert len(problems) == 3 and all("OSError" in p for p in problems)
    assert not any(SECRET in p for p in problems)


async def test_a_run_cleans_up_after_a_lease_closeout_fails(gateway):
    db = _Db({"HLD-1": True}, threads={"t1": ["HLD-1"]}, close_fails=True)
    gateway(db)
    problems = await live_support.cleanup_run(db, live_support.JORDAN, test="t", threads=["t1"],
                                              executions=["exe_1"])
    assert db.active["HLD-1"] is False
    assert problems == ["closing lease exe_1 failed (RuntimeError)"]


async def test_the_run_leases_close_before_any_hold_is_released(gateway):
    """The holds Lambda refuses a release with no execution id while the run's lease is live."""
    db = _Db({"HLD-1": True}, threads={"t1": ["HLD-1"]})
    made = gateway(db)
    closed_when_released = []
    call_tool = made.call_tool

    def release(name, args):
        closed_when_released.append(any("UPDATE journey_executions" in s for s in db.statements))
        return call_tool(name, args)
    made.call_tool = release
    problems = await live_support.cleanup_run(db, live_support.JORDAN, test="t", threads=["t1"],
                                              executions=["exe_1"])
    assert problems == []
    assert closed_when_released == [True]
    assert db.active["HLD-1"] is False


async def test_a_failed_lookup_does_not_stop_the_other_threads(gateway):
    db = _Db({"HLD-2": True}, threads={"t2": ["HLD-2"]}, lookup_fails={"t1"})
    gateway(db)
    problems = await live_support.cleanup_run(db, live_support.JORDAN, test="t",
                                              threads=["t1", "t2"])
    assert db.active["HLD-2"] is False
    assert problems == ["reading holds for thread t1 failed (TimeoutError)"]


def test_cleanup_problems_note_a_test_failure_and_fail_a_passing_test():
    failure = AssertionError("the test's own failure")
    live_support.report_cleanup(["HLD-1 still held"], failure)
    assert failure.__notes__ == ["cleanup: HLD-1 still held"]
    with pytest.raises(AssertionError, match="cleanup: HLD-1 still held"):
        live_support.report_cleanup(["HLD-1 still held"])
    live_support.report_cleanup([])


def test_ids_are_recorded_only_when_the_run_log_is_set(tmp_path, monkeypatch):
    monkeypatch.delenv(live_support.RUN_LOG_ENV, raising=False)
    live_support.record_live_ids("t", booking_id="HLD-6")
    log = tmp_path / "ids.jsonl"
    monkeypatch.setenv(live_support.RUN_LOG_ENV, str(log))
    live_support.record_live_ids("t", booking_id="HLD-7")
    assert [json.loads(line) for line in log.read_text().splitlines()] == [
        {"test": "t", "booking_id": "HLD-7"}]


async def test_a_cleanup_whose_holds_are_all_inactive_needs_no_gateway_or_token(monkeypatch):
    def broken():
        raise RuntimeError(SECRET)

    monkeypatch.setattr("backend.agentcore.gateway.get_agentcore_gateway", broken)
    monkeypatch.setattr("scripts.agentcore_caller.caller_scope",
                        lambda user_key: (_ for _ in ()).throw(RuntimeError(SECRET)))
    db = _Db({"HLD-1": False}, threads={"t1": ["HLD-1"]})

    assert await live_support.cleanup_run(db, live_support.JORDAN, test="t",
                                          threads=["t1"]) == []
