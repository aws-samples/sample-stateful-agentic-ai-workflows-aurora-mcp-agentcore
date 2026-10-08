"""Deploy the AgentCore stack in the one order that works: Gateway first, stack, read back.

CloudFormation cannot change an existing Gateway's authorizer type: ``agentcore deploy`` fails with
"Authorizer type cannot be updated for an existing gateway" and the stack rolls back. The
interceptor cannot be declared in the template either, and an update that omits
``interceptorConfigurations`` detaches it. So the order is:

1. ``release_identity.py gateway`` moves the live Gateway with ``UpdateGateway`` (authorizer,
   allowed clients and interceptor in one call), so the rendered template already equals it.
2. ``deploy`` refuses unless the rendered ``agentcore.json`` and the live Gateway agree on the
   authorizer and the live Gateway already reports everything the release wants.
3. ``agentcore deploy -y`` runs (``/opt/homebrew/bin/agentcore`` only).
4. The Gateway is read back. Whether the deploy leaves the authorizer and the interceptor alone is
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
from scripts.identity_release import gateway_release, preflight, settings
from scripts.provision_service_logins import require_account

RENDERED = Path("agentcore") / "agentcore.json"
DEPLOY_ARGV = (settings.AGENTCORE_BIN, "deploy", "-y")
DEPLOY_TIMEOUT_SECONDS = 3600
TAIL_LINES = 30
OK, COULD_NOT_RUN, EXIT_REFUSED = 0, 2, 3
AUTHORIZER_FOR_MODE = {IAM: "AWS_IAM", JWT: "CUSTOM_JWT"}
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


def _rendered_pool(rendered: Mapping[str, Any]) -> tuple[Any, Any]:
    block = rendered.get("authorizerConfiguration")
    jwt = block.get("customJwtAuthorizer") if isinstance(block, Mapping) else None
    jwt = jwt if isinstance(jwt, Mapping) else {}
    return jwt.get("discoveryUrl"), jwt.get("allowedClients")


def _live_pool(live: Mapping[str, Any]) -> tuple[Any, Any]:
    block = live.get("authorizerConfiguration")
    jwt = block.get("customJWTAuthorizer") if isinstance(block, Mapping) else None
    jwt = jwt if isinstance(jwt, Mapping) else {}
    return jwt.get("discoveryUrl"), jwt.get("allowedClients")


def render_findings(rendered: Mapping[str, Any], wanted: preflight.Target) -> list[str]:
    """Where the rendered Gateway is not the one the release mode needs."""
    kind = AUTHORIZER_FOR_MODE[wanted.mode]
    if rendered.get("authorizerType") != kind:
        return [f"Rendered config: the Gateway authorizer is {rendered.get('authorizerType')}, "
                f"not {kind}; run python scripts/render_agentcore_config.py with "
                f"MERIDIAN_AGENTCORE_AUTH={wanted.mode}"]
    if wanted.mode != JWT:
        return []
    discovery, clients = _rendered_pool(rendered)
    if discovery != wanted.cognito.discovery_url or clients != [wanted.cognito.client_id]:
        return ["Rendered config: the Gateway's discoveryUrl or allowedClients is not this "
                "pool's; run python scripts/render_agentcore_config.py"]
    return []


def ordering_findings(rendered: Mapping[str, Any], live: Mapping[str, Any],
                      wanted: preflight.Target) -> list[str]:
    """Every reason ``agentcore deploy`` must not run now; empty when the order is right.

    The live Gateway must already equal the template's authorizer (CloudFormation refuses to
    change the type) and report everything the release wants, the interceptor included.
    """
    found = render_findings(rendered, wanted)
    command = (f"python scripts/release_identity.py gateway --to {wanted.mode} --apply "
               f"{settings.CONFIRM_FLAG}")
    live_type, rendered_type = live.get("authorizerType"), rendered.get("authorizerType")
    if live_type != rendered_type:
        found.append(
            f"the deploy would change the Gateway authorizer type from {live_type} to "
            f"{rendered_type}, and CloudFormation refuses that ('{CLI_ERROR}'); move the live "
            f"Gateway first with the UpdateGateway API: {command}")
    elif live_type == AUTHORIZER_FOR_MODE[JWT] and _live_pool(live) != _rendered_pool(rendered):
        found.append(
            "the deploy would change the Gateway's discoveryUrl or allowedClients; move the "
            f"live Gateway first: {command}")
    return found + preflight.check_gateway(live, wanted)


def _steps(wanted: preflight.Target) -> list[str]:
    reattach = "re-attach the interceptor" if wanted.interceptor_arn else "restore the authorizer"
    return [
        "1. before the deploy (checked now): the live Gateway already reports the "
        f"{AUTHORIZER_FOR_MODE[wanted.mode]} authorizer"
        + (" and the interceptor" if wanted.interceptor_arn else "")
        + ", equal to the rendered template",
        f"2. deploy: {' '.join(DEPLOY_ARGV)}   (run in meridian_agentcore)",
        "3. after the deploy: read the Gateway back; if the deploy changed the authorizer or "
        f"detached the interceptor, {reattach} with the gateway update and read it back again",
        "4. read every hop: python scripts/release_identity.py check --skip-service",
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
