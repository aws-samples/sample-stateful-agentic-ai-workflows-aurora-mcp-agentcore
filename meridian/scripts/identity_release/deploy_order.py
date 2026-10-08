"""Deploy the AgentCore stack one build stage at a time, checked against the live Gateway.

CloudFormation cannot change an existing Gateway's authorizer type (``agentcore deploy`` fails with
"Authorizer type cannot be updated for an existing gateway") and neither can ``UpdateGateway``, so
a mode switch builds a new Gateway under the mode's own name and the stack deletes the old one.
The stack cannot hold both (see ``stages.py``), so the new Gateway is built in four passes. This
command runs exactly one: the one the rendered ``agentcore.json`` is, which must also be the one
the CLI's deployed state and the live Gateway say is next. It is the only sanctioned way to run
``agentcore deploy -y`` in a release.

Before it runs anything it checks, and an apply refuses (nothing deployed) on any finding:

1. the rendered Gateway is the mode's (name, authorizer type and, for jwt, the pool);
2. the stage of the render equals the stage the deployed state implies;
3. the stage agrees with the live Gateway found by name: the first stage needs none to exist
   (otherwise the state file is stale and the plan would delete resources), the others need it;
4. in the first stage, when the other mode's Gateway exists and is about to be deleted: its role
   carries no invoke grant and it has no interceptor, and for jwt the identity stack, the backend
   login proof at HEAD and the interceptor function are in place, and a complete snapshot of the
   other mode exists, because the deletion cannot be undone.

Then it reads the plan (``agentcore deploy --diff --json``) and refuses one that is not the plan
of the stage (``deploy_diff``), runs ``agentcore deploy -y`` (``/opt/homebrew/bin/agentcore``
only), waits for the Gateway to be READY and reads it back. It never changes the Gateway itself:
the interceptor is attached afterwards by ``release_identity.py gateway``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from backend.agentcore.auth_mode import IAM, JWT
from scripts.identity_release import deploy_diff, gateway_release, preflight, settings, snapshot
from scripts.identity_release import stages
from scripts.provision_service_logins import require_account

RENDERED = Path("agentcore") / "agentcore.json"
TARGETS_FILE = Path("agentcore") / "aws-targets.json"
STATE_FILE = Path("agentcore") / ".cli" / "deployed-state.json"
DEPLOY_ARGV = (settings.AGENTCORE_BIN, "deploy", "-y")
DIFF_ARGV = (settings.AGENTCORE_BIN, "deploy", "--diff", "--json")
DEPLOY_TIMEOUT_SECONDS = 3600
TAIL_LINES = 30
OK, DRIFT, COULD_NOT_RUN, EXIT_REFUSED = 0, 1, 2, 3
OTHER_MODE = {IAM: JWT, JWT: IAM}
CLI_ERROR = "Authorizer type cannot be updated for an existing gateway"
RENDER = "python scripts/render_agentcore_config.py"
SYNC = "python scripts/sync_agentcore_env.py --write"


class DeployOrderError(RuntimeError):
    """The deploy is not safe to run now, or could not be run."""


def stage_label(stage: str) -> str:
    """``stage gateway (1 of 4)``."""
    return f"stage {stage} ({stages.STAGES.index(stage) + 1} of {len(stages.STAGES)})"


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


# ------------------------------------------------------------------ the files


def _read_json(path: Path, remedy: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DeployOrderError(
            f"{path.name} cannot be read ({type(error).__name__}); {remedy}") from error


def rendered_spec(project_dir: Path) -> dict[str, Any]:
    """The whole rendered ``agentcore.json``.

    Raises:
        DeployOrderError: When it is missing or unreadable, or has not exactly one Gateway.
    """
    document = _read_json(project_dir / RENDERED, f"run {RENDER} first")
    gateways = document.get("agentCoreGateways") if isinstance(document, Mapping) else None
    if not isinstance(gateways, list) or len(gateways) != 1 or not isinstance(gateways[0], dict):
        raise DeployOrderError(
            f"the rendered config does not hold exactly one Gateway; run {RENDER}")
    return document


def deployed_stage(project_dir: Path, mode: str) -> str:
    """The stage the CLI's deployed state implies for the mode's Gateway (none: the first).

    Raises:
        DeployOrderError: When the state or the targets file is not readable JSON.
    """
    targets = _read_json(project_dir / TARGETS_FILE, f"run {RENDER}")
    name = targets[0].get("name") if isinstance(targets, list) and targets \
        and isinstance(targets[0], dict) else None
    if not name:
        raise DeployOrderError("aws-targets.json names no deployment target; "
                               f"run {RENDER}")
    path = project_dir / STATE_FILE
    state = _read_json(path, "run agentcore deploy through this tool, or restore the file") \
        if path.exists() else {}
    ids = stages.deployed_ids(state, name, settings.gateway_logical_name(mode))
    return stages.stage_for(ids)


# --------------------------------------------------------------------- findings


def render_findings(rendered: Mapping[str, Any], wanted: preflight.Target) -> list[str]:
    """Where the rendered Gateway is not the mode's: its name, authorizer type and pool."""
    subject = "Rendered config: the Gateway"
    expected_name = settings.gateway_logical_name(wanted.mode)
    kind = "CUSTOM_JWT" if wanted.mode == JWT else "AWS_IAM"
    found = []
    if rendered.get("name") != expected_name:
        found.append(f"{subject} is named {rendered.get('name')!r}, expected {expected_name!r}; "
                     f"run {RENDER} in the {wanted.mode} mode")
    if rendered.get("authorizerType") != kind:
        found.append(f"{subject} authorizer is {rendered.get('authorizerType')}, expected "
                     f"{kind}; run {RENDER} in the {wanted.mode} mode")
    block = rendered.get("authorizerConfiguration")
    if wanted.mode != JWT:
        return found + ([f"{subject} keeps an authorizerConfiguration (a token authorizer "
                         f"block) in the iam mode; run {RENDER}"] if block else [])
    authorizer = block.get("customJwtAuthorizer") if isinstance(block, Mapping) else None
    if not isinstance(authorizer, Mapping):
        return found + [f"{subject} has no customJwtAuthorizer; run {RENDER}"]
    return found + [f"{line}; run {RENDER}"
                    for line in preflight.check_authorizer(subject, authorizer, wanted)]


