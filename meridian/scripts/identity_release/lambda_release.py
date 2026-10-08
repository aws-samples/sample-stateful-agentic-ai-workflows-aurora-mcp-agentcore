"""Check, and restart, the Lambdas that move from the master login to the meridian_gateway login.

Three things carry the master login today: the SSM parameter the ``MeridianHolds`` Lambda reads at
its first call after a cold start, the ``meridian-semantic-trip-search`` Lambda's environment (set
by hand, outside the CDK app), and both roles' ``GetSecretValue`` grants. The stages are:

``master``     the baseline: nothing has moved.
``gateway``    the parameter and the semantic Lambda name the gateway login's secret, and both
               roles can read it. The master grant may remain, so a rollback still works.
``tightened``  as ``gateway``, and no role can read the master login's secret any more.

A grant counts when an Allow statement lets the role call ``secretsmanager:GetSecretValue`` (a
wildcard action counts) on a resource pattern that matches the secret's ARN (``*`` matches every
secret). An Allow with ``NotAction`` counts unless a ``NotAction`` pattern covers that action; an
Allow with ``NotResource`` counts unless a ``NotResource`` pattern matches the secret. Conditions
and Deny statements are not evaluated, so the answer errs toward "still can read". Inline,
customer-managed and AWS-managed attached policies are all read.

The holds Lambda caches its configuration for the life of an execution environment, so a changed
parameter takes effect only after a restart; ``restart_holds`` forces one by changing an
environment variable.

Every call is a read (``ssm:GetParameter``, ``lambda:GetFunctionConfiguration``,
``iam:ListRolePolicies``, ``iam:GetRolePolicy``, ``iam:ListAttachedRolePolicies``,
``iam:GetPolicy`` and ``iam:GetPolicyVersion`` (also for AWS-managed policies),
``bedrock-agentcore:ListGatewayTargets``, ``bedrock-agentcore:GetGatewayTarget``) except
``restart_holds``, which makes one ``lambda:UpdateFunctionConfiguration`` on the holds function
after proving it is this project's.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError, WaiterError

from scripts.identity_release import preflight, settings
from scripts.provision_service_logins import require_account

HOLDS_TARGET = "MeridianHolds"
SEMANTIC_FUNCTION = "meridian-semantic-trip-search"
SSM_SECRET_PARAMETER = "/meridian/aurora/secret_arn"
MARKER = "MERIDIAN_COLD_START"
STAGES = ("master", "gateway", "tightened")
READ_ACTION = "secretsmanager:getsecretvalue"
STAMP = "%Y%m%dT%H%M%SZ"
WAIT = {"Delay": 5, "MaxAttempts": 24}
HOLDS_NAME = re.compile(r"^AgentCore-[A-Za-z0-9_-]+-MeridianHolds[A-Za-z0-9]*$")
FUNCTION_ARN = re.compile(r"^arn:aws:lambda:(?P<region>[^:]+):(?P<account>\d{12}):function:"
                          r"(?P<name>[A-Za-z0-9_-]+)$")
OK, DRIFT, REFUSED = 0, 1, 3


class LambdaError(settings.ReleaseConfigError):
    """The Lambda or its target could not be found, or is not this project's holds function."""


@dataclass(frozen=True)
class Secrets:
    """The two login secrets this release moves between."""

    master: str
    gateway: str


def holds_function_arn(control: Any, gateway_id: str) -> str:
    """The MeridianHolds Lambda's ARN, read from the Gateway's own target.

    Raises:
        LambdaError: When the Gateway has no MeridianHolds target.
    """
    token = None
    while True:
        extra = {"nextToken": token} if token else {}
        page = control.list_gateway_targets(gatewayIdentifier=gateway_id, **extra)
        found = next((t for t in page.get("items", []) if t.get("name") == HOLDS_TARGET), None)
        token = page.get("nextToken")
        if found is not None or not token:
            break
    if found is None:
        raise LambdaError(f"the Gateway has no {HOLDS_TARGET} target; check the gateway ID")
    target = control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=found["targetId"])
    mcp = (target.get("targetConfiguration") or {}).get("mcp") or {}
    arn = (mcp.get("lambda") or {}).get("lambdaArn")
    if not arn:
        raise LambdaError(f"the {HOLDS_TARGET} target is not a Lambda target (it has no "
                          "mcp.lambda.lambdaArn), so there is no function to check")
    return arn


def _as_list(value: Any) -> list[str]:
    return [value] if isinstance(value, str) else list(value or [])


def _matches(pattern: str, text: str) -> bool:
    """IAM wildcard match: ``*`` and ``?`` only, case-sensitive."""
    regex = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
    return re.fullmatch(regex, text) is not None


