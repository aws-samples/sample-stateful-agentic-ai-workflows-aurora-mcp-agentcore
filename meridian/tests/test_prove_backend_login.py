"""The backend-login proof runs the real backend as meridian_backend and records the result."""

from __future__ import annotations

import json
import os
import signal
import socket
import stat
import subprocess
import sys
import urllib.error
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import prove_backend_login as proof
from scripts import warm_demo
from scripts.identity_release import preflight, settings

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
SHA = "0123456789abcdef0123456789abcdef01234567"
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
ALL_CHECKS = {"backend_login", "warm", "phase2_sql_mcp", "phase2_concierge_mcp", "rls_probe",
              "session_receipt", "recovery", "purge"}


def mcp_reply(tool: str, rows: int = 3) -> dict:
    return {
        "message": "Here is what the tools found.", "products": [{"product_id": "p1"}],
        "activities": [
            {"activity_type": "mcp", "title": "MCP server discovered: a"},
            {"activity_type": "mcp", "title": tool},
            {"activity_type": "mcp", "title": "MCP turn complete: 1 server",
             "details": f"Retrieved {rows} rows in 900ms"},
        ]}


def probe_body() -> dict:
    return {"tables": [
        {"table": "traveler_preferences", "scoped_count": 4, "unscoped_count": 9, "error": None},
        {"table": "trip_interactions", "scoped_count": 2, "unscoped_count": 5, "error": None},
    ], "debug": {"effective_role": "meridian_app"}}


def receipt_body() -> dict:
    return {"lines": [
        {"table": "traveler_access_audit", "count": 3},
        {"table": "conversation_messages", "count": 6},
        {"table": "trip_interactions", "count": 0},
    ]}


class Backend:
    def __init__(self):
        self.stopped = False
        self.dead = False

    def stop(self):
        self.stopped = True

    def alive(self):
        return not self.dead


class Harness:
    """Fakes for every outside effect, recording the order they were used in."""

    def __init__(self, tmp_path: Path, *, login="meridian_backend", bypass=False,
                 failing=None, healthy=True, target=("123456789012", "us-east-1"), crash=None,
                 free=True, dirty=None, db_user="meridian_backend", replies=None,
                 leftovers=None, sweep_crash=None, superuser=False, member=False,
                 exits_while_starting=False):
        self.events: list[str] = []
        self.backend = Backend()
        self.started_with: dict = {}
        self.steps_run: list[proof.Step] = []
        self.login, self.bypass, self.failing, self.healthy = login, bypass, failing, healthy
        self.target, self.crash, self.free, self.dirty = target, crash, free, dirty or []
        self.superuser, self.member = superuser, member
        self.leftovers, self.sweep_crash = leftovers or [], sweep_crash
        self.exits_while_starting = exits_while_starting
        self.path = tmp_path / "release-b2" / "backend-login-proof.json"
        self.receipt_at_start = None
        self.clock = 0
        self.http_replies = {
            ("GET", "/api/health"): (200, {"status": "healthy", "database_user": db_user}),
            ("POST", "/api/diagnostics/rls-probe"): (200, probe_body()),
            ("POST", "/api/diagnostics/session-receipt"): (200, receipt_body()),
            **(replies or {}),
        }
        self.chat = [(200, mcp_reply("postgres-mcp: run_query")),
                     (200, mcp_reply("meridian-concierge: compare_packages"))]
        self.chat_bodies: list[dict] = []

    def caller(self, region):
        self.events.append("guard")
        return self.target

    def identity(self, secret_arn):
        self.events.append(f"identity {secret_arn}")
        return {"login": self.login, "bypass_rls": self.bypass,
                "superuser": self.superuser, "master_member": self.member}

    def port_free(self, port):
        self.events.append(f"port {port}")
        return self.free

    def start(self, env, port):
        self.events.append("start")
        self.started_with = dict(env)
        if self.path.exists():
            self.receipt_at_start = json.loads(self.path.read_text())
        return self.backend

    def wait(self, url, alive):
        self.events.append(f"wait {url}")
        if self.exits_while_starting:
            self.backend.dead = True
        return self.healthy and alive()

    def http(self, method, path, body=None):
        self.events.append(f"{method} {path}")
        if path == "/api/chat":
            self.chat_bodies.append(body)
            return self.chat.pop(0)
        return self.http_replies[(method, path)]

    def run_step(self, step):
        self.events.append(f"step {step.name}")
        if self.crash:
            raise self.crash
        self.steps_run.append(step)
        code = 1 if step.name == self.failing else 0
        return proof.StepResult(step.name, code == 0, 1.5, "tail of the output")

    def sweep(self, env):
        self.events.append("sweep")
        if self.sweep_crash:
            raise self.sweep_crash
        return self.leftovers

    def now(self):
        self.clock += 1
        return NOW + timedelta(minutes=self.clock - 1)

    def deps(self) -> proof.Dependencies:
        return proof.Dependencies(
            env=ENV, caller=self.caller, identity=self.identity, port_free=self.port_free,
            start_backend=self.start, wait_healthy=self.wait, http=self.http,
            run_step=self.run_step, sweep=self.sweep, dirty=lambda: self.dirty, now=self.now,
            git_sha=lambda: SHA, receipt_path=self.path)