def stage_findings(rendered: str, deployed: str, live: str | None, name: str) -> list[str]:
    """Where the render, the deployed state and the live Gateway disagree on the stage."""
    found = []
    if rendered != deployed:
        found.append(f"the render is {stage_label(rendered)} but the CLI's deployed state is at "
                     f"{stage_label(deployed)}: the render is stale (or the state is); run "
                     f"{RENDER} again")
    if rendered == stages.GATEWAY and live is not None:
        found.append("the render is the first stage, which builds the Gateway, but it already "
                     "exists: the deployed state file is stale and this plan would delete the "
                     "holds Lambda and the engine; run agentcore status, restore "
                     f"agentcore/.cli/deployed-state.json, then {RENDER} again")
    if rendered != stages.GATEWAY and live is None:
        found.append(f"the render is {stage_label(rendered)}, which needs the Gateway, but no "
                     f"Gateway named {name} exists yet; build the first stage first")
    return found


def residue_findings(control: Any, iam: Any, mode: str, gateway_id: str) -> list[str]:
    """What stops CloudFormation deleting the other mode's Gateway and its role cleanly."""
    described = control.get_gateway(gatewayIdentifier=gateway_id)
    role = described.get("roleArn") or ""
    command = (f"python scripts/release_identity.py gateway --to {mode} "
               + ("" if mode == IAM else "--only revoke ") + f"--apply {settings.CONFIRM_FLAG}")
    found = []
    if role and gateway_release.grant_present(iam, role):
        found.append(f"the {mode} Gateway's role still carries "
                     f"{gateway_release.INVOKE_POLICY_NAME}, which CloudFormation may refuse to "
                     f"delete with the role; run {command}")
    if described.get("interceptorConfigurations"):
        found.append(f"the {mode} Gateway still has an interceptor attached; run {command}")
    return found