@dataclass(frozen=True)
class Grant:
    """Resources an Allow statement opens: those matching ``include`` but not ``exclude``."""

    include: tuple[str, ...]
    exclude: tuple[str, ...] = ()

    def covers(self, secret_arn: str) -> bool:
        """Whether the statement lets the role read ``secret_arn``."""
        return (any(_matches(p, secret_arn) for p in self.include)
                and not any(_matches(p, secret_arn) for p in self.exclude))


def _reads_secret(statement: Mapping[str, Any]) -> bool:
    if "NotAction" in statement:
        excluded = [a.lower() for a in _as_list(statement["NotAction"])]
        return not any(_matches(a, READ_ACTION) for a in excluded)
    actions = [a.lower() for a in _as_list(statement.get("Action"))]
    return any(_matches(a, READ_ACTION) for a in actions)


def _document_grants(document: dict[str, Any]) -> list[Grant]:
    statements = document.get("Statement", [])
    grants: list[Grant] = []
    for statement in [statements] if isinstance(statements, dict) else list(statements):
        if statement.get("Effect") != "Allow" or not _reads_secret(statement):
            continue
        if "NotResource" in statement:
            grants.append(Grant(("*",), tuple(_as_list(statement["NotResource"]))))
        else:
            grants.append(Grant(tuple(_as_list(statement.get("Resource")))))
    return grants


def _paged(call: Callable[..., dict[str, Any]], key: str, **kwargs: Any) -> list[Any]:
    items: list[Any] = []
    marker = None
    while True:
        page = call(**kwargs, **({"Marker": marker} if marker else {}))
        items += page[key]
        marker = page.get("Marker") if page.get("IsTruncated") else None
        if not marker:
            return items


def secret_grants(iam: Any, role: str) -> list[Grant]:
    """Every grant in the role's inline, customer-managed and AWS-managed policies."""
    grants: list[Grant] = []
    for name in _paged(iam.list_role_policies, "PolicyNames", RoleName=role):
        document = iam.get_role_policy(RoleName=role, PolicyName=name)["PolicyDocument"]
        grants += _document_grants(document)
    for attached in _paged(iam.list_attached_role_policies, "AttachedPolicies", RoleName=role):
        arn = attached["PolicyArn"]
        version = iam.get_policy(PolicyArn=arn)["Policy"]["DefaultVersionId"]
        document = iam.get_policy_version(PolicyArn=arn, VersionId=version)["PolicyVersion"]
        grants += _document_grants(document["Document"])
    return grants


def _can_read(grants: list[Grant], secret_arn: str) -> bool:
    return any(grant.covers(secret_arn) for grant in grants)


def _role_findings(subject: str, grants: list[Grant], secrets: Secrets, stage: str) -> list[str]:
    found = []
    if stage != "master" and not _can_read(grants, secrets.gateway):
        found.append(f"{subject}: its role cannot read the meridian_gateway secret")
    if stage == "tightened" and _can_read(grants, secrets.master):
        found.append(f"{subject}: its role still can read the master login's secret")
    return found


def _configuration(lam: Any, name: str) -> dict[str, Any] | None:
    try:
        return lam.get_function_configuration(FunctionName=name)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return None
        raise


def _parameter(ssm: Any) -> str | None:
    try:
        return ssm.get_parameter(Name=SSM_SECRET_PARAMETER)["Parameter"]["Value"]
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ParameterNotFound":
            return None
        raise


def _role_name(configuration: dict[str, Any]) -> str:
    return configuration["Role"].rsplit("/", 1)[-1]


def _holds_findings(lam: Any, iam: Any, arn: str, secrets: Secrets, stage: str) -> list[str]:
    holds = _configuration(lam, arn)
    if holds is None:
        return [f"Lambda {HOLDS_TARGET}: does not exist"]
    grants = secret_grants(iam, _role_name(holds))
    return _role_findings(f"Lambda {HOLDS_TARGET}", grants, secrets, stage)


def _semantic_findings(lam: Any, iam: Any, wanted: str, named: str, secrets: Secrets,
                       stage: str) -> list[str]:
    semantic = _configuration(lam, SEMANTIC_FUNCTION)
    if semantic is None:
        return [f"Lambda {SEMANTIC_FUNCTION}: does not exist"]
    found = []
    if semantic.get("Environment", {}).get("Variables", {}).get("AURORA_SECRET_ARN") != wanted:
        found.append(f"Lambda {SEMANTIC_FUNCTION}: AURORA_SECRET_ARN is not the {named} login's "
                     "secret")
    grants = secret_grants(iam, _role_name(semantic))
    return found + _role_findings(f"Lambda {SEMANTIC_FUNCTION}", grants, secrets, stage)


