#!/usr/bin/env python3
"""Prove the backend works as the least-privilege meridian_backend login, before the cutover.

The hosted backend will stop using the master login's secret. This runs the real backend on this
machine with ``AURORA_SECRET_ARN`` set to the meridian_backend secret (for that one process only),
then drives it through the two paths nothing exercised as that login: Phases 1 to 4, which include
the Phase 2 MCP subprocess, and a Phase 5 recovery that stops its Runtime session and resumes it.
The proof scripts themselves keep the master login for their own reads and clean-up. The recovery
runs on its own throwaway thread and removes the rows it creates.

A passing run writes ``.local/release-b2/backend-login-proof.json``; ``publish.py`` refuses the jwt
release without a recent one. Without ``--apply`` it prints the plan. With it, the run calls
Bedrock and the deployed MeridianWorkflow Runtime, places one test hold and removes what it made.

    python scripts/prove_backend_login.py
    python scripts/prove_backend_login.py --apply --i-understand-this-changes-aws

Exit codes: 0 passed, 1 a step failed, the backend never answered or the run crashed, 3 refused.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

MERIDIAN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MERIDIAN_DIR))

from scripts.identity_release import settings  # noqa: E402

BACKEND_LOGIN = "meridian_backend"
PORT = 8014
HEALTH_ATTEMPTS = 60
STEP_SECONDS = 900
STRIPPED = ("MERIDIAN_API_TOKEN", "MERIDIAN_COGNITO_REGION", "MERIDIAN_COGNITO_USER_POOL_ID",
            "MERIDIAN_COGNITO_APP_CLIENT_ID")
ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
TOKEN = re.compile(r"\bey[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.?[A-Za-z0-9_-]*")
EXIT_PASS, EXIT_FAIL, EXIT_REFUSED = 0, 1, 3


def mask(text: str) -> str:
    """Hide 12-digit account ids and bearer tokens."""
    return TOKEN.sub("<token>", ACCOUNT_ID.sub("<acct>", text))


def say(text: str) -> None:
    """Print with account ids and tokens masked."""
    print(mask(text))


@dataclass(frozen=True)
class Step:
    """One proof script and the environment it runs in."""

    name: str
    argv: list[str]
    env: dict[str, str]


@dataclass(frozen=True)
class StepResult:
    """How one step ended; ``tail`` is the end of its output, safe to print."""

    name: str
    ok: bool
    seconds: float
    tail: str


@dataclass
class Dependencies:
    """Everything the run reaches outside itself for, replaceable in tests."""

    env: Mapping[str, str | None]
    caller: Callable[[str], tuple[str, str]]
    identity: Callable[[str], dict[str, Any]]
    start_backend: Callable[[dict[str, str], int], Any]
    wait_healthy: Callable[[str], bool]
    run_step: Callable[[Step], StepResult]
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    git_sha: Callable[[], str] = settings.git_head
    receipt_path: Path = settings.PROOF_PATH


def backend_environment(env: Mapping[str, str | None], secret_arn: str) -> dict[str, str]:
    """The backend process's environment: the login's secret, no shared token, loopback allowed.

    The shared token and the pool settings are blanked rather than removed, because the backend
    loads ``.env`` without overriding variables that are already set, and would put them back.
    """
    child = {k: v for k, v in env.items() if v is not None}
    child.update({name: "" for name in STRIPPED})
    child.update({"AURORA_SECRET_ARN": secret_arn, "ENVIRONMENT": "development",
                  "MERIDIAN_ALLOW_INSECURE_LOCALHOST": "1", "MERIDIAN_AGENTCORE_AUTH": "iam",
                  "AGENTCORE_SKIP_CLI_SYNC": "1"})
    return child


def plan_steps(env: Mapping[str, str | None], port: int) -> list[Step]:
    """The two proof scripts, run with the shell's own login (the master) against the backend."""
    base = {k: v for k, v in env.items() if v is not None}
    base["MERIDIAN_AGENTCORE_AUTH"] = "iam"
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
            are missing or malformed.
    """
    account, region = settings.deployment_target(deps.env)
    pool_id = settings.cognito_settings(deps.env).pool_id
    return Binding(account, region, pool_id, deps.git_sha())


def write_receipt(path: Path, ok: bool, results: list[StepResult], binding: Binding,
                  deps: Dependencies) -> None:
    """Record the run in the schema ``settings.PROOF_FIELDS`` documents, private to the owner."""
    payload = {"ok": ok, "at": deps.now().isoformat(), "account": binding.account,
               "region": binding.region, "user_pool_id": binding.user_pool_id,
               "git_sha": binding.git_sha, "login": BACKEND_LOGIN,
               "checks": {r.name: r.ok for r in results}}
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".tmp")
    try:
        descriptor = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2) + "\n")
        os.replace(scratch, path)
    finally:
        scratch.unlink(missing_ok=True)


def run_steps(steps: list[Step], deps: Dependencies) -> list[StepResult]:
    """Run the steps in order and stop at the first that fails."""
    results = []
    for step in steps:
        say(f"step {step.name} ...")
        result = deps.run_step(step)
        results.append(result)
        say(f"step {step.name}: {'ok' if result.ok else 'FAILED'} in {result.seconds:.0f} s")
        if not result.ok:
            say(result.tail)
            break
    return results


def prove(secret_arn: str, binding: Binding, deps: Dependencies) -> int:
    """Start the backend as the login, run the steps, always stop the backend, record the result.

    A crash is recorded as a failed run before it is raised, so an older passing receipt cannot
    outlive it.
    """
    steps = plan_steps(deps.env, PORT)
    results: list[StepResult] = []
    backend = None
    crash: Exception | None = None
    try:
        backend = deps.start_backend(backend_environment(deps.env, secret_arn), PORT)
        if deps.wait_healthy(f"http://127.0.0.1:{PORT}/health"):
            results = run_steps(steps, deps)
        else:
            say("The backend did not answer /health in time; see its output above.")
    except Exception as exc:  # noqa: BLE001 - recorded in the receipt, then raised to main
        crash = exc
    finally:
        if backend is not None:
            backend.stop()
    ok = crash is None and len(results) == len(steps) and all(r.ok for r in results)
    write_receipt(deps.receipt_path, ok, results, binding, deps)
    if crash is not None:
        raise crash
    say(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return EXIT_PASS if ok else EXIT_FAIL


def refuse(text: str) -> int:
    """Print why nothing was started."""
    say(f"REFUSED: {text}")
    return EXIT_REFUSED


def dry_run(deps: Dependencies) -> int:
    """Print what an applied run would do."""
    say(f"DRY RUN. The backend would run on port {PORT} as {BACKEND_LOGIN} "
        "(AURORA_SECRET_ARN set for that process only), then:")
    for step in plan_steps(deps.env, PORT):
        say(f"  {step.name}: {' '.join(Path(part).name for part in step.argv[1:])}")
    say("Run it (ASK FIRST): python scripts/prove_backend_login.py "
        f"--apply {settings.CONFIRM_FLAG}")
    return EXIT_PASS


def guard_deployment(deps: Dependencies) -> tuple[str, str]:
    """The deployment's account and Region, after checking the credentials are for them.

    Raises:
        settings.ReleaseConfigError: When the cluster ARN is unusable or the caller is elsewhere.
    """
    account, region = settings.deployment_target(deps.env)
    seen_account, seen_region = deps.caller(region)
    if (seen_account, seen_region) != (account, region):
        raise settings.ReleaseConfigError(
            f"the AWS credentials are for account {seen_account} in {seen_region}; this "
            f"deployment is account {account} in {region}. Sign in to the right account")
    return account, region


def run(args: argparse.Namespace, deps: Dependencies) -> int:
    """Refuse, print the plan, or run the proof."""
    secret_arn = (deps.env.get("AURORA_BACKEND_SECRET_ARN") or "").strip()
    if not secret_arn:
        return refuse("AURORA_BACKEND_SECRET_ARN is not set; run "
                      "scripts/provision_service_logins.py --login backend --apply --write-env")
    if not args.apply:
        return dry_run(deps)
    if not args.confirmed:
        return refuse(f"--apply also needs {settings.CONFIRM_FLAG}; it calls AWS and places "
                      "a test hold")
    try:
        binding = bind(deps)
        _account, region = guard_deployment(deps)
    except settings.ReleaseConfigError as exc:
        return refuse(str(exc))
    who = deps.identity(secret_arn)
    if who.get("login") != BACKEND_LOGIN or who.get("bypass_rls"):
        return refuse(f"the secret connects as {who.get('login')!r} (bypass RLS: "
                      f"{who.get('bypass_rls')}), not as {BACKEND_LOGIN} without BYPASSRLS")
    with_region = replace(deps, env={**deps.env, "AWS_DEFAULT_REGION": region})
    return prove(secret_arn, binding, with_region)


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    parser.add_argument("--apply", action="store_true", help="run the proof (live)")
    parser.add_argument(settings.CONFIRM_FLAG, action="store_true", dest="confirmed",
                        help="required with --apply: it calls AWS and places a test hold")
    return parser


def main(argv: list[str] | None = None, deps: Dependencies | None = None) -> int:
    """Print the plan, or with both flags run the proof. Never prints a traceback."""
    args = build_parser().parse_args(argv)
    try:
        return run(args, deps or default_dependencies())
    except KeyboardInterrupt:
        say("Interrupted; the backend was stopped and the receipt records a failed run.")
        return EXIT_FAIL
    except Exception as exc:  # noqa: BLE001 - the one place that turns a crash into a message
        say(f"ERROR: {type(exc).__name__}: {exc}")
        return EXIT_FAIL


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

    def stop(self) -> None:
        """Terminate the backend, killing it if it does not exit."""
        self._process.terminate()
        try:
            self._process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._reader.join(timeout=5)


def _start_backend(env: dict[str, str], port: int) -> _Process:
    command = [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1",
               "--port", str(port)]
    return _Process(subprocess.Popen(command, cwd=MERIDIAN_DIR, env=env, text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT))


def _wait_healthy(url: str) -> bool:
    for _ in range(HEALTH_ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(1)
    return False


def _run_step(step: Step) -> StepResult:
    started = time.monotonic()
    try:
        done = subprocess.run(step.argv, cwd=MERIDIAN_DIR, env=step.env, capture_output=True,
                              text=True, timeout=STEP_SECONDS, check=False)
        ok, tail = done.returncode == 0, (done.stdout + done.stderr)[-600:]
    except subprocess.TimeoutExpired:
        ok, tail = False, f"timed out after {STEP_SECONDS} s"
    return StepResult(step.name, ok, time.monotonic() - started, mask(tail))


def _caller(region: str) -> tuple[str, str]:
    import boto3

    account = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    configured = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
    return account, configured or boto3.Session().region_name or region


def _identity(secret_arn: str, env: Mapping[str, str | None]) -> dict[str, Any]:
    from backend.db.rds_data_client import RDSDataClient

    _account, region = settings.deployment_target(env)
    client = RDSDataClient(cluster_arn=env.get("AURORA_CLUSTER_ARN"), secret_arn=secret_arn,
                           region=region)
    rows = asyncio.run(client.execute(
        "SELECT current_user AS login, (SELECT rolbypassrls FROM pg_roles "
        "WHERE rolname = current_user) AS bypass_rls"))
    return {"login": rows[0]["login"], "bypass_rls": bool(rows[0]["bypass_rls"])}


def default_dependencies() -> Dependencies:
    """The real environment, subprocesses and Data API."""
    env = {**dotenv_values(MERIDIAN_DIR / ".env"), **os.environ}
    return Dependencies(
        env=env, caller=_caller, identity=lambda arn: _identity(arn, env),
        start_backend=_start_backend, wait_healthy=_wait_healthy, run_step=_run_step)


if __name__ == "__main__":
    sys.exit(main())
