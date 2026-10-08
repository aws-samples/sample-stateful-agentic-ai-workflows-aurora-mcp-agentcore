#!/usr/bin/env python3
"""Operate the coordinated identity release: read every hop back, deploy, roll back.

Every command that changes AWS is a dry run unless it gets both ``--apply`` and
``--i-understand-this-changes-aws``. ``check`` only reads.

    python scripts/release_identity.py check [--expect iam|jwt] (--service-arn ARN | --skip-service)
    python scripts/release_identity.py interceptor [--apply --i-understand-this-changes-aws]
    python scripts/release_identity.py interceptor-delete [--apply --i-understand-this-changes-aws]
    python scripts/release_identity.py lambdas [--expect master|gateway|tightened] [--restart-holds]
    python scripts/release_identity.py gateway [--to iam|jwt] [--only grant|move]
        [--apply --i-understand-this-changes-aws]
    python scripts/release_identity.py snapshot --service-arn ARN
    python scripts/release_identity.py rollback [--snapshot FILE]
        [--apply --i-understand-this-changes-aws]

``check`` compares the Gateway, both Runtimes, the Cedar rules, the identity stack, the backend
login proof, the interceptor Lambda's environment and the App Runner environment against the mode
in ``meridian/.env`` (or ``--expect``). The service must be named: ``--service-arn`` reads it,
``--skip-service`` leaves it out on purpose. ``interceptor`` deploys the Gateway request
interceptor Lambda and its log-only role (dry run by default) and then reads it back. Both
resources carry the release tags; one that exists without them is never modified.
``interceptor-delete`` removes only resources that carry those tags, re-reading the tags before
each delete.
``lambdas`` checks that the SSM parameter, the semantic-search Lambda and both roles are at a stage
of the move to the meridian_gateway login (read-only); with ``--restart-holds`` (a dry run unless
both flags) it forces the holds Lambda to re-read its configuration.
``gateway`` moves the live Gateway to the mode (dry run by default: before and after of the
authorizer, allowed clients and interceptor, and every precondition); an apply writes the invoke
grants, sends the complete update and reads it back; ``--to iam`` is the rollback of the move.
``snapshot`` (read-only) saves the replaced configuration of every hop to ``.local/release-b2/``
before the window, with no secret value. ``rollback`` restores it in the reverse of the release
order, reading each hop back (dry run by default; the roles stack and the Cedar rules are checked
and their commands printed); it exits 1 when any hop is not restored.

Exit codes, the same for every command:

    0  ok: every hop matches (and the service was checked or skipped on purpose), or a dry run
    1  drift: one ``DRIFT`` line per problem, or the read-back after an apply found a difference
    2  could not run or compare: a missing or malformed setting, credentials for another
       account, an AWS error or failed wait, a foreign resource, an unexpected error
    3  usage error (unknown or misspelled argument), or an apply refused because the
       confirmation flag is missing
    4  every hop matches but the App Runner service was not named, so the release is not fully
       checked
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
from typing import Any, NoReturn

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agentcore.auth_mode import IAM, JWT  # noqa: E402
from scripts.gateway_harness.verdicts import JWT_SHAPE  # noqa: E402
from scripts.identity_release import interceptor_lambda, preflight, settings  # noqa: E402
from scripts.identity_release import lambda_release  # noqa: E402
from scripts.identity_release import gateway_release  # noqa: E402
from scripts.identity_release import rollback, snapshot  # noqa: E402
from scripts.provision_service_logins import redact, require_account  # noqa: E402
from scripts.sync_cognito_env import FRONTEND_ENV_FILE, stack_outputs  # noqa: E402

IDENTITY_STACK = "MeridianIdentity"
EXIT_DRIFT = 1
EXIT_COULD_NOT_RUN = 2
EXIT_USAGE = 3
EXIT_REFUSED = EXIT_USAGE
EXIT_NOT_CHECKED = 4


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
    head_sha: Callable[[], str] = settings.git_head
    proof_path: Path = field(default=settings.PROOF_PATH)
    release_dir: Path = field(default=settings.RELEASE_DIR)
    hosted_release_path: Path = field(default=snapshot.HOSTED_RELEASE_PATH)
    sleep: Callable[[float], None] = time.sleep


def default_dependencies() -> Dependencies:
    """The real environment (``meridian/.env`` under the process environment) and boto3."""
    return Dependencies(
        env={**dotenv_values(FRONTEND_ENV_FILE), **dotenv_values(settings.MERIDIAN_DIR / ".env"),
             **os.environ},
        session=lambda region: boto3.Session(region_name=region),
    )


class MaskingParser(argparse.ArgumentParser):
    """A parser whose usage errors exit 3 and never echo an account id or a token."""

    def error(self, message: str) -> NoReturn:
        """Print the usage and the masked message to stderr, then exit with ``EXIT_USAGE``."""
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {mask(message)}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = MaskingParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="read every hop back and report drift",
                                allow_abbrev=False)
    check.add_argument("--expect", choices=(IAM, JWT),
                       help="the mode to expect instead of MERIDIAN_AGENTCORE_AUTH")
    service = check.add_mutually_exclusive_group()
    service.add_argument("--service-arn", help="check this App Runner service's environment")
    service.add_argument("--skip-service", action="store_true",
                         help="leave the App Runner service out of this check on purpose")
    deploy = commands.add_parser("interceptor", allow_abbrev=False,
                                 help="deploy the Gateway request interceptor Lambda")
    add_apply_flags(deploy)
    remove = commands.add_parser("interceptor-delete", allow_abbrev=False,
                                 help="delete the interceptor Lambda and role (tagged ones only)")
    add_apply_flags(remove)
    move_lambdas = commands.add_parser(
        "lambdas", allow_abbrev=False,
        help="check the Lambdas' move to the meridian_gateway login, or restart the holds Lambda")
    move_lambdas.add_argument("--expect", choices=lambda_release.STAGES, default="gateway",
                              help="the stage to expect (default: gateway)")
    move_lambdas.add_argument("--restart-holds", action="store_true",
                              help="force the holds Lambda to re-read its configuration")
    add_apply_flags(move_lambdas)
    move_gateway = commands.add_parser(
        "gateway", allow_abbrev=False,
        help="move the live Gateway's authorizer and interceptor (dry run by default)")
    gateway_release.add_arguments(move_gateway)
    save = commands.add_parser("snapshot", allow_abbrev=False,
                               help="save the configuration the release replaces (read-only)")
    snapshot.add_arguments(save)
    back = commands.add_parser("rollback", allow_abbrev=False,
                               help="restore the saved configuration (dry run by default)")
    rollback.add_arguments(back)
    add_apply_flags(back)
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


def service_findings(session: Any, service_arn: str, target: preflight.Target) -> list[str]:
    """Findings for the running App Runner service named by ``--service-arn``."""
    found = preflight.check_service_arn(service_arn, target)
    if found:
        return found
    response = session.client("apprunner").describe_service(ServiceArn=service_arn)
    environment = preflight.image_environment(
        response.get("Service") if isinstance(response, dict) else None)
    if environment is None:
        return ["Service: not an image-based source, so its environment cannot be compared"]
    return preflight.check_service_environment(*environment, target)


def interceptor_findings(session: Any, target: preflight.Target) -> list[str]:
    """Findings for the interceptor's environment; reads it with ``GetFunctionConfiguration``."""
    try:
        configuration = session.client("lambda").get_function_configuration(
            FunctionName=target.interceptor_arn)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise
        return ["Interceptor Lambda: not deployed; run scripts/release_identity.py interceptor "
                f"--apply {settings.CONFIRM_FLAG}"]
    return preflight.interceptor_environment_findings(configuration, target)