def check(ssm: Any, lam: Any, iam: Any, control: Any, gateway_id: str, secrets: Secrets,
          stage: str) -> list[str]:
    """One line per way the three Lambdas differ from the ``stage``. Only reads.

    Raises:
        ValueError: When ``stage`` is not one of ``master``, ``gateway`` or ``tightened``.
    """
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}, not {stage!r}")
    wanted = secrets.master if stage == "master" else secrets.gateway
    named = "master" if stage == "master" else "meridian_gateway"
    found = []
    value = _parameter(ssm)
    if value is not None and value != wanted and value.strip() == wanted:
        found.append(f"SSM {SSM_SECRET_PARAMETER}: names the {named} login's secret with "
                     "whitespace around it, so the holds Lambda would not find the secret")
    elif value != wanted:
        found.append(f"SSM {SSM_SECRET_PARAMETER}: does not name the {named} login's secret")
    found += _holds_findings(lam, iam, holds_function_arn(control, gateway_id), secrets, stage)
    return found + _semantic_findings(lam, iam, wanted, named, secrets, stage)


def remedies(findings: list[str], stage: str) -> list[str]:
    """What the operator does about each kind of finding that no command here fixes."""
    key = "AURORA_SECRET_ARN" if stage == "master" else "AURORA_GATEWAY_SECRET_ARN"
    flag = "" if stage == "master" else " --gateway-login"
    steps = []
    if any(line.startswith(f"SSM {SSM_SECRET_PARAMETER}:") for line in findings):
        steps.append("FIX SSM (ASK FIRST): python scripts/publish_gateway_parameters.py" + flag
                     + ", then lambdas --restart-holds")
    if any(f"{SEMANTIC_FUNCTION}: AURORA_SECRET_ARN" in line for line in findings):
        steps.append(
            f"MANUAL STEP (the {SEMANTIC_FUNCTION} Lambda is outside the CDK app): set its "
            f"AURORA_SECRET_ARN environment variable to {key} from meridian/.env. "
            "update-function-configuration replaces the whole environment, so read it first "
            "(get-function-configuration) and send every variable back with this one changed.")
    steps += _role_remedies(findings)
    return steps


def _role_remedies(findings: list[str]) -> list[str]:
    subject = f"Lambda {SEMANTIC_FUNCTION}: its role"
    steps = []
    if any(line.startswith(f"{subject} cannot read") for line in findings):
        steps.append(
            f"MANUAL STEP (the {SEMANTIC_FUNCTION} Lambda and its role are outside the CDK app): "
            "add an inline policy to that role that allows secretsmanager:GetSecretValue on the "
            "secret named by AURORA_GATEWAY_SECRET_ARN in meridian/.env (iam put-role-policy), "
            "then run lambdas again.")
    if any(line.startswith(f"{subject} still can read") for line in findings):
        steps.append(
            f"MANUAL STEP (ASK FIRST; the {SEMANTIC_FUNCTION} role is outside the CDK app): "
            "remove the secretsmanager:GetSecretValue grant on the master login's secret "
            "(AURORA_SECRET_ARN) from that role's inline and attached policies, only after the "
            "gateway stage passes and a rollback no longer needs it.")
    return steps


def require_holds(lam: Any, function_arn: str, account: str, region: str) -> dict[str, Any]:
    """The holds function's configuration, after proving it is this project's holds function.

    Raises:
        LambdaError: When the ARN is for another account or Region, its name is not an
            AgentCore ``MeridianHolds`` function, it does not exist, or its role is not in the
            same account.
    """
    parsed = FUNCTION_ARN.match(function_arn)
    if parsed is None or (parsed["account"], parsed["region"]) != (account, region):
        raise LambdaError(
            f"the {HOLDS_TARGET} target names a function outside this account and Region")
    if not HOLDS_NAME.match(parsed["name"]):
        raise LambdaError(f"the {HOLDS_TARGET} target names a function that is not an AgentCore "
                          f"{HOLDS_TARGET} function ({parsed['name']})")
    configuration = _configuration(lam, function_arn)
    if configuration is None:
        raise LambdaError(f"the {HOLDS_TARGET} function does not exist")
    if f":{account}:role/" not in configuration.get("Role", ""):
        raise LambdaError(f"the {HOLDS_TARGET} function's role is not in this account")
    return configuration


def _wait_settled(lam: Any, function_arn: str) -> None:
    try:
        lam.get_waiter("function_updated_v2").wait(FunctionName=function_arn, WaiterConfig=WAIT)
    except WaiterError as error:
        raise LambdaError(
            f"the {HOLDS_TARGET} function did not settle within "
            f"{WAIT['Delay'] * WAIT['MaxAttempts']} seconds ({type(error).__name__}); "
            "read it with lambdas before trying again") from error


