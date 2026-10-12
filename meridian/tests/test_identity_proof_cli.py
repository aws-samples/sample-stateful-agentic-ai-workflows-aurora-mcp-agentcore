"""The command: a dry run by default, a guarded apply, exit codes and the files it writes."""

import json
import os
import re
import signal
import stat
from datetime import datetime, timezone

import pytest

from scripts import identity_proof
from scripts.identity_probes.probes import PLAN
from scripts.identity_probes.receipt import leaks
from scripts.identity_release import settings
from tests.identity_proof_support import FakeCleanup, good_world

ENV = {
    "AURORA_CLUSTER_ARN": "arn:aws:rds:us-east-1:123456789012:cluster:meridian",
    "MERIDIAN_AGENTCORE_AUTH": "jwt",
    "MERIDIAN_COGNITO_REGION": "us-east-1",
    "MERIDIAN_COGNITO_USER_POOL_ID": "us-east-1_AbCdEfGhI",
    "MERIDIAN_COGNITO_APP_CLIENT_ID": "abc123clientid",
}
NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
APPLY = ["--apply", settings.CONFIRM_FLAG]
ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")


def make_deps(tmp_path, *, env=None, changes=(), account="123456789012", ports=None,
              cleanup=None, build_error=None):
    world, _ = good_world()
    rig = identity_proof.Rig(ports or world, cleanup or FakeCleanup())
    built = []

    def build_rig(environment, region, url):
        built.append((region, url))
        if build_error:
            raise build_error
        return rig

    deps = identity_proof.Dependencies(
        env={**ENV, **(env or {})}, caller=lambda region: (account, region),
        git_sha=lambda: "a" * 40, changes=lambda: list(changes),
        site_url=lambda: "https://site.example.net", build_rig=build_rig, now=lambda: NOW,
        run_id=lambda: "abc12345", local_root=tmp_path)
    return deps, built


def run(argv, deps, capsys):
    code = identity_proof.main(argv, deps)
    return code, capsys.readouterr().out


def test_with_no_flags_it_prints_the_plan_and_calls_nothing(tmp_path, capsys):
    deps, built = make_deps(tmp_path)

    code, out = run([], deps, capsys)

    assert code == 0 and built == []
    assert "DRY RUN" in out and all(spec.id in out for spec in PLAN)
    assert f"identity_proof.py --apply {settings.CONFIRM_FLAG}" in out
    assert not list(tmp_path.iterdir())


def test_the_dry_run_declares_the_residue_including_the_concierge_memory_events(
        tmp_path, capsys):
    deps, _ = make_deps(tmp_path)

    _, out = run([], deps, capsys)

    assert "AgentCore Memory" in out and "not deleted by this command" in out
    assert "append-only" in out
    assert "17 probes" in out


def test_the_dry_run_says_what_would_be_refused(tmp_path, capsys):
    deps, _ = make_deps(tmp_path, env={"MERIDIAN_AGENTCORE_AUTH": "iam"}, changes=[" M a.py"])

    code, out = run([], deps, capsys)

    assert code == 0 and out.count("WOULD REFUSE") >= 1 and "not jwt" in out


def test_apply_without_the_confirmation_is_refused(tmp_path, capsys):
    deps, built = make_deps(tmp_path)

    code, out = run(["--apply"], deps, capsys)

    assert code == 3 and built == [] and settings.CONFIRM_FLAG in out


@pytest.mark.parametrize(("kwargs", "words"), [
    ({"env": {"MERIDIAN_AGENTCORE_AUTH": "iam"}}, "not jwt"),
    ({"env": {"MERIDIAN_COGNITO_USER_POOL_ID": ""}}, "MERIDIAN_COGNITO_USER_POOL_ID"),
    ({"changes": [" M scripts/x.py"]}, "uncommitted change"),
    ({"account": "999999999999"}, "another account or Region"),
])
def test_a_guard_refuses_before_anything_is_built(tmp_path, capsys, kwargs, words):
    deps, built = make_deps(tmp_path, **kwargs)

    code, out = run(APPLY, deps, capsys)

    assert code == 3 and built == [] and words in out and not ACCOUNT_ID.search(out)


def test_a_base_url_that_could_leak_a_token_is_refused(tmp_path, capsys):
    deps, built = make_deps(tmp_path)

    code, out = run([*APPLY, "--base-url", "http://site.example.net"], deps, capsys)

    assert code == 3 and built == [] and "use https" in out


def test_an_output_directory_outside_local_is_refused(tmp_path, capsys):
    deps, built = make_deps(tmp_path / "local")

    code, out = run([*APPLY, "--output-dir", str(tmp_path / "elsewhere")], deps, capsys)

    assert code == 3 and built == [] and "inside" in out


def test_printed_text_masks_pool_client_and_arn_ids(tmp_path, capsys):
    pool = "us-east-1" + "_" + "AbCdEfGhI"
    failure = RuntimeError(f"cannot reach {pool} at arn:aws:s3:::x")
    deps, _ = make_deps(tmp_path, build_error=failure)

    code, out = run(APPLY, deps, capsys)

    assert code == 1 and pool not in out and "arn:aws" not in out and "<pool-id>" in out


