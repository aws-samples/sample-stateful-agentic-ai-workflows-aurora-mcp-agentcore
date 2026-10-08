"""The backend-login proof runs the real backend as meridian_backend and records the result."""

from __future__ import annotations

import json
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import prove_backend_login as proof
from scripts import warm_demo
from scripts.identity_release import preflight, settings

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
BACKEND_SECRET = "arn:aws:secretsmanager:us-east-1:123456789012:secret:backend-login-AbC123"
MASTER_SECRET = "arn:aws:secretsmanager:us-east-1:123456789012:secret:master-AbC123"
ENV = {
    "AURORA_SECRET_ARN": MASTER_SECRET,
    "AURORA_BACKEND_SECRET_ARN": BACKEND_SECRET,
    "AURORA_CLUSTER_ARN": "arn:aws:rds:us-east-1:123456789012:cluster:meridian",
    "MERIDIAN_API_TOKEN": "shared",
    "MERIDIAN_COGNITO_REGION": "us-east-1",
    "MERIDIAN_COGNITO_USER_POOL_ID": "us-east-1_AbCdEfGhI",
    "MERIDIAN_COGNITO_APP_CLIENT_ID": "exampleclientid123",
    "MERIDIAN_AGENTCORE_AUTH": "jwt",
    "ENVIRONMENT": "production",
}


class Backend:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class Harness:
    """Fakes for every outside effect, recording the order they were used in."""

    def __init__(self, tmp_path: Path, *, login="meridian_backend", bypass=False,
                 failing=None, healthy=True, target=("123456789012", "us-east-1"), crash=None):
        self.events: list[str] = []
        self.backend = Backend()
        self.started_with: dict = {}
        self.steps_run: list[proof.Step] = []
        self.login, self.bypass, self.failing, self.healthy = login, bypass, failing, healthy
        self.target, self.crash = target, crash
        self.path = tmp_path / "release-b2" / "backend-login-proof.json"

    def deps(self) -> proof.Dependencies:
        def caller(region):
            self.events.append("guard")
            return self.target

        def identity(secret_arn):
            self.events.append(f"identity {secret_arn}")
            return {"login": self.login, "bypass_rls": self.bypass}

        def start(env, port):
            self.events.append("start")
            self.started_with = dict(env)
            return self.backend

        def wait(url):
            self.events.append(f"wait {url}")
            return self.healthy

        def run_step(step):
            self.events.append(f"step {step.name}")
            if self.crash:
                raise self.crash
            self.steps_run.append(step)
            code = 1 if step.name == self.failing else 0
            return proof.StepResult(step.name, code == 0, 1.5, "tail of the output")

        return proof.Dependencies(
            env=ENV, caller=caller, identity=identity, start_backend=start, wait_healthy=wait,
            run_step=run_step, now=lambda: NOW, commit=lambda: "abc1234", receipt_path=self.path)


def run(harness, *flags):
    return proof.main(["--apply", settings.CONFIRM_FLAG, *flags], harness.deps())


def test_a_dry_run_lists_the_plan_and_touches_nothing(tmp_path, capsys):
    harness = Harness(tmp_path)

    assert proof.main([], harness.deps()) == 0

    out = capsys.readouterr().out
    assert "DRY RUN" in out and "warm" in out and "recovery" in out
    assert harness.events == [] and not harness.path.exists()
    assert "--apply" in out and settings.CONFIRM_FLAG in out


def test_apply_without_the_confirmation_is_refused(tmp_path, capsys):
    harness = Harness(tmp_path)

    assert proof.main(["--apply"], harness.deps()) == 3

    assert settings.CONFIRM_FLAG in capsys.readouterr().out and harness.events == []