def identity_stack_findings(session: Any, deps: Dependencies,
                            target: preflight.Target) -> list[str]:
    """Findings for the identity stack and the backend login proof (jwt only)."""
    outputs = stack_outputs(session.client("cloudformation"), IDENTITY_STACK)
    domain = (deps.env.get("VITE_COGNITO_DOMAIN") or "").strip()
    found = preflight.identity_findings(outputs, target.cognito, domain)
    return found + preflight.check_backend_login_proof(
        deps.proof_path, target, deps.head_sha(), deps.now())


def run_check(args: argparse.Namespace, deps: Dependencies) -> int:
    """Read every hop and print the drift."""
    env = deps.env
    mode = args.expect or settings.release_mode(env)
    account, region = settings.deployment_target(env)
    target = preflight.target_for(mode, env, account, region)
    session = deps.session(region)
    require_account(session.client("sts"), env["AURORA_CLUSTER_ARN"])
    gateway_id, runtime_ids = preflight.hop_ids(env)
    state = preflight.read_state(
        session.client("bedrock-agentcore-control"), gateway_id, runtime_ids)
    findings = preflight.check_hop_locations(env, target)
    findings += preflight.hop_findings(state, target)
    if mode == JWT:
        findings += identity_stack_findings(session, deps, target)
    if target.interceptor_arn:
        findings += interceptor_findings(session, target)
    if args.service_arn:
        findings += service_findings(session, args.service_arn, target)
    for line in findings:
        print(f"DRIFT  {line}")
    return verdict(findings, mode, args)