def run(harness, *flags):
    return proof.main(["--apply", settings.CONFIRM_FLAG, *flags], harness.deps())


def test_a_dry_run_lists_the_plan_and_touches_nothing(tmp_path, capsys):
    harness = Harness(tmp_path)

    assert proof.main([], harness.deps()) == 0

    out = capsys.readouterr().out
    assert "DRY RUN" in out and "warm" in out and "recovery" in out
    assert harness.events == [] and not harness.path.exists()
    assert "--apply" in out and settings.CONFIRM_FLAG in out


def test_the_dry_run_states_the_residue_and_the_open_loopback_backend(tmp_path, capsys):
    proof.main([], Harness(tmp_path).deps())

    out = capsys.readouterr().out
    for table in ("conversation_messages", "trip_interactions", "conversations",
                  "traveler_preferences", "append-only"):
        assert table in out
    assert "NOT deleted" in out
    assert "127.0.0.1:8014" in out and "Jordan" in out


def test_apply_without_the_confirmation_is_refused(tmp_path, capsys):
    harness = Harness(tmp_path)

    assert proof.main(["--apply"], harness.deps()) == 3

    assert settings.CONFIRM_FLAG in capsys.readouterr().out and harness.events == []


PASSING_EVENTS = [
    "guard", f"identity {BACKEND_SECRET}", "port 8014", "start",
    "wait http://127.0.0.1:8014/health", "GET /api/health", "step warm", "POST /api/chat",
    "POST /api/chat", "POST /api/diagnostics/rls-probe", "POST /api/diagnostics/session-receipt",
    "step recovery", "sweep"]


def test_a_passing_run_checks_the_login_first_starts_the_backend_and_records_the_proof(tmp_path):
    harness = Harness(tmp_path)

    assert run(harness) == 0

    assert harness.events == PASSING_EVENTS
    assert harness.backend.stopped
    receipt = json.loads(harness.path.read_text())
    assert receipt == {
        "ok": True, "at": NOW.isoformat(), "account": "123456789012", "region": "us-east-1",
        "user_pool_id": "us-east-1_AbCdEfGhI", "git_sha": SHA, "login": "meridian_backend",
        "checks": dict.fromkeys(ALL_CHECKS, True)}
    assert set(receipt) == set(settings.PROOF_FIELDS)
    assert stat.S_IMODE(harness.path.stat().st_mode) == 0o600


def test_the_receipt_records_when_the_run_started_not_when_it_ended(tmp_path):
    harness = Harness(tmp_path)

    run(harness)

    assert json.loads(harness.path.read_text())["at"] == NOW.isoformat()


def target():
    return preflight.target_for("jwt", ENV, "123456789012", "us-east-1")


def test_the_recorded_proof_satisfies_the_publish_preflight(tmp_path):
    harness = Harness(tmp_path)
    run(harness)

    assert preflight.check_backend_login_proof(harness.path, target(), SHA, NOW) == []


