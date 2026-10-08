"""Attach and detach the request interceptor on the live jwt Gateway, with its invoke grant.

The Gateway's authorizer is not this module's business any more. CloudFormation and the
``UpdateGateway`` API both refuse to change an existing Gateway's authorizer type, so the jwt
Gateway is a different Gateway, created by ``release_identity.py deploy`` under its own name. This
module finds the Gateway of the release mode by that name (the id in ``meridian/.env`` goes stale
when a deploy replaces it), checks that it is a finished token Gateway, and then does the one thing
the template cannot: ``update_gateway`` replaces what it is given, so the update resends every
field the Gateway already has, authorizer included, and adds only ``interceptorConfigurations``.
The API refuses an empty interceptor list, so a detach leaves the field out; the read-back proves
the omission detached it. A later ``agentcore deploy`` that updates the Gateway can reset the
interceptor, so the read-back (``preflight.check_gateway``) is also the drift check.

The Gateway may invoke the interceptor only after one grant, written before the interceptor is
attached, removed after it is detached, and separable from the attach (``--only grant``): an inline
policy on the Gateway's role (``MeridianTravelerPinInvoke``) that allows ``lambda:InvokeFunction``
on the interceptor and nothing else, as the throwaway-Gateway harness validated. The AWS
documentation asks for nothing more in the same account: no statement on the function's resource
policy, so none is written, read or removed here. An inline policy written outside CloudFormation
can stop CloudFormation from deleting the role of a Gateway it replaces, so the old Gateway is
cleaned with ``revoke`` before the deploy that replaces it.

Nothing here runs without ``--apply`` and the confirmation flag; the dry run reads and prints the
before and after.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import botocore.session
from botocore.exceptions import ClientError
from botocore.validate import ParamValidator

from backend.agentcore.auth_mode import IAM, JWT
from scripts.gateway_harness.private_files import write_private
from scripts.gateway_harness.verdicts import JWT_SHAPE
from scripts.identity_release import interceptor_lambda, preflight, settings
from scripts.provision_service_logins import require_account
from scripts.sync_cognito_env import stack_outputs

IDENTITY_STACK = "MeridianIdentity"
CONTROL_SERVICE = "bedrock-agentcore-control"
KEPT_FIELDS = (
    "name", "roleArn", "description", "protocolType", "protocolConfiguration", "exceptionLevel",
    "kmsKeyArn", "policyEngineConfiguration", "customTransformConfiguration", "wafConfiguration",
    "authorizerType", "authorizerConfiguration",
)
CHANGED_FIELDS = ("interceptorConfigurations",)
READ_ONLY_FIELDS = (
    "gatewayId", "gatewayArn", "gatewayUrl", "status", "statusReasons", "createdAt", "updatedAt",
    "webAclArn", "workloadIdentityDetails", "ResponseMetadata",
)
REQUIRED_FIELDS = ("gatewayId", "gatewayArn", "name", "roleArn", "authorizerType", "status")
INVOKE_POLICY_NAME = "MeridianTravelerPinInvoke"
FAILED = ("FAILED", "UPDATE_UNSUCCESSFUL", "CREATE_FAILED")
UPDATE_TIMEOUT_SECONDS = 300
SETTLE_ATTEMPTS = 12
POLL_SECONDS = 5
OUTPUT_NAME = "gateway-interceptor.json"
ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
GRANT, ATTACH, REVOKE = "grant", "attach", "revoke"
FULL, NONE = "full", "none"
ONLY_CHOICES = (GRANT, ATTACH, REVOKE)
OK, DRIFT, COULD_NOT_RUN = 0, 1, 2


class GatewayError(RuntimeError):
    """The Gateway cannot be read, is not safe to change, or did not reach the wanted state."""


def scrub(text: str) -> str:
    """Hide 12-digit account ids and token-shaped strings."""
    return ACCOUNT_ID.sub("<acct>", JWT_SHAPE.sub("<token>", text))


@dataclass(frozen=True)
class Clients:
    """The three AWS clients the move uses, built only after the account guard passed."""

    control: Any
    iam: Any
    lam: Any
    cfn: Any = None


@dataclass(frozen=True)
class Snapshot:
    """The Gateway exactly as ``get_gateway`` returned it, before any change."""

    gateway_id: str
    described: dict[str, Any]


@dataclass
class ApplyResult:
    """What ``apply`` did: the snapshot it started from, the read-back, notes and findings."""

    before: Snapshot
    after: dict[str, Any] | None
    notes: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ the request


def role_name(role_arn: str) -> str:
    """The IAM role name at the end of a role ARN."""
    return role_arn.rsplit("/", 1)[-1]


def _gateway_arn(wanted: preflight.Target, gateway_id: str) -> str:
    return f"arn:aws:bedrock-agentcore:{wanted.region}:{wanted.account}:gateway/{gateway_id}"


def interceptor_entries(interceptor_arn: str) -> list[dict[str, Any]]:
    """The one request interceptor the release attaches: at REQUEST, with the headers."""
    return [{
        "interceptor": {"lambda": {"arn": interceptor_arn}},
        "interceptionPoints": ["REQUEST"],
        "inputConfiguration": {"passRequestHeaders": True},
    }]


def update_arguments(current: Mapping[str, Any], interceptor_arn: str | None) -> dict[str, Any]:
    """The ``update_gateway`` arguments that attach the interceptor, or detach it when ``None``.

    Everything the Gateway already has is sent again, the authorizer included and copied so the
    request never aliases ``current``; only ``interceptorConfigurations`` differs.
    """
    arguments: dict[str, Any] = {"gatewayIdentifier": current["gatewayId"]}
    arguments.update({key: deepcopy(current[key]) for key in KEPT_FIELDS
                      if current.get(key) is not None})
    if interceptor_arn:
        arguments["interceptorConfigurations"] = interceptor_entries(interceptor_arn)
    return arguments


def request_problems(arguments: Mapping[str, Any]) -> list[str]:
    """Where ``arguments`` is not a valid ``update_gateway`` call for the service model."""
    model = botocore.session.get_session().get_service_model(CONTROL_SERVICE)
    shape = model.operation_model("UpdateGateway").input_shape
    report = ParamValidator().validate(dict(arguments), shape)
    return [scrub(report.generate_report())] if report.has_errors() else []


def predicted(current: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The Gateway as ``get_gateway`` should describe it once ``arguments`` have been applied."""
    after = {key: deepcopy(value) for key, value in current.items()
             if key not in CHANGED_FIELDS}
    after.update({key: deepcopy(value) for key, value in arguments.items()
                  if key != "gatewayIdentifier"})
    after["status"] = "READY"
    return after


