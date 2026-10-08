"""Deploy the AgentCore stack in the one order that works: Gateway first, stack, read back.

CloudFormation cannot change an existing Gateway's authorizer type: ``agentcore deploy`` fails with
"Authorizer type cannot be updated for an existing gateway" and the stack rolls back. It also
compares the template with the deployed stack template, not with the live Gateway. So the rendered
template keeps the Gateway resource on the stack's ``AWS_IAM`` in both modes, and the live
Gateway's authorizer and interceptor belong to ``release_identity.py gateway`` (``UpdateGateway``).
In ``jwt`` mode the live Gateway is CUSTOM_JWT while the template says AWS_IAM: that divergence is
deliberate and expected. The order is:

1. ``release_identity.py gateway`` moves the live Gateway with ``UpdateGateway`` (authorizer,
   allowed clients and interceptor in one call).
2. ``deploy`` refuses unless the rendered Gateway is the stack's (``AWS_IAM``, no JWT block) and the
   live Gateway already reports everything the mode wants, the interceptor included. The live
   Gateway is compared with the mode, never with the template.
3. ``agentcore deploy --diff --json`` runs; the command refuses if the plan changes the Gateway
   authorizer (see ``deploy_diff``).
4. ``agentcore deploy -y`` runs (``/opt/homebrew/bin/agentcore`` only).
5. The Gateway is read back. Whether the deploy leaves the authorizer and the interceptor alone is
   not documented offline, so it is never assumed: any difference is re-applied with the same
   update the ``gateway`` command sends, then read back again.

The same order runs in reverse for a rollback: the Gateway returns to IAM through the API first.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from argparse import Namespace
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from backend.agentcore.auth_mode import IAM, JWT
from scripts.identity_release import deploy_diff, gateway_release, preflight, settings
from scripts.provision_service_logins import require_account

RENDERED = Path("agentcore") / "agentcore.json"
DEPLOY_ARGV = (settings.AGENTCORE_BIN, "deploy", "-y")
DIFF_ARGV = (settings.AGENTCORE_BIN, "deploy", "--diff", "--json")
DEPLOY_TIMEOUT_SECONDS = 3600
TAIL_LINES = 30
OK, COULD_NOT_RUN, EXIT_REFUSED = 0, 2, 3
AUTHORIZER_FOR_MODE = {IAM: "AWS_IAM", JWT: "CUSTOM_JWT"}
STACK_AUTHORIZER = "AWS_IAM"
CLI_ERROR = "Authorizer type cannot be updated for an existing gateway"


class DeployOrderError(RuntimeError):
    """The deploy is not safe to run now, or could not be run."""


def run_command(argv: Sequence[str], cwd: Path) -> tuple[int, str]:
    """Run ``argv`` in ``cwd``; return its exit status and its output (both streams).

    Raises:
        DeployOrderError: When the program is missing or does not finish in time.
    """
    try:
        done = subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True,
                              timeout=DEPLOY_TIMEOUT_SECONDS, check=False)
    except FileNotFoundError as error:
        raise DeployOrderError(
            f"could not run {argv[0]}: not found; install the AgentCore CLI there (the "
            "nvm copy fails synth with a schema mismatch)") from error
    except subprocess.TimeoutExpired as error:
        raise DeployOrderError(
            f"{argv[0]} did not finish within {DEPLOY_TIMEOUT_SECONDS} s; the stack may still be "
            "updating, read it in CloudFormation before running anything else") from error
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def rendered_gateway(project_dir: Path) -> dict[str, Any]:
    """The Gateway the render wrote for the next deploy.

    Raises:
        DeployOrderError: When the render is missing, unreadable or has not exactly one Gateway.
    """
    path = project_dir / RENDERED
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DeployOrderError(
            "the rendered config cannot be read; run python scripts/render_agentcore_config.py "
            f"first ({type(error).__name__})") from error
    gateways = document.get("agentCoreGateways") if isinstance(document, Mapping) else None
    if not isinstance(gateways, list) or len(gateways) != 1 or not isinstance(gateways[0], dict):
        raise DeployOrderError(
            "the rendered config does not hold exactly one Gateway; run "
            "python scripts/render_agentcore_config.py")
    return gateways[0]


def render_findings(rendered: Mapping[str, Any]) -> list[str]:
    """Where the rendered Gateway is not the deployed stack's: ``AWS_IAM`` and no JWT block.

    The render keeps that in both modes; the live Gateway's authorizer is not the template's job.
    """
    if (rendered.get("authorizerType") == STACK_AUTHORIZER
            and "authorizerConfiguration" not in rendered):
        return []
    return [f"Rendered config: the Gateway is {rendered.get('authorizerType')}"
            + (" with an authorizerConfiguration" if "authorizerConfiguration" in rendered else "")
            + f", but the deployed stack has {STACK_AUTHORIZER} and CloudFormation cannot change "
            "the type; run python scripts/render_agentcore_config.py (it leaves the Gateway "
            "authorizer alone)"]


def ordering_findings(rendered: Mapping[str, Any], live: Mapping[str, Any],
                      wanted: preflight.Target) -> list[str]:
    """Every reason ``agentcore deploy`` must not run now; empty when the order is right.

    The rendered Gateway must be the stack's. The live Gateway is compared with the mode, not with
    the template (in ``jwt`` it is CUSTOM_JWT while the template says AWS_IAM, on purpose), and
    must already report everything the release wants, the interceptor included.
    """
    found = render_findings(rendered)
    live_type, kind = live.get("authorizerType"), AUTHORIZER_FOR_MODE[wanted.mode]
    if live_type != kind:
        command = (f"python scripts/release_identity.py gateway --to {wanted.mode} --apply "
                   f"{settings.CONFIRM_FLAG}")
        found.append(
            f"the live Gateway authorizer is {live_type}, not {kind}; CloudFormation would refuse "
            f"to change it ('{CLI_ERROR}'), so move the live Gateway first with the "
            f"UpdateGateway API: {command}")
    return found + preflight.check_gateway(live, wanted)


def _steps(wanted: preflight.Target) -> list[str]:
    reattach = "re-attach the interceptor" if wanted.interceptor_arn else "restore the authorizer"
    return [
        "1. before the deploy (checked now): the live Gateway already reports the "
        f"{AUTHORIZER_FOR_MODE[wanted.mode]} authorizer"
        + (" and the interceptor" if wanted.interceptor_arn else "")
        + f"; the rendered template keeps {STACK_AUTHORIZER} on purpose (the stack's value)",
        f"2. plan, refused if it changes the Gateway authorizer: {' '.join(DIFF_ARGV)}   "
        "(run in meridian_agentcore)",
        f"3. deploy: {' '.join(DEPLOY_ARGV)}   (run in meridian_agentcore)",
        "4. after the deploy: read the Gateway back; if the deploy changed the authorizer or "
        f"detached the interceptor, {reattach} with the gateway update and read it back again",
        "5. read every hop: python scripts/release_identity.py check --skip-service",
    ]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The ``deploy`` command's flags."""
    parser.add_argument("--to", choices=(IAM, JWT),
                        help="the mode the render and the Gateway must be in instead of "
                             "MERIDIAN_AGENTCORE_AUTH")
    parser.add_argument("--apply", action="store_true", help="deploy (live)")
    parser.add_argument(settings.CONFIRM_FLAG, action="store_true", dest="confirmed",
                        help="required with --apply: it changes AWS")