def test_a_passing_run_writes_a_private_clean_receipt_and_two_pages(tmp_path, capsys):
    deps, built = make_deps(tmp_path)

    code, out = run(APPLY, deps, capsys)

    folder = tmp_path / "identity-proof"
    receipt = folder / "receipt-20261008T120000Z.json"
    assert code == 0 and "RESULT: PASS" in out and out.count("Wrote ") == 3
    assert built == [("us-east-1", "https://site.example.net")]
    for name in ("receipt-20261008T120000Z.json", "latest.json"):
        assert stat.S_IMODE((folder / name).stat().st_mode) == 0o600
    assert (folder / "receipt-20261008T120000Z.summary.html").exists()
    assert (folder / "receipt-20261008T120000Z.full.html").exists()
    data = json.loads(receipt.read_text())
    assert data["ok"] is True and data["design"] == "both" and data["account"] == "<acct>"
    assert leaks(receipt.read_text()) == [] and leaks(out) == []
    assert "RESULT: PASS" in out


def test_a_failing_probe_exits_one_and_still_writes_the_receipt(tmp_path, capsys):
    world, _ = good_world()
    broken = world.__class__(**{**world.__dict__, "http": lambda *a: (200, {"ok": True})})
    deps, _ = make_deps(tmp_path, ports=broken)

    code, out = run(APPLY, deps, capsys)

    data = json.loads((tmp_path / "identity-proof" / "latest.json").read_text())
    assert code == 1 and data["ok"] is False and "RESULT: FAIL" in out


def test_a_leftover_exits_one(tmp_path, capsys):
    deps, _ = make_deps(tmp_path, cleanup=FakeCleanup(leftovers=1))

    code, out = run(APPLY, deps, capsys)

    assert code == 1 and "RESULT: FAIL" in out


def test_jordan_only_runs_the_controls_and_records_the_mode(tmp_path, capsys):
    deps, _ = make_deps(tmp_path)

    code, _ = run([*APPLY, "--jordan-only"], deps, capsys)

    data = json.loads((tmp_path / "identity-proof" / "latest.json").read_text())
    assert code == 0 and data["mode"] == "jordan-only" and len(data["outcomes"]) == 8


def test_a_crash_while_building_is_one_masked_line_and_exit_one(tmp_path, capsys):
    deps, _ = make_deps(
        tmp_path, build_error=RuntimeError("cannot sign in as jordan in 123456789012"))

    code, out = run(APPLY, deps, capsys)

    assert code == 1 and "ERROR: RuntimeError" in out and "<acct>" in out
    assert "Traceback" not in out and not ACCOUNT_ID.search(out)


def test_an_interrupt_is_reported_and_cleanup_still_ran(tmp_path, capsys):
    world, _ = good_world()

    def interrupt(*args):
        raise KeyboardInterrupt

    cleanup = FakeCleanup()
    broken = world.__class__(**{**world.__dict__, "http": interrupt})
    deps, _ = make_deps(tmp_path, ports=broken, cleanup=cleanup)

    code, out = run(APPLY, deps, capsys)

    assert code == 1 and "Interrupted" in out and cleanup.prefix is not None


def test_render_rebuilds_the_pages_from_a_recorded_receipt(tmp_path, capsys):
    deps, _ = make_deps(tmp_path)
    run(APPLY, deps, capsys)
    latest = tmp_path / "identity-proof" / "latest.json"

    code, out = run(["--render", str(latest)], deps, capsys)

    assert code == 0 and (tmp_path / "identity-proof" / "latest.summary.html").exists()


def test_render_recomputes_the_verdict_and_does_not_trust_a_stored_pass(tmp_path, capsys):
    deps, _ = make_deps(tmp_path)
    folder = tmp_path / "identity-proof"
    folder.mkdir()
    data = good_receipt_dict()
    data["outcomes"][0].update(result="allowed", refused_by=None, passed=True)
    data["ok"] = True
    (folder / "forged.json").write_text(json.dumps(data))

    code, _ = run(["--render", str(folder / "forged.json")], deps, capsys)

    page = (folder / "forged.full.html").read_text()
    assert code == 0 and "Result: FAIL" in page and "Result: PASS" not in page


def test_render_refuses_a_receipt_outside_the_local_folder(tmp_path, capsys):
    deps, _ = make_deps(tmp_path / "local")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "r.json").write_text(json.dumps(good_receipt_dict()))

    code, out = run(["--render", str(outside / "r.json")], deps, capsys)

    assert code == 3 and "inside" in out and not list(outside.glob("*.html"))


def test_render_refuses_a_receipt_whose_pages_would_leak_and_writes_nothing(tmp_path, capsys):
    deps, _ = make_deps(tmp_path)
    folder = tmp_path / "identity-proof"
    folder.mkdir()
    data = good_receipt_dict()
    data["cleanup"]["problems"] = ["arn:aws:rds:us-east-1:123456789012:cluster:meridian"]
    (folder / "leaky.json").write_text(json.dumps(data))

    code, out = run(["--render", str(folder / "leaky.json")], deps, capsys)

    assert code == 3 and "arn" in out and not list(folder.glob("*.html"))