def summarize(config: Mapping[str, Any]) -> dict[str, Any]:
    """The fields a reader checks before and after: authorizer, allowed clients, interceptors."""
    block = config.get("authorizerConfiguration")
    jwt = block.get("customJWTAuthorizer") if isinstance(block, Mapping) else None
    clients = jwt.get("allowedClients") if isinstance(jwt, Mapping) else None
    interceptors = []
    for entry in config.get("interceptorConfigurations") or []:
        entry = entry if isinstance(entry, Mapping) else {}
        interceptors.append({
            "arn": ((entry.get("interceptor") or {}).get("lambda") or {}).get("arn"),
            "interceptionPoints": entry.get("interceptionPoints"),
            "passRequestHeaders": (entry.get("inputConfiguration") or {}).get(
                "passRequestHeaders"),
        })
    return {"authorizerType": config.get("authorizerType"),
            "allowedClients": list(clients) if isinstance(clients, list) else [],
            "interceptors": interceptors}


def summary_lines(label: str, summary: Mapping[str, Any]) -> list[str]:
    """Printable lines for one ``summarize`` result."""
    lines = [f"  {label}: authorizerType {summary['authorizerType']}",
             f"  {label}: allowedClients {summary['allowedClients'] or 'none'}"]
    if not summary["interceptors"]:
        lines.append(f"  {label}: interceptor none")
    for entry in summary["interceptors"]:
        lines.append(f"  {label}: interceptor {entry['arn']} at {entry['interceptionPoints']}")
        lines.append(f"  {label}: passRequestHeaders {entry['passRequestHeaders']}")
    return lines