def snapshot_findings(deps: Any, replaced: str) -> list[str]:
    """The replaced mode needs a complete snapshot: the deletion cannot be undone."""
    path, _ = snapshot.latest_complete(deps.release_dir)
    take = ("python scripts/release_identity.py snapshot --service-arn ARN "
            "(--accept-baseline when the hops already report findings)")
    if path is None:
        return [f"no complete snapshot of the {replaced} release exists, and the replaced "
                f"Gateway cannot be restored from anything else; run {take}"]
    mode = snapshot.load(path).get("mode")
    if mode != replaced:
        return [f"the newest complete snapshot is of the {mode} release, but the {replaced} "
                f"Gateway is the one being deleted; take one now: {take}"]
    return []


def jwt_findings(session: Any, deps: Any, wanted: preflight.Target) -> list[str]:
    """What the jwt first stage needs besides the Gateway: stack, proof and interceptor."""
    domain = (deps.env.get("VITE_COGNITO_DOMAIN") or "").strip()
    outputs = gateway_release.stack_outputs(
        session.client("cloudformation"), gateway_release.IDENTITY_STACK)
    found = preflight.identity_findings(outputs, wanted.cognito, domain)
    found += preflight.check_backend_login_proof(
        deps.proof_path, wanted, deps.head_sha(), deps.now())
    if settings.uses_interceptor(wanted.design):
        found += gateway_release.lambda_problems(session.client("lambda"), wanted)
    return found


def first_stage_findings(session: Any, deps: Any, control: Any,
                         wanted: preflight.Target) -> tuple[list[str], bool]:
    """The first stage's extra findings, and whether the other mode's Gateway is replaced."""
    other = OTHER_MODE[wanted.mode]
    other_id = preflight.find_gateway_id(control, settings.gateway_physical_name(other))
    found = []
    if wanted.mode == JWT:
        found += jwt_findings(session, deps, wanted)
    if other_id is not None:
        found += residue_findings(control, session.client("iam"), other, other_id)
        found += snapshot_findings(deps, other)
    return found, other_id is not None


# ------------------------------------------------------------------- the command


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The ``deploy`` command's flags."""
    parser.add_argument("--to", choices=(IAM, JWT),
                        help="the mode the render and the Gateway must be in instead of "
                             "MERIDIAN_AGENTCORE_AUTH")
    parser.add_argument("--apply", action="store_true", help="deploy one stage (live)")
    parser.add_argument(settings.CONFIRM_FLAG, action="store_true", dest="confirmed",
                        help="required with --apply: it changes AWS")


def _tail(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.strip()][-TAIL_LINES:]


def _check_plan(deps: Any, say: Callable[[str], None], stage: str, mode: str,
                replacing: bool) -> None:
    say(f"Reading the plan: {' '.join(DIFF_ARGV)} in meridian_agentcore.")
    code, output = deps.run_command(list(DIFF_ARGV), deps.agentcore_dir)
    found = deploy_diff.plan_findings(code, output, DIFF_ARGV, stage=stage, mode=mode,
                                      replacing=replacing)
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
            "CloudFormation refused to change a Gateway's authorizer type: the render did not "
            "rename the Gateway, so the template changed the existing one. The stack rolled "
            f"back by itself. Run {RENDER} in the release mode (it gives the jwt Gateway its own "
            "name) and start again from the first stage")
    raise DeployOrderError(
        f"the deploy failed (exit {code}). CloudFormation rolls the stack back by itself, so "
        "the stack is as it was before this stage. Read every hop first: python "
        "scripts/release_identity.py check --skip-service; then fix the cause, render again and "
        "re-run this stage")


def _read_back(control: Any, wanted: preflight.Target, stage: str, deps: Any,
               say: Callable[[str], None]) -> int:
    gateway_id = preflight.find_gateway_id(control, wanted.gateway_name)
    if gateway_id is None:
        say(f"DRIFT  no Gateway named {wanted.gateway_name} exists after the deploy")
        return DRIFT
    described = gateway_release.wait_ready(control, gateway_id, deps.sleep, time.monotonic)
    found = preflight.check_gateway(
        described, wanted, expect_interceptor=False,
        expect_engine=stage in (stages.GOVERNANCE, stages.COMPLETE))
    for line in found:
        say(f"DRIFT  {line}")
    return DRIFT if found else OK


