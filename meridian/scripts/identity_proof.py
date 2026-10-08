#!/usr/bin/env python3
"""Prove that a second signed-in user is refused at four layers and Jordan is allowed at each.

After the Cognito release, this signs in as the two seeded users (tokens minted in memory from
Secrets Manager, never printed or stored) and sends fifteen probes at four layers: the hosted
backend, the two Runtimes, the Gateway and the database. For every probe it records the layer
that answered. The decoy (a valid token, a real second traveler) must be refused at all four
layers; Jordan must be allowed at all four. Jordan's controls write two things: one review-only
Workflow run and one courtesy hold. Both are removed by id at the end, and a leftover fails the
receipt.

The database probe opens its own Data API transaction, pins the decoy's traveler, steps down to
meridian_app and counts Jordan's rows. It does not go through the workload grant, which the
decoy lacks by decision. The Gateway refusal is attributed with a before and after count of
traveler_access_audit deny rows: a new row means the Holds Lambda refused after the interceptor
rewrote the traveler, no row means Cedar or the interceptor refused first.

Without --apply it prints the plan and what would be refused, and calls nothing. With --apply it
needs the confirmation flag as well. Run it only from a clean checkout of the released commit.

    python scripts/identity_proof.py
    python scripts/identity_proof.py --apply --i-understand-this-changes-aws
    python scripts/identity_proof.py --apply --i-understand-this-changes-aws --jordan-only
    python scripts/identity_proof.py --render .local/identity-proof/latest.json

Before anything is signed in or sent, it writes a receipt with "ok": false over latest.json, so
an older passing receipt cannot outlive a run that dies. A crash, Ctrl-C or SIGTERM replaces it
with a failed receipt that says how the run stopped. It refuses a checkout with uncommitted
changes, because the receipt names the commit.

Files, all under .local/identity-proof/: receipt-<stamp>.json and latest.json (mode 0600, no
token, no account id), receipt-<stamp>.summary.html and receipt-<stamp>.full.html.

Exit codes: 0 every probe met its expectation and nothing was left behind; 1 a probe failed or
errored, a layer has no probe, a leftover remains, or the run crashed or was interrupted;
3 refused (a guard, a usage error, a missing flag or a bad output path).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import dotenv_values, load_dotenv

MERIDIAN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MERIDIAN_DIR))

from scripts.identity_probes.probes import PLAN, Context, Ports  # noqa: E402
from scripts.identity_probes.receipt import (  # noqa: E402
    FULL,
    JORDAN_ONLY,
    Receipt,
    leaks,
    render_table,
    scrub,
    write_receipt,
)
from scripts.identity_probes.render_html import full_html, summary_html  # noqa: E402
from scripts.identity_probes.runner import Cleanup, Header, run_proof  # noqa: E402
from scripts.identity_release import settings  # noqa: E402
from scripts.prove_backend_login import mask  # noqa: E402

LOCAL_DIR = MERIDIAN_DIR / ".local"
EXIT_PASS, EXIT_FAIL, EXIT_REFUSED = 0, 1, 3
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
STAMP = "%Y%m%dT%H%M%SZ"
COMMAND = f"python scripts/identity_proof.py --apply {settings.CONFIRM_FLAG}"


@dataclass(frozen=True)
class Rig:
    """The ports and the cleanup a run uses."""

    ports: Ports
    cleanup: Cleanup


@dataclass(frozen=True)
class Target:
    """What the guards established about the deployment and the checkout."""

    region: str
    pool_suffix: str
    design: str
    url: str
    sha: str


@dataclass(frozen=True)
class Dependencies:
    """Everything the command reaches outside itself for."""

    env: Mapping[str, str | None]
    caller: Callable[[str], tuple[str, str]]
    git_sha: Callable[[], str]
    changes: Callable[[], list[str]]
    site_url: Callable[[], str]
    build_rig: Callable[[Mapping[str, str | None], str, str], Rig]
    now: Callable[[], datetime]
    run_id: Callable[[], str]
    local_root: Path = LOCAL_DIR


class UsageError(Exception):
    """The command line was not understood."""


def say(text: str) -> None:
    """Print with account ids, tokens and keys masked."""
    print(mask(text))


def refuse(text: str) -> int:
    """Print why nothing was started."""
    say(f"REFUSED: {text}")
    return EXIT_REFUSED


def checked_url(url: str) -> str:
    """The base URL when a token may be sent to it: https, or http on loopback.

    Raises:
        settings.ReleaseConfigError: When the address could receive a credential unsafely.
    """
    parsed = urlparse(url)
    if parsed.username or parsed.password or not parsed.hostname:
        raise settings.ReleaseConfigError(
            "the base URL must be a plain address with no credentials")
    if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in LOOPBACK):
        return url.rstrip("/")
    raise settings.ReleaseConfigError(
        f"refusing to send a token to {parsed.scheme}://{parsed.hostname}: use https, or http on "
        "localhost")


def offline_target(deps: Dependencies, base_url: str | None) -> Target:
    """Check every guard that needs no AWS call and return what it established.

    Raises:
        settings.ReleaseConfigError: When a setting is missing, the mode is not ``jwt``, the
            checkout has uncommitted changes or the address is unsafe.
    """
    env = deps.env
    if settings.release_mode(env) != "jwt":
        raise settings.ReleaseConfigError(
            "MERIDIAN_AGENTCORE_AUTH is not jwt; the proof signs in as the seeded users, so run "
            "it only against the released system")
    pool_id = settings.cognito_settings(env).pool_id
    design = settings.enforcement(env)
    _account, region = settings.deployment_target(env)
    changes = deps.changes()
    if changes:
        shown = "; ".join(changes[:5]) + (" ..." if len(changes) > 5 else "")
        raise settings.ReleaseConfigError(
            f"meridian/ has {len(changes)} uncommitted change(s) ({shown}); the receipt names the "
            "commit, so commit or stash them and run again")
    url = checked_url(base_url or deps.site_url())
    return Target(region, pool_id[-4:], design, url, deps.git_sha())


def check_account(deps: Dependencies) -> None:
    """Refuse unless the credentials are for the deployment's account and Region.

    Raises:
        settings.ReleaseConfigError: When they are not.
    """
    account, region = settings.deployment_target(deps.env)
    if deps.caller(region) != (account, region):
        raise settings.ReleaseConfigError(
            f"the AWS credentials are for another account or Region than this deployment "
            f"(its Region is {region}); sign in to the right account")


def output_dir(deps: Dependencies, requested: Path | None) -> Path:
    """The folder for the files, which must be inside the local root.

    Raises:
        settings.ReleaseConfigError: When the folder is elsewhere.
    """
    folder = (requested or deps.local_root / "identity-proof").resolve()
    root = deps.local_root.resolve()
    if folder != root and root not in folder.parents:
        raise settings.ReleaseConfigError(
            f"the output folder must be inside {root.name}/, which Git ignores")
    return folder


def dry_run(deps: Dependencies, args: argparse.Namespace) -> int:
    """Print the plan and what an applied run would refuse. Nothing is called."""
    say("DRY RUN. Nothing was called, signed in or written.")
    try:
        target = offline_target(deps, args.base_url)
        say(f"Region {target.region}, Gateway design {target.design}, site {target.url}")
    except settings.ReleaseConfigError as exc:
        say(f"WOULD REFUSE: {exc}")
    say(f"{len(PLAN)} probes, in order:")
    for spec in PLAN:
        say(f"  {spec.layer:<9} {spec.actor:<6} {spec.expected:<8} {spec.id}: {spec.sends}")
    say("On --apply it also checks that the AWS credentials match the deployment's account and "
        "Region.")
    say("Residue: Jordan's review-only Workflow run and one courtesy hold are removed by id; "
        "audit rows (traveler_access_audit, the agent audit log) are append-only and stay.")
    say(f"Run it (ASK FIRST): {COMMAND}")
    return EXIT_PASS


def save(receipt: Receipt, folder: Path, stamp: str) -> list[Path]:
    """Write the receipt as JSON twice (dated and latest) and the two HTML pages.

    Raises:
        ValueError: When the receipt or a page would hold a token, key id, long secret or
            account id; nothing more is written.
    """
    data = receipt.to_dict()
    pages = {".summary.html": summary_html(data), ".full.html": full_html(data)}
    for name, text in pages.items():
        found = leaks(text)
        if found:
            raise ValueError(f"the {name} page would contain {', '.join(found)}; not written")
    dated = folder / f"receipt-{stamp}.json"
    write_receipt(dated, receipt)
    write_receipt(folder / "latest.json", receipt)
    for suffix, text in pages.items():
        dated.with_suffix(suffix).write_text(text, encoding="utf-8")
    return [dated, *[dated.with_suffix(suffix) for suffix in pages]]


def shown(path: Path) -> str:
    """``path`` relative to the repository's meridian/ folder when it is inside it."""
    try:
        return str(path.relative_to(MERIDIAN_DIR))
    except ValueError:
        return str(path)


