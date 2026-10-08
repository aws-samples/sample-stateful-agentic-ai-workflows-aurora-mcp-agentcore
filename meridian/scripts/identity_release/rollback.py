"""The one-command rollback: put the saved configuration back, hop by hop, reading each back.

``rollback`` loads the snapshot named by ``--snapshot``, or else the newest complete one of the
release that is not live (a snapshot of the live mode is never chosen on its own: after a ``jwt``
release the ``iam`` snapshot is the way back, even when a newer ``jwt`` one was taken for the
deploy gate). It refuses one that is missing, changed, partial or for another account or Region,
and prints what it will restore; every rollback command it prints passes ``--snapshot FILE``.
Nothing is changed without ``--apply`` and the confirmation flag. The hops are restored in the
reverse of the release order (site, service, roles stack, Gateway, Runtimes, Cedar rules,
Lambdas); after each write the hop is read back until it matches the snapshot, a bounded number
of times. A failed hop is reported and the steps that do not depend on it still run; a step that
needs a failed one (the Runtimes need the Gateway, the Lambdas need the secret parameter) is
skipped and says so.
The exit code is then 1. The roles stack, the Cedar rules and the distribution's behaviors cannot
be restored through their APIs without deleting or bypassing CloudFormation, so they are checked
and the commands to run from a checkout of the snapshot's commit are printed (exit 1 until they
are run). A holds restart that is still owed (the secret parameter was restored) is kept in the
result file, so a later run does it. Running it twice is safe: a hop that already matches is only
read. Nothing is deleted.

A release replaces the Gateway, so a rollback usually finds the saved Gateway gone or rebuilt
under a new id. The Gateway is looked up by its saved name; when its id is not the saved one the
Gateway, both Runtimes and the service are compared (leaving out what a re-creation changes) but
never written, because their saved environments name a Gateway that no longer exists. The staged
deploys that rebuild them are printed under the AgentCore stack step, and the exit code stays 1
until a later run finds every hop as saved.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from backend.agentcore.auth_mode import IAM, JWT
from scripts.gateway_harness.private_files import write_private
from scripts.gateway_harness.verdicts import JWT_SHAPE
from scripts.identity_release import preflight, restore_hops, settings, snapshot
from scripts.provision_service_logins import require_account

Context = restore_hops.Context
WAIT_ATTEMPTS = restore_hops.WAIT_ATTEMPTS
RESULT_NAME = "rollback-result.json"
ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
UNCHANGED, RESTORED, MANUAL, FAILED, DIFFERS, SKIPPED = (
    "unchanged", "restored", "manual", "failed", "differs", "skipped")
MALFORMED = (KeyError, TypeError, AttributeError)


@dataclass
class Result:
    """What happened to one hop."""

    name: str
    status: str
    lines: list[str] = field(default_factory=list)


@dataclass
class Outcome:
    """Every hop's result and the exit code."""

    results: list[Result]
    code: int


def scrub(text: str) -> str:
    """Hide 12-digit account ids and token-shaped strings."""
    return ACCOUNT_ID.sub("<acct>", JWT_SHAPE.sub("<token>", text))


def require_same_deployment(saved: Mapping[str, Any], account: str, region: str) -> None:
    """Refuse a snapshot taken in another account or Region.

    Raises:
        ReleaseConfigError: Without printing either account id.
    """
    if saved.get("account") != account:
        raise settings.ReleaseConfigError(
            "the snapshot was taken in another account than the cluster's; roll back with "
            "the snapshot of this deployment")
    if saved.get("region") != region:
        raise settings.ReleaseConfigError(
            "the snapshot was taken in another Region than the cluster's; roll back with the "
            "snapshot of this deployment")


def _failure_text(error: Exception) -> str:
    if isinstance(error, ClientError):
        message = error.response.get("Error", {}).get("Message")
        return f"AWS {restore_hops.code_of(error)}: {message}"
    known = (restore_hops.RestoreRefused, settings.ReleaseConfigError)
    return str(error) if isinstance(error, known) else type(error).__name__