def _next_steps(stage: str, wanted: preflight.Target, say: Callable[[str], None]) -> None:
    say(f"Stage {stage} deployed ({stages.STAGES.index(stage) + 1} of {len(stages.STAGES)}).")
    following = stages.next_stage(stage)
    if stage == stages.GATEWAY:
        say(f"Next: {SYNC}, so meridian/.env names the new Gateway.")
    if following:
        say(f"Next: {RENDER}")
        say(f"Then: python scripts/release_identity.py deploy --to {wanted.mode} --apply "
            f"{settings.CONFIRM_FLAG}   (stage {following})")
        return
    say(f"Next: {SYNC}")
    if wanted.mode == JWT and settings.uses_interceptor(wanted.design):
        say("Then attach the interceptor: python scripts/release_identity.py gateway --to jwt "
            f"--apply {settings.CONFIRM_FLAG}")
    say("Then: python scripts/release_identity.py check --skip-service")


def _dry_run(wanted: preflight.Target, stage: str, found: list[str],
             say: Callable[[str], None]) -> int:
    say("DRY RUN. Nothing is deployed.")
    say(f"  this is {stage_label(stage)} for the {wanted.mode} release")
    for line in _steps(stage):
        say(f"  {line}")
    for line in found:
        say(f"BLOCKED  {line}")
    say(f"Deploy (ASK FIRST): python scripts/release_identity.py deploy --to {wanted.mode} "
        f"--apply {settings.CONFIRM_FLAG}")
    return COULD_NOT_RUN if found else OK


def _steps(stage: str) -> list[str]:
    first = ("1. checked now: the render, the deployed state and the live Gateway agree on this "
             "stage" + ("; the other mode's Gateway has no grant or interceptor and a complete "
                        "snapshot exists; for jwt the identity stack, the backend login proof "
                        "and the interceptor function are in place"
                        if stage == stages.GATEWAY else ""))
    return [
        first,
        f"2. plan, refused unless it is this stage's plan: {' '.join(DIFF_ARGV)}   "
        "(run in meridian_agentcore)",
        f"3. deploy: {' '.join(DEPLOY_ARGV)}   (run in meridian_agentcore)",
        "4. read the Gateway back by name once it is READY (the interceptor is attached "
        "afterwards, by the gateway command)",
    ]


def run(args: argparse.Namespace, deps: Any, say: Callable[[str], None]) -> int:
    """Plan, or run, one build stage and read the Gateway back.

    ``deps`` also carries ``agentcore_dir`` and ``run_command`` (the release CLI's
    ``Dependencies``).

    Raises:
        DeployOrderError: When a check fails (apply), or the plan or the deploy fails.
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
    spec = rendered_spec(deps.agentcore_dir)
    stage = stages.stage_of_render(spec)
    session = deps.session(region)
    require_account(session.client("sts"), env["AURORA_CLUSTER_ARN"])
    control = session.client(gateway_release.CONTROL_SERVICE)
    live = preflight.find_gateway_id(control, wanted.gateway_name)
    found = render_findings(spec["agentCoreGateways"][0], wanted)
    found += stage_findings(stage, deployed_stage(deps.agentcore_dir, mode), live,
                            wanted.gateway_name)
    replacing = False
    if stage == stages.GATEWAY and live is None:
        extra, replacing = first_stage_findings(session, deps, control, wanted)
        found += extra
    if not args.apply:
        return _dry_run(wanted, stage, found, say)
    if found:
        raise DeployOrderError("refusing: " + str(len(found)) + " problem(s):\n  "
                               + "\n  ".join(found))
    _check_plan(deps, say, stage, mode, replacing)
    _deploy(deps, say)
    code = _read_back(control, wanted, stage, deps, say)
    if code == OK:
        _next_steps(stage, wanted, say)
    return code