def _current_variables(configuration: Mapping[str, Any]) -> dict[str, str]:
    """The function's environment variables, or a refusal when they cannot be read in full."""
    environment = configuration.get("Environment")
    if environment is None:
        return {}
    if environment.get("Error") or "Variables" not in environment:
        raise LambdaError(
            f"the {HOLDS_TARGET} function's configuration cannot read its environment "
            "(Lambda returned an error instead of the variables, for example when a "
            "customer-managed KMS key cannot decrypt them); sending only the marker would "
            "replace every other variable, so nothing was changed")
    return dict(environment["Variables"])


def restart_holds(lam: Any, function_arn: str, stamp: str, *, account: str, region: str) -> str:
    """Force new execution environments by changing an environment variable on the holds Lambda.

    Raises:
        LambdaError: When the function is not this project's holds function, its environment
            cannot be read in full, a wait runs out, or the environment afterwards is not
            exactly the one sent (every old variable plus the marker).
    """
    require_holds(lam, function_arn, account, region)
    _wait_settled(lam, function_arn)
    configuration = lam.get_function_configuration(FunctionName=function_arn)
    variables = _current_variables(configuration)
    variables[MARKER] = stamp
    revision = {"RevisionId": configuration["RevisionId"]} if "RevisionId" in configuration else {}
    lam.update_function_configuration(
        FunctionName=function_arn, Environment={"Variables": variables}, **revision)
    _wait_settled(lam, function_arn)
    after = lam.get_function_configuration(FunctionName=function_arn)
    if (after.get("Environment") or {}).get("Variables") != variables:
        raise LambdaError(
            f"the {HOLDS_TARGET} function's environment after the update is not the one sent "
            f"(the {MARKER} marker or another variable differs); read it with "
            "get-function-configuration and repair it before going on")
    return f"Lambda {HOLDS_TARGET}: restarted ({MARKER}={stamp})"


def _secrets(env: Mapping[str, str | None]) -> Secrets:
    secrets = Secrets(master=(env.get("AURORA_SECRET_ARN") or "").strip(),
                      gateway=(env.get("AURORA_GATEWAY_SECRET_ARN") or "").strip())
    if not secrets.master or not secrets.gateway:
        raise settings.ReleaseConfigError(
            "AURORA_SECRET_ARN and AURORA_GATEWAY_SECRET_ARN must both be set in meridian/.env")
    return secrets


def _restart(args: argparse.Namespace, session: Any, where: tuple[str, str, str],
             now: datetime, say: Callable[[str], None]) -> int:
    account, region, gateway_id = where
    lam = session.client("lambda")
    arn = holds_function_arn(session.client("bedrock-agentcore-control"), gateway_id)
    require_holds(lam, arn, account, region)
    if not args.apply:
        say(f"DRY RUN. Would restart the {HOLDS_TARGET} Lambda by setting {MARKER}. Apply "
            f"(ASK FIRST): python scripts/release_identity.py lambdas --restart-holds --apply "
            f"{settings.CONFIRM_FLAG}")
        return OK
    say(restart_holds(lam, arn, now.strftime(STAMP), account=account, region=region))
    return OK


def run(args: argparse.Namespace, deps: Any, say: Callable[[str], None]) -> int:
    """The ``lambdas`` command: check against a stage, or restart the holds Lambda.

    ``deps`` carries ``env``, ``session`` and ``now`` (the release CLI's ``Dependencies``).
    """
    env = deps.env
    account, region = settings.deployment_target(env)
    secrets = _secrets(env)
    if args.apply and not (args.restart_holds and args.confirmed):
        say(f"REFUSED: --apply needs --restart-holds and {settings.CONFIRM_FLAG}; it changes AWS.")
        return REFUSED
    if not settings.REGION.fullmatch(region):
        raise settings.ReleaseConfigError(
            "the Region in AURORA_CLUSTER_ARN is not a Region name; check meridian/.env")
    session = deps.session(region)
    require_account(session.client("sts"), env["AURORA_CLUSTER_ARN"])
    gateway_id, _ = preflight.hop_ids(env)
    if args.restart_holds:
        return _restart(args, session, (account, region, gateway_id), deps.now(), say)
    findings = check(session.client("ssm"), session.client("lambda"), session.client("iam"),
                     session.client("bedrock-agentcore-control"), gateway_id, secrets, args.expect)
    for line in findings:
        say(f"DRIFT  {line}")
    for line in remedies(findings, args.expect):
        say(line)
    if not findings:
        say(f"OK  the Lambdas are at the {args.expect} stage")
    return DRIFT if findings else OK