def test_a_passing_run_checks_the_login_first_starts_the_backend_and_records_the_proof(tmp_path):
    harness = Harness(tmp_path)

    assert run(harness) == 0

    assert harness.events == [
        "guard", f"identity {BACKEND_SECRET}", "start", "wait http://127.0.0.1:8014/health",
        "step warm", "step recovery"]
    assert harness.backend.stopped
    receipt = json.loads(harness.path.read_text())
    assert receipt["ok"] is True and receipt["login"] == "meridian_backend"
    assert receipt["commit"] == "abc1234" and receipt["at"] == NOW.isoformat()
    assert [s["name"] for s in receipt["steps"]] == ["warm", "recovery"]
    assert stat.S_IMODE(harness.path.stat().st_mode) == 0o600


def test_the_recorded_proof_satisfies_the_publish_preflight(tmp_path):
    harness = Harness(tmp_path)
    run(harness)

    assert preflight.check_backend_login_proof(harness.path, NOW) == []


def test_the_backend_runs_as_the_login_with_no_shared_token_or_pool_and_loopback_on(tmp_path):
    harness = Harness(tmp_path)
    run(harness)

    started = harness.started_with
    assert started["AURORA_SECRET_ARN"] == BACKEND_SECRET
    assert started["ENVIRONMENT"] == "development" and started["MERIDIAN_AGENTCORE_AUTH"] == "iam"
    assert started["MERIDIAN_ALLOW_INSECURE_LOCALHOST"] == "1"
    for name in ("MERIDIAN_API_TOKEN", "MERIDIAN_COGNITO_REGION", "MERIDIAN_COGNITO_USER_POOL_ID",
                 "MERIDIAN_COGNITO_APP_CLIENT_ID"):
        assert started[name] == ""


def test_the_proof_scripts_keep_the_master_login_and_target_the_login_backend(tmp_path):
    harness = Harness(tmp_path)
    run(harness)

    warm, recovery = harness.steps_run
    assert warm.argv[-2:] == ["--base-url", "http://127.0.0.1:8014"]
    assert recovery.argv[-2:] == ["--during", "waiting"]
    for step in harness.steps_run:
        assert step.env["AURORA_SECRET_ARN"] == MASTER_SECRET
    assert recovery.env["MERIDIAN_PROOF_API"] == "http://127.0.0.1:8014/api"


def test_a_failing_step_stops_the_run_records_a_failure_and_still_stops_the_backend(tmp_path):
    harness = Harness(tmp_path, failing="warm")

    assert run(harness) == 1

    assert "step recovery" not in harness.events and harness.backend.stopped
    receipt = json.loads(harness.path.read_text())
    assert receipt["ok"] is False and [s["ok"] for s in receipt["steps"]] == [False]


def test_a_failure_cannot_leave_an_old_passing_proof_in_place(tmp_path):
    harness = Harness(tmp_path)
    run(harness)
    failing = Harness(tmp_path, failing="recovery")

    assert run(failing) == 1

    assert json.loads(harness.path.read_text())["ok"] is False


@pytest.mark.parametrize(
    ("login", "bypass"), [("meridian_admin", False), ("meridian_backend", True)])
def test_a_secret_that_is_not_the_least_privilege_login_is_refused_before_anything_starts(
        tmp_path, capsys, login, bypass):
    harness = Harness(tmp_path, login=login, bypass=bypass)

    assert run(harness) == 3

    assert harness.events == ["guard", f"identity {BACKEND_SECRET}"]
    assert not harness.path.exists()
    assert "meridian_backend" in capsys.readouterr().out


def test_a_backend_that_never_answers_fails_the_run_and_is_stopped(tmp_path, capsys):
    harness = Harness(tmp_path, healthy=False)

    assert run(harness) == 1

    assert harness.backend.stopped and "step warm" not in harness.events
    assert "did not answer" in capsys.readouterr().out
    assert json.loads(harness.path.read_text())["ok"] is False


def test_a_missing_backend_login_secret_is_refused(tmp_path, capsys):
    harness = Harness(tmp_path)
    deps = harness.deps()
    deps.env = {k: v for k, v in ENV.items() if k != "AURORA_BACKEND_SECRET_ARN"}

    assert proof.main(["--apply", settings.CONFIRM_FLAG], deps) == 3

    assert "AURORA_BACKEND_SECRET_ARN" in capsys.readouterr().out