@pytest.mark.parametrize(("field", "value"), [
    ("ok", False), ("ok", "true"), ("at", "2026-10-08T12:00:00"),
    ("at", "2026-09-01T00:00:00+00:00"), ("account", "999999999999"), ("region", "eu-west-1"),
    ("user_pool_id", "us-east-1_Other"),
    ("git_sha", "f" * 40), ("login", "meridian_admin"), ("checks", {}),
    ("checks", {"warm": False}),
])
def test_each_tampered_field_of_the_receipt_is_refused(tmp_path, field, value):
    harness = Harness(tmp_path)
    run(harness)
    receipt = json.loads(harness.path.read_text())
    receipt[field] = value
    harness.path.write_text(json.dumps(receipt))

    found = preflight.check_backend_login_proof(harness.path, target(), SHA, NOW)

    assert found and all(line.startswith("Backend login proof:") for line in found)


@pytest.mark.parametrize("harness_args", [
    {"failing": "recovery"}, {"healthy": False}, {"crash": RuntimeError("boom")},
    {"crash": KeyboardInterrupt()}, {"leftovers": ["phase5-proof-ab: left behind"]},
    {"db_user": "meridian_admin"}])
def test_a_failed_crashed_or_interrupted_run_writes_a_receipt_the_preflight_refuses(
        tmp_path, harness_args):
    harness = Harness(tmp_path, **harness_args)
    run(harness)

    found = preflight.check_backend_login_proof(harness.path, target(), SHA, NOW)

    assert len(found) == 1 and "the last run did not pass" in found[0]


def test_a_missing_pool_is_refused_before_anything_starts(tmp_path, capsys):
    harness = Harness(tmp_path)
    deps = harness.deps()
    deps.env = {k: v for k, v in ENV.items() if k != "MERIDIAN_COGNITO_USER_POOL_ID"}

    assert proof.main(["--apply", settings.CONFIRM_FLAG], deps) == 3

    assert harness.events == [] and not harness.path.exists()
    assert "MERIDIAN_COGNITO_USER_POOL_ID" in capsys.readouterr().out


def test_a_dirty_working_tree_is_refused_with_no_events_and_no_receipt(tmp_path, capsys):
    harness = Harness(tmp_path, dirty=[" M backend/main.py", "?? scripts/new.py"])

    assert run(harness) == 3

    out = capsys.readouterr().out
    assert harness.events == [] and not harness.path.exists()
    assert "uncommitted" in out and "backend/main.py" in out


def test_an_unreadable_head_is_refused_before_anything_starts(tmp_path, capsys):
    harness = Harness(tmp_path)
    deps = harness.deps()

    def unreadable():
        raise settings.ReleaseConfigError("cannot read the git HEAD of /x")

    deps.git_sha = unreadable

    assert proof.main(["--apply", settings.CONFIRM_FLAG], deps) == 3

    assert harness.events == [] and "git HEAD" in capsys.readouterr().out


def test_the_hint_command_parses_with_the_proof_scripts_own_parser():
    words = preflight.PROOF_COMMAND.split()
    assert words[:2] == ["python", "scripts/prove_backend_login.py"]
    assert settings.CONFIRM_FLAG in words

    args = proof.build_parser().parse_args(words[2:])

    assert args.apply and args.confirmed


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


def test_a_failing_warm_step_stops_the_run_records_a_failure_and_stops_the_backend(tmp_path):
    harness = Harness(tmp_path, failing="warm")

    assert run(harness) == 1

    assert harness.events[-1] == "step warm" and harness.backend.stopped
    receipt = json.loads(harness.path.read_text())
    assert receipt["ok"] is False and receipt["checks"] == {"backend_login": True, "warm": False}


def test_a_failed_recovery_still_sweeps_for_leftover_proof_threads(tmp_path):
    harness = Harness(tmp_path, failing="recovery")

    assert run(harness) == 1

    assert harness.events[-2:] == ["step recovery", "sweep"]
    checks = json.loads(harness.path.read_text())["checks"]
    assert checks["recovery"] is False and checks["purge"] is True


def test_leftover_proof_threads_found_by_the_sweep_fail_the_receipt(tmp_path, capsys):
    harness = Harness(tmp_path, leftovers=["phase5-proof-ab12: left behind by the recovery"])

    assert run(harness) == 1

    out = capsys.readouterr().out
    assert json.loads(harness.path.read_text())["checks"]["purge"] is False
    assert "phase5-proof-ab12" in out


def test_a_sweep_that_crashes_fails_the_receipt_and_is_reported(tmp_path, capsys):
    harness = Harness(tmp_path, sweep_crash=ConnectionError("data api down"))

    assert run(harness) == 1

    assert json.loads(harness.path.read_text())["checks"]["purge"] is False
    assert "ConnectionError" in capsys.readouterr().out