def unfinished(header: Header, reason: str) -> Receipt:
    """A receipt that cannot pass: no probe ran to the end and the cleanup state is unknown."""
    return Receipt(
        at=header.at, git_sha=header.git_sha, region=header.region,
        pool_suffix=header.pool_suffix, design=header.design, site_host=header.site_host,
        mode=header.mode, cleanup={"leftovers": "unknown", "problems": [scrub(reason)]})


def record_failure(folder: Path, stamp: str, header: Header, exc: BaseException) -> None:
    """Replace the starting receipt with one that says how the run stopped.

    If that cannot be written the starting receipt, which already fails, stays in place.
    """
    notes = " ".join(getattr(exc, "__notes__", []))
    reason = f"the run stopped: {type(exc).__name__}. {notes}".strip()
    try:
        save_json(unfinished(header, reason), folder, stamp)
    except (OSError, ValueError) as problem:
        say(f"Could not update the failed receipt ({type(problem).__name__}); the one written "
            "before the run started still says it did not finish.")


def save_json(receipt: Receipt, folder: Path, stamp: str) -> None:
    """Write ``receipt`` as the dated and the latest JSON file."""
    write_receipt(folder / f"receipt-{stamp}.json", receipt)
    write_receipt(folder / "latest.json", receipt)


def prove(args: argparse.Namespace, deps: Dependencies, target: Target, folder: Path) -> int:
    """Invalidate the last receipt, build the ports, run the plan, clean up and write the files.

    A receipt that fails is written before anything is signed in or sent, so an older passing
    one cannot outlive a run that dies. Any ``BaseException`` (a crash, Ctrl-C, SIGTERM) replaces
    it with a receipt that says how the run stopped, and is re-raised.
    """
    started = deps.now()
    stamp = started.strftime(STAMP)
    header = Header(
        at=started.isoformat(timespec="seconds"), git_sha=target.sha, region=target.region,
        pool_suffix=target.pool_suffix, design=target.design,
        site_host=urlparse(target.url).hostname or "",
        mode=JORDAN_ONLY if args.jordan_only else FULL)
    save_json(unfinished(header, "the run started and has not finished"), folder, stamp)
    try:
        rig = deps.build_rig(deps.env, target.region, target.url)
        context = Context(run_id=deps.run_id(), design=target.design)
        receipt = run_proof(rig.ports, rig.cleanup, context, header, jordan_only=args.jordan_only)
        paths = save(receipt, folder, stamp)
    except BaseException as exc:
        record_failure(folder, stamp, header, exc)
        raise
    say(render_table(receipt))
    for path in paths:
        say(f"Wrote {shown(path)}")
    return EXIT_PASS if receipt.ok else EXIT_FAIL