def _blocked(lines: list[str]) -> bool:
    return all(line.startswith(restore_hops.NOT_RESTORABLE) for line in lines)


def _converge(ctx: Context, step: restore_hops.Step) -> list[str]:
    """Check the hop until it matches or the attempts run out; the last lines.

    It stops at once when only differences are left that no write here can change.
    """
    lines: list[str] = []
    for attempt in range(WAIT_ATTEMPTS):
        lines = step.check(ctx)
        if not lines or _blocked(lines):
            return lines
        if attempt < WAIT_ATTEMPTS - 1:
            ctx.sleep(restore_hops.POLL_SECONDS)
    return lines


def _manual(ctx: Context, step: restore_hops.Step, lines: list[str]) -> Result:
    return Result(step.name, MANUAL, [*lines, *(step.remedy(ctx) if step.remedy else [])])


def _restore(ctx: Context, step: restore_hops.Step, lines: list[str]) -> Result:
    assert step.restore is not None
    step.restore(ctx)
    left = _converge(ctx, step)
    if not left:
        return Result(step.name, RESTORED, lines)
    if _blocked(left):
        return _manual(ctx, step, left)
    settling = all(line.startswith(restore_hops.STATUS_PREFIX) for line in left)
    reason = "still not settled" if settling else "did not take effect"
    return Result(step.name, FAILED, [f"{reason} after {WAIT_ATTEMPTS} checks:", *left])


def _run_step(ctx: Context, step: restore_hops.Step) -> Result:
    lines = step.check(ctx)
    if not lines:
        return Result(step.name, UNCHANGED)
    if step.restore is None or _blocked(lines):
        return _manual(ctx, step, lines)
    if not ctx.apply:
        return Result(step.name, DIFFERS, lines)
    return _restore(ctx, step, lines)


def run_step(ctx: Context, step: restore_hops.Step) -> Result:
    """Compare one hop and, when asked, restore and verify it; every failure is a result."""
    try:
        return _run_step(ctx, step)
    except (restore_hops.RestoreRefused, settings.ReleaseConfigError, ClientError,
            BotoCoreError) as error:
        return Result(step.name, FAILED, [_failure_text(error)])
    except MALFORMED as error:
        return Result(step.name, FAILED, [
            f"the saved copy of this hop is malformed ({type(error).__name__} {error}); take a "
            "new snapshot or restore this hop by hand"])


def _announce(result: Result, apply: bool, say: Callable[[str], None]) -> None:
    verb = {UNCHANGED: "already as saved", RESTORED: "restored and read back",
            MANUAL: "NOT restored by this command", FAILED: "FAILED",
            DIFFERS: "would restore", SKIPPED: "SKIPPED"}[result.status]
    say(f"{result.name}: {verb}")
    for line in result.lines:
        say(f"    {line}")


def run(ctx: Context, say: Callable[[str], None]) -> Outcome:
    """Run every hop in order and report; a failed hop skips only the steps that need it."""
    emit: Callable[[str], None] = lambda text: say(scrub(text))
    results: list[Result] = []
    failed_names: set[str] = set()
    plan = restore_hops.steps(sorted(ctx.where.runtime_ids))
    for number, step in enumerate(plan, start=1):
        emit(f"step {number} of {len(plan)}: {step.name}")
        blocker = next((name for name in step.needs if name in failed_names), None)
        if blocker:
            result = Result(step.name, SKIPPED, [f"skipped because {blocker} failed"])
        else:
            result = run_step(ctx, step)
        if result.status == FAILED:
            failed_names.add(step.name)
        results.append(result)
        _announce(result, ctx.apply, emit)
    failed = [r.name for r in results if r.status == FAILED]
    skipped = [r.name for r in results if r.status == SKIPPED]
    manual = [r.name for r in results if r.status == MANUAL]
    code = 1 if failed or (ctx.apply and manual) else 0
    emit(_closing(ctx, failed, manual, skipped))
    return Outcome(results, code)