def test_a_failure_cannot_leave_an_old_passing_proof_in_place(tmp_path):
    harness = Harness(tmp_path)
    run(harness)
    failing = Harness(tmp_path, failing="recovery")

    assert run(failing) == 1

    assert json.loads(harness.path.read_text())["ok"] is False


def test_the_old_receipt_is_already_invalid_when_the_backend_starts(tmp_path):
    first = Harness(tmp_path)
    run(first)
    assert json.loads(first.path.read_text())["ok"] is True
    second = Harness(tmp_path)

    run(second)

    assert second.receipt_at_start["ok"] is False and second.receipt_at_start["checks"] == {}


@pytest.mark.parametrize("who", [
    {"login": "meridian_admin"}, {"bypass": True}, {"superuser": True}, {"member": True}])
def test_a_secret_that_is_not_the_least_privilege_login_is_refused_before_anything_starts(
        tmp_path, capsys, who):
    harness = Harness(tmp_path, **who)

    assert run(harness) == 3

    assert harness.events == ["guard", f"identity {BACKEND_SECRET}"]
    assert not harness.path.exists()
    assert "meridian_backend" in capsys.readouterr().out


def test_a_port_that_is_already_bound_is_refused_before_a_backend_is_started(tmp_path, capsys):
    harness = Harness(tmp_path, free=False)

    assert run(harness) == 3

    assert harness.events == ["guard", f"identity {BACKEND_SECRET}", "port 8014"]
    assert not harness.path.exists() and "8014" in capsys.readouterr().out


def test_the_real_port_check_sees_a_listener_and_a_free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        bound = listener.getsockname()[1]

        assert proof._port_is_free(bound) is False

    assert proof._port_is_free(bound) is True


def test_a_backend_that_never_answers_fails_the_run_and_is_stopped(tmp_path, capsys):
    harness = Harness(tmp_path, healthy=False)

    assert run(harness) == 1

    assert harness.backend.stopped and "step warm" not in harness.events
    assert "did not answer" in capsys.readouterr().out
    assert json.loads(harness.path.read_text())["ok"] is False


def test_a_backend_that_exits_while_starting_fails_the_run_and_says_so(tmp_path, capsys):
    harness = Harness(tmp_path, exits_while_starting=True)

    assert run(harness) == 1

    assert "exited" in capsys.readouterr().out and "step warm" not in harness.events
    assert json.loads(harness.path.read_text())["ok"] is False