def verdict(findings: list[str], mode: str, args: argparse.Namespace) -> int:
    """Print the closing line and return the exit code of ``check``."""
    unchecked = not (args.service_arn or args.skip_service)
    if unchecked:
        print("NOT CHECKED  App Runner service: pass --service-arn ARN to check it, or "
              "--skip-service to leave it out on purpose")
    if findings:
        return EXIT_DRIFT
    if unchecked:
        return EXIT_NOT_CHECKED
    note = " (App Runner service skipped by request)" if args.skip_service else ""
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
        return EXIT_DRIFT
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


def run_lambdas(args: argparse.Namespace, deps: Dependencies) -> int:
    """Check the Lambdas against a stage, or restart the holds Lambda."""
    return lambda_release.run(args, deps, say)


def run_gateway(args: argparse.Namespace, deps: Dependencies) -> int:
    """Plan, or apply and read back, the Gateway's move to a mode."""
    return gateway_release.run(args, deps, say=say)


def run_snapshot(args: argparse.Namespace, deps: Dependencies) -> int:
    """Save the configuration the release replaces."""
    return snapshot.command(args, deps, say)


def run_rollback(args: argparse.Namespace, deps: Dependencies) -> int:
    """Show, or run, the rollback from a snapshot."""
    return rollback.command(args, deps, say)


HANDLERS = {"check": run_check, "interceptor": run_interceptor,
            "interceptor-delete": run_interceptor_delete, "lambdas": run_lambdas,
            "gateway": run_gateway, "snapshot": run_snapshot, "rollback": run_rollback}


def main(argv: list[str] | None = None, deps: Dependencies | None = None) -> int:
    """Run one command; whatever stops it from comparing exits 2 with one line, no traceback."""
    args = build_parser().parse_args(argv)
    deps = deps or default_dependencies()
    try:
        return HANDLERS[args.command](args, deps)
    except (settings.ReleaseConfigError, interceptor_lambda.DeployError,
            gateway_release.GatewayError) as exc:
        print(f"error: {mask(str(exc))}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    except (BotoCoreError, ClientError) as exc:
        print(f"error: AWS call failed ({type(exc).__name__}): {mask(str(exc))}; "
              "check AWS_PROFILE and the Region", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    except OSError as exc:
        print(f"error: could not write the release outputs ({type(exc).__name__}): "
              f"{mask(str(exc))}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    except SystemExit as stopped:
        if not isinstance(stopped.code, str):
            raise
        print(f"error: {mask(stopped.code)}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    except Exception as exc:  # noqa: BLE001 - last resort: one line, the type only, no leak
        print(f"error: unexpected ({type(exc).__name__})", file=sys.stderr)
        return EXIT_COULD_NOT_RUN


if __name__ == "__main__":
    sys.exit(main())