def test_output_is_masked_of_account_ids(tmp_path, capsys):
    harness = Harness(tmp_path, login="arn:aws:iam::123456789012:role/x")

    run(harness)

    assert "123456789012" not in capsys.readouterr().out


def test_the_warm_up_takes_a_base_url_but_not_together_with_hosted():
    parser = warm_demo.build_parser()
    assert parser.parse_args([]).base_url == warm_demo.LOCAL_URL
    assert parser.parse_args(["--base-url", "http://127.0.0.1:8014"]).base_url == (
        "http://127.0.0.1:8014")
    with pytest.raises(SystemExit):
        parser.parse_args(["--hosted", "--base-url", "http://127.0.0.1:8014"])


@pytest.mark.parametrize("target", [("210987654321", "us-east-1"), ("123456789012", "eu-west-1")])
def test_a_caller_in_another_account_or_region_is_refused_before_any_client_is_used(
        tmp_path, capsys, target):
    harness = Harness(tmp_path, target=target)

    assert run(harness) == 3

    out = capsys.readouterr().out
    assert harness.events == ["guard"] and not harness.path.exists()
    assert "210987654321" not in out and "<acct>" in out


def test_a_cluster_arn_that_is_not_a_cluster_is_refused_before_the_guard(tmp_path, capsys):
    harness = Harness(tmp_path)
    deps = harness.deps()
    deps.env = {**ENV, "AURORA_CLUSTER_ARN": "not-an-arn"}

    assert proof.main(["--apply", settings.CONFIRM_FLAG], deps) == 3

    assert harness.events == [] and "AURORA_CLUSTER_ARN" in capsys.readouterr().out


def test_an_abbreviated_flag_is_not_accepted(tmp_path):
    harness = Harness(tmp_path)

    with pytest.raises(SystemExit):
        proof.main(["--app", "--i-understand"], harness.deps())

    assert harness.events == []


def test_a_crash_mid_run_stops_the_backend_records_a_failure_and_prints_no_traceback(
        tmp_path, capsys):
    token = "e" + "yJ" + "abcdefghij.klmnopqrstu.vwxyz012345"
    crashing = Harness(tmp_path, crash=RuntimeError(f"boom 123456789012 {token}"))

    assert run(crashing) == 1

    out = capsys.readouterr().out
    assert crashing.backend.stopped and json.loads(crashing.path.read_text())["ok"] is False
    assert "Traceback" not in out and "123456789012" not in out and token not in out
    assert "RuntimeError" in out


def test_a_failing_identity_read_is_reported_masked_with_the_failure_exit_code(tmp_path, capsys):
    harness = Harness(tmp_path)
    deps = harness.deps()

    def broken(_secret_arn):
        raise ConnectionError("cannot reach 123456789012")

    deps.identity = broken

    assert proof.main(["--apply", settings.CONFIRM_FLAG], deps) == 1

    out = capsys.readouterr().out
    assert "123456789012" not in out and "Traceback" not in out and "ConnectionError" in out


def test_the_receipt_holds_no_secret_no_output_and_stays_private_when_it_is_replaced(tmp_path):
    harness = Harness(tmp_path)
    harness.path.parent.mkdir(parents=True)
    harness.path.write_text("{}")
    harness.path.chmod(0o644)

    run(harness)

    text = harness.path.read_text()
    assert stat.S_IMODE(harness.path.stat().st_mode) == 0o600
    for forbidden in (BACKEND_SECRET, MASTER_SECRET, "123456789012", "tail of the output"):
        assert forbidden not in text
    receipt = json.loads(text)
    scripts = [c["script"] for c in receipt["commands"]]
    assert scripts == ["warm_demo.py", "stop_and_resume_proof.py"]
    assert set(receipt["steps"][0]) == {"name", "ok", "seconds"}


def test_the_masking_hides_account_ids_and_bearer_tokens():
    token = "e" + "yJ" + "abcdefghij.klmnopqrstu.vwxyz012345"

    masked = proof.mask(f"arn:aws:iam::123456789012:role/x {token}")

    assert "123456789012" not in masked and token not in masked
