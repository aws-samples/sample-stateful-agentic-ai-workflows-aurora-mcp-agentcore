"""Check, and restart, the Lambdas that read an Aurora login's secret.

The holds path moves from the master login to the meridian_gateway login: the SSM parameter the
``MeridianHolds`` Lambda reads at its first call after a cold start, and the holds role's
``GetSecretValue`` grants. The stages are:

``master``     the baseline: nothing has moved.
``gateway``    the parameter names the gateway login's secret, and the holds role can read it.
               The master grant may remain, so a rollback still works.
``tightened``  as ``gateway``, and the holds role cannot read the master login's secret any more.

The ``meridian-catalog-tools`` Lambda (set by hand, outside the CDK app) never moves: it runs as
its own login, meridian_catalog, from creation. At every stage its ``AURORA_SECRET_ARN`` names
that login's secret, its role can read that secret, and its role can read neither the master nor
the gateway login's secret.

A statement applies to a secret when its action covers ``secretsmanager:GetSecretValue`` (a
wildcard action covers it; ``NotAction`` covers it unless a pattern matches that action) and its
resource matches the secret's ARN (``*`` matches every secret; ``NotResource`` matches every
secret that no listed pattern matches). Allow and Deny statements are read the same way. Inline,
customer-managed and AWS-managed attached policies are all read.

Conditions are not evaluated, so each check reads them in the direction that cannot clear a
finding by mistake:

``may_use``      for a forbidden grant (the finding is "the role can read it"). An Allow counts with
                 or without a ``Condition``; only a Deny without a ``Condition`` removes it. A
                 conditional Deny does not clear the finding, because the role can still read the
                 secret wherever the condition does not deny it.
``surely_uses``  for a required grant (the finding is "the role cannot read it"). Only an Allow
                 without a ``Condition`` counts, and any Deny, conditional or not, removes it.

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
CATALOG_FUNCTION = "meridian-catalog-tools"
CATALOG_SECRET_NAME = "meridian/aurora/catalog-login"
SSM_SECRET_PARAMETER = "/meridian/aurora/secret_arn"
MARKER = "MERIDIAN_COLD_START"
STAGES = ("master", "gateway", "tightened")
READ_ACTION = "secretsmanager:getsecretvalue"
STAMP = "%Y%m%dT%H%M%SZ"
WAIT = {"Delay": 5, "MaxAttempts": 24}
HOLDS_NAME = re.compile(r"^(AgentCore-)?[A-Za-z0-9_-]+-MeridianHolds[A-Za-z0-9]*$")
FUNCTION_ARN = re.compile(r"^arn:aws:lambda:(?P<region>[^:]+):(?P<account>\d{12}):function:"
                          r"(?P<name>[A-Za-z0-9_-]+)$")
OK, DRIFT, REFUSED = 0, 1, 3


class LambdaError(settings.ReleaseConfigError):
    """The Lambda or its target could not be found, or is not this project's holds function."""


@dataclass(frozen=True)
class Secrets:
    """The two login secrets the holds path moves between, and the catalog Lambda's own.

    ``catalog`` is empty when ``AURORA_CATALOG_SECRET_ARN`` is not set; ``check``, ``rollback`` and
    the ``lambdas`` run all need it.
    """

    master: str
    gateway: str
    catalog: str = ""


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
    """Resources a statement covers: those matching ``include`` but not ``exclude``.

    ``deny`` marks a Deny statement (an Allow is the default); ``conditional`` marks a statement
    that carries a ``Condition``, which is never evaluated.
    """

    include: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    deny: bool = False
    conditional: bool = False

    def covers(self, secret_arn: str) -> bool:
        """Whether the statement applies to ``secret_arn``."""
        return (any(_matches(p, secret_arn) for p in self.include)
                and not any(_matches(p, secret_arn) for p in self.exclude))


def _allows_action(statement: Mapping[str, Any], action: str) -> bool:
    if "NotAction" in statement:
        excluded = [a.lower() for a in _as_list(statement["NotAction"])]
        return not any(_matches(a, action) for a in excluded)
    actions = [a.lower() for a in _as_list(statement.get("Action"))]
    return any(_matches(a, action) for a in actions)


def _document_grants(document: dict[str, Any], action: str = READ_ACTION) -> list[Grant]:
    statements = document.get("Statement", [])
    grants: list[Grant] = []
    for statement in [statements] if isinstance(statements, dict) else list(statements):
        effect = statement.get("Effect")
        if effect not in ("Allow", "Deny") or not _allows_action(statement, action):
            continue
        deny = effect == "Deny"
        conditional = bool(statement.get("Condition"))
        if "NotResource" in statement:
            excluded = tuple(_as_list(statement["NotResource"]))
            grants.append(Grant(("*",), excluded, deny, conditional))
        else:
            resources = tuple(_as_list(statement.get("Resource")))
            grants.append(Grant(resources, deny=deny, conditional=conditional))
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