def test_the_real_health_wait_gives_up_as_soon_as_the_process_has_exited(monkeypatch):
    sleeps = []

    def refuse(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(proof.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(proof.time, "sleep", sleeps.append)
    alive = iter([True, True, False])

    assert proof._wait_healthy("http://127.0.0.1:8014/health", lambda: next(alive)) is False
    assert len(sleeps) == 2


def test_the_real_health_wait_returns_true_on_a_200(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(proof.urllib.request, "urlopen", lambda *_a, **_k: Response())

    assert proof._wait_healthy("http://127.0.0.1:8014/health", lambda: True) is True


@pytest.mark.parametrize("db_user", ["meridian_admin", None])
def test_a_backend_that_is_not_connected_as_the_login_fails_before_any_step(
        tmp_path, capsys, db_user):
    harness = Harness(tmp_path, db_user=db_user)

    assert run(harness) == 1

    assert "step warm" not in harness.events and harness.backend.stopped
    checks = json.loads(harness.path.read_text())["checks"]
    assert checks == {"backend_login": False}
    assert "meridian_backend" in capsys.readouterr().out


def test_a_health_route_that_errors_fails_the_login_check(tmp_path):
    harness = Harness(tmp_path, replies={("GET", "/api/health"): (503, {})})

    assert run(harness) == 1

    assert json.loads(harness.path.read_text())["checks"] == {"backend_login": False}


def test_the_phase_two_turns_ask_the_two_mcp_servers_as_the_demo_traveler(tmp_path):
    harness = Harness(tmp_path)
    run(harness)

    sql, concierge = harness.chat_bodies
    assert sql["phase"] == 2 and concierge["phase"] == 2
    assert sql["message"] == warm_demo.PROMPT_LADDER[1].works[0]
    assert concierge["message"] == warm_demo.PROMPT_LADDER[2].works[0]
    assert sql["customer_id"] == concierge["customer_id"] == warm_demo.TRAVELER_ID


def activity(kind="mcp", title="x", **extra) -> dict:
    return {"activity_type": kind, "title": title, **extra}


SQL_TOOL = "postgres-mcp: run_query"
CONCIERGE_TOOL = "meridian-concierge:"


def test_a_good_mcp_turn_passes_the_check():
    assert proof.check_mcp_turn(mcp_reply(SQL_TOOL), SQL_TOOL) is None
    concierge = mcp_reply("meridian-concierge: price_range")
    assert proof.check_mcp_turn(concierge, CONCIERGE_TOOL) is None


def broken(**changes) -> dict:
    return {**mcp_reply(SQL_TOOL), **changes}


FALLBACK = broken(
    message="The meridian-concierge domain tool failed to run. Reference abc123.",
    activities=[activity("mcp", "MCP server discovered: meridian-concierge (custom)"),
                activity("error", "meridian-concierge MCP error"),
                activity("mcp", "MCP turn complete: 1 server", details="Retrieved 3 rows in 1ms")])
NO_TOOL = broken(activities=[activity("mcp", "MCP server discovered: a"),
                             activity("mcp", "MCP turn complete: 1 server",
                                      details="Retrieved 3 rows in 1ms")])
ERRORED_TELEMETRY = broken(activities=[
    activity("mcp", SQL_TOOL, telemetry={"status": "error"}),
    activity("mcp", "MCP turn complete: 1 server", details="Retrieved 3 rows in 1ms")])
NO_SUMMARY = broken(activities=[activity("mcp", SQL_TOOL)])
WRONG_TOOL = mcp_reply("meridian-concierge: compare_packages")


@pytest.mark.parametrize("reply", [
    FALLBACK, NO_TOOL, ERRORED_TELEMETRY, NO_SUMMARY, mcp_reply(SQL_TOOL, rows=0),
    broken(products=[]), broken(products=None), broken(message=""), broken(activities=[]),
    WRONG_TOOL, "not a reply"])
def test_a_fallback_a_missing_tool_or_zero_rows_fails_the_mcp_check(reply):
    assert proof.check_mcp_turn(reply, SQL_TOOL)


@pytest.mark.parametrize(("name", "reply"), [
    ("phase2_sql_mcp", FALLBACK), ("phase2_concierge_mcp", mcp_reply(SQL_TOOL))])
def test_a_bad_phase_two_reply_fails_the_run_before_the_recovery(tmp_path, name, reply):
    harness = Harness(tmp_path)
    if name == "phase2_sql_mcp":
        harness.chat[0] = (200, reply)
    else:
        harness.chat[1] = (200, reply)

    assert run(harness) == 1

    assert "step recovery" not in harness.events
    checks = json.loads(harness.path.read_text())["checks"]
    assert checks[name] is False and "recovery" not in checks


def test_a_chat_route_that_answers_500_fails_the_check(tmp_path):
    harness = Harness(tmp_path)
    harness.chat[0] = (500, {"detail": "boom"})

    assert run(harness) == 1

    assert json.loads(harness.path.read_text())["checks"]["phase2_sql_mcp"] is False


def probe_with(**changes) -> dict:
    return {**probe_body(), **changes}


def table_row(scoped=1, unscoped=3, error=None) -> dict:
    return {"table": "traveler_preferences", "scoped_count": scoped, "unscoped_count": unscoped,
            "error": error}


@pytest.mark.parametrize("body", [
    probe_with(tables=[]),
    probe_with(tables=[table_row(error="Probe query failed. Reference ab.")]),
    probe_with(tables=[table_row(scoped=5, unscoped=3)]),
    probe_with(tables=[table_row(scoped=3, unscoped=3)]),
    probe_with(debug={"effective_role": "meridian_admin"}),
    probe_with(debug=None),
    {},
])
def test_an_rls_probe_that_shows_no_filtering_or_the_wrong_role_fails(tmp_path, body):
    harness = Harness(tmp_path, replies={("POST", "/api/diagnostics/rls-probe"): (200, body)})

    assert run(harness) == 1

    checks = json.loads(harness.path.read_text())["checks"]
    assert checks["rls_probe"] is False and "session_receipt" not in checks


def test_an_rls_probe_the_backend_refuses_fails(tmp_path):
    harness = Harness(tmp_path, replies={("POST", "/api/diagnostics/rls-probe"): (403, {})})

    assert run(harness) == 1


@pytest.mark.parametrize("body", [
    {"lines": []},
    {"lines": [{"table": "conversation_messages", "count": None}]},
    {"lines": [{"table": "conversation_messages", "count": 0}]},
    {"lines": [{"table": "traveler_access_audit", "count": 2}]},
    {},
])
def test_a_session_receipt_that_cannot_count_or_shows_no_turns_fails(tmp_path, body):
    harness = Harness(
        tmp_path, replies={("POST", "/api/diagnostics/session-receipt"): (200, body)})

    assert run(harness) == 1

    checks = json.loads(harness.path.read_text())["checks"]
    assert checks["session_receipt"] is False and "recovery" not in checks


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


def test_a_caller_with_no_configured_region_is_refused(tmp_path, capsys):
    harness = Harness(tmp_path, target=("123456789012", ""))

    assert run(harness) == 3

    assert "Region" in capsys.readouterr().out and harness.events == ["guard"]


def test_the_configured_region_comes_from_the_environment_then_the_session(monkeypatch):
    assert proof._configured_region({"AWS_DEFAULT_REGION": "eu-west-1"}) == "eu-west-1"
    assert proof._configured_region({"AWS_REGION": "ap-south-1"}) == "ap-south-1"
    monkeypatch.setattr(proof, "_session_region", lambda: "us-west-2")
    assert proof._configured_region({}) == "us-west-2"
    monkeypatch.setattr(proof, "_session_region", lambda: None)
    assert proof._configured_region({}) == ""


def test_the_aws_settings_of_the_env_file_reach_this_process_without_overriding_the_shell():
    environ = {"AWS_REGION": "from-shell"}

    proof._export_aws_settings(
        {"AWS_PROFILE": "demo", "AWS_REGION": "from-file", "AWS_DEFAULT_REGION": "us-east-1",
         "OTHER": "x"}, environ)

    assert environ == {"AWS_PROFILE": "demo", "AWS_REGION": "from-shell",
                       "AWS_DEFAULT_REGION": "us-east-1"}


def test_a_cluster_arn_that_is_not_a_cluster_is_refused_before_the_guard(tmp_path, capsys):
    harness = Harness(tmp_path)
    deps = harness.deps()
    deps.env = {**ENV, "AURORA_CLUSTER_ARN": "not-an-arn"}

    assert proof.main(["--apply", settings.CONFIRM_FLAG], deps) == 3

    assert harness.events == [] and "AURORA_CLUSTER_ARN" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["--app", "--i-understand"], ["--bogus"], ["extra"]])
def test_a_usage_error_exits_3_without_starting_anything(tmp_path, capsys, argv):
    harness = Harness(tmp_path)

    assert proof.main(argv, harness.deps()) == 3

    assert harness.events == [] and "usage" in capsys.readouterr().out.lower()


def test_a_crash_mid_run_stops_the_backend_records_a_failure_and_prints_no_traceback(
        tmp_path, capsys):
    token = "e" + "yJ" + "abcdefghij.klmnopqrstu.vwxyz012345"
    crashing = Harness(tmp_path, crash=RuntimeError(f"boom 123456789012 {token}"))

    assert run(crashing) == 1

    out = capsys.readouterr().out
    assert crashing.backend.stopped and json.loads(crashing.path.read_text())["ok"] is False
    assert "Traceback" not in out and "123456789012" not in out and token not in out
    assert "RuntimeError" in out


def test_an_interrupt_mid_run_stops_the_backend_sweeps_and_records_a_failed_receipt(tmp_path):
    harness = Harness(tmp_path, crash=KeyboardInterrupt())

    assert run(harness) == 1

    assert harness.backend.stopped
    receipt = json.loads(harness.path.read_text())
    assert receipt["ok"] is False and receipt["checks"]["warm"] is False


def test_an_interrupt_during_the_recovery_sweeps_the_proof_threads(tmp_path):
    harness = Harness(tmp_path)
    deps = harness.deps()
    original = deps.run_step

    def interrupted(step):
        if step.name == "recovery":
            harness.events.append("step recovery")
            raise KeyboardInterrupt
        return original(step)

    deps.run_step = interrupted

    assert proof.main(["--apply", settings.CONFIRM_FLAG], deps) == 1

    assert harness.events[-2:] == ["step recovery", "sweep"] and harness.backend.stopped
    assert json.loads(harness.path.read_text())["ok"] is False


def test_a_sigterm_is_handled_like_an_interrupt_and_the_old_handler_is_restored(tmp_path):
    harness = Harness(tmp_path)
    deps = harness.deps()
    previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)

    def terminated(_step):
        os.kill(os.getpid(), signal.SIGTERM)
        return proof.StepResult("warm", True, 0.0, "")

    deps.run_step = terminated

    try:
        assert proof.main(["--apply", settings.CONFIRM_FLAG], deps) == 1
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert harness.backend.stopped and json.loads(harness.path.read_text())["ok"] is False