def resolve_action(mode: str, only: str | None, design: str) -> str:
    """What the flags and the mode ask for: ``full``, ``grant``, ``attach``, ``revoke`` or ``none``.

    ``iam`` can only revoke (the iam Gateway has no interceptor, and a leftover grant is removed
    before a deploy replaces it). ``jwt`` attaches, unless the design uses no interceptor.

    Raises:
        ReleaseConfigError: When ``--only grant`` or ``--only attach`` is asked for a mode or
            design that attaches no interceptor.
    """
    if only == REVOKE or mode != JWT:
        if only in (GRANT, ATTACH):
            raise settings.ReleaseConfigError(
                f"--only {only} needs the jwt mode with a design that attaches the interceptor")
        return REVOKE
    if not settings.uses_interceptor(design):
        if only:
            raise settings.ReleaseConfigError(
                f"--only {only} needs the jwt mode with a design that attaches the interceptor")
        return NONE
    return {None: FULL, GRANT: GRANT, ATTACH: ATTACH}[only]


def locate(control: Any, wanted: preflight.Target) -> Snapshot:
    """Read the Gateway the mode's name designates, in full.

    Raises:
        GatewayError: When no Gateway carries the name, or it cannot be read in full.
    """
    gateway_id = preflight.find_gateway_id(control, wanted.gateway_name)
    if gateway_id is None:
        raise GatewayError(
            f"no Gateway named {wanted.gateway_name} exists; the {wanted.mode} release's Gateway "
            "is created by `python scripts/release_identity.py deploy`, not by this command")
    return read_snapshot(control, gateway_id, wanted)


# ------------------------------------------------------------------- the snapshot


def snapshot_problems(described: Any, gateway_id: str, wanted: preflight.Target) -> list[str]:
    """Why ``described`` cannot be treated as a full, safe-to-replace Gateway description."""
    if not isinstance(described, Mapping):
        return [f"Gateway: the description is unreadable (got {type(described).__name__})"]
    missing = [key for key in REQUIRED_FIELDS if not described.get(key)]
    if missing:
        return [f"Gateway: the description lacks {', '.join(missing)}"]
    found = []
    if described["gatewayId"] != gateway_id:
        found.append("Gateway: the description is for another Gateway than the configured one")
    if described["gatewayArn"] != _gateway_arn(wanted, gateway_id):
        found.append("Gateway: its ARN is not in the deployment's account and Region")
    role = str(described["roleArn"]).split(":")
    if len(role) < 6 or role[4] != wanted.account:
        found.append("Gateway: its role is not in the deployment's account")
    if described["status"] != "READY":
        found.append(f"Gateway: status is {described['status']}; wait for READY before changing it")
    known = set(KEPT_FIELDS) | set(CHANGED_FIELDS) | set(READ_ONLY_FIELDS)
    unknown = sorted(key for key, value in described.items() if key not in known and value)
    if unknown:
        found.append("Gateway: has fields this tool does not resend, so the replace could drop "
                     f"them: {', '.join(unknown)}")
    return found


def read_snapshot(control: Any, gateway_id: str, wanted: preflight.Target) -> Snapshot:
    """Read the Gateway in full.

    Raises:
        GatewayError: When it cannot be read in full, or is not READY.
    """
    described = control.get_gateway(gatewayIdentifier=gateway_id)
    problems = snapshot_problems(described, gateway_id, wanted)
    if problems:
        raise GatewayError("refusing: the Gateway cannot be read in full:\n  "
                           + "\n  ".join(problems))
    return Snapshot(gateway_id, deepcopy(dict(described)))


# ------------------------------------------------------------------ the grants


