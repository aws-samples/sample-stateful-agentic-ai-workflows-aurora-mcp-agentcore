#!/usr/bin/env python3
"""Operate the coordinated identity release: read every hop back, deploy, roll back.

Every command that changes AWS is a dry run unless it gets both ``--apply`` and
``--i-understand-this-changes-aws``. ``check`` only reads.

    python scripts/release_identity.py check [--expect iam|jwt] [--service-arn ARN]

``check`` compares the Gateway, both Runtimes, the Cedar rules, the identity stack, the backend
login proof and (with ``--service-arn``) the App Runner environment against the mode in
``meridian/.env`` (or ``--expect``). It prints one ``DRIFT`` line per problem and exits 1, or
prints ``OK`` and exits 0; a missing or malformed setting exits 2.
"""

from __future__ import annotations

import argparse
import os
import sys
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
from scripts.identity_release import preflight, settings  # noqa: E402
from scripts.provision_service_logins import redact, require_account  # noqa: E402
from scripts.sync_cognito_env import stack_outputs  # noqa: E402

IDENTITY_STACK = "MeridianIdentity"


@dataclass
class Dependencies:
    """Everything a command reaches outside itself for, replaceable in tests."""

    env: Mapping[str, str | None]
    session: Callable[[str], Any]
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    proof_path: Path = field(default=settings.PROOF_PATH)


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
    return parser


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


HANDLERS = {"check": run_check}


def main(argv: list[str] | None = None, deps: Dependencies | None = None) -> int:
    """Run one command; a missing or malformed setting exits 2."""
    args = build_parser().parse_args(argv)
    deps = deps or default_dependencies()
    try:
        return HANDLERS[args.command](args, deps)
    except settings.ReleaseConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (BotoCoreError, ClientError) as exc:
        print(f"error: AWS read failed ({type(exc).__name__}): {redact(str(exc))}; "
              "check AWS_PROFILE and the Region", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