def test_a_failing_identity_read_is_reported_masked_with_the_failure_exit_code(tmp_path, capsys):
    harness = Harness(tmp_path)
    deps = harness.deps()

    def broken_read(_secret_arn):
        raise ConnectionError("cannot reach 123456789012")

    deps.identity = broken_read

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
    for forbidden in (BACKEND_SECRET, MASTER_SECRET, "secretsmanager", "tail of the output"):
        assert forbidden not in text


def receipt_args(tmp_path):
    binding = proof.Binding("123456789012", "us-east-1", "us-east-1_AbCdEfGhI", SHA)
    return Harness(tmp_path).path, binding


def test_the_temporary_receipt_is_made_private_even_when_a_wide_one_was_left_behind(tmp_path):
    path, binding = receipt_args(tmp_path)
    path.parent.mkdir(parents=True)
    leftover = path.with_name(path.name + ".tmp")
    leftover.write_text("stale")
    leftover.chmod(0o666)

    proof.write_receipt(path, True, {"warm": True}, binding, NOW.isoformat())

    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and not leftover.exists()


def test_the_temporary_receipt_is_never_written_through_a_symlink(tmp_path):
    path, binding = receipt_args(tmp_path)
    path.parent.mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    path.with_name(path.name + ".tmp").symlink_to(victim)

    with pytest.raises(OSError):
        proof.write_receipt(path, True, {"warm": True}, binding, NOW.isoformat())

    assert victim.read_text() == "keep me"


