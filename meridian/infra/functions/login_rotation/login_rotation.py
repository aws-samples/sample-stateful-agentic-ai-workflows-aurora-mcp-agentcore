"""Secrets Manager rotation for one Aurora workload login, with the alternating users strategy.

The secret holds ``{"username", "password"}`` for ``meridian_x`` or its clone ``meridian_x_clone``.
Each rotation sets a new password on the user the secret does not name, then makes the secret name
that user. The user the secret named before keeps its old password, so a consumer that still holds
the previous value keeps working until the next rotation.

The cluster has no VPC path from Lambda, so every statement goes through the RDS Data API as the
``meridian_rotator`` login (migration 034). That login holds CREATEROLE and ADMIN OPTION on the
workload logins and their clones, which is what ALTER ROLE ... PASSWORD needs on PostgreSQL 16 and
later; it cannot touch the master, and it cannot grant BYPASSRLS or SUPERUSER. Only a SCRAM-SHA-256
verifier is sent to Postgres, never the password.

The Data API takes a secret ARN and reads its AWSCURRENT version, so testSecret cannot connect with
the AWSPENDING credential. testSecret (``check_secret``) checks the two roles instead (same
attributes and limits, the target can log in). finishSecret promotes the pending version, connects
through the Data API with the new current version, and moves AWSCURRENT back when that fails.
Moving back is safe because the other user's password was not changed.

Every step is idempotent: Secrets Manager calls a step again after a failure or a timeout.
Errors are fixed phrases with the AWS error code in the log, never the service's own text.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

CLONE_SUFFIX = "_clone"
ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,40}")
CURRENT = "AWSCURRENT"
PENDING = "AWSPENDING"
PREVIOUS = "AWSPREVIOUS"
VERIFY_ATTEMPTS = 3
VERIFY_DELAY_SECONDS = 2.0
ITERATIONS = 4096

PHRASE_CONFIG = "Rotation failed: the rotation Lambda is not configured for this secret."
PHRASE_SECRET = "Rotation failed: the secret or its version is not in the expected state."
PHRASE_ROLE = "Rotation failed: the secret names a role this Lambda does not rotate."
PHRASE_DATA_API = "Rotation failed: the Data API refused the request."
PHRASE_PAIR = "Rotation failed: the login and its clone differ or are missing."
PHRASE_VERIFY = "Rotation failed: the new credential did not connect, the old one is restored."

ROLE_QUERY = (
    "SELECT r.rolname, r.rolcanlogin, r.rolsuper, r.rolbypassrls, r.rolcreaterole, "
    "r.rolcreatedb, r.rolreplication, r.rolinherit, r.rolconnlimit, "
    "coalesce((SELECT string_agg(c, ',' ORDER BY c) FROM unnest(r.rolconfig) c), '') AS config, "
    "pg_has_role(CAST(:clone AS name), CAST(:login AS name), 'MEMBER') AS clone_is_member "
    "FROM pg_roles r WHERE r.rolname IN (:login, :clone)"
)
CONNECT_QUERY = (
    "SELECT current_user AS u, "
    "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS b"
)
VERIFIER = re.compile(r"SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+")
FORBIDDEN_ATTRIBUTES = (
    "rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb", "rolreplication",
)
SAME_ATTRIBUTES = ("rolinherit", "rolconnlimit", "config")


class RotationError(Exception):
    """A rotation step failed; the message is a fixed phrase that is safe to log."""


@dataclass(frozen=True)
class Settings:
    """What one rotation Lambda is allowed to touch.

    Attributes:
        cluster_arn: The Aurora cluster the Data API calls target.
        database: The database name.
        rotator_secret_arn: The ``meridian_rotator`` login's secret.
        login_role: The workload login; its clone is ``login_role + "_clone"``.
    """

    cluster_arn: str
    database: str
    rotator_secret_arn: str
    login_role: str

    @property
    def clone_role(self) -> str:
        """The clone's role name."""
        return self.login_role + CLONE_SUFFIX


def settings_from_env(env: Optional[Dict[str, str]] = None) -> Settings:
    """Read and check the Lambda's configuration."""
    env = os.environ if env is None else env
    try:
        settings = Settings(
            cluster_arn=env["ROTATION_CLUSTER_ARN"],
            database=env["ROTATION_DATABASE"],
            rotator_secret_arn=env["ROTATION_ROTATOR_SECRET_ARN"],
            login_role=env["ROTATION_LOGIN_ROLE"],
        )
    except KeyError:
        raise RotationError(PHRASE_CONFIG) from None
    if not ROLE_NAME.fullmatch(settings.login_role) or settings.login_role.endswith(CLONE_SUFFIX):
        raise RotationError(PHRASE_CONFIG)
    return settings