def _tail(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.strip()][-TAIL_LINES:]


def _check_plan(deps: Any, say: Callable[[str], None]) -> None:
    """Run the read-only plan and refuse when it changes the Gateway authorizer."""
    say(f"Reading the plan: {' '.join(DIFF_ARGV)} in meridian_agentcore.")
    code, output = deps.run_command(list(DIFF_ARGV), deps.agentcore_dir)
    found = deploy_diff.plan_findings(code, output, DIFF_ARGV)
    if found:
        raise DeployOrderError("refusing: the deploy plan is not safe to apply:\n  "
                               + "\n  ".join(found))


def _deploy(deps: Any, say: Callable[[str], None]) -> None:
    say(f"Running {' '.join(DEPLOY_ARGV)} in meridian_agentcore (this takes minutes).")
    code, output = deps.run_command(list(DEPLOY_ARGV), deps.agentcore_dir)
    for line in _tail(output):
        say(f"  {line}")
    if code == 0:
        return
    if CLI_ERROR in output:
        raise DeployOrderError(
            "CloudFormation refused the authorizer even though the live Gateway already matched "
            "the render: it compares the template with its own previous state, not with the "
            "live Gateway, so moving the Gateway first does not help. The stack rolled back by "
            "itself. Do not retry; roll back with release_identity.py rollback --apply and bring "
            "the stack-level fix to the owner decision (the Gateway cannot change authorizer "
            "type through CloudFormation)")
    raise DeployOrderError(
        f"the deploy failed (exit {code}). CloudFormation rolls the stack back by itself; the "
        "Gateway's authorizer and interceptor live outside the stack and were not touched by the "
        "rollback. The Runtimes still expect IAM while the Gateway expects the token, so the "
        "site stays down until a retry succeeds or release_identity.py rollback runs. Read "
        "every hop first: python scripts/release_identity.py check --skip-service")