def invoke_policy(function_arn: str) -> dict[str, Any]:
    """Allow the Gateway's role to invoke the interceptor function, and only that."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow", "Action": "lambda:InvokeFunction",
            "Resource": [function_arn, f"{function_arn}:*"],
        }],
    }


def _code(error: ClientError) -> str:
    return error.response.get("Error", {}).get("Code", "")


def _same_policy(found: Any, function_arn: str) -> bool:
    wanted = invoke_policy(function_arn)
    if not isinstance(found, Mapping):
        return False
    statements = found.get("Statement")
    if not isinstance(statements, list) or len(statements) != 1:
        return False
    statement = dict(statements[0])
    resources = statement.get("Resource")
    statement["Resource"] = sorted(resources) if isinstance(resources, list) else resources
    expected = dict(wanted["Statement"][0])
    expected["Resource"] = sorted(expected["Resource"])
    return statement == expected and found.get("Version") == wanted["Version"]


def grant_present(iam: Any, role_arn: str) -> bool:
    """Whether the Gateway's role carries the invoke policy, whatever it says."""
    try:
        iam.get_role_policy(RoleName=role_name(role_arn), PolicyName=INVOKE_POLICY_NAME)
    except ClientError as error:
        if _code(error) == "NoSuchEntity":
            return False
        raise
    return True


def role_grant_findings(iam: Any, role_arn: str, function_arn: str) -> list[str]:
    """Findings for the invoke policy on the Gateway's role."""
    subject = f"Gateway role {role_name(role_arn)}"
    try:
        document = iam.get_role_policy(
            RoleName=role_name(role_arn), PolicyName=INVOKE_POLICY_NAME)["PolicyDocument"]
    except ClientError as error:
        if _code(error) == "NoSuchEntity":
            return [f"{subject}: has no {INVOKE_POLICY_NAME} policy, so the Gateway cannot "
                    "invoke the interceptor"]
        raise
    if not _same_policy(document, function_arn):
        return [f"{subject}: {INVOKE_POLICY_NAME} differs from invoking the traveler-pin "
                "function only"]
    return []


def grant_findings(clients: Clients, role_arn: str, wanted: preflight.Target) -> list[str]:
    """Findings for the invoke grant (the policy on the Gateway's role)."""
    return role_grant_findings(clients.iam, role_arn, wanted.interceptor_arn)


def grant_plan(role_arn: str, wanted: preflight.Target) -> list[str]:
    """What ``grant`` would make sure of. Makes no AWS call."""
    return [f"Gateway role {role_name(role_arn)}: inline policy {INVOKE_POLICY_NAME} allows "
            "lambda:InvokeFunction on the interceptor and nothing else"]


def grant(clients: Clients, role_arn: str, wanted: preflight.Target) -> list[str]:
    """Let the Gateway invoke the interceptor (idempotent: the role's inline policy)."""
    clients.iam.put_role_policy(
        RoleName=role_name(role_arn), PolicyName=INVOKE_POLICY_NAME,
        PolicyDocument=json.dumps(invoke_policy(wanted.interceptor_arn)))
    return [f"Gateway role: may invoke the interceptor ({INVOKE_POLICY_NAME} written)"]


def revoke(clients: Clients, role_arn: str) -> list[str]:
    """Remove the grant; one that is already gone is not an error."""
    try:
        clients.iam.delete_role_policy(
            RoleName=role_name(role_arn), PolicyName=INVOKE_POLICY_NAME)
    except ClientError as error:
        if _code(error) != "NoSuchEntity":
            raise
    return [f"Gateway role: {INVOKE_POLICY_NAME} removed"]


# ------------------------------------------------------------ the preconditions


def lambda_problems(lam: Any, wanted: preflight.Target) -> list[str]:
    """The interceptor must exist, carry the release tags and have the expected environment."""
    try:
        found = lam.get_function(FunctionName=wanted.interceptor_arn)
    except ClientError as error:
        if _code(error) != "ResourceNotFoundException":
            raise
        return ["Interceptor Lambda: not deployed; run scripts/release_identity.py interceptor "
                f"--apply {settings.CONFIRM_FLAG}"]
    tags = found.get("Tags") if isinstance(found, Mapping) else None
    if not all((tags or {}).get(key) == value for key, value in interceptor_lambda.TAGS.items()):
        return ["Interceptor Lambda: exists without the release tags, so it is not ours"]
    return preflight.interceptor_environment_findings(found.get("Configuration"), wanted)