def render_command(path: Path) -> int:
    """Write the summary and full pages next to a recorded receipt. Nothing else is touched."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        full, summary = full_html(data), summary_html(data)
    except (OSError, ValueError, KeyError, TypeError):
        return refuse(f"{shown(path)} is not a receipt this command wrote")
    path.with_suffix(".summary.html").write_text(summary, encoding="utf-8")
    path.with_suffix(".full.html").write_text(full, encoding="utf-8")
    say(f"Wrote {shown(path.with_suffix('.summary.html'))} and "
        f"{shown(path.with_suffix('.full.html'))}")
    return EXIT_PASS


def run(args: argparse.Namespace, deps: Dependencies) -> int:
    """Render, print the plan, refuse, or run the proof."""
    if args.render:
        return render_command(args.render)
    if not args.apply:
        return dry_run(deps, args)
    if not args.confirmed:
        return refuse(f"--apply also needs {settings.CONFIRM_FLAG}; it signs in as the seeded "
                      "users, calls AWS and places one test hold")
    try:
        target = offline_target(deps, args.base_url)
        check_account(deps)
        folder = output_dir(deps, args.output_dir)
    except settings.ReleaseConfigError as exc:
        return refuse(str(exc))
    return prove(args, deps, target, folder)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # noqa: D102 - argparse hook
        raise UsageError(f"{self.format_usage().strip()}\n{message}")


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = _Parser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    parser.add_argument("--apply", action="store_true", help="run the proof (live)")
    parser.add_argument(settings.CONFIRM_FLAG, action="store_true", dest="confirmed",
                        help="required with --apply: it signs in, calls AWS and places one hold")
    parser.add_argument("--jordan-only", action="store_true", help="run only Jordan's controls")
    parser.add_argument("--base-url", help="the hosted site; default from the release record")
    parser.add_argument("--output-dir", type=Path, help="a folder inside .local/")
    parser.add_argument("--render", type=Path, metavar="RECEIPT",
                        help="write the HTML pages for a recorded receipt; no AWS call")
    return parser


def _raise_interrupt(_signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None, deps: Dependencies | None = None) -> int:
    """Run the command. It never prints a traceback."""
    try:
        args = build_parser().parse_args(argv)
    except UsageError as exc:
        return refuse(str(exc))
    previous = signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        return run(args, deps or default_dependencies())
    except KeyboardInterrupt as exc:
        say("Interrupted; the cleanup ran and the receipt records a failed run.")
        for note in getattr(exc, "__notes__", []):
            say(note)
        return EXIT_FAIL
    except Exception as exc:  # noqa: BLE001 - the one place that turns a crash into a message
        say(f"ERROR: {type(exc).__name__}: {exc}")
        for note in getattr(exc, "__notes__", []):
            say(note)
        return EXIT_FAIL
    finally:
        signal.signal(signal.SIGTERM, previous)


def _caller(region: str) -> tuple[str, str]:
    import boto3

    session_region = boto3.Session().region_name or ""
    configured = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
    account = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    return account, configured or session_region


def _build_rig(env: Mapping[str, str | None], region: str, url: str) -> Rig:
    from scripts.identity_probes.effects import build_rig

    return Rig(*build_rig(env, region, url))


def default_dependencies() -> Dependencies:
    """The real environment, release record, ports and clock."""
    from scripts import published

    load_dotenv(MERIDIAN_DIR / ".env")
    env = {**dotenv_values(MERIDIAN_DIR / ".env"), **os.environ}
    return Dependencies(
        env=env, caller=_caller, git_sha=settings.git_head,
        changes=settings.working_tree_changes,
        site_url=lambda: published.release_url(published.load_record()),
        build_rig=_build_rig, now=lambda: datetime.now(timezone.utc),
        run_id=lambda: uuid.uuid4().hex[:8])


if __name__ == "__main__":
    sys.exit(main())
