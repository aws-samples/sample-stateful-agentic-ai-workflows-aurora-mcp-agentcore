"""The one-command rollback: put the saved configuration back, hop by hop, reading each back.

``rollback`` loads the newest complete snapshot (or the one named), refuses one that is missing,
changed, partial or for another account or Region, and prints what it will restore. Nothing is
changed without ``--apply`` and the confirmation flag. The hops are restored in the reverse of the
release order (site, service, roles stack, Gateway, Runtimes, Cedar rules, Lambdas); after each
write the hop is read back until it matches the snapshot, a bounded number of times. A failed hop
is reported and the others still run, because each restore is independent; the exit code is then
1. The roles stack and the Cedar rules cannot be restored through their APIs without deleting
or bypassing CloudFormation, so they are checked and their exact commands are printed (exit 1
until they are run). Running it twice is safe: a hop that already matches is only read. Nothing
is deleted.
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

from scripts.gateway_harness.private_files import write_private
from scripts.gateway_harness.verdicts import JWT_SHAPE
from scripts.identity_release import preflight, restore_hops, settings, snapshot
from scripts.provision_service_logins import require_account

Context = restore_hops.Context
WAIT_ATTEMPTS = restore_hops.WAIT_ATTEMPTS
RESULT_NAME = "rollback-result.json"
ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
UNCHANGED, RESTORED, MANUAL, FAILED, DIFFERS = (
    "unchanged", "restored", "manual", "failed", "differs")


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
    return str(error) if isinstance(error, restore_hops.RestoreRefused) else type(error).__name__


def _converge(ctx: Context, step: restore_hops.Step) -> list[str]:
    """Check the hop until it matches or the attempts run out; the last lines."""
    lines: list[str] = []
    for attempt in range(WAIT_ATTEMPTS):
        lines = step.check(ctx)
        if not lines:
            return []
        if attempt < WAIT_ATTEMPTS - 1:
            ctx.sleep(restore_hops.POLL_SECONDS)
    return lines


def _restore(ctx: Context, step: restore_hops.Step, lines: list[str]) -> Result:
    assert step.restore is not None
    step.restore(ctx)
    left = _converge(ctx, step)
    if not left:
        return Result(step.name, RESTORED, lines)
    settling = all(line.startswith(restore_hops.STATUS_PREFIX) for line in left)
    reason = "still not settled" if settling else "did not take effect"
    return Result(step.name, FAILED, [f"{reason} after {WAIT_ATTEMPTS} checks:", *left])


def run_step(ctx: Context, step: restore_hops.Step) -> Result:
    """Compare one hop and, when asked, restore and verify it."""
    try:
        lines = step.check(ctx)
        if not lines:
            return Result(step.name, UNCHANGED)
        if step.restore is None:
            return Result(step.name, MANUAL, [*lines, *(step.remedy(ctx) if step.remedy else [])])
        if not ctx.apply:
            return Result(step.name, DIFFERS, lines)
        return _restore(ctx, step, lines)
    except (restore_hops.RestoreRefused, settings.ReleaseConfigError, ClientError,
            BotoCoreError) as error:
        return Result(step.name, FAILED, [_failure_text(error)])


def _announce(result: Result, apply: bool, say: Callable[[str], None]) -> None:
    verb = {UNCHANGED: "already as saved", RESTORED: "restored and read back",
            MANUAL: "NOT restored by this command", FAILED: "FAILED",
            DIFFERS: "would restore"}[result.status]
    say(f"{result.name}: {verb}")
    for line in result.lines:
        say(f"    {line}")


def run(ctx: Context, say: Callable[[str], None]) -> Outcome:
    """Run every hop in order and report; a failed hop does not stop the others."""
    emit: Callable[[str], None] = lambda text: say(scrub(text))
    results = []
    plan = restore_hops.steps(sorted(ctx.where.runtime_ids))
    for number, step in enumerate(plan, start=1):
        emit(f"step {number} of {len(plan)}: {step.name}")
        result = run_step(ctx, step)
        results.append(result)
        _announce(result, ctx.apply, emit)
    failed = [r.name for r in results if r.status == FAILED]
    manual = [r.name for r in results if r.status == MANUAL]
    code = 1 if failed or (ctx.apply and manual) else 0
    emit(_closing(ctx.apply, failed, manual))
    return Outcome(results, code)


def _closing(apply: bool, failed: list[str], manual: list[str]) -> str:
    if not apply:
        return "DRY RUN done. Nothing was changed."
    if not failed and not manual:
        return "Rollback complete: every hop is as saved."
    parts = [f"failed: {', '.join(failed)}"] if failed else []
    parts += [f"to run by hand: {', '.join(manual)}"] if manual else []
    return "Rollback finished, not complete (" + "; ".join(parts) + "). Run it again after fixing."


# -------------------------------------------------------------------- the command


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The ``rollback`` command's flags (the apply flags are added by the caller)."""
    parser.add_argument("--snapshot", type=Path,
                        help="the snapshot file to restore instead of the newest complete one")


def _choose(args: argparse.Namespace, directory: Path) -> tuple[Path, list[str]]:
    if args.snapshot is not None:
        return args.snapshot, []
    found, skipped = snapshot.latest_complete(directory)
    if found is None and skipped:
        snapshot.load(directory / skipped[0])
    if found is None:
        raise settings.ReleaseConfigError(
            "no complete snapshot to roll back from; run `python scripts/release_identity.py "
            "snapshot --service-arn <arn>` before the window")
    return found, skipped


def _record(directory: Path, name: str, outcome: Outcome, at: str) -> None:
    payload = {"at": at, "snapshot": name, "code": outcome.code,
               "steps": [{"name": r.name, "status": r.status} for r in outcome.results]}
    write_private(directory / RESULT_NAME, scrub(json.dumps(payload, indent=2)) + "\n")


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
    path, skipped = _choose(args, deps.release_dir)
    saved = snapshot.load(path)
    require_same_deployment(saved, account, region)
    preflight.target_for(saved["mode"], env, account, region)
    gateway_id, runtime_ids = preflight.hop_ids(env)
    session = deps.session(region)
    require_account(session.client("sts"), env["AURORA_CLUSTER_ARN"])
    clients = snapshot.Clients(
        control=session.client("bedrock-agentcore-control"), apprunner=session.client("apprunner"),
        cloudfront=session.client("cloudfront"), cfn=session.client("cloudformation"),
        ssm=session.client("ssm"), lam=session.client("lambda"))
    where = snapshot.Where(account, region, gateway_id, runtime_ids,
                           saved["service"]["ServiceArn"], env)
    ctx = Context(saved=saved, clients=clients, where=where, apply=args.apply,
                  sleep=deps.sleep, stamp=deps.now().strftime(snapshot.STAMP))
    for name in skipped:
        say(f"skipped {name}: it is newer but not a complete, intact snapshot")
    say(f"Rolling back from {path.name} (taken {saved['takenAt']}, mode {saved['mode']}).")
    if not args.apply:
        say("DRY RUN. Nothing is changed. What each hop would do:")
    outcome = run(ctx, say)
    if args.apply:
        _record(deps.release_dir, path.name, outcome, deps.now().isoformat())
    else:
        say(f"Run it (ASK FIRST): python scripts/release_identity.py rollback --apply "
            f"{settings.CONFIRM_FLAG}")
    return outcome.code
