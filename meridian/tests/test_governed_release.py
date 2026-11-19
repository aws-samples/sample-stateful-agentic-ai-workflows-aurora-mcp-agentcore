"""One rule gives a hold back: read it back first, at most two calls, unknown is not released."""

import json

import pytest

from scripts import governed_release, kill_and_resume_proof, lost_response_proof
from scripts.governed_release import CallLedger, final_code
from tests.governed_release_support import JORDAN, FakeAurora, install_gateway


class Journal(FakeAurora):
    """Records the order of read-backs and release calls in one list."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events: list[str] = []

    async def execute(self, sql, params=(), transaction_id=None, **kwargs):
        if sql == governed_release.ACTIVE_SQL:
            self.events.append("read-back")
        return await super().execute(sql, params, transaction_id=transaction_id, **kwargs)


def refusing(db, monkeypatch, *, commits_on_first=False):
    made = install_gateway(monkeypatch, db)

    def call_tool(name, args):
        booking = args["holdRequestId"].removeprefix("hrq-")
        made.calls.append(booking)
        db.events.append("call")
        if commits_on_first and len(made.calls) == 1:
            db.status[booking] = "released"
            raise TimeoutError("reply lost")
        return {"result": {"content": [{"type": "text",
                                        "text": json.dumps({"error": "hold_not_found"})}]}}

    monkeypatch.setattr(made, "call_tool", call_tool)
    return made


async def test_a_hold_is_read_back_before_each_call_and_a_second_call_needs_it_still_held(
        monkeypatch):
    db = Journal({"bk-1": ("held", "jrn-1")})
    made = refusing(db, monkeypatch)

    problems = await governed_release.release_bookings(db, JORDAN, ["bk-1"])

    assert db.events == ["read-back", "call", "read-back", "call", "read-back"]
    assert made.calls == ["bk-1", "bk-1"] and len(problems) == 1


async def test_a_third_call_is_never_sent_even_by_a_later_run_sharing_the_ledger(
        monkeypatch, tmp_path):
    db = Journal({"bk-1": ("held", "jrn-1")})
    made = refusing(db, monkeypatch)
    ledger = tmp_path / "calls"

    await governed_release.release_bookings(db, JORDAN, ["bk-1"], CallLedger(ledger))
    await governed_release.release_bookings(db, JORDAN, ["bk-1"], CallLedger(ledger))

    assert len(made.calls) == 2


async def test_a_reply_lost_after_the_commit_is_read_back_and_not_repeated(monkeypatch):
    db = Journal({"bk-1": ("held", "jrn-1")})
    made = refusing(db, monkeypatch, commits_on_first=True)

    assert await governed_release.release_bookings(db, JORDAN, ["bk-1"]) == []
    assert made.calls == ["bk-1"]


async def test_a_booking_that_cannot_be_seen_is_unknown_never_released(monkeypatch):
    db = Journal({"bk-1": ("held", "jrn-1")}, owner="trv_someone_else")
    made = install_gateway(monkeypatch, db)

    problems = await governed_release.release_bookings(db, JORDAN, ["bk-1"])

    assert len(made.calls) == 1 and "not visible or its state unknown" in problems[0]


async def test_a_confirmed_booking_needs_no_call_and_is_not_a_problem(monkeypatch):
    db = FakeAurora({"bk-1": ("confirmed", "jrn-1")})
    made = install_gateway(monkeypatch, db)

    assert await governed_release.release_bookings(db, JORDAN, ["bk-1"]) == []
    assert made.calls == [] and db.deletes() == []


async def test_a_booking_with_no_hold_request_is_a_problem_unless_it_reads_inactive(
        monkeypatch):
    db = FakeAurora({"bk-gone": ("confirmed", "jrn-1"), "bk-held": ("held", "jrn-2")})
    del db.journey["bk-gone"], db.journey["bk-held"]
    made = install_gateway(monkeypatch, db)

    problems = await governed_release.release_bookings(db, JORDAN, ["bk-gone", "bk-held"])

    assert made.calls == [] and len(problems) == 1 and "bk-held" in problems[0]


async def test_a_thread_shared_with_another_thread_is_not_released(monkeypatch):
    db = FakeAurora({"bk-1": ("held", "jrn-1")}, threads={"t-a": "jrn-1", "t-b": "jrn-1"})
    made = install_gateway(monkeypatch, db)

    holds, problems = await governed_release.thread_holds(db, "t-a")

    assert holds == [] and "other threads" in problems[0] and made.calls == []


def test_the_ledger_counts_in_memory_and_in_a_file(tmp_path):
    for ledger in (CallLedger(), CallLedger(tmp_path / "calls")):
        assert ledger.made("bk-1") == 0
        ledger.record("bk-1")
        ledger.record("bk-1")
        ledger.record("bk-2")
        assert (ledger.made("bk-1"), ledger.made("bk-2")) == (2, 1)


@pytest.mark.parametrize(("code", "clean", "expected"), [
    (0, True, 0), (0, False, 1), (1, False, 1), (2, False, 2), (1, True, 1)])
def test_a_failed_run_keeps_its_own_result_when_the_cleanup_also_fails(code, clean, expected):
    assert final_code(code, clean) == expected


@pytest.fixture
def finishing(monkeypatch):
    db = FakeAurora({"bk-1": ("held", "jrn-1")})
    purged = []

    async def purge(client, journey, thread):
        purged.append((journey, thread))

    monkeypatch.setattr(kill_and_resume_proof, "TRAVELER", JORDAN)
    monkeypatch.setattr(kill_and_resume_proof, "_purge", purge)
    monkeypatch.setattr(lost_response_proof, "_purge", purge)
    return db, install_gateway(monkeypatch, db), purged


@pytest.mark.parametrize("finish", [
    lambda db: kill_and_resume_proof._finish(db, "jrn-1", "proof-x", False, False),
    lambda db: lost_response_proof._finish(db, "jrn-1", "loss-x", False),
])
async def test_the_kill_and_loss_proofs_release_the_hold_and_delete_nothing_by_default(
        finishing, finish):
    db, gateway, purged = finishing

    assert await finish(db) is True

    assert gateway.calls == ["bk-1"] and db.status["bk-1"] == "released"
    assert purged == [] and db.deletes() == []


@pytest.mark.parametrize("finish", [
    lambda db: kill_and_resume_proof._finish(db, "jrn-1", "proof-x", False, True),
    lambda db: lost_response_proof._finish(db, "jrn-1", "loss-x", True),
])
async def test_the_kill_and_loss_proofs_delete_only_with_the_disposable_database_flag(
        finishing, finish):
    db, gateway, purged = finishing

    assert await finish(db) is True

    assert len(purged) == 1 and purged[0][0] == "jrn-1" and gateway.calls == []


async def test_keep_leaves_the_hold_held_and_every_row(finishing):
    db, gateway, purged = finishing

    assert await kill_and_resume_proof._finish(db, "jrn-1", "proof-x", True, False) is True

    assert gateway.calls == [] and db.status["bk-1"] == "held" and purged == []


async def test_all_inactive_holds_pass_without_gateway_settings_or_a_token(monkeypatch):
    def broken_gateway():
        raise RuntimeError("no Gateway settings")

    def no_token(user_key):
        raise RuntimeError("no Cognito secret")

    monkeypatch.setattr("backend.agentcore.gateway.get_agentcore_gateway", broken_gateway)
    monkeypatch.setattr("scripts.agentcore_caller.caller_scope", no_token)
    db = FakeAurora({"bk-1": ("confirmed", "jrn-1"), "bk-2": ("released", "jrn-2")})

    problems = await governed_release.release_bookings(db, JORDAN, ["bk-1", "bk-2"])

    assert problems == []


async def test_something_still_active_sets_up_the_gateway_once_and_releases(monkeypatch):
    opened = []
    db = FakeAurora({"bk-1": ("held", "jrn-1"), "bk-2": ("held", "jrn-2"),
                     "bk-3": ("confirmed", "jrn-3")})
    made = install_gateway(monkeypatch, db)
    real = governed_release.agentcore_caller.caller_scope

    def counting(user_key):
        opened.append(user_key)
        return real(user_key)

    monkeypatch.setattr("scripts.agentcore_caller.caller_scope", counting)

    assert await governed_release.release_bookings(db, JORDAN, ["bk-3", "bk-1", "bk-2"]) == []

    assert made.calls == ["bk-1", "bk-2"] and opened == ["jordan"]


async def test_an_active_hold_with_no_gateway_is_reported_not_released(monkeypatch):
    def broken_gateway():
        raise RuntimeError("secret-detail")

    monkeypatch.setattr("backend.agentcore.gateway.get_agentcore_gateway", broken_gateway)
    db = FakeAurora({"bk-1": ("held", "jrn-1"), "bk-2": ("confirmed", "jrn-2")})

    problems = await governed_release.release_bookings(db, JORDAN, ["bk-1", "bk-2"])

    assert problems == ["booking bk-1 not released: no governed release possible (RuntimeError)"]
    assert db.status["bk-1"] == "held"


async def test_a_cleanup_problem_is_a_note_and_the_tests_own_failure_stays_primary(
        monkeypatch):
    from tests import test_gateway_login_aurora as gateway_tests

    async def failing(*args, **kwargs):
        return ["bk-1 (still held)"]

    monkeypatch.setattr(gateway_tests, "cleanup_run", failing)
    monkeypatch.setattr(gateway_tests, "get_rds_data_client", lambda: object())

    with pytest.raises(ValueError, match="the test's own failure") as raised:
        try:
            raise ValueError("the test's own failure")
        finally:
            await gateway_tests._give_back("bk-1", "ref")

    assert any("still held" in note for note in raised.value.__notes__)