def _read_back(control: Any, gateway_id: str, wanted: preflight.Target, deps: Any,
               say: Callable[[str], None]) -> int:
    described = gateway_release.wait_ready(control, gateway_id, deps.sleep, time.monotonic)
    found = preflight.check_gateway(described, wanted)
    if not found:
        say("OK  the deploy left the Gateway as the release wants (authorizer, allowed "
            "clients and interceptor read back)")
        return OK
    say("the deploy changed the Gateway:")
    for line in found:
        say(f"  DRIFT  {line}")
    say("Applying the gateway update again (the same call `gateway --apply` sends).")
    arguments = Namespace(to=wanted.mode, only=None, apply=True, confirmed=True)
    return gateway_release.run(arguments, deps, say=say)


def run(args: argparse.Namespace, deps: Any, say: Callable[[str], None]) -> int:
    """Plan, or run, the deploy in the safe order and read the Gateway back.

    ``deps`` also carries ``agentcore_dir`` and ``run_command`` (the release CLI's
    ``Dependencies``).

    Raises:
        DeployOrderError: When the order is wrong (apply), or the deploy fails.
        ReleaseConfigError: When a setting is missing or malformed.
    """
    env = deps.env
    mode = args.to or settings.release_mode(env)
    account, region = settings.deployment_target(env)
    wanted = preflight.target_for(mode, env, account, region)
    if args.apply and not args.confirmed:
        say(f"REFUSED: --apply also needs {settings.CONFIRM_FLAG}; it changes AWS.")
        return EXIT_REFUSED
    if not settings.REGION.fullmatch(region):
        raise settings.ReleaseConfigError(
            "the Region in AURORA_CLUSTER_ARN is not a Region name; check meridian/.env")
    rendered = rendered_gateway(deps.agentcore_dir)
    session = deps.session(region)
    require_account(session.client("sts"), env["AURORA_CLUSTER_ARN"])
    gateway_id, _ = preflight.hop_ids(env)
    control = session.client(gateway_release.CONTROL_SERVICE)
    found = ordering_findings(rendered, control.get_gateway(gatewayIdentifier=gateway_id), wanted)
    if not args.apply:
        return _dry_run(wanted, found, say)
    if found:
        raise DeployOrderError("refusing: " + str(len(found)) + " ordering problem(s):\n  "
                               + "\n  ".join(found))
    _check_plan(deps, say)
    _deploy(deps, say)
    return _read_back(control, gateway_id, wanted, deps, say)


def _dry_run(wanted: preflight.Target, found: list[str], say: Callable[[str], None]) -> int:
    say("DRY RUN. Nothing is deployed.")
    for line in _steps(wanted):
        say(f"  {line}")
    for line in found:
        say(f"BLOCKED  {line}")
    say(f"Deploy (ASK FIRST): python scripts/release_identity.py deploy --to {wanted.mode} "
        f"--apply {settings.CONFIRM_FLAG}")
    return COULD_NOT_RUN if found else OK
