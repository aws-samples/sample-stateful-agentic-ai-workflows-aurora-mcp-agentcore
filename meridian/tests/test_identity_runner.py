"""A run executes the plan, always cleans up, and counts a leftover as a failure."""

import pytest

from scripts.identity_probes.probes import (
    DECOY_TRAVELER,
    JORDAN_TRAVELER,
    THREAD_PREFIX,
    Context,
)
from scripts.identity_probes.receipt import JORDAN
from scripts.identity_probes.runner import Header, run_proof, tidy
from tests.identity_proof_support import FakeCleanup, good_world

HEADER = Header(at="2026-10-08T12:00:00+00:00", git_sha="a" * 40, region="us-east-1",
                design="both", site_host="site.example.net")


def test_a_correct_system_gives_a_passing_receipt_and_a_clean_cleanup():
    ports, _ = good_world()
    cleanup = FakeCleanup()

    receipt = run_proof(ports, cleanup, Context(run_id="abc12345", design="both"), HEADER)

    assert receipt.ok is True and len(receipt.outcomes) == 17
    assert receipt.cleanup["leftovers"] == 0 and receipt.cleanup["problems"] == []
    assert cleanup.released == [("trv_meridian_demo", "HLD-TEST0001")]
    assert all(t.startswith(THREAD_PREFIX + "abc12345") for t in cleanup.purged)
    assert cleanup.prefix == THREAD_PREFIX + "abc12345"


def test_jordan_only_runs_eight_probes_and_records_the_mode():
    ports, _ = good_world()

    receipt = run_proof(ports, FakeCleanup(), Context(run_id="abc12345", design="both"),
                        Header(**{**HEADER.__dict__, "mode": "jordan-only"}), jordan_only=True)

    assert len(receipt.outcomes) == 8 and {o.actor for o in receipt.outcomes} == {JORDAN}
    assert receipt.mode == "jordan-only" and receipt.ok is True


def test_a_leftover_fails_the_run():
    ports, _ = good_world()

    receipt = run_proof(ports, FakeCleanup(leftovers=2), Context(run_id="abc12345", design="both"),
                        HEADER)

    assert receipt.cleanup["leftovers"] == 2 and receipt.ok is False


def test_cleanup_failures_are_recorded_masked_and_fail_the_run():
    ports, _ = good_world()
    cleanup = FakeCleanup(fail_purge=True, fail_release=True, fail_check=True)

    receipt = run_proof(ports, cleanup, Context(run_id="abc12345", design="both"), HEADER)

    problems = receipt.cleanup["problems"]
    assert any("<acct>" in p for p in problems) and not any("123456789012" in p for p in problems)
    assert receipt.cleanup["leftovers"] >= 1 and receipt.ok is False


def test_cleanup_runs_even_when_the_run_is_interrupted():
    ports, _ = good_world()
    cleanup = FakeCleanup()
    calls = {"n": 0}

    def interrupt(*args):
        calls["n"] += 1
        raise KeyboardInterrupt

    broken = ports.__class__(**{**ports.__dict__, "http": interrupt})
    ctx = Context(run_id="abc12345", design="both")

    with pytest.raises(KeyboardInterrupt):
        run_proof(broken, cleanup, ctx, HEADER)

    assert calls["n"] == 1 and cleanup.prefix == THREAD_PREFIX + "abc12345"


def test_the_bookings_are_released_before_the_threads_are_purged():
    ports, _ = good_world()
    cleanup = FakeCleanup()

    run_proof(ports, cleanup, Context(run_id="abc12345", design="both"), HEADER)

    assert cleanup.order.index("release") < cleanup.order.index("purge")
    assert cleanup.order[-1] == "check"


def test_the_run_takes_a_booking_baseline_for_both_travelers_before_the_first_probe():
    ports, database = good_world()
    database.add_booking(DECOY_TRAVELER, "HLD-OLDDECOY", None)
    database.add_booking(JORDAN_TRAVELER, "HLD-OLDJORDAN", None)
    cleanup = FakeCleanup()
    ctx = Context(run_id="abc12345", design="both")

    run_proof(ports, cleanup, ctx, HEADER)

    assert database.preflights == 1
    assert cleanup.baseline == {JORDAN_TRAVELER: frozenset({"HLD-OLDJORDAN"}),
                                DECOY_TRAVELER: frozenset({"HLD-OLDDECOY"})}


