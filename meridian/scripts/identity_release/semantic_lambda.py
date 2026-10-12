"""Move the semantic-search Lambda to the meridian_gateway login, or back.

The ``meridian-semantic-trip-search`` Lambda is outside the CDK app: its environment and its role
were set by hand. Moving it off the master login takes two changes, in this order:

1. its role gets ``secretsmanager:GetSecretValue`` on the gateway login's secret, as an inline
   policy this tool names and marks (``POLICY_NAME``, statement id ``POLICY_SID``). A policy of
   that name that is not marked is never touched;
2. its ``AURORA_SECRET_ARN`` environment variable names the gateway login's secret. Lambda
   replaces the whole environment on an update, so the tool reads the full map, changes that one
   value and sends every variable back with the revision it read. It refuses a function whose
   environment cannot be read in full (an ``Error`` entry, no variables), and one whose
   ``AURORA_SECRET_ARN`` names neither login's secret.

Both changes are read back, and the stage check ``lambda_release`` already uses must pass.
``--to master`` is the way back: it restores the master value and keeps the grant, so a later
move needs no new permission; ``--remove-grant`` also deletes the marked policy. Every step is
idempotent. Without ``--apply`` and the confirmation flag nothing is written. Calls: ``sts``
``GetCallerIdentity`` first, then ``lambda:GetFunctionConfiguration`` and
``UpdateFunctionConfiguration``, ``iam:GetRolePolicy``, ``PutRolePolicy`` and
``DeleteRolePolicy`` on the Lambda's role, and the reads ``lambda_release`` makes.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any

from botocore.exceptions import ClientError, WaiterError

from scripts.identity_release import lambda_release, settings
from scripts.provision_service_logins import require_account

FUNCTION = lambda_release.SEMANTIC_FUNCTION
VARIABLE = "AURORA_SECRET_ARN"
POLICY_NAME = "meridian-gateway-login-read"
POLICY_SID = "MeridianGatewayLoginRead"
TARGETS = ("gateway", "master")
OK, DRIFT, REFUSED = 0, 1, 3


class SemanticError(settings.ReleaseConfigError):
    """The Lambda or its role cannot be changed safely."""


def grant_document(secret_arn: str) -> dict[str, Any]:
    """The inline policy: read one secret, marked so the tool recognizes it as its own."""
    return {"Version": "2012-10-17", "Statement": [{
        "Sid": POLICY_SID, "Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
        "Resource": secret_arn}]}


def _is_ours(document: Any) -> bool:
    statements = document.get("Statement") if isinstance(document, dict) else None
    if isinstance(statements, dict):
        statements = [statements]
    return (isinstance(statements, list) and bool(statements)
            and all(isinstance(s, dict) and s.get("Sid") == POLICY_SID for s in statements))


def read_policy(iam: Any, role: str) -> dict[str, Any] | None:
    """The role's inline policy named ``POLICY_NAME``, or None.

    Raises:
        SemanticError: When a policy of that name exists but is not this tool's.
    """
    try:
        document = iam.get_role_policy(RoleName=role, PolicyName=POLICY_NAME)["PolicyDocument"]
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "NoSuchEntity":
            return None
        raise
    if not _is_ours(document):
        raise SemanticError(
            f"the role already has an inline policy named {POLICY_NAME} that this tool did not "
            "write; nothing was changed. Rename or remove it by hand, or grant the read yourself")
    return document


def require_function(lam: Any, account: str, region: str) -> dict[str, Any]:
    """The semantic Lambda's configuration, after checking it is this account's.

    Raises:
        SemanticError: When it does not exist or its role is in another account.
    """
    configuration = lambda_release.function_configuration(lam, FUNCTION)
    if configuration is None:
        raise SemanticError(f"the {FUNCTION} Lambda does not exist in this account and Region")
    if f":{account}:role/" not in configuration.get("Role", ""):
        raise SemanticError(f"the {FUNCTION} Lambda's role is not in this account")
    return configuration


def current_variables(configuration: dict[str, Any], secrets: lambda_release.Secrets
                      ) -> dict[str, str]:
    """The full environment, or a refusal when it cannot be read or is not the expected one."""
    environment = configuration.get("Environment")
    if not isinstance(environment, dict) or environment.get("Error") \
            or not isinstance(environment.get("Variables"), dict):
        raise SemanticError(
            f"the {FUNCTION} Lambda's environment cannot be read in full (no variables, or Lambda "
            "returned an error instead, for example when a KMS key cannot decrypt them); sending "
            "one variable would replace the rest, so nothing was changed")
    variables = dict(environment["Variables"])
    if variables.get(VARIABLE) not in (secrets.master, secrets.gateway):
        raise SemanticError(
            f"{VARIABLE} on the {FUNCTION} Lambda is missing or names neither the master nor the "
            "gateway login's secret, so this is not the function this release expects; nothing "
            "was changed")
    return variables


def _settle(lam: Any) -> None:
    try:
        lam.get_waiter("function_updated_v2").wait(
            FunctionName=FUNCTION, WaiterConfig=lambda_release.WAIT)
    except WaiterError as error:
        raise SemanticError(
            f"the {FUNCTION} Lambda did not settle ({type(error).__name__}); read it with "
            "release_identity.py lambdas before trying again") from error


def set_secret(lam: Any, secrets: lambda_release.Secrets, wanted: str) -> bool:
    """Set ``AURORA_SECRET_ARN`` to ``wanted`` keeping every other variable; True if it changed.

    Raises:
        SemanticError: When the environment is unreadable or not the one sent afterwards.
    """
    _settle(lam)
    configuration = lam.get_function_configuration(FunctionName=FUNCTION)
    variables = current_variables(configuration, secrets)
    if variables[VARIABLE] == wanted:
        return False
    variables[VARIABLE] = wanted
    revision = {"RevisionId": configuration["RevisionId"]} if "RevisionId" in configuration else {}
    lam.update_function_configuration(
        FunctionName=FUNCTION, Environment={"Variables": variables}, **revision)
    _settle(lam)
    after = lam.get_function_configuration(FunctionName=FUNCTION)
    if (after.get("Environment") or {}).get("Variables") != variables:
        raise SemanticError(
            f"the {FUNCTION} Lambda's environment after the update is not the one sent; read it "
            "with get-function-configuration and repair it before going on")
    return True


def add_grant(iam: Any, role: str, secret_arn: str) -> bool:
    """Put the marked inline policy on ``role`` and read it back; True if it changed."""
    wanted = grant_document(secret_arn)
    if read_policy(iam, role) == wanted:
        return False
    iam.put_role_policy(RoleName=role, PolicyName=POLICY_NAME, PolicyDocument=json.dumps(wanted))
    if read_policy(iam, role) != wanted:
        raise SemanticError(f"the inline policy on the {FUNCTION} role is not the one sent; "
                            "read it with iam get-role-policy")
    return True


def drop_grant(iam: Any, role: str) -> bool:
    """Delete the marked inline policy and confirm it is gone; True if it was there."""
    if read_policy(iam, role) is None:
        return False
    iam.delete_role_policy(RoleName=role, PolicyName=POLICY_NAME)
    if read_policy(iam, role) is not None:
        raise SemanticError(f"the inline policy {POLICY_NAME} is still on the {FUNCTION} role")
    return True


def _plan_lines(args: argparse.Namespace, variables: dict[str, str], policy: dict | None,
                secrets: lambda_release.Secrets) -> list[str]:
    wanted = secrets.gateway if args.to == "gateway" else secrets.master
    lines = [f"DRY RUN. The {FUNCTION} Lambda, move to the {args.to} login:"]
    if args.to == "gateway":
        state = "already present" if policy else "would be added"
        lines.append(f"  role: inline policy {POLICY_NAME} (GetSecretValue on the gateway "
                     f"login's secret) {state}")
    if args.remove_grant:
        lines.append(f"  role: inline policy {POLICY_NAME} "
                     + ("would be removed" if policy else "is not there"))
    if variables[VARIABLE] == wanted:
        lines.append(f"  {VARIABLE}: unchanged ({len(variables) - 1} other variables kept)")
    else:
        lines.append(f"  {VARIABLE}: would be set to the {args.to} login's secret "
                     f"({len(variables) - 1} other variables kept)")
    return lines


def _apply(args: argparse.Namespace, lam: Any, iam: Any, role: str,
           secrets: lambda_release.Secrets) -> list[str]:
    done = []
    if args.to == "gateway" and add_grant(iam, role, secrets.gateway):
        done.append(f"role: inline policy {POLICY_NAME} added")
    wanted = secrets.gateway if args.to == "gateway" else secrets.master
    if set_secret(lam, secrets, wanted):
        done.append(f"{VARIABLE}: now the {args.to} login's secret")
    if args.remove_grant and drop_grant(iam, role):
        done.append(f"role: inline policy {POLICY_NAME} removed")
    return done


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The command's arguments."""
    parser.add_argument("--to", choices=TARGETS, default="gateway",
                        help="which login's secret the Lambda names (default: gateway)")
    parser.add_argument("--remove-grant", action="store_true",
                        help="with --to master: also delete the inline policy this tool added")
    parser.add_argument("--apply", action="store_true", help="make the change (live)")
    parser.add_argument(settings.CONFIRM_FLAG, action="store_true", dest="confirmed",
                        help="required with --apply: it changes AWS")


