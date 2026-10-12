#!/usr/bin/env python3
"""Prove the backend works as the least-privilege meridian_backend login, before the cutover.

The hosted backend will stop using the master login's secret. This runs the real backend on this
machine with ``AURORA_SECRET_ARN`` set to the meridian_backend secret (for that one process only),
refuses to start if port 8014 is already bound, and checks through ``/api/health`` that the
running process is connected as ``meridian_backend``. It then drives the backend through the paths
nothing exercised as that login: the Phase 1 to 4 warm-up, two Phase 2 turns that must show
successful MCP tool activity with rows (the generic postgres-mcp subprocess and the custom
concierge server), the RLS probe and the session receipt, and a Phase 5 recovery that stops its
Runtime session and resumes it. The proof scripts keep the master login for their own reads and
clean-up. The recovery runs on its own ``phase5-proof-`` thread; its rows are counted at zero in
every table after the purge, and a sweep of every ``phase5-proof-`` thread follows the recovery
whether it passed or not. A leftover fails the receipt.

While it runs, the loopback backend on 127.0.0.1:8014 is open as Jordan (trv_meridian_demo)
without a token (MERIDIAN_ALLOW_INSECURE_LOCALHOST); any local process can call it. Run it on a
machine you trust, and only from a checkout with no uncommitted change under meridian/, because
the receipt names HEAD. It does not re-check the tree at release time; neither
``release_identity.py`` nor ``publish.py`` compares the working tree to the receipt's commit.

Residue, deliberately NOT deleted: the warm-up turns write conversation_messages,
trip_interactions, conversations and traveler_preferences rows for Jordan, and the access checks
add append-only audit rows (traveler_access_audit and the agent audit log). Only the recovery
step's journey, snapshots, executions, stops, hold requests, bookings and booking lines are
removed.

A passing run writes ``.local/release-b2/backend-login-proof.json``; ``publish.py`` refuses the jwt
release without a recent one. A failing or interrupted run overwrites it with ``ok: false``, and an
invalid receipt is written before anything starts, so an older passing receipt cannot survive.
The proof follows the identity mode the live Runtimes and Gateway are in: ``--mode iam|jwt``,
default the mode in ``.env`` or the shell (``MERIDIAN_AGENTCORE_AUTH``). In ``iam`` mode the
backend and the two proof scripts sign with the shell's AWS credentials. In ``jwt`` mode the
backend runs with the pool settings and verifies Jordan's Cognito access token, this tool signs
Jordan in through ``scripts/cognito_tokens.py`` (the token lives in memory and goes only into the
``Authorization`` header of its own requests to the loopback backend), and the proof scripts
sign in themselves. The receipt has the same eight fields in both modes.
Without ``--apply`` it prints the plan. With it, the run calls Bedrock and the deployed
MeridianWorkflow Runtime, places one test hold and removes what the recovery made.

    python scripts/prove_backend_login.py
    python scripts/prove_backend_login.py --apply --i-understand-this-changes-aws
    python scripts/prove_backend_login.py --mode jwt --apply --i-understand-this-changes-aws

Exit codes: 0 passed, 1 a check failed, the backend never answered, the run crashed or was
interrupted (SIGINT or SIGTERM), 3 refused (a precondition, a usage error or a missing flag).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

MERIDIAN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MERIDIAN_DIR))

from backend.agentcore.auth_mode import IAM, JWT, MODES  # noqa: E402
from backend.demo_prompts import PROMPT_LADDER  # noqa: E402
from scripts.identity_release import settings  # noqa: E402

BACKEND_LOGIN = "meridian_backend"
MASTER_LOGIN = "meridian_admin"
APP_ROLE = "meridian_app"
TRAVELER = "trv_meridian_demo"
PORT = 8014
HEALTH_ATTEMPTS = 60
STEP_SECONDS = 900
ESCALATION = ((signal.SIGINT, 30), (signal.SIGTERM, 10))
KILL_WAIT = 10
HTTP_SECONDS = 240
RECEIPT_WINDOW_MINUTES = 30
SQL_PROMPT = PROMPT_LADDER[1].works[0]
CONCIERGE_PROMPT = PROMPT_LADDER[2].works[0]
SQL_TOOL = "postgres-mcp: run_query"
CONCIERGE_TOOL = "meridian-concierge:"
CHECKS = ("backend_login", "warm", "phase2_sql_mcp", "phase2_concierge_mcp", "rls_probe",
          "session_receipt", "recovery", "purge")
STRIPPED = ("MERIDIAN_API_TOKEN", "MERIDIAN_COGNITO_REGION", "MERIDIAN_COGNITO_USER_POOL_ID",
            "MERIDIAN_COGNITO_APP_CLIENT_ID")
JWT_STRIPPED = ("MERIDIAN_API_TOKEN",)
SIGNED_IN_USER = "jordan"
AWS_SETTINGS = ("AWS_PROFILE", "AWS_DEFAULT_REGION", "AWS_REGION")
ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
TOKEN = re.compile(r"\bey[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.?[A-Za-z0-9_-]*")
AWS_KEY_ID = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
LONG_SECRET = re.compile(r"[A-Za-z0-9+/=_-]{100,}")
ROWS = re.compile(r"Retrieved (\d+) rows")
EXIT_PASS, EXIT_FAIL, EXIT_REFUSED = 0, 1, 3
RESIDUE = (
    "Residue, NOT deleted: the warm-up turns write conversation_messages, trip_interactions, "
    "conversations and traveler_preferences rows for Jordan, and the access checks add "
    "append-only audit rows (traveler_access_audit and the agent audit log). Only the recovery "
    "step's own rows are removed.")
OPEN_BACKEND = (
    f"While it runs, the backend on 127.0.0.1:{PORT} is open as Jordan ({TRAVELER}) without a "
    "token; any local process can call it.")
SWEEP_SQL = """
SELECT thread_id AS thread FROM journey_threads WHERE thread_id LIKE %s
UNION SELECT session_id FROM workflow_snapshots WHERE session_id LIKE %s
UNION SELECT thread_id FROM journey_executions WHERE thread_id LIKE %s
"""
IDENTITY_SQL = """
SELECT current_user AS login,
       (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS bypass_rls,
       (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS superuser,
       COALESCE((SELECT pg_has_role(current_user, oid, 'MEMBER') FROM pg_roles
                  WHERE rolname = %s), false) AS master_member
"""


def mask(text: str) -> str:
    """Hide 12-digit account ids, bearer tokens, AWS access key ids and long base64 secrets."""
    text = TOKEN.sub("<token>", text)
    text = AWS_KEY_ID.sub("<key-id>", text)
    return LONG_SECRET.sub("<secret>", ACCOUNT_ID.sub("<acct>", text))


def say(text: str) -> None:
    """Print with account ids, tokens and keys masked."""
    print(mask(text))


@dataclass(frozen=True)
class Step:
    """One proof script and the environment it runs in."""

    name: str
    argv: list[str]
    env: dict[str, str]


@dataclass(frozen=True)
class StepResult:
    """How one check ended; ``tail`` is the end of its output or its problem, safe to print."""

    name: str
    ok: bool
    seconds: float
    tail: str


class UsageError(Exception):
    """The command line was not understood."""


@dataclass
class Dependencies:
    """Everything the run reaches outside itself for, replaceable in tests."""

    env: Mapping[str, str | None]
    caller: Callable[[str], tuple[str, str]]
    identity: Callable[[str], dict[str, Any]]
    port_free: Callable[[int], bool]
    start_backend: Callable[[dict[str, str], int], Any]
    wait_healthy: Callable[[str, Callable[[], bool]], bool]
    http: Callable[[str, str, dict | None, Mapping[str, str]], tuple[int, Any]]
    run_step: Callable[[Step], StepResult]
    sweep: Callable[[Mapping[str, str | None]], list[str]]
    mint: Callable[[str], str] = lambda user: ""
    mode: str = IAM
    dirty: Callable[[], list[str]] = settings.working_tree_changes
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    git_sha: Callable[[], str] = settings.git_head
    receipt_path: Path = settings.PROOF_PATH


def backend_environment(env: Mapping[str, str | None], secret_arn: str,
                        mode: str = IAM) -> dict[str, str]:
    """The backend process's environment: the login's secret, no shared token, loopback allowed.

    The shared token (and, in ``iam`` mode, the pool settings) are blanked rather than removed,
    because the backend loads ``.env`` without overriding variables that are already set, and
    would put them back. In ``jwt`` mode the pool settings stay, so the backend verifies the
    bearer token it is sent and forwards it to the Runtimes and the Gateway.
    """
    child = {k: v for k, v in env.items() if v is not None}
    child.update({name: "" for name in (JWT_STRIPPED if mode == JWT else STRIPPED)})
    child.update({"AURORA_SECRET_ARN": secret_arn, "ENVIRONMENT": "development",
                  "MERIDIAN_ALLOW_INSECURE_LOCALHOST": "1", "MERIDIAN_AGENTCORE_AUTH": mode,
                  "AGENTCORE_SKIP_CLI_SYNC": "1"})
    return child


def plan_steps(env: Mapping[str, str | None], port: int, mode: str = IAM) -> list[Step]:
    """The two proof scripts, run with the shell's own login (the master) against the backend.

    In ``jwt`` mode they sign in themselves (``scripts/agentcore_caller.py``); the shared token
    is blanked so nothing falls back to it.
    """
    base = {k: v for k, v in env.items() if v is not None}
    base["MERIDIAN_AGENTCORE_AUTH"] = mode
    if mode == JWT:
        base.update({name: "" for name in JWT_STRIPPED})
    scripts = MERIDIAN_DIR / "scripts"
    return [
        Step("warm", [sys.executable, str(scripts / "warm_demo.py"), "--base-url",
                      f"http://127.0.0.1:{port}"], base),
        Step("recovery", [sys.executable, str(scripts / "stop_and_resume_proof.py"), "--during",
                          "waiting"], {**base, "MERIDIAN_PROOF_API": f"http://127.0.0.1:{port}/api"}),
    ]


@dataclass(frozen=True)
class Binding:
    """What the receipt is bound to: the deployment, its pool and the commit of this checkout."""

    account: str
    region: str
    user_pool_id: str
    git_sha: str


def bind(deps: Dependencies) -> Binding:
    """The deployment and commit a receipt names, read before anything is started.

    Raises:
        settings.ReleaseConfigError: When the cluster ARN, the pool settings or the git HEAD
            are missing or malformed, or when meridian/ differs from HEAD.
    """
    account, region = settings.deployment_target(deps.env)
    pool_id = settings.cognito_settings(deps.env).pool_id
    sha = deps.git_sha()
    changes = deps.dirty()
    if changes:
        shown = "; ".join(changes[:5]) + (" ..." if len(changes) > 5 else "")
        raise settings.ReleaseConfigError(
            f"meridian/ has {len(changes)} uncommitted change(s) ({shown}); the receipt names "
            "the commit, so commit or stash them and run again")
    return Binding(account, region, pool_id, sha)


def write_receipt(path: Path, ok: bool, checks: dict[str, bool], binding: Binding,
                  at: str) -> None:
    """Record the run in the schema ``settings.PROOF_FIELDS`` documents, private to the owner.

    The temporary file is opened without following a symbolic link and made 0600 before it is
    written, then renamed over the receipt.
    """
    payload = {"ok": ok, "at": at, "account": binding.account, "region": binding.region,
               "user_pool_id": binding.user_pool_id, "git_sha": binding.git_sha,
               "login": BACKEND_LOGIN, "checks": checks}
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".tmp")
    try:
        descriptor = os.open(
            scratch, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2) + "\n")
        os.replace(scratch, path)
    finally:
        scratch.unlink(missing_ok=True)


# ------------------------------------------------------------------- the checks


def _problem_result(name: str, started: float, problem: str | None) -> StepResult:
    return StepResult(name, problem is None, time.monotonic() - started, problem or "")


def _timed(name: str, check: Callable[[], str | None]) -> StepResult:
    """Run one in-process check; a problem string or an exception fails it."""
    started = time.monotonic()
    try:
        problem = check()
    except Exception as exc:  # noqa: BLE001 - reported as the check's failure
        problem = f"{type(exc).__name__}: {exc}"
    return _problem_result(name, started, problem)


def check_login(status: int, body: Any) -> str | None:
    """Whether ``/api/health`` shows the backend connected as the least-privilege login."""
    if status != 200 or not isinstance(body, dict):
        return f"/api/health answered HTTP {status}"
    user = body.get("database_user")
    if user != BACKEND_LOGIN:
        return f"/api/health says the backend runs as {user!r}, not {BACKEND_LOGIN}"
    return None


def _failed_telemetry(activities: list[dict]) -> list[str]:
    return [str(a.get("title")) for a in activities
            if a.get("activity_type") == "error"
            or (a.get("telemetry") or {}).get("status") in {"error", "failed"}]


def check_mcp_turn(reply: Any, tool_prefix: str) -> str | None:
    """Whether a Phase 2 reply shows the MCP tool ran and returned rows.

    The turn must carry an ``mcp`` activity titled ``tool_prefix``, no errored activity, a final
    "MCP turn complete" activity that retrieved at least one row, products and a message. A
    fallback reply (the domain tool failed, or none matched) carries an error activity or no
    tool activity and fails.
    """
    if not isinstance(reply, dict):
        return "the reply is not a JSON object"
    activities = [a for a in reply.get("activities") or [] if isinstance(a, dict)]
    failed = _failed_telemetry(activities)
    if failed:
        return "errored activity: " + ", ".join(failed)
    tools = [a for a in activities if a.get("activity_type") == "mcp"
             and str(a.get("title", "")).startswith(tool_prefix)]
    if not tools:
        return f"no MCP activity titled {tool_prefix!r}"
    found = [ROWS.search(str(a.get("details") or "")) for a in activities
             if str(a.get("title", "")).startswith("MCP turn complete")]
    if not any(m and int(m.group(1)) > 0 for m in found):
        return "the MCP turn retrieved no rows"
    if not reply.get("products") or not reply.get("message"):
        return "the reply has no products or no message"
    return None


def check_probe(status: int, body: Any) -> str | None:
    """Whether the RLS probe shows the scope filtering rows and the app role in effect."""
    if status != 200 or not isinstance(body, dict):
        return f"the RLS probe answered HTTP {status}"
    tables = body.get("tables") or []
    if not tables:
        return "the RLS probe returned no tables"
    errors = [t.get("table") for t in tables if t.get("error")]
    if errors:
        return f"the RLS probe could not count {', '.join(map(str, errors))}"
    if any(t["scoped_count"] > t["unscoped_count"] for t in tables):
        return "a scoped count is above the baseline"
    if not any(t["scoped_count"] < t["unscoped_count"] for t in tables):
        return "no table shows the scope hiding another traveler's rows"
    role = (body.get("debug") or {}).get("effective_role")
    return None if role == APP_ROLE else f"the effective role is {role!r}, not {APP_ROLE}"


def check_receipt(status: int, body: Any) -> str | None:
    """Whether the session receipt counted every line and saw the conversation turns."""
    if status != 200 or not isinstance(body, dict):
        return f"the session receipt answered HTTP {status}"
    lines = body.get("lines") or []
    if not lines:
        return "the session receipt has no lines"
    uncounted = [str(line.get("table")) for line in lines if line.get("count") is None]
    if uncounted:
        return f"the session receipt could not count {', '.join(uncounted)}"
    turns = [line["count"] for line in lines if line.get("table") == "conversation_messages"]
    return None if turns and turns[0] > 0 else "the receipt shows no persisted conversation turns"


def _call(deps: Dependencies, method: str, path: str, body: dict | None) -> tuple[int, Any]:
    """One request to the proof backend; in ``jwt`` mode it carries Jordan's fresh token."""
    headers = {"Authorization": "Bearer " + deps.mint(SIGNED_IN_USER)} if deps.mode == JWT else {}
    return deps.http(method, path, body, headers)


def _chat_body(prompt: str) -> dict:
    return {"message": prompt, "phase": 2, "customer_id": TRAVELER}


def _phase_two(deps: Dependencies, prompt: str, tool: str) -> str | None:
    status, reply = _call(deps, "POST", "/api/chat", _chat_body(prompt))
    return f"the chat route answered HTTP {status}" if status != 200 else check_mcp_turn(
        reply, tool)


def check_plan(deps: Dependencies) -> list[tuple[str, Callable[[], StepResult]]]:
    """The ordered checks: each returns a result, and the run stops at the first that fails."""
    warm, recovery = plan_steps(deps.env, PORT, deps.mode)
    probe = "/api/diagnostics/rls-probe"
    receipt = "/api/diagnostics/session-receipt"
    return [
        ("backend_login", lambda: _timed("backend_login", lambda: check_login(
            *_call(deps, "GET", "/api/health", None)))),
        ("warm", lambda: deps.run_step(warm)),
        ("phase2_sql_mcp", lambda: _timed("phase2_sql_mcp", lambda: _phase_two(
            deps, SQL_PROMPT, SQL_TOOL))),
        ("phase2_concierge_mcp", lambda: _timed("phase2_concierge_mcp", lambda: _phase_two(
            deps, CONCIERGE_PROMPT, CONCIERGE_TOOL))),
        ("rls_probe", lambda: _timed("rls_probe", lambda: check_probe(
            *_call(deps, "POST", probe, {})))),
        ("session_receipt", lambda: _timed("session_receipt", lambda: check_receipt(
            *_call(deps, "POST", receipt, {"window_minutes": RECEIPT_WINDOW_MINUTES})))),
        ("recovery", lambda: deps.run_step(recovery)),
    ]


def run_checks(backend: Any, checks: dict[str, bool], deps: Dependencies) -> None:
    """Wait for the backend, then run the checks in order, stopping at the first failure."""
    if not deps.wait_healthy(f"http://127.0.0.1:{PORT}/health", backend.alive):
        if backend.alive():
            say("The backend did not answer /health in time; see its output above.")
        else:
            say("The backend exited while starting; see its output above.")
        return
    for name, check in check_plan(deps):
        say(f"check {name} ...")
        checks[name] = False
        result = check()
        checks[name] = result.ok
        say(f"check {name}: {'ok' if result.ok else 'FAILED'} in {result.seconds:.0f} s")
        if not result.ok:
            say(result.tail)
            return


def sweep_leftovers(deps: Dependencies) -> bool:
    """Purge every ``phase5-proof-`` thread; True only when there was nothing left to purge."""
    try:
        found = deps.sweep(deps.env)
    except Exception as exc:  # noqa: BLE001 - a sweep that cannot run fails the receipt
        say(f"The sweep for leftover proof threads failed: {type(exc).__name__}: {exc}")
        return False
    for line in found:
        say(f"leftover: {line}")
    return not found


def prove(secret_arn: str, binding: Binding, deps: Dependencies) -> int:
    """Start the backend as the login, run the checks, always stop it, record the result.

    An invalid receipt is written first, so an older passing one cannot outlive a run that dies.
    Any ``BaseException`` (a crash, Ctrl-C, SIGTERM) is recorded as a failed run and re-raised.
    When the recovery was attempted the proof threads are swept, pass or fail.
    """
    started = deps.now().isoformat()
    write_receipt(deps.receipt_path, False, {}, binding, started)
    checks: dict[str, bool] = {}
    backend = None
    failure: BaseException | None = None
    try:
        backend = deps.start_backend(
            backend_environment(deps.env, secret_arn, deps.mode), PORT)
        run_checks(backend, checks, deps)
    except BaseException as exc:  # noqa: BLE001 - recorded in the receipt, then re-raised
        failure = exc
    finally:
        if backend is not None:
            backend.stop()
    try:
        if "recovery" in checks:
            checks["purge"] = sweep_leftovers(deps)
    except BaseException as exc:  # noqa: BLE001 - recorded in the receipt, then re-raised
        failure = failure or exc
        checks["purge"] = False
    ok = failure is None and set(checks) == set(CHECKS) and all(checks.values())
    write_receipt(deps.receipt_path, ok, checks, binding, started)
    if failure is not None:
        raise failure
    say(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return EXIT_PASS if ok else EXIT_FAIL


def refuse(text: str) -> int:
    """Print why nothing was started."""
    say(f"REFUSED: {text}")
    return EXIT_REFUSED


def _mode(deps: Dependencies, chosen: str | None) -> str:
    """The identity mode: ``--mode``, else the setting.

    Raises:
        settings.ReleaseConfigError: When the setting is neither ``iam`` nor ``jwt``.
    """
    return chosen or settings.release_mode(deps.env)


def dry_run(deps: Dependencies, chosen: str | None = None) -> int:
    """Print what an applied run would do."""
    deps = replace(deps, mode=_mode(deps, chosen))
    say(f"DRY RUN. The backend would run on port {PORT} as {BACKEND_LOGIN} "
        "(AURORA_SECRET_ARN set for that process only), refusing to start if the port is bound.")
    say(f"Identity mode: {deps.mode} (the mode the live Runtimes and Gateway are in; "
        "--mode overrides).")
    say("Checks, in order, stopping at the first failure: " + ", ".join(CHECKS) + ".")
    for step in plan_steps(deps.env, PORT, deps.mode):
        say(f"  {step.name}: {' '.join(Path(part).name for part in step.argv[1:])}")
    say(OPEN_BACKEND)
    say(RESIDUE)
    say("Run it (ASK FIRST): python scripts/prove_backend_login.py "
        f"--apply {settings.CONFIRM_FLAG}")
    return EXIT_PASS


def guard_deployment(deps: Dependencies) -> tuple[str, str]:
    """The deployment's account and Region, after checking the credentials are for them.

    Raises:
        settings.ReleaseConfigError: When the cluster ARN is unusable, no Region is configured
            or the caller is elsewhere.
    """
    account, region = settings.deployment_target(deps.env)
    seen_account, seen_region = deps.caller(region)
    if not seen_region:
        raise settings.ReleaseConfigError(
            "no AWS Region is configured; set AWS_DEFAULT_REGION or AWS_REGION (or a profile "
            "Region) to the deployment's Region")
    if (seen_account, seen_region) != (account, region):
        raise settings.ReleaseConfigError(
            f"the AWS credentials are for account {seen_account} in {seen_region}; this "
            f"deployment is account {account} in {region}. Sign in to the right account")
    return account, region


def _login_problem(who: Mapping[str, Any]) -> str | None:
    if who.get("login") != BACKEND_LOGIN:
        return f"the secret connects as {who.get('login')!r}, not as {BACKEND_LOGIN}"
    flags = [name for name, key in (("BYPASSRLS", "bypass_rls"), ("superuser", "superuser"),
                                    (f"a member of {MASTER_LOGIN}", "master_member"))
             if who.get(key)]
    return f"{BACKEND_LOGIN} is {' and '.join(flags)}; it must be none of these" if flags else None


def run(args: argparse.Namespace, deps: Dependencies) -> int:
    """Refuse, print the plan, or run the proof."""
    secret_arn = (deps.env.get("AURORA_BACKEND_SECRET_ARN") or "").strip()
    if not secret_arn:
        return refuse("AURORA_BACKEND_SECRET_ARN is not set; run "
                      "scripts/provision_service_logins.py --login backend --apply --write-env")
    if not args.apply:
        try:
            return dry_run(deps, args.mode)
        except settings.ReleaseConfigError as exc:
            return refuse(str(exc))
    if not args.confirmed:
        return refuse(f"--apply also needs {settings.CONFIRM_FLAG}; it calls AWS and places "
                      "a test hold")
    try:
        mode = _mode(deps, args.mode)
        binding = bind(deps)
        _account, region = guard_deployment(deps)
    except settings.ReleaseConfigError as exc:
        return refuse(str(exc))
    problem = _login_problem(deps.identity(secret_arn))
    if problem:
        return refuse(problem)
    if not deps.port_free(PORT):
        return refuse(f"port {PORT} is already bound, so a backend there may not be the one "
                      "this run starts; stop it and run again")
    with_region = replace(deps, env={**deps.env, "AWS_DEFAULT_REGION": region}, mode=mode)
    return prove(secret_arn, binding, with_region)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # noqa: D102 - argparse hook
        raise UsageError(f"{self.format_usage().strip()}\n{message}")


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = _Parser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    parser.add_argument("--apply", action="store_true", help="run the proof (live)")
    parser.add_argument("--mode", choices=MODES, default=None,
                        help="identity mode of the live Runtimes and Gateway; default: the "
                             "MERIDIAN_AGENTCORE_AUTH setting")
    parser.add_argument(settings.CONFIRM_FLAG, action="store_true", dest="confirmed",
                        help="required with --apply: it calls AWS and places a test hold")
    return parser


def _raise_interrupt(_signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None, deps: Dependencies | None = None) -> int:
    """Print the plan, or with both flags run the proof. Never prints a traceback."""
    try:
        args = build_parser().parse_args(argv)
    except UsageError as exc:
        return refuse(str(exc))
    previous = signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        return run(args, deps or default_dependencies())
    except KeyboardInterrupt:
        say("Interrupted; the backend was stopped and the receipt records a failed run.")
        return EXIT_FAIL
    except Exception as exc:  # noqa: BLE001 - the one place that turns a crash into a message
        say(f"ERROR: {type(exc).__name__}: {exc}")
        return EXIT_FAIL
    finally:
        signal.signal(signal.SIGTERM, previous)


# ------------------------------------------------------------- the real effects


class _Process:
    """The backend subprocess; its output is forwarded masked."""

    def __init__(self, process: subprocess.Popen) -> None:
        self._process = process
        self._reader = threading.Thread(target=self._forward, daemon=True)
        self._reader.start()

    def _forward(self) -> None:
        for line in self._process.stdout or []:
            say(f"  backend: {line.rstrip()}")

    def alive(self) -> bool:
        """Whether the backend process is still running."""
        return self._process.poll() is None

    def stop(self) -> None:
        """Terminate the backend, killing it if it does not exit."""
        self._process.terminate()
        try:
            self._process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._reader.join(timeout=5)


def _port_is_free(port: int) -> bool:
    """Whether nothing is bound to the loopback port; a bind without address reuse tells."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _start_backend(env: dict[str, str], port: int) -> _Process:
    command = [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1",
               "--port", str(port)]
    return _Process(subprocess.Popen(command, cwd=MERIDIAN_DIR, env=env, text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT))


def _wait_healthy(url: str, alive: Callable[[], bool]) -> bool:
    """Poll ``url`` until it answers 200; give up at once when the process has exited."""
    for _ in range(HEALTH_ATTEMPTS):
        if not alive():
            return False
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(1)
    return False


def _http(method: str, path: str, body: dict | None,
          headers: Mapping[str, str]) -> tuple[int, Any]:
    """One JSON request to the loopback backend; the status and the parsed body."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_SECONDS) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def _stop_group(process: subprocess.Popen) -> str:
    """End a step's process group: SIGINT and SIGTERM each with a grace period, then SIGKILL.

    The step's own ``finally`` clean-up runs on SIGINT, which a plain kill would skip.
    """
    for signum, grace in ESCALATION:
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            break
        try:
            return process.communicate(timeout=grace)[0] or ""
        except subprocess.TimeoutExpired:
            continue
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return process.communicate(timeout=KILL_WAIT)[0] or ""


def _run_step(step: Step) -> StepResult:
    started = time.monotonic()
    process = subprocess.Popen(
        step.argv, cwd=MERIDIAN_DIR, env=step.env, text=True, start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        output = process.communicate(timeout=STEP_SECONDS)[0] or ""
        ok, tail = process.returncode == 0, output[-600:]
    except subprocess.TimeoutExpired:
        ok, tail = False, f"timed out after {STEP_SECONDS:g} s; " + _stop_group(process)[-500:]
    except BaseException:
        _stop_group(process)
        raise
    return StepResult(step.name, ok, time.monotonic() - started, mask(tail))


def _session_region() -> str | None:
    import boto3

    return boto3.Session().region_name


def _configured_region(environ: Mapping[str, str | None]) -> str:
    """The Region the shell's credentials use: the environment, then the profile; else empty."""
    return environ.get("AWS_DEFAULT_REGION") or environ.get("AWS_REGION") or _session_region() or ""


def _export_aws_settings(env: Mapping[str, str | None], environ: MutableMapping[str, str]) -> None:
    """Make the AWS settings of ``.env`` visible to boto3, which reads only the process
    environment; a variable already set in the shell keeps its value."""
    for name in AWS_SETTINGS:
        if env.get(name) and name not in environ:
            environ[name] = str(env[name])


def _caller(region: str) -> tuple[str, str]:
    import boto3

    account = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    return account, _configured_region(os.environ)


def _identity(secret_arn: str, env: Mapping[str, str | None]) -> dict[str, Any]:
    from backend.db.rds_data_client import RDSDataClient

    _account, region = settings.deployment_target(env)
    client = RDSDataClient(cluster_arn=env.get("AURORA_CLUSTER_ARN"), secret_arn=secret_arn,
                           database=env.get("AURORA_DATABASE") or None, region=region)
    rows = asyncio.run(client.execute(IDENTITY_SQL, (MASTER_LOGIN,)))
    row = rows[0]
    return {"login": row["login"], "bypass_rls": bool(row["bypass_rls"]),
            "superuser": bool(row["superuser"]), "master_member": bool(row["master_member"])}


async def _sweep_threads(env: Mapping[str, str | None]) -> list[str]:
    from backend.db.rds_data_client import RDSDataClient
    from scripts import stop_and_resume_proof as recovery

    _account, region = settings.deployment_target(env)
    client = RDSDataClient(cluster_arn=env.get("AURORA_CLUSTER_ARN"),
                           secret_arn=env.get("AURORA_SECRET_ARN"),
                           database=env.get("AURORA_DATABASE") or None, region=region)
    pattern = recovery.PROOF_THREAD_PREFIX + "%"
    found = sorted({row["thread"] for row in await client.execute(SWEEP_SQL, (pattern,) * 3)})
    problems = []
    for thread in found:
        problems.append(f"{thread}: left behind by the recovery step; purging it")
        try:
            await recovery._purge_run(client, thread)
        except Exception as exc:  # noqa: BLE001 - reported, and the receipt fails either way
            problems.append(f"{thread}: the purge failed: {type(exc).__name__}: {exc}")
    return problems


def _sweep(env: Mapping[str, str | None]) -> list[str]:
    return asyncio.run(_sweep_threads(env))


def _mint(env: Mapping[str, str | None], user_key: str) -> str:
    """Jordan's access token, signed in with the shell's AWS credentials and the pool of ``env``.

    The pool and the Region come from ``env`` (not ``os.environ``), so a value only in ``.env``
    is honoured. The token is returned in memory and never printed.
    """
    import boto3

    from scripts.cognito_tokens import mint_access_token

    pool = settings.cognito_settings(env)
    region = pool.region
    return mint_access_token(
        user_key, pool_id=pool.pool_id, client_id=pool.client_id,
        sm=boto3.client("secretsmanager", region_name=region),
        idp=boto3.client("cognito-idp", region_name=region))


def default_dependencies() -> Dependencies:
    """The real environment, subprocesses and Data API."""
    env = {**dotenv_values(MERIDIAN_DIR / ".env"), **os.environ}
    _export_aws_settings(env, os.environ)
    return Dependencies(
        env=env, caller=_caller, identity=lambda arn: _identity(arn, env),
        port_free=_port_is_free, start_backend=_start_backend, wait_healthy=_wait_healthy,
        http=_http, run_step=_run_step, sweep=_sweep, mint=lambda user: _mint(env, user))


if __name__ == "__main__":
    sys.exit(main())
