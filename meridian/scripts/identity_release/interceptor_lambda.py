"""Deploy the Gateway request interceptor as its own Lambda function with a log-only role.

The interceptor decodes the already validated access token and overwrites ``travelerId``. It
needs no database, no secret and no network: its role may write its own logs and nothing else.
Letting the Gateway invoke it is a separate grant on the Gateway's role
(``scripts/identity_release/gateway.py``). ``apply`` is idempotent, so a second run changes
nothing and a changed package, environment or setting is brought back.

Every resource carries ``TAGS``. ``apply`` and ``teardown`` refuse to touch a role or function
that exists without them, and ``teardown`` re-reads the tags before each delete.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError, WaiterError

from scripts.identity_release import settings

SOURCE = (settings.MERIDIAN_DIR / "meridian_agentcore" / "agentcore" / "interceptors"
          / "traveler_pin" / "lambda_function.py")
POLICY_NAME = "traveler-pin-logs"
HANDLER = "lambda_function.lambda_handler"
RUNTIME = "python3.13"
TIMEOUT_SECONDS = 5
MEMORY_MB = 128
DESCRIPTION = "Meridian Gateway request interceptor: pins travelerId to the signed-in traveler"
TAGS = {
    "project": "meridian",
    "meridian-component": "gateway-interceptor",
    "meridian-release": "b2b-identity",
}
ASSUME_ATTEMPTS = 12
ASSUME_WAIT_SECONDS = 5
WAIT_DELAY_SECONDS = 2
WAIT_MAX_ATTEMPTS = 60
OUTPUT_NAME = "interceptor-lambda.json"


class DeployError(RuntimeError):
    """The function or its role could not be brought to the wanted state."""


@dataclass(frozen=True)
class Desired:
    """Everything the deployment must end up as."""

    function_name: str
    function_arn: str
    role_name: str
    role_arn: str
    trust_policy: str
    role_policy: dict[str, Any]
    environment: dict[str, str]
    code_sha256: str


def package() -> bytes:
    """The deployment package: the production interceptor, zipped reproducibly."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("lambda_function.py", date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        archive.writestr(info, SOURCE.read_bytes())
    return buffer.getvalue()


def desired(account: str, region: str, cognito: settings.CognitoSettings) -> Desired:
    """The function, its role and the log-only policy for this deployment.

    ``PINNED_TOOLS`` is deliberately not set: an override replaces the default tool list.
    """
    name = settings.INTERCEPTOR_FUNCTION
    group = f"arn:aws:logs:{region}:{account}:log-group:/aws/lambda/{name}"
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {"StringEquals": {"aws:SourceAccount": account}},
        }],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": "logs:CreateLogGroup", "Resource": group},
            {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
             "Resource": f"{group}:*"},
        ],
    }
    digest = base64.b64encode(hashlib.sha256(package()).digest()).decode()
    return Desired(
        function_name=name, function_arn=settings.interceptor_arn(account, region),
        role_name=name, role_arn=f"arn:aws:iam::{account}:role/{name}",
        trust_policy=json.dumps(trust), role_policy=policy,
        environment={"EXPECTED_CLIENT_ID": cognito.client_id, "EXPECTED_ISSUER": cognito.issuer},
        code_sha256=digest,
    )


def plan(wanted: Desired) -> list[str]:
    """What ``apply`` would make sure of, in order. Makes no AWS call."""
    return [
        f"IAM role {wanted.role_name}: Lambda may assume it; one inline policy "
        f"({POLICY_NAME}) writes this function's logs and nothing else",
        f"Lambda function {wanted.function_name}: {RUNTIME}, {HANDLER}, {TIMEOUT_SECONDS} s, "
        f"{MEMORY_MB} MB, the production interceptor (sha256 {wanted.code_sha256[:12]}...), "
        "checking the app client and issuer from the Cognito settings",
        f"Both are tagged {_tag_text()}; one that exists without those tags is left alone",
        "The Gateway's permission to invoke it is a separate step (the gateway command)",
    ]


def teardown_plan(name: str = settings.INTERCEPTOR_FUNCTION) -> list[str]:
    """What ``teardown`` would delete, in order. Makes no AWS call."""
    return [
        f"Lambda function {name}: deleted if it carries {_tag_text()}",
        f"IAM role {name}: its inline policy and the role deleted if it carries the same tags",
        "Anything without those tags is never touched; the log group is left in place",
    ]


def _tag_text() -> str:
    return ", ".join(f"{key}={value}" for key, value in TAGS.items())


# ------------------------------------------------------------------ ownership


def _missing(error: ClientError, *codes: str) -> bool:
    return error.response.get("Error", {}).get("Code") in codes


def _is_ours(tags: dict[str, str] | None) -> bool:
    return all((tags or {}).get(key) == value for key, value in TAGS.items())


def _role_tags(role: dict[str, Any]) -> dict[str, str]:
    return {pair["Key"]: pair["Value"] for pair in role.get("Tags") or []}


def _require_ours(tags: dict[str, str] | None, subject: str) -> None:
    if not _is_ours(tags):
        raise DeployError(
            f"{subject} exists but is not tagged {_tag_text()}; this tool only changes "
            "resources it created. Nothing was changed.")