def test_a_leftover_is_listed_by_name_in_the_cleanup_notes():
    ports, _ = good_world()

    receipt = run_proof(ports, FakeCleanup(leftovers=2),
                        Context(run_id="abc12345", design="both"), HEADER)

    assert receipt.cleanup["leftover_items"] == ["leftover 0", "leftover 1"]


def test_a_failed_preflight_stops_the_plan_and_still_cleans_up():
    ports, database = good_world()

    def refuse():
        raise RuntimeError("the audit table hides rows from this role")

    database.preflight = refuse
    cleanup = FakeCleanup()

    with pytest.raises(RuntimeError, match="hides rows") as caught:
        run_proof(ports, cleanup, Context(run_id="abc12345", design="both"), HEADER)

    assert cleanup.prefix is not None
    assert any("baseline" in note for note in caught.value.__notes__)


def test_without_a_baseline_the_leftover_check_cannot_pass():
    cleanup = FakeCleanup()

    notes = tidy(cleanup, Context(run_id="abc12345", design="both"))

    assert notes["leftovers"] >= 1 and any("baseline" in p for p in notes["problems"])


def test_the_receipt_carries_the_residue_notes():
    ports, _ = good_world()

    receipt = run_proof(ports, FakeCleanup(), Context(run_id="abc12345", design="both"), HEADER)

    assert any("AgentCore Memory" in note for note in receipt.notes)
    assert receipt.to_dict()["notes"] == receipt.notes


def test_tidy_purges_each_thread_once():
    ctx = Context(run_id="abc12345", design="both")
    ctx.baseline = {}
    ctx.threads += [ctx.thread("d")] * 2
    cleanup = FakeCleanup()

    notes = tidy(cleanup, ctx)

    assert notes["threads_purged"] == 1 and len(cleanup.purged) == 1


class InterruptedOnce(FakeCleanup):
    """A cleanup whose named step is hit by a Ctrl-C on its first call."""

    def __init__(self, step):
        super().__init__()
        self.step, self.hit = step, False

    def _maybe(self, step):
        if step == self.step and not self.hit:
            self.hit = True
            raise KeyboardInterrupt

    def purge_thread(self, thread):
        self._maybe("purge")
        super().purge_thread(thread)

    def release_bookings(self, booking_ids):
        self._maybe("release")
        return super().release_bookings(booking_ids)

    def leftovers(self, prefix, booking_ids):
        self._maybe("check")
        return super().leftovers(prefix, booking_ids)


@pytest.mark.parametrize("step", ["purge", "release", "check"])
def test_a_second_interrupt_during_cleanup_is_reported_and_the_rest_still_runs(step):
    ports, _ = good_world()
    cleanup = InterruptedOnce(step)

    receipt = run_proof(ports, cleanup, Context(run_id="abc12345", design="both"), HEADER)

    assert receipt.ok is False and cleanup.hit
    assert any("interrupted" in p for p in receipt.cleanup["problems"])
    assert (receipt.cleanup["leftovers"] >= 1) is (step == "check")
    assert cleanup.released == ([("trv_meridian_demo", "HLD-TEST0001")]
                                 if step != "release" else [])


def test_a_second_interrupt_after_a_first_still_leaves_notes_on_the_error():
    ports, _ = good_world()
    broken = ports.__class__(**{**ports.__dict__, "http": lambda *a: (_ for _ in ()).throw(
        KeyboardInterrupt())})
    cleanup = InterruptedOnce("purge")

    with pytest.raises(KeyboardInterrupt) as caught:
        run_proof(broken, cleanup, Context(run_id="abc12345", design="both"), HEADER)

    assert any("cleanup" in note for note in caught.value.__notes__)