def scram_sha256_verifier(password: str, *, salt: Optional[bytes] = None) -> str:
    """Return the SCRAM-SHA-256 verifier Postgres stores for an ASCII ``password``."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, ITERATIONS)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()

    def encode(raw: bytes) -> str:
        return base64.b64encode(raw).decode("ascii")

    return f"SCRAM-SHA-256${ITERATIONS}:{encode(salt)}${encode(stored_key)}:{encode(server_key)}"


def alternate_role(username: str, settings: Settings) -> str:
    """Return the other user of the pair, or refuse a username outside the pair."""
    if username == settings.login_role:
        return settings.clone_role
    if username == settings.clone_role:
        return settings.login_role
    raise RotationError(PHRASE_ROLE)


def _log_error(step: str, err: Exception) -> None:
    code = "unknown"
    if isinstance(err, ClientError):
        code = err.response.get("Error", {}).get("Code", "unknown")
    LOGGER.error("rotation step %s failed (%s: %s)", step, type(err).__name__, code)


def _read_secret(sm, secret_id: str, **version: str) -> Dict[str, Any]:
    """Return a secret version's JSON value, or raise RotationError when it is not usable."""
    value = json.loads(sm.get_secret_value(SecretId=secret_id, **version)["SecretString"])
    if not isinstance(value, dict) or not value.get("username") or not value.get("password"):
        raise RotationError(PHRASE_SECRET)
    return value


def _has_version(sm, secret_id: str, token: str, stage: str) -> bool:
    try:
        sm.get_secret_value(SecretId=secret_id, VersionId=token, VersionStage=stage)
    except ClientError as err:
        if err.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise
    return True


def _field(cell: Dict[str, Any]) -> Any:
    if cell.get("isNull"):
        return None
    return next(iter(cell.values()))


def _rows(response: Dict[str, Any], names: List[str]) -> List[Dict[str, Any]]:
    return [
        {name: _field(cell) for name, cell in zip(names, record, strict=True)}
        for record in response.get("records", [])
    ]


ROLE_COLUMNS = [
    "rolname", "rolcanlogin", "rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb",
    "rolreplication", "rolinherit", "rolconnlimit", "config", "clone_is_member",
]


def _read_roles(data, settings: Settings) -> Dict[str, Dict[str, Any]]:
    response = data.execute_statement(
        resourceArn=settings.cluster_arn,
        secretArn=settings.rotator_secret_arn,
        database=settings.database,
        sql=ROLE_QUERY,
        parameters=[
            {"name": "login", "value": {"stringValue": settings.login_role}},
            {"name": "clone", "value": {"stringValue": settings.clone_role}},
        ],
    )
    return {row["rolname"]: row for row in _rows(response, ROLE_COLUMNS)}


def require_consistent_pair(roles: Dict[str, Dict[str, Any]], settings: Settings) -> None:
    """Refuse to hand out a credential when the clone is missing or differs from the login."""
    login, clone = roles.get(settings.login_role), roles.get(settings.clone_role)
    if login is None or clone is None:
        raise RotationError(PHRASE_PAIR)
    for row in (login, clone):
        if any(row[name] for name in FORBIDDEN_ATTRIBUTES):
            raise RotationError(PHRASE_PAIR)
    if any(login[name] != clone[name] for name in SAME_ATTRIBUTES):
        raise RotationError(PHRASE_PAIR)
    if not clone["clone_is_member"]:
        raise RotationError(PHRASE_PAIR)


def create_secret(sm, secret_id: str, token: str, settings: Settings) -> None:
    """Put a pending version for the other user with a new password, once per token."""
    current = _read_secret(sm, secret_id, VersionStage=CURRENT)
    other = alternate_role(current["username"], settings)
    if _has_version(sm, secret_id, token, PENDING):
        return
    pending = {**current, "username": other, "password": secrets.token_urlsafe(32)}
    sm.put_secret_value(
        SecretId=secret_id, ClientRequestToken=token, SecretString=json.dumps(pending),
        VersionStages=[PENDING],
    )


def set_secret(sm, data, secret_id: str, token: str, settings: Settings) -> None:
    """Set the pending password on the other user, as a SCRAM verifier."""
    pending = _read_secret(sm, secret_id, VersionId=token, VersionStage=PENDING)
    current = _read_secret(sm, secret_id, VersionStage=CURRENT)
    if pending["username"] != alternate_role(current["username"], settings):
        raise RotationError(PHRASE_ROLE)
    require_consistent_pair(_read_roles(data, settings), settings)
    verifier = scram_sha256_verifier(pending["password"])
    if not VERIFIER.fullmatch(verifier) or not ROLE_NAME.fullmatch(pending["username"]):
        raise RotationError(PHRASE_CONFIG)
    data.execute_statement(
        resourceArn=settings.cluster_arn,
        secretArn=settings.rotator_secret_arn,
        database=settings.database,
        sql=f"ALTER ROLE {pending['username']} LOGIN PASSWORD '{verifier}'",
    )