def preconditions(clients: Clients, deps: Any, wanted: preflight.Target, snapshot: Snapshot,
                  action: str) -> list[str]:
    """Every reason the command must not be applied now; empty when it may proceed.

    A revoke asks nothing of the pool. Anything else needs a finished token Gateway (judged
    without its interceptor, so a wrong one can be replaced), the identity stack and, under a
    design with an interceptor, the deployed function.
    """
    found = preflight.check_hop_locations(deps.env, wanted)
    if action == REVOKE:
        return found
    bare = {key: value for key, value in snapshot.described.items()
            if key not in CHANGED_FIELDS}
    found += preflight.check_gateway(bare, wanted, expect_interceptor=False)
    domain = (deps.env.get("VITE_COGNITO_DOMAIN") or "").strip()
    outputs = stack_outputs(clients.cfn, IDENTITY_STACK)
    found += preflight.identity_findings(outputs, wanted.cognito, domain)
    if action != NONE:
        found += lambda_problems(clients.lam, wanted)
    return found


# --------------------------------------------------------------------- applying


def wait_ready(control: Any, gateway_id: str, sleep: Callable[[float], None],
               clock: Callable[[], float]) -> dict[str, Any]:
    """Poll until the Gateway is READY.

    Raises:
        GatewayError: When it ends in a failed state or is not READY within the timeout.
    """
    deadline = clock() + UPDATE_TIMEOUT_SECONDS
    while True:
        described = control.get_gateway(gatewayIdentifier=gateway_id)
        status = described.get("status")
        if status == "READY":
            return described
        if status in FAILED:
            reasons = scrub("; ".join(str(r) for r in described.get("statusReasons") or []))
            raise GatewayError(f"the Gateway ended in {status}: {reasons}")
        if clock() > deadline:
            raise GatewayError(f"the Gateway is still {status} after {UPDATE_TIMEOUT_SECONDS} s")
        sleep(POLL_SECONDS)


def _read_back(control: Any, gateway_id: str, judge: Callable[[Mapping[str, Any]], list[str]],
               sleep: Callable[[float], None],
               clock: Callable[[], float]) -> tuple[dict[str, Any], list[str]]:
    """READY, then ``judge``; repeat for a while, since READY can come before UPDATING."""
    for attempt in range(SETTLE_ATTEMPTS):
        described = wait_ready(control, gateway_id, sleep, clock)
        findings = judge(described)
        if not findings:
            break
        if attempt < SETTLE_ATTEMPTS - 1:
            sleep(POLL_SECONDS)
    return described, findings


def _view(described: Mapping[str, Any]) -> dict[str, Any]:
    return {key: described.get(key) for key in (*KEPT_FIELDS, *CHANGED_FIELDS)}


def _unchanged_since(control: Any, before: Snapshot, wanted: preflight.Target) -> None:
    fresh = read_snapshot(control, before.gateway_id, wanted)
    if _view(fresh.described) != _view(before.described):
        raise GatewayError("refusing: the Gateway changed after it was read; run the dry run "
                           "again to see the current difference")


def _attached(described: Mapping[str, Any], wanted: preflight.Target) -> bool:
    return (described.get("interceptorConfigurations") or []) == interceptor_entries(
        wanted.interceptor_arn or "")


def _detached_findings(described: Mapping[str, Any]) -> list[str]:
    if described.get("interceptorConfigurations"):
        return ["Gateway: an interceptor is still attached after the update"]
    return []


def _attach(clients: Clients, wanted: preflight.Target, before: Snapshot, result: ApplyResult,
            wait: Callable[..., Any]) -> None:
    control, gateway_id = clients.control, before.gateway_id
    if _attached(before.described, wanted):
        result.after = before.described
        result.notes.append("Gateway: interceptor already attached")
        return
    control.update_gateway(**update_arguments(before.described, wanted.interceptor_arn))
    result.after, result.findings = _read_back(
        control, gateway_id, lambda described: preflight.check_gateway(described, wanted),
        *wait)
    result.notes.append("Gateway: update sent" if result.findings
                        else "Gateway: interceptor attached")


def _detach(clients: Clients, before: Snapshot, result: ApplyResult,
            wait: Callable[..., Any]) -> None:
    control = clients.control
    if not before.described.get("interceptorConfigurations"):
        result.after = before.described
        result.notes.append("Gateway: no interceptor attached")
        return
    control.update_gateway(**update_arguments(before.described, None))
    result.after, result.findings = _read_back(
        control, before.gateway_id, _detached_findings, *wait)
    result.notes.append("Gateway: interceptor detached" if not result.findings
                        else "Gateway: update sent")