AWS_KEY_ID = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
TEMP_KEY_ID = "AS" + "IA" + "ABCDEFGHIJKLMNOP"
SESSION_TOKEN = "IQoJb3JpZ2luX2Vj" + "A" * 200 + "/+=="


def test_the_masking_hides_account_ids_bearer_tokens_aws_key_ids_and_session_tokens():
    token = "e" + "yJ" + "abcdefghij.klmnopqrstu.vwxyz012345"

    masked = proof.mask(
        f"arn:aws:iam::123456789012:role/x {token} {AWS_KEY_ID} {TEMP_KEY_ID} {SESSION_TOKEN}")

    for secret in ("123456789012", token, AWS_KEY_ID, TEMP_KEY_ID, SESSION_TOKEN[:60]):
        assert secret not in masked


def test_forwarded_backend_output_is_masked(capsys):
    class Popen:
        stdout = [f"loaded key {AWS_KEY_ID} for 123456789012\n"]

    process = proof._Process.__new__(proof._Process)
    process._process = Popen()

    process._forward()

    out = capsys.readouterr().out
    assert AWS_KEY_ID not in out and "123456789012" not in out and "backend:" in out


def child_script(tmp_path, body: str) -> list[str]:
    script = tmp_path / "child.py"
    script.write_text(body)
    return [sys.executable, str(script), str(tmp_path / "marker")]


CLEANS_UP_ON_SIGINT = """
import pathlib, signal, sys, time
def stop(*_):
    pathlib.Path(sys.argv[1]).write_text("cleaned up")
    sys.exit(0)
signal.signal(signal.SIGINT, stop)
print("ready", flush=True)
time.sleep(60)
"""
IGNORES_EVERYTHING = """
import signal, time
signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
print("ready", flush=True)
time.sleep(60)
"""