def check_secret(sm, data, secret_id: str, token: str, settings: Settings) -> None:
    """Check the pair and that the pending user can log in; the Data API cannot test a version."""
    pending = _read_secret(sm, secret_id, VersionId=token, VersionStage=PENDING)
    current = _read_secret(sm, secret_id, VersionStage=CURRENT)
    if pending["username"] != alternate_role(current["username"], settings):
        raise RotationError(PHRASE_ROLE)
    roles = _read_roles(data, settings)
    require_consistent_pair(roles, settings)
    if not roles[pending["username"]]["rolcanlogin"]:
        raise RotationError(PHRASE_PAIR)


def _stage_holders(sm, secret_id: str) -> Dict[str, Optional[str]]:
    versions = sm.describe_secret(SecretId=secret_id).get("VersionIdsToStages", {})
    holders: Dict[str, Optional[str]] = {CURRENT: None, PREVIOUS: None}
    for version, stages in versions.items():
        for stage in (CURRENT, PREVIOUS):
            if stage in stages:
                holders[stage] = version
    return holders


def _connects_as_current(sm, data, secret_id: str, settings: Settings,
                         sleep: Callable[[float], None]) -> bool:
    expected = _read_secret(sm, secret_id, VersionStage=CURRENT)["username"]
    for attempt in range(1, VERIFY_ATTEMPTS + 1):
        try:
            response = data.execute_statement(
                resourceArn=settings.cluster_arn, secretArn=secret_id,
                database=settings.database, sql=CONNECT_QUERY,
            )
            row = _rows(response, ["u", "b"])[0]
            return row["u"] == expected and row["b"] is False
        except (ClientError, BotoCoreError) as err:
            _log_error("verify", err)
            if attempt < VERIFY_ATTEMPTS:
                sleep(VERIFY_DELAY_SECONDS)
    return False


def finish_secret(sm, data, secret_id: str, token: str, settings: Settings, *,
                  sleep: Callable[[float], None] = time.sleep) -> None:
    """Promote the pending version, connect with it, and move AWSCURRENT back when it fails."""
    holders = _stage_holders(sm, secret_id)
    if holders[CURRENT] != token:
        sm.update_secret_version_stage(
            SecretId=secret_id, VersionStage=CURRENT, MoveToVersionId=token,
            RemoveFromVersionId=holders[CURRENT],
        )
        old_version = holders[CURRENT]
    else:
        old_version = holders[PREVIOUS]
    if _connects_as_current(sm, data, secret_id, settings, sleep):
        return
    if old_version is not None:
        sm.update_secret_version_stage(
            SecretId=secret_id, VersionStage=CURRENT, MoveToVersionId=old_version,
            RemoveFromVersionId=token,
        )
    raise RotationError(PHRASE_VERIFY)


def rotate(step: str, secret_id: str, token: str, *, sm, data, settings: Settings,
           sleep: Callable[[float], None] = time.sleep) -> None:
    """Run one rotation step after checking the secret is rotating this version."""
    meta = sm.describe_secret(SecretId=secret_id)
    stages = meta.get("VersionIdsToStages", {}).get(token)
    if not meta.get("RotationEnabled") or stages is None:
        raise RotationError(PHRASE_SECRET)
    if CURRENT in stages and step != "finishSecret":
        return
    if CURRENT not in stages and PENDING not in stages:
        raise RotationError(PHRASE_SECRET)
    steps = {
        "createSecret": lambda: create_secret(sm, secret_id, token, settings),
        "setSecret": lambda: set_secret(sm, data, secret_id, token, settings),
        "testSecret": lambda: check_secret(sm, data, secret_id, token, settings),
        "finishSecret": lambda: finish_secret(sm, data, secret_id, token, settings, sleep=sleep),
    }
    if step not in steps:
        raise RotationError(PHRASE_SECRET)
    steps[step]()


def lambda_handler(event: Dict[str, str], _context: Any) -> None:
    """Entry point Secrets Manager calls once per step."""
    step = event.get("Step", "")
    try:
        rotate(
            step, event["SecretId"], event["ClientRequestToken"],
            sm=boto3.client("secretsmanager"), data=boto3.client("rds-data"),
            settings=settings_from_env(),
        )
    except RotationError as err:
        LOGGER.error("rotation step %s failed: %s", step, err)
        raise
    except (ClientError, BotoCoreError) as err:
        _log_error(step, err)
        raise RotationError(PHRASE_DATA_API) from None