# ----------------------------------------------------------------------- apply


def _find_role(iam: Any, name: str) -> dict[str, Any] | None:
    try:
        return iam.get_role(RoleName=name)["Role"]
    except ClientError as error:
        if _missing(error, "NoSuchEntity"):
            return None
        raise


def _ensure_role(iam: Any, wanted: Desired) -> list[str]:
    role = _find_role(iam, wanted.role_name)
    if role is None:
        iam.create_role(
            RoleName=wanted.role_name, AssumeRolePolicyDocument=wanted.trust_policy,
            Description=DESCRIPTION, Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()])
        note = f"IAM role {wanted.role_name}: created"
    else:
        _require_ours(_role_tags(role), f"IAM role {wanted.role_name}")
        iam.update_assume_role_policy(
            RoleName=wanted.role_name, PolicyDocument=wanted.trust_policy)
        note = f"IAM role {wanted.role_name}: trust policy rewritten"
    iam.put_role_policy(RoleName=wanted.role_name, PolicyName=POLICY_NAME,
                        PolicyDocument=json.dumps(wanted.role_policy))
    return [note, f"IAM role {wanted.role_name}: policy {POLICY_NAME} written"]


def _wait(lam: Any, waiter: str, name: str) -> None:
    config = {"Delay": WAIT_DELAY_SECONDS, "MaxAttempts": WAIT_MAX_ATTEMPTS}
    try:
        lam.get_waiter(waiter).wait(FunctionName=name, WaiterConfig=config)
    except WaiterError as error:
        limit = WAIT_DELAY_SECONDS * WAIT_MAX_ATTEMPTS
        raise DeployError(
            f"Lambda {name}: {waiter} did not finish within {limit} s ({error})") from error


def _create_function(lam: Any, wanted: Desired, sleep: Callable[[float], None]) -> None:
    request = {
        "FunctionName": wanted.function_name, "Runtime": RUNTIME, "Role": wanted.role_arn,
        "Handler": HANDLER, "Code": {"ZipFile": package()}, "Timeout": TIMEOUT_SECONDS,
        "MemorySize": MEMORY_MB, "Description": DESCRIPTION, "Tags": dict(TAGS),
        "Environment": {"Variables": wanted.environment},
    }
    for attempt in range(ASSUME_ATTEMPTS):
        try:
            lam.create_function(**request)
            return
        except ClientError as error:
            message = error.response.get("Error", {}).get("Message", "")
            if not (_missing(error, "InvalidParameterValueException")
                    and "cannot be assumed" in message):
                raise
            if attempt == ASSUME_ATTEMPTS - 1:
                raise DeployError(f"the new role {message}; wait a minute and run again") from error
            sleep(ASSUME_WAIT_SECONDS)


def _settings_differ(configuration: dict[str, Any], wanted: Desired) -> bool:
    return (configuration.get("Handler"), configuration.get("Runtime"), configuration.get("Role"),
            configuration.get("Timeout"), configuration.get("MemorySize"),
            (configuration.get("Environment") or {}).get("Variables")) != (
        HANDLER, RUNTIME, wanted.role_arn, TIMEOUT_SECONDS, MEMORY_MB, wanted.environment)


def _update_function(lam: Any, configuration: dict[str, Any], wanted: Desired) -> list[str]:
    notes = []
    if configuration.get("CodeSha256") != wanted.code_sha256:
        lam.update_function_code(FunctionName=wanted.function_name, ZipFile=package())
        _wait(lam, "function_updated_v2", wanted.function_name)
        notes.append(f"Lambda {wanted.function_name}: code uploaded")
    if _settings_differ(configuration, wanted):
        lam.update_function_configuration(
            FunctionName=wanted.function_name, Role=wanted.role_arn, Handler=HANDLER,
            Runtime=RUNTIME, Timeout=TIMEOUT_SECONDS, MemorySize=MEMORY_MB,
            Environment={"Variables": wanted.environment})
        _wait(lam, "function_updated_v2", wanted.function_name)
        notes.append(f"Lambda {wanted.function_name}: settings updated")
    return notes or [f"Lambda {wanted.function_name}: unchanged"]


def apply(iam: Any, lam: Any, wanted: Desired, *,
          sleep: Callable[[float], None] = time.sleep) -> list[str]:
    """Create or bring back the role and the function; return one note per change.

    Raises:
        DeployError: When a role or function exists without this tool's tags, a new role stays
            unassumable for about a minute, or a function does not settle in time.
    """
    notes = _ensure_role(iam, wanted)
    try:
        found = lam.get_function(FunctionName=wanted.function_name)
    except ClientError as error:
        if not _missing(error, "ResourceNotFoundException"):
            raise
        _create_function(lam, wanted, sleep)
        _wait(lam, "function_active_v2", wanted.function_name)
        return notes + [f"Lambda {wanted.function_name}: created"]
    _require_ours(found.get("Tags"), f"Lambda {wanted.function_name}")
    return notes + _update_function(lam, found["Configuration"], wanted)