def test_render_writes_private_pages_and_does_not_follow_a_symlink(tmp_path, capsys):
    deps, _ = make_deps(tmp_path)
    run(APPLY, deps, capsys)
    folder = tmp_path / "identity-proof"
    target = tmp_path / "victim.txt"
    target.write_text("keep")
    link = folder / "latest.summary.html"
    link.unlink(missing_ok=True)
    link.symlink_to(target)

    code, _ = run(["--render", str(folder / "latest.json")], deps, capsys)

    assert code == 0 and target.read_text() == "keep" and not link.is_symlink()
    assert stat.S_IMODE(link.stat().st_mode) == 0o600
    assert stat.S_IMODE((folder / "latest.full.html").stat().st_mode) == 0o600


def test_render_refuses_a_file_that_is_not_a_receipt(tmp_path, capsys):
    deps, _ = make_deps(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{}")

    code, out = run(["--render", str(bad)], deps, capsys)

    assert code == 3 and "not a receipt" in out


@pytest.mark.parametrize("argv", [["--bogus"], ["--appl"], ["--apply", "--jordan"]])
def test_a_usage_error_or_an_abbreviation_is_a_refusal_not_a_traceback(tmp_path, capsys, argv):
    deps, built = make_deps(tmp_path)

    code, out = run(argv, deps, capsys)

    assert code == 3 and built == [] and "Traceback" not in out


def good_receipt_dict():
    world, _ = good_world()
    from scripts.identity_probes.probes import Context
    from scripts.identity_probes.runner import Header, run_proof

    header = Header(at="2026-10-08T12:00:00+00:00", git_sha="a" * 40, region="us-east-1",
                    design="both", site_host="site.example.net")
    return run_proof(world, FakeCleanup(), Context(run_id="abc12345", design="both"),
                     header).to_dict()


def latest(tmp_path):
    return json.loads((tmp_path / "identity-proof" / "latest.json").read_text())


def passing_run_then(tmp_path, capsys, **kwargs):
    """Record a passing receipt, then run again with ``kwargs`` and return code and output."""
    run(APPLY, make_deps(tmp_path)[0], capsys)
    assert latest(tmp_path)["ok"] is True
    return run(APPLY, make_deps(tmp_path, **kwargs)[0], capsys)


def test_a_crash_before_any_probe_invalidates_an_older_passing_receipt(tmp_path, capsys):
    code, _ = passing_run_then(tmp_path, capsys, build_error=RuntimeError("no sign-in"))

    data = latest(tmp_path)
    assert code == 1 and data["ok"] is False and data["outcomes"] == []
    assert any("RuntimeError" in p for p in data["cleanup"]["problems"])


def test_an_interrupt_invalidates_an_older_passing_receipt_and_reports_the_cleanup(
        tmp_path, capsys):
    world, _ = good_world()
    broken = world.__class__(**{**world.__dict__, "http": lambda *a: (_ for _ in ()).throw(
        KeyboardInterrupt())})

    code, out = passing_run_then(tmp_path, capsys, ports=broken)

    assert code == 1 and latest(tmp_path)["ok"] is False
    assert "Interrupted" in out and "cleanup:" in out and "0 leftover(s)" in out


@pytest.mark.parametrize("name", ["SIGTERM", "SIGHUP"])
def test_a_termination_signal_is_handled_like_an_interrupt(tmp_path, capsys, name):
    world, _ = good_world()
    number = getattr(signal, name)

    def terminate(*args):
        os.kill(os.getpid(), number)
        raise AssertionError("the handler should have raised first")

    broken = world.__class__(**{**world.__dict__, "http": terminate})
    before = signal.getsignal(number)

    code, out = passing_run_then(tmp_path, capsys, ports=broken)

    assert code == 1 and "Interrupted" in out and latest(tmp_path)["ok"] is False
    assert signal.getsignal(number) is before


def test_an_interrupt_inside_the_cleanup_fails_the_receipt_and_names_the_problem(
        tmp_path, capsys):
    class Impatient(FakeCleanup):
        def release_bookings(self, booking_ids):
            raise KeyboardInterrupt

    deps, _ = make_deps(tmp_path, cleanup=Impatient())

    code, out = run(APPLY, deps, capsys)

    assert code == 1 and "CLEANUP: release: interrupted" in out and latest(tmp_path)["ok"] is False


def test_a_cleanup_problem_alone_exits_one(tmp_path, capsys):
    deps, _ = make_deps(tmp_path, cleanup=FakeCleanup(fail_release=True))

    code, out = run(APPLY, deps, capsys)

    assert code == 1 and "RESULT: FAIL" in out


def test_a_header_that_would_leak_stops_the_run_before_any_probe(tmp_path, capsys):
    deps, built = make_deps(tmp_path)
    leaky = "https://" + "a" * 120 + ".example.net"
    deps = deps.__class__(**{**deps.__dict__, "site_url": lambda: leaky})

    code, out = run(APPLY, deps, capsys)

    assert code == 1 and built == [] and "ERROR: ValueError" in out
    assert not list((tmp_path / "identity-proof").glob("*.json"))