def apply(clients: Clients, wanted: preflight.Target, before: Snapshot, action: str, *,
          sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic) -> ApplyResult:
    """Do ``action`` (idempotent), read the Gateway back and return what was done.

    The caller read ``before`` with ``locate`` and may save it first. The grant is written before
    an interceptor is attached and removed only after the read-back shows the Gateway without one.

    Raises:
        GatewayError: When the Gateway changed since ``before``, an attach would run without its
            grant, or the update fails or times out.
    """
    role_arn = before.described["roleArn"]
    result = ApplyResult(before=before, after=None)
    wait = (sleep, clock)
    _unchanged_since(clients.control, before, wanted)
    if action == NONE:
        result.notes.append("Gateway: this design attaches no interceptor, nothing to do")
        return result
    if action in (FULL, GRANT):
        result.notes += grant(clients, role_arn, wanted)
    if action == GRANT:
        return result
    if action in (FULL, ATTACH):
        missing = grant_findings(clients, role_arn, wanted)
        if missing:
            raise GatewayError("refusing: the Gateway cannot invoke the interceptor yet:\n  "
                               + "\n  ".join(missing))
        _attach(clients, wanted, before, result, wait)
        return result
    _detach(clients, before, result, wait)
    if not result.findings:
        result.notes += revoke(clients, role_arn)
    return result


def record(directory: Path, result: ApplyResult, wanted: preflight.Target, action: str,
           at: datetime) -> Path:
    """Write what changed (masked summaries, not a rollback snapshot) as a private file (0600)."""
    payload = {
        "applied_at": at.isoformat(), "mode": wanted.mode, "design": wanted.design,
        "action": action, "before": summarize(result.before.described),
        "after": summarize(result.after or {}), "findings": result.findings,
    }
    path = directory / OUTPUT_NAME
    write_private(path, scrub(json.dumps(payload, indent=2)) + "\n")
    return path


# ---------------------------------------------------------------------- the command


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The ``gateway`` command's flags."""
    parser.add_argument("--to", choices=(IAM, JWT),
                        help="the Gateway to work on: the mode's instead of "
                             "MERIDIAN_AGENTCORE_AUTH's; iam only revokes")
    parser.add_argument("--only", choices=ONLY_CHOICES,
                        help="grant: write only the invoke grant; attach: attach the interceptor "
                             "(the grant must exist); revoke: detach the interceptor and remove "
                             "the grant")
    parser.add_argument("--apply", action="store_true", help="make the change (live)")
    parser.add_argument(settings.CONFIRM_FLAG, action="store_true", dest="confirmed",
                        help="required with --apply: it changes AWS")


def _blocked(found: list[str], say: Callable[[str], None]) -> None:
    for line in found:
        say(f"BLOCKED  {line}")


def _planned_arguments(wanted: preflight.Target, snapshot: Snapshot,
                       action: str) -> dict[str, Any] | None:
    if action in (FULL, ATTACH):
        return update_arguments(snapshot.described, wanted.interceptor_arn)
    if action == REVOKE and snapshot.described.get("interceptorConfigurations"):
        return update_arguments(snapshot.described, None)
    return None


def _plan(wanted: preflight.Target, snapshot: Snapshot, action: str,
          say: Callable[[str], None]) -> None:
    described = snapshot.described
    arguments = _planned_arguments(wanted, snapshot, action)
    if arguments is None:
        say("  the Gateway itself is not updated")
        return
    for line in summary_lines("before", summarize(described)):
        say(line)
    for line in summary_lines("after ", summarize(predicted(described, arguments))):
        say(line)
    say("  resent unchanged: " + ", ".join(k for k in KEPT_FIELDS if k in arguments))


def _grant_lines(wanted: preflight.Target, snapshot: Snapshot, action: str,
                 say: Callable[[str], None]) -> None:
    role_arn = snapshot.described["roleArn"]
    if action in (FULL, GRANT):
        for line in grant_plan(role_arn, wanted):
            say(f"  would ensure: {line}")
    if action == REVOKE:
        say(f"  would remove: Gateway role {role_name(role_arn)}: inline policy "
            f"{INVOKE_POLICY_NAME} (if present)")


