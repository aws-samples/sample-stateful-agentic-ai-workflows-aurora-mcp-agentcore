"""Move the live Gateway between the IAM and the Cognito authorizer, with or without the pin.

``update_gateway`` replaces what it is given, so every update resends the fields the Gateway
already has and changes only the authorizer and the interceptor. The API refuses an empty
interceptor list, so a Gateway that should have none gets the field left out; the read-back
proves the omission detached it. A later ``agentcore deploy`` can reset the interceptor, so the
same read-back (``preflight.check_gateway``) is the drift check.

The Gateway may invoke the interceptor only after two grants, both written before the interceptor
is attached and removed after it is detached, and both separable from the move (``--only grant``):

* an inline policy on the Gateway's role (``MeridianTravelerPinInvoke``) that allows
  ``lambda:InvokeFunction`` on the interceptor and nothing else, as the throwaway-Gateway
  harness validated;
* a statement on the function's resource policy for the AgentCore service principal, limited by
  ``aws:SourceArn`` to this Gateway and by ``aws:SourceAccount`` to this account.

Changing the authorizer in place cuts off every SigV4 caller at once. Nothing here runs without
``--apply`` and the confirmation flag; the dry run reads and prints the before and after.

``read_snapshot`` returns the Gateway exactly as read; ``apply`` takes that object and hands it
back in its result, so the rollback task can save it before any write.
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
)
CHANGED_FIELDS = ("authorizerType", "authorizerConfiguration", "interceptorConfigurations")
READ_ONLY_FIELDS = (
    "gatewayId", "gatewayArn", "gatewayUrl", "status", "statusReasons", "createdAt", "updatedAt",
    "webAclArn", "workloadIdentityDetails",
)
REQUIRED_FIELDS = ("gatewayId", "gatewayArn", "name", "roleArn", "authorizerType", "status")
INVOKE_POLICY_NAME = "MeridianTravelerPinInvoke"
PERMISSION_ID = "MeridianGatewayInvoke"
GATEWAY_PRINCIPAL = "bedrock-agentcore.amazonaws.com"
FAILED = ("FAILED", "UPDATE_UNSUCCESSFUL", "CREATE_FAILED")
UPDATE_TIMEOUT_SECONDS = 300
SETTLE_ATTEMPTS = 12
POLL_SECONDS = 5
OUTPUT_NAME = "gateway-move.json"
ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
ONLY_CHOICES = ("grant", "move")


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


def update_arguments(current: Mapping[str, Any], wanted: preflight.Target) -> dict[str, Any]:
    """The ``update_gateway`` arguments that bring ``current`` to ``wanted``.

    Everything the Gateway already has is sent again, copied so the request never aliases
    ``current``; only the authorizer and the interceptor change.
    """
    arguments: dict[str, Any] = {"gatewayIdentifier": current["gatewayId"]}
    arguments.update({key: deepcopy(current[key]) for key in KEPT_FIELDS
                      if current.get(key) is not None})
    if wanted.mode != JWT:
        arguments["authorizerType"] = "AWS_IAM"
        return arguments
    arguments["authorizerType"] = "CUSTOM_JWT"
    arguments["authorizerConfiguration"] = {"customJWTAuthorizer": {
        "discoveryUrl": wanted.cognito.discovery_url, "allowedClients": [wanted.cognito.client_id]}}
    if settings.uses_interceptor(wanted.design):
        arguments["interceptorConfigurations"] = [{
            "interceptor": {"lambda": {"arn": wanted.interceptor_arn}},
            "interceptionPoints": ["REQUEST"],
            "inputConfiguration": {"passRequestHeaders": True},
        }]
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


def plan_problems(snapshot: Snapshot, wanted: preflight.Target) -> list[str]:
    """Findings that would remain if the update were sent: it would not reach ``wanted``."""
    arguments = update_arguments(snapshot.described, wanted)
    found = request_problems(arguments)
    if found:
        return [f"update_gateway request is invalid: {line}" for line in found]
    return [f"the update would leave: {line}"
            for line in preflight.check_gateway(predicted(snapshot.described, arguments), wanted)]


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


def _read_policy(lam: Any, function_arn: str) -> list[dict[str, Any]]:
    try:
        raw = lam.get_policy(FunctionName=function_arn)["Policy"]
    except ClientError as error:
        if _code(error) == "ResourceNotFoundException":
            return []
        raise
    try:
        statements = json.loads(raw)["Statement"]
    except (ValueError, KeyError, TypeError) as exc:
        raise GatewayError("the interceptor's resource policy is unreadable; fix it by hand "
                           "before the Gateway is moved") from exc
    return [s for s in statements if isinstance(s, dict)]


def _statement_matches(statement: Mapping[str, Any], function_arn: str, gateway_arn: str,
                       account: str) -> bool:
    condition = statement.get("Condition") or {}
    return (statement.get("Effect") == "Allow"
            and (statement.get("Principal") or {}).get("Service") == GATEWAY_PRINCIPAL
            and statement.get("Action") == "lambda:InvokeFunction"
            and statement.get("Resource") == function_arn
            and (condition.get("ArnLike") or {}).get("AWS:SourceArn") == gateway_arn
            and (condition.get("StringEquals") or {}).get("AWS:SourceAccount") == account)


def permission_findings(lam: Any, function_arn: str, gateway_arn: str,
                        account: str) -> list[str]:
    """Findings for the Gateway's statement on the interceptor's resource policy."""
    ours = [s for s in _read_policy(lam, function_arn) if s.get("Sid") == PERMISSION_ID]
    if not ours:
        return [f"Interceptor Lambda: its resource policy has no {PERMISSION_ID} statement for "
                "this Gateway"]
    if not _statement_matches(ours[0], function_arn, gateway_arn, account):
        return [f"Interceptor Lambda: {PERMISSION_ID} differs from allowing this Gateway "
                "(aws:SourceArn) to invoke it"]
    return []


def grant_findings(clients: Clients, role_arn: str, wanted: preflight.Target,
                   gateway_arn: str) -> list[str]:
    """Findings for both invoke grants (role policy and the function's resource policy)."""
    return (role_grant_findings(clients.iam, role_arn, wanted.interceptor_arn)
            + permission_findings(clients.lam, wanted.interceptor_arn, gateway_arn,
                                  wanted.account))


def grant_plan(role_arn: str, wanted: preflight.Target, gateway_arn: str) -> list[str]:
    """What ``grant`` would make sure of. Makes no AWS call."""
    return [
        f"Gateway role {role_name(role_arn)}: inline policy {INVOKE_POLICY_NAME} allows "
        "lambda:InvokeFunction on the interceptor and nothing else",
        f"Interceptor Lambda resource policy: {PERMISSION_ID} lets {GATEWAY_PRINCIPAL} invoke "
        f"it only for {gateway_arn} (aws:SourceArn) in this account",
    ]


def _grant_role(iam: Any, role_arn: str, function_arn: str) -> str:
    iam.put_role_policy(
        RoleName=role_name(role_arn), PolicyName=INVOKE_POLICY_NAME,
        PolicyDocument=json.dumps(invoke_policy(function_arn)))
    return f"Gateway role: may invoke the interceptor ({INVOKE_POLICY_NAME} written)"


def _grant_permission(lam: Any, wanted: preflight.Target, gateway_arn: str) -> str:
    function_arn = wanted.interceptor_arn
    ours = [s for s in _read_policy(lam, function_arn) if s.get("Sid") == PERMISSION_ID]
    if ours and _statement_matches(ours[0], function_arn, gateway_arn, wanted.account):
        return f"Interceptor Lambda: {PERMISSION_ID} unchanged"
    if ours:
        lam.remove_permission(FunctionName=function_arn, StatementId=PERMISSION_ID)
    lam.add_permission(
        FunctionName=function_arn, StatementId=PERMISSION_ID, Action="lambda:InvokeFunction",
        Principal=GATEWAY_PRINCIPAL, SourceArn=gateway_arn, SourceAccount=wanted.account)
    return f"Interceptor Lambda: {PERMISSION_ID} written for this Gateway only"


def grant(clients: Clients, role_arn: str, wanted: preflight.Target,
          gateway_arn: str) -> list[str]:
    """Let the Gateway invoke the interceptor (idempotent; role policy, then resource policy)."""
    return [_grant_role(clients.iam, role_arn, wanted.interceptor_arn),
            _grant_permission(clients.lam, wanted, gateway_arn)]


def revoke(clients: Clients, role_arn: str, function_arn: str) -> list[str]:
    """Remove both grants; one that is already gone is not an error."""
    notes = []
    try:
        clients.iam.delete_role_policy(
            RoleName=role_name(role_arn), PolicyName=INVOKE_POLICY_NAME)
    except ClientError as error:
        if _code(error) != "NoSuchEntity":
            raise
    notes.append(f"Gateway role: {INVOKE_POLICY_NAME} removed")
    try:
        clients.lam.remove_permission(FunctionName=function_arn, StatementId=PERMISSION_ID)
    except ClientError as error:
        if _code(error) != "ResourceNotFoundException":
            raise
    notes.append(f"Interceptor Lambda: {PERMISSION_ID} removed")
    return notes


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


def preconditions(clients: Clients, deps: Any, wanted: preflight.Target,
                  snapshot: Snapshot) -> list[str]:
    """Every reason the move must not be applied now; empty when it may proceed."""
    found = preflight.check_hop_locations(deps.env, wanted)
    found += plan_problems(snapshot, wanted)
    if wanted.mode != JWT:
        return found
    domain = (deps.env.get("VITE_COGNITO_DOMAIN") or "").strip()
    outputs = stack_outputs(clients.cfn, IDENTITY_STACK)
    found += preflight.identity_findings(outputs, wanted.cognito, domain)
    found += preflight.check_backend_login_proof(
        deps.proof_path, wanted, deps.head_sha(), deps.now())
    if settings.uses_interceptor(wanted.design):
        found += lambda_problems(clients.lam, wanted)
    return found


# --------------------------------------------------------------------- applying


def _wait_ready(control: Any, gateway_id: str, sleep: Callable[[float], None],
                clock: Callable[[], float]) -> dict[str, Any]:
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


def _read_back(control: Any, gateway_id: str, wanted: preflight.Target,
               sleep: Callable[[float], None],
               clock: Callable[[], float]) -> tuple[dict[str, Any], list[str]]:
    """READY, then the check; repeat for a while, since READY can come before UPDATING."""
    for attempt in range(SETTLE_ATTEMPTS):
        described = _wait_ready(control, gateway_id, sleep, clock)
        findings = preflight.check_gateway(described, wanted)
        if not findings:
            break
        if attempt < SETTLE_ATTEMPTS - 1:
            sleep(POLL_SECONDS)
    return described, findings


def _unchanged_since(control: Any, before: Snapshot, wanted: preflight.Target) -> None:
    fresh = read_snapshot(control, before.gateway_id, wanted)
    if update_arguments(fresh.described, wanted) != update_arguments(before.described, wanted):
        raise GatewayError("refusing: the Gateway changed after it was read; run the dry run "
                           "again to see the current difference")


def apply(clients: Clients, wanted: preflight.Target, before: Snapshot, *, only: str | None = None,
          sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic) -> ApplyResult:
    """Bring the Gateway to ``wanted`` (idempotent), read it back and return what was done.

    The caller read ``before`` with ``read_snapshot`` and may save it first. The grants are
    written before an interceptor is attached and removed only after the read-back shows the
    Gateway without one. ``only="grant"`` writes the grants and stops; ``only="move"`` requires
    them to be in place already.

    Raises:
        GatewayError: When the Gateway changed since ``before``, a move would attach the
            interceptor without its grants, or the update fails or times out.
    """
    control, gateway_id = clients.control, before.gateway_id
    role_arn = before.described["roleArn"]
    gateway_arn = before.described["gatewayArn"]
    attach = wanted.mode == JWT and settings.uses_interceptor(wanted.design)
    result = ApplyResult(before=before, after=None)
    _unchanged_since(control, before, wanted)
    if attach and only != "move":
        result.notes += grant(clients, role_arn, wanted, gateway_arn)
    if only == "grant":
        return result
    if attach:
        missing = grant_findings(clients, role_arn, wanted, gateway_arn)
        if missing:
            raise GatewayError("refusing: the Gateway cannot invoke the interceptor yet:\n  "
                               + "\n  ".join(missing))
    if preflight.check_gateway(before.described, wanted):
        control.update_gateway(**update_arguments(before.described, wanted))
        result.after, result.findings = _read_back(control, gateway_id, wanted, sleep, clock)
        result.notes.append(f"Gateway: updated to {wanted.mode}" if not result.findings
                            else f"Gateway: update sent for {wanted.mode}")
    else:
        result.after = before.described
        result.notes.append(f"Gateway: unchanged, already {wanted.mode}")
    if not attach and not result.findings and only != "move":
        function_arn = settings.interceptor_arn(wanted.account, wanted.region)
        result.notes += revoke(clients, role_arn, function_arn)
    return result


def record(directory: Path, result: ApplyResult, wanted: preflight.Target, at: datetime) -> Path:
    """Write what changed (masked summaries, not a rollback snapshot) as a private file (0600)."""
    payload = {
        "applied_at": at.isoformat(), "mode": wanted.mode, "design": wanted.design,
        "before": summarize(result.before.described),
        "after": summarize(result.after or {}), "findings": result.findings,
    }
    path = directory / OUTPUT_NAME
    write_private(path, scrub(json.dumps(payload, indent=2)) + "\n")
    return path


# ---------------------------------------------------------------------- the command


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The ``gateway`` command's flags."""
    parser.add_argument("--to", choices=(IAM, JWT),
                        help="the mode to move to instead of MERIDIAN_AGENTCORE_AUTH")
    parser.add_argument("--only", choices=ONLY_CHOICES,
                        help="grant: write only the invoke grants; move: change only the "
                             "Gateway (the grants must exist)")
    parser.add_argument("--apply", action="store_true", help="make the change (live)")
    parser.add_argument(settings.CONFIRM_FLAG, action="store_true", dest="confirmed",
                        help="required with --apply: it changes AWS")


def _blocked(found: list[str], say: Callable[[str], None]) -> None:
    for line in found:
        say(f"BLOCKED  {line}")


def _dry_run(wanted: preflight.Target, snapshot: Snapshot, found: list[str],
             say: Callable[[str], None]) -> int:
    described = snapshot.described
    arguments = update_arguments(described, wanted)
    say("DRY RUN. Nothing is changed.")
    for line in summary_lines("before", summarize(described)):
        say(line)
    for line in summary_lines("after ", summarize(predicted(described, arguments))):
        say(line)
    say("  resent unchanged: " + ", ".join(k for k in KEPT_FIELDS if k in arguments))
    changes = preflight.check_gateway(described, wanted)
    for line in changes or ["the Gateway already matches"]:
        say(f"  {'would change: ' if changes else ''}{line}")
    if wanted.mode == JWT and settings.uses_interceptor(wanted.design):
        for line in grant_plan(described["roleArn"], wanted, described["gatewayArn"]):
            say(f"  would ensure: {line}")
    _blocked(found, say)
    say("Apply (ASK FIRST): python scripts/release_identity.py gateway "
        f"--to {wanted.mode} --apply {settings.CONFIRM_FLAG}")
    return 1 if found else 0


def run(args: argparse.Namespace, deps: Any, *, say: Callable[[str], None]) -> int:
    """Plan, or apply and read back, the Gateway's move to a mode.

    ``deps`` provides ``env``, ``session``, ``now``, ``head_sha``, ``proof_path``,
    ``release_dir`` and ``sleep`` (the release CLI's ``Dependencies``).

    Raises:
        GatewayError: When a precondition fails or the update does not complete.
        ReleaseConfigError: When a setting is missing or malformed, or ``--only grant`` is
            asked for a design that attaches no interceptor.
    """
    env = deps.env
    mode = args.to or settings.release_mode(env)
    account, region = settings.deployment_target(env)
    wanted = preflight.target_for(mode, env, account, region)
    if args.apply and not args.confirmed:
        say(f"REFUSED: --apply also needs {settings.CONFIRM_FLAG}; it changes AWS.")
        return 3
    if args.only == "grant" and not (mode == JWT and settings.uses_interceptor(wanted.design)):
        raise settings.ReleaseConfigError("--only grant needs the jwt mode with a design that "
                                          "attaches the interceptor")
    if not settings.REGION.fullmatch(region):
        raise settings.ReleaseConfigError(
            "the Region in AURORA_CLUSTER_ARN is not a Region name; check meridian/.env")
    session = deps.session(region)
    require_account(session.client("sts"), env["AURORA_CLUSTER_ARN"])
    gateway_id, _ = preflight.hop_ids(env)
    clients = Clients(session.client(CONTROL_SERVICE), session.client("iam"),
                      session.client("lambda"),
                      session.client("cloudformation") if mode == JWT else None)
    snapshot = read_snapshot(clients.control, gateway_id, wanted)
    found = preconditions(clients, deps, wanted, snapshot)
    if not args.apply:
        return _dry_run(wanted, snapshot, found, say)
    if found:
        raise GatewayError("refusing: " + str(len(found)) + " precondition(s) not met:\n  "
                           + "\n  ".join(found))
    return _apply_and_report(clients, deps, wanted, snapshot, args.only, say)


def _apply_and_report(clients: Clients, deps: Any, wanted: preflight.Target, snapshot: Snapshot,
                      only: str | None, say: Callable[[str], None]) -> int:
    result = apply(clients, wanted, snapshot, only=only, sleep=deps.sleep)
    for note in result.notes:
        say(note)
    if only == "grant":
        return 0
    if wanted.mode == JWT and settings.uses_interceptor(wanted.design):
        result.findings += grant_findings(
            clients, snapshot.described["roleArn"], wanted, snapshot.described["gatewayArn"])
    record(deps.release_dir, result, wanted, deps.now())
    for line in result.findings:
        say(f"DRIFT  {line}")
    if not result.findings:
        say(f"OK  the Gateway reports {wanted.mode}")
    return 1 if result.findings else 0