def secret_grants(iam: Any, role: str, action: str = READ_ACTION) -> list[Grant]:
    """Every Allow and Deny of ``action`` (lower case) in the role's inline and attached policies.

    Ask ``may_use`` or ``surely_uses`` whether they let the role act on a resource.
    Customer-managed and AWS-managed attached policies are both read. The default action is
    the secret read the Lambda moves care about.
    """
    grants: list[Grant] = []
    for name in _paged(iam.list_role_policies, "PolicyNames", RoleName=role):
        document = iam.get_role_policy(RoleName=role, PolicyName=name)["PolicyDocument"]
        grants += _document_grants(document, action)
    for attached in _paged(iam.list_attached_role_policies, "AttachedPolicies", RoleName=role):
        arn = attached["PolicyArn"]
        version = iam.get_policy(PolicyArn=arn)["Policy"]["DefaultVersionId"]
        document = iam.get_policy_version(PolicyArn=arn, VersionId=version)["PolicyVersion"]
        grants += _document_grants(document["Document"], action)
    return grants


def may_use(grants: list[Grant], resource: str) -> bool:
    """Whether the role may use ``resource``: use it for a forbidden grant.

    Some Allow covers it, with or without a ``Condition``, and no Deny without a ``Condition``
    does.
    """
    return (any(g.covers(resource) for g in grants if not g.deny)
            and not any(g.covers(resource) for g in grants if g.deny and not g.conditional))


def surely_uses(grants: list[Grant], resource: str) -> bool:
    """Whether the role surely can use ``resource``: use it for a required grant.

    Some Allow without a ``Condition`` covers it and no Deny does, conditional or not.
    """
    return (any(g.covers(resource) for g in grants if not g.deny and not g.conditional)
            and not any(g.covers(resource) for g in grants if g.deny))


def _role_findings(subject: str, grants: list[Grant], secrets: Secrets, stage: str) -> list[str]:
    found = []
    if stage != "master" and not surely_uses(grants, secrets.gateway):
        found.append(f"{subject}: its role cannot read the meridian_gateway secret")
    if stage == "tightened" and may_use(grants, secrets.master):
        found.append(f"{subject}: its role still can read the master login's secret")
    return found


def function_configuration(lam: Any, name: str) -> dict[str, Any] | None:
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


def role_name(configuration: dict[str, Any]) -> str:
    return configuration["Role"].rsplit("/", 1)[-1]


def _holds_findings(lam: Any, iam: Any, arn: str, secrets: Secrets, stage: str) -> list[str]:
    holds = function_configuration(lam, arn)
    if holds is None:
        return [f"Lambda {HOLDS_TARGET}: does not exist"]
    grants = secret_grants(iam, role_name(holds))
    return _role_findings(f"Lambda {HOLDS_TARGET}", grants, secrets, stage)


def holds_read_findings(lam: Any, iam: Any, control: Any, gateway_id: str,
                        secrets: Secrets) -> list[str]:
    """Findings when the holds Lambda's role cannot read the gateway login's secret yet.

    The grant comes from the stack deploy, so the parameter and the Lambda move only after it.
    """
    return _holds_findings(lam, iam, holds_function_arn(control, gateway_id), secrets, "gateway")


def catalog_findings(lam: Any, iam: Any, secrets: Secrets) -> list[str]:
    """Findings when the catalog Lambda is not on its own login. The same at every stage."""
    catalog = function_configuration(lam, CATALOG_FUNCTION)
    subject = f"Lambda {CATALOG_FUNCTION}"
    if catalog is None:
        return [f"{subject}: does not exist"]
    found = []
    variables = catalog.get("Environment", {}).get("Variables", {})
    if variables.get("AURORA_SECRET_ARN") != secrets.catalog:
        found.append(f"{subject}: AURORA_SECRET_ARN is not the meridian_catalog login's secret")
    grants = secret_grants(iam, role_name(catalog))
    if not surely_uses(grants, secrets.catalog):
        found.append(f"{subject}: its role cannot read the meridian_catalog secret")
    for named, secret in (("master", secrets.master), ("meridian_gateway", secrets.gateway)):
        if may_use(grants, secret):
            found.append(f"{subject}: its role can read the {named} login's secret, which it "
                         "never needs")
    return found


def parameter_findings(ssm: Any, wanted: str, named: str) -> list[str]:
    """Findings when the SSM secret parameter does not hold exactly ``wanted``. Only reads."""
    value = _parameter(ssm)
    if value is not None and value != wanted and value.strip() == wanted:
        return [f"SSM {SSM_SECRET_PARAMETER}: names the {named} login's secret with "
                "whitespace around it, so the holds Lambda would not find the secret"]
    if value != wanted:
        return [f"SSM {SSM_SECRET_PARAMETER}: does not name the {named} login's secret"]
    return []