@pytest.fixture
def quick_timeouts(monkeypatch):
    monkeypatch.setattr(proof, "STEP_SECONDS", 1.5)
    monkeypatch.setattr(proof, "ESCALATION", ((signal.SIGINT, 1.0), (signal.SIGTERM, 1.0)))
    monkeypatch.setattr(proof, "KILL_WAIT", 5)


def test_a_timed_out_step_gets_sigint_first_so_its_own_clean_up_runs(tmp_path, quick_timeouts):
    argv = child_script(tmp_path, CLEANS_UP_ON_SIGINT)

    result = proof._run_step(proof.Step("recovery", argv, dict(os.environ)))

    assert result.ok is False and "timed out" in result.tail
    assert (tmp_path / "marker").read_text() == "cleaned up"


def test_a_timed_out_step_that_ignores_signals_is_killed_after_the_grace_period(
        tmp_path, quick_timeouts):
    argv = child_script(tmp_path, IGNORES_EVERYTHING)

    result = proof._run_step(proof.Step("recovery", argv, dict(os.environ)))

    assert result.ok is False and "timed out" in result.tail and result.seconds < 15


def test_a_timeout_in_the_recovery_runs_the_sweep_and_fails_the_receipt(
        tmp_path, quick_timeouts):
    harness = Harness(tmp_path)
    deps = harness.deps()
    argv = child_script(tmp_path, CLEANS_UP_ON_SIGINT)
    fake = deps.run_step

    def real_for_recovery(step):
        if step.name != "recovery":
            return fake(step)
        harness.events.append("step recovery")
        return proof._run_step(replace(step, argv=argv))

    deps.run_step = real_for_recovery

    assert proof.main(["--apply", settings.CONFIRM_FLAG], deps) == 1

    assert harness.events[-2:] == ["step recovery", "sweep"]
    receipt = json.loads(harness.path.read_text())
    assert receipt["ok"] is False and receipt["checks"]["recovery"] is False


def test_an_interrupt_while_a_step_runs_stops_the_step_gracefully(tmp_path, quick_timeouts,
                                                                  monkeypatch):
    argv = child_script(tmp_path, CLEANS_UP_ON_SIGINT)
    real_popen = subprocess.Popen

    class Interrupting(real_popen):
        def communicate(self, *args, **kwargs):
            if not getattr(self, "interrupted", False):
                self.interrupted = True
                self.stdout.readline()
                raise KeyboardInterrupt
            return super().communicate(*args, **kwargs)

    monkeypatch.setattr(proof.subprocess, "Popen", Interrupting)

    with pytest.raises(KeyboardInterrupt):
        proof._run_step(proof.Step("recovery", argv, dict(os.environ)))

    assert (tmp_path / "marker").read_text() == "cleaned up"


def test_a_step_that_finishes_reports_its_exit_code_and_a_masked_tail(tmp_path):
    argv = child_script(tmp_path, "import sys\nprint('done 123456789012')\nsys.exit(3)\n")

    result = proof._run_step(proof.Step("warm", argv, dict(os.environ)))

    assert result.ok is False and "123456789012" not in result.tail and "done" in result.tail


class IdentityClient:
    created: list[dict] = []
    sql = ""

    def __init__(self, **kwargs):
        self.created.append(kwargs)

    async def execute(self, sql, params=None):
        IdentityClient.sql = sql
        return [{"login": "meridian_backend", "bypass_rls": False, "superuser": False,
                 "master_member": False}]


def test_the_identity_read_uses_the_configured_database_and_asks_about_the_master(monkeypatch):
    import backend.db.rds_data_client as data_client

    monkeypatch.setattr(data_client, "RDSDataClient", IdentityClient)
    IdentityClient.created = []

    who = proof._identity(BACKEND_SECRET, {**ENV, "AURORA_DATABASE": "meridian_prod"})

    assert IdentityClient.created[0]["database"] == "meridian_prod"
    assert IdentityClient.created[0]["secret_arn"] == BACKEND_SECRET
    assert "rolsuper" in IdentityClient.sql and "pg_has_role" in IdentityClient.sql
    assert who == {"login": "meridian_backend", "bypass_rls": False, "superuser": False,
                   "master_member": False}


def test_the_dry_run_never_reads_the_working_tree_or_the_network(tmp_path):
    harness = Harness(tmp_path, dirty=[" M x"], free=False)

    assert proof.main([], harness.deps()) == 0

    assert harness.events == []