def _closing(ctx: Context, failed: list[str], manual: list[str], skipped: list[str]) -> str:
    if not ctx.apply:
        return "DRY RUN done. Nothing was changed."
    if not failed and not manual:
        return "Rollback complete: every hop is as saved."
    parts = [f"failed: {', '.join(failed)}"] if failed else []
    parts += [f"skipped: {', '.join(skipped)}"] if skipped else []
    parts += [f"to run by hand: {', '.join(manual)}"] if manual else []
    return ("Rollback finished, not complete (" + "; ".join(parts) + "). Run "
            f"{ctx.rerun} --apply {settings.CONFIRM_FLAG} again after fixing.")


# -------------------------------------------------------------------- the command


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The ``rollback`` command's flags (the apply flags are added by the caller)."""
    parser.add_argument("--snapshot", type=Path,
                        help="the snapshot file to restore instead of the newest complete one")


def display_path(path: Path) -> str:
    """The path as it is typed from ``meridian/``: relative when it lies below it."""
    try:
        return str(path.resolve().relative_to(settings.MERIDIAN_DIR.resolve()))
    except ValueError:
        return str(path)


def live_mode(control: Any) -> str:
    """The release that is live: the mode of the Gateway (or Gateways) found by name.

    Raises:
        ReleaseConfigError: When none exists or the two modes both do.
    """
    modes: set[str] = set()
    for mode in (IAM, JWT):
        gateway_id = preflight.find_gateway_id(control, settings.gateway_physical_name(mode))
        if gateway_id is not None:
            modes.add(snapshot.detected_mode(control.get_gateway(gatewayIdentifier=gateway_id)))
    if len(modes) != 1:
        raise settings.ReleaseConfigError(
            f"cannot tell which release is live ({len(modes)} found among the Gateways named "
            "for the two modes), so no snapshot can be chosen for you; name one with "
            "--snapshot FILE")
    return next(iter(modes))


def _open_session(deps: Any, env: Mapping[str, Any], region: str) -> Any:
    session = deps.session(region)
    require_account(session.client("sts"), env["AURORA_CLUSTER_ARN"])
    return session


def _choose(deps: Any, env: Mapping[str, Any], account: str,
            region: str) -> tuple[Path, list[str], Any]:
    """The newest complete snapshot of the release that is not live; the session it needed.

    The local checks come first, so a missing or foreign snapshot builds no client.
    """
    loaded, broken = snapshot.complete_snapshots(deps.release_dir)
    if not loaded:
        if broken:
            snapshot.load(deps.release_dir / broken[0])
        raise settings.ReleaseConfigError(
            "no complete snapshot to roll back from; run `python scripts/release_identity.py "
            "snapshot --service-arn <arn>` before the window")
    here = [(path, document) for path, document in loaded
            if document.get("account") == account and document.get("region") == region]
    if not here:
        require_same_deployment(loaded[0][1], account, region)
    session = _open_session(deps, env, region)
    live = live_mode(session.client(restore_hops.CONTROL))
    chosen = next((path for path, document in here if document.get("mode") != live), None)
    if chosen is None:
        raise settings.ReleaseConfigError(
            f"every complete snapshot is of the {live} release, which is the one live; a "
            "rollback does not choose that on its own. Name the snapshot you mean with "
            "--snapshot FILE")
    return chosen, [name for name in broken if name > chosen.name], session


def _record(directory: Path, name: str, results: list[Result], code: int | None,
            pending: set[str], at: str) -> None:
    payload = {"at": at, "snapshot": name, "code": code, "pending": sorted(pending),
               "complete": code is not None,
               "steps": [{"name": r.name, "status": r.status} for r in results]}
    write_private(directory / RESULT_NAME, scrub(json.dumps(payload, indent=2)) + "\n")