def require_catalog_secret(secrets: Secrets) -> None:
    """Refuse when ``AURORA_CATALOG_SECRET_ARN`` is not set. Calls nothing.

    The hint reads the existing secret. It never points at the provisioner with ``--apply``,
    because provisioning a login that exists rotates its password.

    Raises:
        ReleaseConfigError: When ``secrets`` has no catalog login secret.
    """
    if secrets.catalog:
        return
    raise settings.ReleaseConfigError(
        "AURORA_CATALOG_SECRET_ARN is not set in meridian/.env, and the catalog Lambda is checked "
        "against the meridian_catalog login's secret. Set it to the ARN of the existing secret "
        f"{CATALOG_SECRET_NAME} (read it with: aws secretsmanager describe-secret --secret-id "
        f"{CATALOG_SECRET_NAME} --query ARN --output text). Only if that secret does not exist, "
        "provision the catalog login as docs/OPERATIONS.md describes under Service logins; "
        "provisioning a login that exists rotates its password.")


def check(ssm: Any, lam: Any, iam: Any, control: Any, gateway_id: str, secrets: Secrets,
          stage: str) -> list[str]:
    """One line per way the parameter and the two Lambdas differ from the ``stage``. Only reads.

    The catalog Lambda is checked against its own login whatever the stage.

    Raises:
        ValueError: When ``stage`` is not one of ``master``, ``gateway`` or ``tightened``.
        ReleaseConfigError: When ``secrets`` has no catalog login secret.
    """
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}, not {stage!r}")
    require_catalog_secret(secrets)
    wanted = secrets.master if stage == "master" else secrets.gateway
    named = "master" if stage == "master" else "meridian_gateway"
    found = parameter_findings(ssm, wanted, named)
    found += _holds_findings(lam, iam, holds_function_arn(control, gateway_id), secrets, stage)
    return found + catalog_findings(lam, iam, secrets)


def remedies(findings: list[str], stage: str) -> list[str]:
    """What the operator does about each kind of finding that no command here fixes."""
    flag = "" if stage == "master" else " --gateway-login"
    steps = []
    if any(line.startswith(f"SSM {SSM_SECRET_PARAMETER}:") for line in findings):
        steps.append("FIX SSM (ASK FIRST): python scripts/publish_gateway_parameters.py" + flag
                     + f" --apply {settings.CONFIRM_FLAG}, then lambdas --restart-holds")
    return steps + _catalog_remedies(findings)


def _catalog_remedies(findings: list[str]) -> list[str]:
    subject = f"Lambda {CATALOG_FUNCTION}: "
    steps = []
    if any(line.startswith((f"{subject}AURORA_SECRET_ARN", f"{subject}its role cannot read"))
           for line in findings):
        steps.append(
            f"MANUAL STEP (ASK FIRST; {CATALOG_FUNCTION} is outside the CDK app): set its "
            "AURORA_SECRET_ARN to AURORA_CATALOG_SECRET_ARN from meridian/.env, keeping every "
            "other variable, and attach the MeridianCatalogAuroraAccess policy to its role, as "
            "\"Create the catalog Lambda\" in docs/AGENTCORE_DEPLOY_RUNBOOK.md does.")
    if any(line.startswith(f"{subject}its role can read") for line in findings):
        steps.append(
            f"MANUAL STEP (ASK FIRST; the {CATALOG_FUNCTION} role is outside the CDK app): "
            "remove every secretsmanager:GetSecretValue grant on the master or the "
            "meridian_gateway login's secret from that role's inline and attached policies; it "
            "needs only the meridian_catalog secret.")
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
    configuration = function_configuration(lam, function_arn)
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


def login_secrets(env: Mapping[str, str | None]) -> Secrets:
    secrets = Secrets(master=(env.get("AURORA_SECRET_ARN") or "").strip(),
                      gateway=(env.get("AURORA_GATEWAY_SECRET_ARN") or "").strip(),
                      catalog=(env.get("AURORA_CATALOG_SECRET_ARN") or "").strip())
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
    secrets = login_secrets(env)
    if args.apply and not (args.restart_holds and args.confirmed):
        say(f"REFUSED: --apply needs --restart-holds and {settings.CONFIRM_FLAG}; it changes AWS.")
        return REFUSED
    if not settings.REGION.fullmatch(region):
        raise settings.ReleaseConfigError(
            "the Region in AURORA_CLUSTER_ARN is not a Region name; check meridian/.env")
    if not args.restart_holds:
        require_catalog_secret(secrets)
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
