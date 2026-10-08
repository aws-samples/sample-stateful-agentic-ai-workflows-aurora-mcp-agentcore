#!/usr/bin/env python3
"""Operate the coordinated identity release: read every hop back, deploy, roll back.

Every command that changes AWS is a dry run unless it gets both ``--apply`` and
``--i-understand-this-changes-aws``. ``check`` only reads.

    python scripts/release_identity.py check [--expect iam|jwt] [--service-arn ARN]
    python scripts/release_identity.py interceptor [--apply FLAG]
    python scripts/release_identity.py interceptor-delete [--apply FLAG]

``check`` compares the Gateway, both Runtimes, the Cedar rules, the identity stack, the backend
login proof and (with ``--service-arn``) the App Runner environment against the mode in
``meridian/.env`` (or ``--expect``). It prints one ``DRIFT`` line per problem and exits 1, or
prints ``OK`` and exits 0; a missing or malformed setting exits 2.

``interceptor`` deploys the Gateway request interceptor Lambda and its log-only role (dry run by
default; ``FLAG`` is ``--i-understand-this-changes-aws``) and then reads it back. Both resources
carry the release tags; one that exists without them is never modified. ``interceptor-delete``
removes only resources that carry those tags, re-reading the tags before each delete. A refused or
unconfirmed apply exits 3; an AWS error, a failed wait or a foreign resource exits 2.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import dotenv_values

MERIDIAN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MERIDIAN_DIR))

from backend.agentcore.auth_mode import IAM, JWT  # noqa: E402
from scripts.gateway_harness.verdicts import JWT_SHAPE  # noqa: E402
from scripts.identity_release import interceptor_lambda, preflight, settings  # noqa: E402
from scripts.provision_service_logins import redact, require_account  # noqa: E402
from scripts.sync_cognito_env import stack_outputs  # noqa: E402

IDENTITY_STACK = "MeridianIdentity"
EXIT_REFUSED = 3


def mask(text: str) -> str:
    """Hide 12-digit account ids and token-shaped strings."""
    return redact(JWT_SHAPE.sub("<token>", text))


def say(text: str) -> None:
    """Print with account ids and tokens masked."""
    print(mask(text))


@dataclass
class Dependencies:
    """Everything a command reaches outside itself for, replaceable in tests."""

    env: Mapping[str, str | None]
    session: Callable[[str], Any]
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    proof_path: Path = field(default=settings.PROOF_PATH)
    release_dir: Path = field(default=settings.RELEASE_DIR)
    sleep: Callable[[float], None] = time.sleep


def default_dependencies() -> Dependencies:
    """The real environment (``meridian/.env`` under the process environment) and boto3."""
    return Dependencies(
        env={**dotenv_values(MERIDIAN_DIR / ".env"), **os.environ},
        session=lambda region: boto3.Session(region_name=region),
    )


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="read every hop back and report drift",
                                allow_abbrev=False)
    check.add_argument("--expect", choices=(IAM, JWT),
                       help="the mode to expect instead of MERIDIAN_AGENTCORE_AUTH")
    check.add_argument("--service-arn", help="also check this App Runner service's environment")
    deploy = commands.add_parser("interceptor", allow_abbrev=False,
                                 help="deploy the Gateway request interceptor Lambda")
    add_apply_flags(deploy)
    remove = commands.add_parser("interceptor-delete", allow_abbrev=False,
                                 help="delete the interceptor Lambda and role (tagged ones only)")
    add_apply_flags(remove)
    return parser


def add_apply_flags(parser: argparse.ArgumentParser) -> None:
    """The two flags every command that changes AWS needs together."""
    parser.add_argument("--apply", action="store_true", help="make the change (live)")
    parser.add_argument(settings.CONFIRM_FLAG, action="store_true", dest="confirmed",
                        help="required with --apply: it changes AWS")


def unconfirmed(args: argparse.Namespace) -> bool:
    """True, after saying so, when --apply came without the confirmation flag."""
    if args.apply and not args.confirmed:
        say(f"REFUSED: --apply also needs {settings.CONFIRM_FLAG}; it changes AWS.")
        return True
    return False


def live_service_environment(apprunner: Any, service_arn: str) -> tuple[dict, dict]:
    """The plain and secret environment of the running App Runner service."""
    service = apprunner.describe_service(ServiceArn=service_arn)["Service"]
    config = service["SourceConfiguration"]["ImageRepository"].get("ImageConfiguration", {})
    return (dict(config.get("RuntimeEnvironmentVariables") or {}),
            dict(config.get("RuntimeEnvironmentSecrets") or {}))


def run_check(args: argparse.Namespace, deps: Dependencies) -> int:
    """Read every hop and print the drift."""
    env = deps.env
    mode = args.expect or settings.release_mode(env)
    account, region = settings.deployment_target(env)
    target = preflight.target_for(mode, env, account, region)
    session = deps.session(region)
    require_account(session.client("sts"), env["AURORA_CLUSTER_ARN"])
    gateway_id, runtime_ids = preflight.hop_ids(env)
    control = session.client("bedrock-agentcore-control")
    state = preflight.read_state(control, gateway_id, runtime_ids)
    findings = preflight.hop_findings(state, target)
    if mode == JWT:
        outputs = stack_outputs(session.client("cloudformation"), IDENTITY_STACK)
        findings += preflight.identity_findings(outputs, target.cognito)
        findings += preflight.check_backend_login_proof(deps.proof_path, deps.now())
    if args.service_arn:
        variables, secrets = live_service_environment(session.client("apprunner"), args.service_arn)
        findings += preflight.check_service_environment(variables, secrets, target)
    for line in findings:
        print(f"DRIFT  {line}")
    if findings:
        return 1
    note = "" if args.service_arn else " (App Runner not checked; pass --service-arn)"
    print(f"OK  every hop reports {mode}{note}")
    return 0


def interceptor_clients(deps: Dependencies, region: str) -> tuple[Any, Any]:
    """The IAM and Lambda clients, after the credentials are shown to be for this account."""
    if not settings.REGION.fullmatch(region):
        raise settings.ReleaseConfigError(
            "the Region in AURORA_CLUSTER_ARN is not a Region name; check meridian/.env")
    session = deps.session(region)
    require_account(session.client("sts"), deps.env["AURORA_CLUSTER_ARN"])
    return session.client("iam"), session.client("lambda")


def run_interceptor(args: argparse.Namespace, deps: Dependencies) -> int:
    """Plan, or create and read back, the interceptor function and its log-only role."""
    env = deps.env
    design = settings.enforcement(env)
    if not settings.uses_interceptor(design):
        raise settings.ReleaseConfigError(
            f"{settings.ENFORCEMENT_ENV} is {design}: that design deploys no interceptor")
    account, region = settings.deployment_target(env)
    wanted = interceptor_lambda.desired(account, region, settings.cognito_settings(env))
    if unconfirmed(args):
        return EXIT_REFUSED
    if not args.apply:
        say("DRY RUN. Nothing is created or changed.")
        for line in interceptor_lambda.plan(wanted):
            say(f"  {line}")
        say(f"Apply (ASK FIRST): python scripts/release_identity.py interceptor --apply "
            f"{settings.CONFIRM_FLAG}")
        return 0
    iam, lam = interceptor_clients(deps, region)
    for note in interceptor_lambda.apply(iam, lam, wanted, sleep=deps.sleep):
        say(note)
    findings = interceptor_lambda.read_back(iam, lam, wanted)
    for line in findings:
        say(f"DRIFT  {line}")
    if findings:
        return 1
    interceptor_lambda.record_outputs(deps.release_dir, wanted, deps.now().isoformat())
    say("OK  the interceptor function matches what the release wants")
    return 0


def run_interceptor_delete(args: argparse.Namespace, deps: Dependencies) -> int:
    """Plan, or delete, the tagged interceptor function and role (any enforcement design)."""
    _, region = settings.deployment_target(deps.env)
    if unconfirmed(args):
        return EXIT_REFUSED
    if not args.apply:
        say("DRY RUN. Nothing is deleted.")
        for line in interceptor_lambda.teardown_plan():
            say(f"  {line}")
        say(f"Delete (ASK FIRST): python scripts/release_identity.py interceptor-delete --apply "
            f"{settings.CONFIRM_FLAG}")
        return 0
    iam, lam = interceptor_clients(deps, region)
    for note in interceptor_lambda.teardown(iam, lam):
        say(note)
    interceptor_lambda.remove_outputs(deps.release_dir)
    return 0


HANDLERS = {"check": run_check, "interceptor": run_interceptor,
            "interceptor-delete": run_interceptor_delete}


def main(argv: list[str] | None = None, deps: Dependencies | None = None) -> int:
    """Run one command; a missing or malformed setting exits 2."""
    args = build_parser().parse_args(argv)
    deps = deps or default_dependencies()
    try:
        return HANDLERS[args.command](args, deps)
    except (settings.ReleaseConfigError, interceptor_lambda.DeployError) as exc:
        print(f"error: {mask(str(exc))}", file=sys.stderr)
        return 2
    except (BotoCoreError, ClientError) as exc:
        print(f"error: AWS call failed ({type(exc).__name__}): {mask(str(exc))}; "
              "check AWS_PROFILE and the Region", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: could not write the release outputs ({type(exc).__name__}): "
              f"{mask(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