def previous_pending(directory: Path, name: str) -> tuple[set[str], str | None]:
    """The restarts an earlier run of this snapshot left owed, and a note when unsure.

    A result file that cannot be read means the earlier run's state is unknown, so the restart
    is done to be safe.
    """
    path = directory / RESULT_NAME
    if not path.exists():
        return set(), None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {restore_hops.PENDING_RESTART}, (
            f"{RESULT_NAME} cannot be read, so the holds Lambda will be restarted to be safe")
    if not isinstance(document, dict) or document.get("snapshot") != name:
        return set(), None
    pending = document.get("pending", [])
    if not isinstance(pending, list):
        return {restore_hops.PENDING_RESTART}, (
            f"{RESULT_NAME} has no usable pending list, so the holds Lambda will be restarted "
            "to be safe")
    return {item for item in pending if isinstance(item, str)}, None


def announce(path: Path, saved: Mapping[str, Any], skipped: list[str],
             say: Callable[[str], None]) -> None:
    """Say which snapshot is used; loudly when a newer file had to be skipped."""
    commit = restore_hops.printable_commit(str(saved["commit"]))
    if skipped:
        say(f"USING AN OLDER SNAPSHOT: {path.name} (taken {saved['takenAt']}, commit {commit}); "
            "newer files were not usable:")
        for name in skipped:
            say(f"  skipped {name}: it is newer but not a complete, intact snapshot")
    say(f"Rolling back from {path.name} (taken {saved['takenAt']}, commit {commit}, "
        f"mode {saved['mode']}).")


def command(args: argparse.Namespace, deps: Any, say: Callable[[str], None]) -> int:
    """Show, or run, the rollback from a snapshot."""
    env = deps.env
    account, region = settings.deployment_target(env)
    if args.apply and not args.confirmed:
        say(f"REFUSED: --apply also needs {settings.CONFIRM_FLAG}; it changes AWS.")
        return 3
    if not settings.REGION.fullmatch(region):
        raise settings.ReleaseConfigError(
            "the Region in AURORA_CLUSTER_ARN is not a Region name; check meridian/.env")
    if args.snapshot is None:
        path, skipped, session = _choose(deps, env, account, region)
    else:
        path, skipped, session = args.snapshot, [], None
    saved = snapshot.load(path)
    require_same_deployment(saved, account, region)
    preflight.target_for(saved["mode"], env, account, region)
    announce(path, saved, skipped, say)
    pending, note = previous_pending(deps.release_dir, path.name)
    if note:
        say(note)
    gateway_id, runtime_ids = preflight.hop_ids(env)
    session = session or _open_session(deps, env, region)
    clients = snapshot.Clients(
        control=session.client(restore_hops.CONTROL), apprunner=session.client("apprunner"),
        cloudfront=session.client("cloudfront"), cfn=session.client("cloudformation"),
        ssm=session.client("ssm"), lam=session.client("lambda"), iam=session.client("iam"))
    where = snapshot.Where(account, region, gateway_id, runtime_ids,
                           saved["service"]["ServiceArn"], env)
    ctx = Context(saved=saved, clients=clients, where=where, apply=args.apply,
                  sleep=deps.sleep, stamp=snapshot.utc_stamp(deps.now()), pending=pending,
                  snapshot_ref=display_path(path))
    when = snapshot.as_utc(deps.now()).isoformat()
    if args.apply:
        ctx.journal = lambda: _record(deps.release_dir, path.name, [], None, ctx.pending, when)
    else:
        say("DRY RUN. Nothing is changed. What each hop would do:")
    outcome = run(ctx, say)
    if args.apply:
        _record(deps.release_dir, path.name, outcome.results, outcome.code, ctx.pending, when)
    else:
        say(f"Run it (ASK FIRST): python scripts/release_identity.py rollback --snapshot "
            f"{ctx.snapshot_ref} --apply {settings.CONFIRM_FLAG}")
    return outcome.code
