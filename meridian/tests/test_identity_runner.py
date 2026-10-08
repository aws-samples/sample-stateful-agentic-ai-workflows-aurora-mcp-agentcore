"""A run executes the plan, always cleans up, and counts a leftover as a failure."""

import pytest

from scripts.identity_probes.probes import THREAD_PREFIX, Context
from scripts.identity_probes.receipt import JORDAN
from scripts.identity_probes.runner import Header, run_proof, tidy
from tests.identity_proof_support import FakeCleanup, good_world

HEADER = Header(at="2026-10-08T12:00:00+00:00", git_sha="a" * 40, region="us-east-1",
                pool_suffix="aaZV", design="both", site_host="site.example.net")


def test_a_correct_system_gives_a_passing_receipt_and_a_clean_cleanup():
    ports, _ = good_world()
    cleanup = FakeCleanup()

    receipt = run_proof(ports, cleanup, Context(run_id="abc12345", design="both"), HEADER)

    assert receipt.ok is True and len(receipt.outcomes) == 15
    assert receipt.cleanup["leftovers"] == 0 and receipt.cleanup["problems"] == []
    assert cleanup.released == ["HLD-TEST0001"]
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


def test_tidy_purges_each_thread_once():
    ctx = Context(run_id="abc12345", design="both")
    ctx.thread("d")
    ctx.thread("d")
    cleanup = FakeCleanup()

    notes = tidy(cleanup, ctx)

    assert notes["threads_purged"] == 1 and len(cleanup.purged) == 1