def _refusal(args: argparse.Namespace) -> str | None:
    if args.remove_grant and args.to != "master":
        return "--remove-grant goes with --to master"
    if args.apply and not args.confirmed:
        return f"--apply also needs {settings.CONFIRM_FLAG}; it changes AWS."
    return None


def run(args: argparse.Namespace, deps: Any, say: Callable[[str], None]) -> int:
    """The ``semantic-lambda`` command: plan, or apply and read back.

    ``deps`` carries ``env`` and ``session`` (the release CLI's ``Dependencies``).
    """
    refusal = _refusal(args)
    if refusal:
        say(f"REFUSED: {refusal}")
        return REFUSED
    account, region = settings.deployment_target(deps.env)
    secrets = lambda_release.login_secrets(deps.env)
    session = deps.session(region)
    require_account(session.client("sts"), deps.env["AURORA_CLUSTER_ARN"])
    lam, iam = session.client("lambda"), session.client("iam")
    configuration = require_function(lam, account, region)
    role = lambda_release.role_name(configuration)
    variables = current_variables(configuration, secrets)
    if not args.apply:
        for line in _plan_lines(args, variables, read_policy(iam, role), secrets):
            say(line)
        say(f"Apply (ASK FIRST): python scripts/release_identity.py semantic-lambda --to {args.to}"
            f"{' --remove-grant' if args.remove_grant else ''} --apply {settings.CONFIRM_FLAG}")
        return OK
    for line in _apply(args, lam, iam, role, secrets):
        say(line)
    return _read_back(args, lam, iam, secrets, say)


def _read_back(args: argparse.Namespace, lam: Any, iam: Any, secrets: lambda_release.Secrets,
               say: Callable[[str], None]) -> int:
    wanted = secrets.gateway if args.to == "gateway" else secrets.master
    stage = "gateway" if args.to == "gateway" else "master"
    findings = lambda_release.semantic_findings(lam, iam, wanted, args.to, secrets, stage)
    for line in findings:
        say(f"DRIFT  {line}")
    if not findings:
        say(f"OK  the {FUNCTION} Lambda is at the {stage} stage")
    return DRIFT if findings else OK