# ------------------------------------------------------------------- read back


def _role_findings(iam: Any, wanted: Desired) -> list[str]:
    subject = f"IAM role {wanted.role_name}"
    role = _find_role(iam, wanted.role_name)
    if role is None:
        return [f"{subject}: does not exist"]
    found = []
    if not _is_ours(_role_tags(role)):
        found.append(f"{subject}: is missing the tags {_tag_text()}")
    inline = iam.list_role_policies(RoleName=wanted.role_name)["PolicyNames"]
    if inline != [POLICY_NAME]:
        found.append(f"{subject}: inline policies are {inline}, expected only {POLICY_NAME}")
    document = iam.get_role_policy(RoleName=wanted.role_name, PolicyName=POLICY_NAME)
    if document["PolicyDocument"] != wanted.role_policy:
        found.append(f"{subject}: the {POLICY_NAME} policy differs from the log-only policy")
    attached = iam.list_attached_role_policies(RoleName=wanted.role_name)["AttachedPolicies"]
    found += [f"{subject}: has the managed policy {p['PolicyName']} attached" for p in attached]
    return found


def _function_findings(lam: Any, wanted: Desired) -> list[str]:
    subject = f"Lambda {wanted.function_name}"
    try:
        described = lam.get_function(FunctionName=wanted.function_name)
    except ClientError as error:
        if _missing(error, "ResourceNotFoundException"):
            return [f"{subject}: does not exist"]
        raise
    configuration = described["Configuration"]
    checks = (
        ("State", configuration.get("State"), "Active"),
        ("code", configuration.get("CodeSha256"), wanted.code_sha256),
        ("Handler", configuration.get("Handler"), HANDLER),
        ("Runtime", configuration.get("Runtime"), RUNTIME),
        ("role", configuration.get("Role"), wanted.role_arn),
        ("Timeout", configuration.get("Timeout"), TIMEOUT_SECONDS),
        ("environment", (configuration.get("Environment") or {}).get("Variables"),
         wanted.environment),
    )
    found = [f"{subject}: {name} is not what the release wants" for name, seen, wanted_value
             in checks if seen != wanted_value]
    if not _is_ours(described.get("Tags")):
        found.append(f"{subject}: is missing the tags {_tag_text()}")
    return found


def read_back(iam: Any, lam: Any, wanted: Desired) -> list[str]:
    """One line per way the deployed function or its role differs from ``wanted``."""
    return _function_findings(lam, wanted) + _role_findings(iam, wanted)


# -------------------------------------------------------------------- teardown


def _delete_function(lam: Any, name: str) -> list[str]:
    subject = f"Lambda {name}"
    try:
        found = lam.get_function(FunctionName=name)
    except ClientError as error:
        if _missing(error, "ResourceNotFoundException"):
            return [f"{subject}: already gone"]
        raise
    _require_ours(found.get("Tags"), subject)
    lam.delete_function(FunctionName=name)
    return [f"{subject}: deleted"]


def _still_ours(iam: Any, name: str) -> bool:
    """Re-read the role's tags; False when it is gone, an error when it is not ours."""
    role = _find_role(iam, name)
    if role is None:
        return False
    _require_ours(_role_tags(role), f"IAM role {name}")
    return True


def _delete_role(iam: Any, name: str) -> list[str]:
    gone = [f"IAM role {name}: already gone"]
    if not _still_ours(iam, name):
        return gone
    for attached in iam.list_attached_role_policies(RoleName=name)["AttachedPolicies"]:
        if not _still_ours(iam, name):
            return gone
        iam.detach_role_policy(RoleName=name, PolicyArn=attached["PolicyArn"])
    for inline in iam.list_role_policies(RoleName=name)["PolicyNames"]:
        if not _still_ours(iam, name):
            return gone
        iam.delete_role_policy(RoleName=name, PolicyName=inline)
    if not _still_ours(iam, name):
        return gone
    iam.delete_role(RoleName=name)
    return [f"IAM role {name}: deleted"]


def teardown(iam: Any, lam: Any, name: str = settings.INTERCEPTOR_FUNCTION) -> list[str]:
    """Delete the function, then the role, but only what carries ``TAGS``.

    Raises:
        DeployError: When the function or role exists without the tags; nothing of it is deleted.
    """
    return _delete_function(lam, name) + _delete_role(iam, name)


# --------------------------------------------------------------------- outputs


def record_outputs(directory: Path, wanted: Desired, applied_at: str) -> Path:
    """Write what was deployed to ``directory`` as a private file (0600); no ARN, no secret."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    payload = {
        "function_name": wanted.function_name, "role_name": wanted.role_name,
        "code_sha256": wanted.code_sha256, "tags": dict(TAGS), "applied_at": applied_at,
    }
    path = directory / OUTPUT_NAME
    scratch = path.with_suffix(".tmp")
    descriptor = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(json.dumps(payload, indent=2) + "\n")
    scratch.chmod(0o600)
    scratch.replace(path)
    return path


def remove_outputs(directory: Path) -> None:
    """Delete the outputs file if there is one."""
    (directory / OUTPUT_NAME).unlink(missing_ok=True)