def _dry_run(wanted: preflight.Target, snapshot: Snapshot, found: list[str],
             action: str, only: str | None, say: Callable[[str], None]) -> int:
    say("DRY RUN. Nothing is changed.")
    if action == NONE:
        say("  this design attaches no interceptor, so there is nothing to do")
    else:
        _plan(wanted, snapshot, action, say)
        _grant_lines(wanted, snapshot, action, say)
    _blocked(found, say)
    only_flag = f" --only {only}" if only else ""
    say("Apply (ASK FIRST): python scripts/release_identity.py gateway "
        f"--to {wanted.mode}{only_flag} --apply {settings.CONFIRM_FLAG}")
    return COULD_NOT_RUN if found else OK


def run(args: argparse.Namespace, deps: Any, *, say: Callable[[str], None]) -> int:
    """Plan, or apply and read back, the interceptor on the mode's Gateway.

    ``deps`` provides ``env``, ``session``, ``now``, ``head_sha``, ``release_dir`` and ``sleep``
    (the release CLI's ``Dependencies``).

    Raises:
        GatewayError: When no Gateway has the mode's name, a precondition fails or the update
            does not complete.
        ReleaseConfigError: When a setting is missing or malformed, or ``--only grant`` or
            ``--only attach`` is asked for a mode or design that attaches no interceptor.
    """
    env = deps.env
    mode = args.to or settings.release_mode(env)
    account, region = settings.deployment_target(env)
    wanted = preflight.target_for(mode, env, account, region)
    if args.apply and not args.confirmed:
        say(f"REFUSED: --apply also needs {settings.CONFIRM_FLAG}; it changes AWS.")
        return 3
    action = resolve_action(mode, args.only, wanted.design)
    if not settings.REGION.fullmatch(region):
        raise settings.ReleaseConfigError(
            "the Region in AURORA_CLUSTER_ARN is not a Region name; check meridian/.env")
    session = deps.session(region)
    require_account(session.client("sts"), env["AURORA_CLUSTER_ARN"])
    needs_stack = mode == JWT and action != REVOKE
    clients = Clients(session.client(CONTROL_SERVICE), session.client("iam"),
                      session.client("lambda"),
                      session.client("cloudformation") if needs_stack else None)
    snapshot = locate(clients.control, wanted)
    found = preconditions(clients, deps, wanted, snapshot, action)
    if not args.apply:
        return _dry_run(wanted, snapshot, found, action, args.only, say)
    if found:
        raise GatewayError("refusing: " + str(len(found)) + " precondition(s) not met:\n  "
                           + "\n  ".join(found))
    return _apply_and_report(clients, deps, wanted, snapshot, action, say)


def _apply_and_report(clients: Clients, deps: Any, wanted: preflight.Target, snapshot: Snapshot,
                      action: str, say: Callable[[str], None]) -> int:
    result = apply(clients, wanted, snapshot, action, sleep=deps.sleep)
    for note in result.notes:
        say(note)
    if action in (FULL, ATTACH, GRANT):
        result.findings += grant_findings(clients, snapshot.described["roleArn"], wanted)
    for line in result.findings:
        say(f"DRIFT  {line}")
    if not result.findings:
        say(_ok_line(action, wanted))
    if action not in (GRANT, NONE):
        _record_or_warn(deps, result, wanted, action, say)
    return DRIFT if result.findings else OK


def _ok_line(action: str, wanted: preflight.Target) -> str:
    return {GRANT: "OK  the grant is in place",
            REVOKE: "OK  the Gateway has no interceptor and its role no grant",
            NONE: f"OK  the {wanted.mode} Gateway needs no interceptor"}.get(
                action, "OK  the Gateway has the interceptor and its role the grant")


def _record_or_warn(deps: Any, result: ApplyResult, wanted: preflight.Target, action: str,
                    say: Callable[[str], None]) -> None:
    try:
        record(deps.release_dir, result, wanted, action, deps.now())
    except OSError as error:
        say(f"WARNING  the change record was not written ({type(error).__name__}: "
            f"{scrub(str(error))}); the Gateway was changed as reported above")
