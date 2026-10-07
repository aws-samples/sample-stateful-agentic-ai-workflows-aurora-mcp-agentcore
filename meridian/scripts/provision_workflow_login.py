"""Give meridian_workflow a password that only Secrets Manager knows.

Migration 015 creates the role with NOLOGIN. This script:
1. generates a password and sends Postgres only its SCRAM-SHA-256 verifier; the
   plaintext password never reaches the database, its logs or the Data API request;
2. stores {"username", "password"} in the Secrets Manager secret the Data API uses;
3. creates or updates the managed policy MeridianWorkflowAuroraAccess, which allows
   the Data API on this cluster and GetSecretValue on that secret only;
4. connects as the new login and checks it is meridian_workflow without BYPASSRLS.

Re-running rotates the password and repairs a half-finished run. During a rotation,
between ALTER ROLE and put_secret_value, anything holding the old secret fails, and a
re-run repairs a half-finished rotation. Without --apply
it only reports what it would do. Run from meridian/ with AWS_PROFILE set.
"""

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db.rds_data_client import RDSDataClient  # noqa: E402

ROLE = "meridian_workflow"
SECRET_NAME = "meridian/aurora/workflow-login"
POLICY_NAME = "MeridianWorkflowAuroraAccess"
DATA_API_ACTIONS = (
    "rds-data:ExecuteStatement",
    "rds-data:BeginTransaction",
    "rds-data:CommitTransaction",
    "rds-data:RollbackTransaction",
)
VERIFIER = re.compile(
    r"SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+"
)
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
ENV_KEY = "AURORA_WORKFLOW_SECRET_ARN"
MAX_POLICY_VERSIONS = 5
VERIFY_ATTEMPTS = 3
VERIFY_DELAY_SECONDS = 2


def _mask(text: str) -> str:
    """Hide the 12-digit account id in an ARN before it is printed."""
    return re.sub(r":\d{12}:", ":<account>:", text)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@dataclass(frozen=True)
class Provisioned:
    """What the run created or found.

    Attributes:
        secret_arn: The login's Secrets Manager secret.
        policy_arn: The managed policy the workflow Runtime role attaches.
        verified_user: The user a Data API call with the secret ran as, or None on a dry run.
    """

    secret_arn: Optional[str]
    policy_arn: Optional[str]
    verified_user: Optional[str]


def scram_sha256_verifier(password: str, *, salt: Optional[bytes] = None,
                          iterations: int = 4096) -> str:
    """Return the SCRAM-SHA-256 verifier Postgres stores for ``password``.

    Args:
        password: An ASCII password (no SASLprep normalization is applied).
        salt: 16 random bytes by default; fixed only in tests.
        iterations: PBKDF2 rounds, Postgres's default 4096.
    """
    salt = salt if salt is not None else secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    return f"SCRAM-SHA-256${iterations}:{_b64(salt)}${_b64(stored_key)}:{_b64(server_key)}"


def policy_document(cluster_arn: str, secret_arn: str) -> Dict[str, Any]:
    """Return the workflow Runtime's Aurora access policy."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "WorkflowDataApi", "Effect": "Allow", "Action": list(DATA_API_ACTIONS),
             "Resource": cluster_arn},
            {"Sid": "WorkflowLoginSecret", "Effect": "Allow",
             "Action": "secretsmanager:GetSecretValue", "Resource": secret_arn},
        ],
    }


def _find_secret_arn(sm) -> Optional[str]:
    """Return the login secret's ARN, or None when it does not exist."""
    try:
        return sm.describe_secret(SecretId=SECRET_NAME).get("ARN") or None
    except ClientError as err:
        if err.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise


def _store_secret(sm, password: str, existing_arn: Optional[str]) -> str:
    """Create the login secret, or write a new value into the existing one."""
    value = json.dumps({"username": ROLE, "password": password})
    if existing_arn is None:
        created = sm.create_secret(
            Name=SECRET_NAME,
            SecretString=value,
            Description="Aurora login for the MeridianWorkflow AgentCore Runtime",
            Tags=[{"Key": "project", "Value": "meridian"}],
        )
        return created["ARN"]
    sm.put_secret_value(SecretId=SECRET_NAME, SecretString=value)
    return existing_arn


def _set_login(master, cluster_arn: str, master_secret_arn: str, database: str,
               verifier: str) -> None:
    """Enable LOGIN on the role with ``verifier`` as its password. Never logs the SQL."""
    if not VERIFIER.fullmatch(verifier):
        raise SystemExit("refusing to send a malformed SCRAM verifier to Postgres")
    master.execute_statement(
        resourceArn=cluster_arn,
        secretArn=master_secret_arn,
        database=database,
        sql=f"ALTER ROLE {ROLE} LOGIN PASSWORD '{verifier}'",
    )


def _find_policy_arn(iam, account: str) -> Optional[str]:
    """Return the managed policy's ARN, or None when it does not exist."""
    arn = f"arn:aws:iam::{account}:policy/{POLICY_NAME}"
    try:
        policy = iam.get_policy(PolicyArn=arn).get("Policy")
    except ClientError as err:
        if err.response["Error"]["Code"] == "NoSuchEntity":
            return None
        raise
    return policy["Arn"] if policy else None


def _upsert_policy(iam, existing_arn: Optional[str], document: Dict[str, Any],
                   account: str) -> str:
    """Create the managed policy, or add a default version to the existing one."""
    body = json.dumps(document)
    if existing_arn is None:
        created = iam.create_policy(
            PolicyName=POLICY_NAME,
            PolicyDocument=body,
            Description="Data API access to the Meridian cluster and the workflow login secret",
        )
        arn = created["Policy"]["Arn"]
        if arn.split(":")[4] != account:
            raise SystemExit(
                f"created policy landed in account {_mask_id(arn.split(':')[4])} "
                f"instead of {_mask_id(account)}; stop and check AWS_PROFILE"
            )
        return arn
    versions = iam.list_policy_versions(PolicyArn=existing_arn).get("Versions", [])
    if len(versions) >= MAX_POLICY_VERSIONS:
        oldest = min((v for v in versions if not v["IsDefaultVersion"]),
                     key=lambda v: v["CreateDate"])
        iam.delete_policy_version(PolicyArn=existing_arn, VersionId=oldest["VersionId"])
    iam.create_policy_version(PolicyArn=existing_arn, PolicyDocument=body, SetAsDefault=True)
    return existing_arn


def _query_as_login(cluster_arn: str, secret_arn: str, database: str,
                    sleep: Callable[[float], None]) -> Optional[Dict[str, Any]]:
    """Query as the new login, retrying while a new secret becomes readable."""
    client = RDSDataClient(cluster_arn=cluster_arn, secret_arn=secret_arn, database=database)
    for attempt in range(1, VERIFY_ATTEMPTS + 1):
        try:
            return asyncio.run(client.execute_one(
                "SELECT current_user AS u, "
                "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS b"
            ))
        except ClientError as err:
            if attempt == VERIFY_ATTEMPTS:
                code = err.response.get("Error", {}).get("Code", "unknown")
                raise SystemExit(
                    f"the new login could not connect ({code}); re-run this script to repair"
                ) from None
            sleep(VERIFY_DELAY_SECONDS)
    return None


def _verify_login(cluster_arn: str, secret_arn: str, database: str, *,
                  sleep: Callable[[float], None] = time.sleep) -> str:
    """Run a Data API query with the new secret and return the user it ran as."""
    row = _query_as_login(cluster_arn, secret_arn, database, sleep)
    if not row or row["u"] != ROLE or row["b"] is not False:
        raise SystemExit(
            f"the new secret did not connect as {ROLE} without BYPASSRLS; "
            "re-run this script to repair"
        )
    return row["u"]


def _print_plan(cluster_arn: str, secret_arn: Optional[str], policy_arn: Optional[str]) -> None:
    print(f"role:   {ROLE} (enable LOGIN with a SCRAM verifier)")
    print(f"secret: {SECRET_NAME} ({'update' if secret_arn else 'create'})")
    print(f"policy: {POLICY_NAME} ({'new version' if policy_arn else 'create'})")
    print(f"cluster: {_mask(cluster_arn)}")
    print("dry run: pass --apply to make these changes")


def provision(*, sm, iam, master, cluster_arn: str, database: str, apply: bool,
              master_secret_arn: str = "", password: Optional[str] = None) -> Provisioned:
    """Provision the login, its secret and its policy, then verify the login.

    Each step is idempotent and ordered so that a failure is repaired by a re-run:
    the verifier is set first, then the secret is overwritten with the matching
    password, then the policy is written, then the login is exercised.
    """
    if apply and not master_secret_arn:
        raise ValueError("master_secret_arn is required to set the login's verifier")
    account = cluster_arn.split(":")[4]
    secret_arn = _find_secret_arn(sm)
    policy_arn = _find_policy_arn(iam, account)
    if not apply:
        _print_plan(cluster_arn, secret_arn, policy_arn)
        return Provisioned(secret_arn, policy_arn, None)
    password = password or secrets.token_urlsafe(32)
    _set_login(master, cluster_arn, master_secret_arn, database,
               scram_sha256_verifier(password))
    secret_arn = _store_secret(sm, password, secret_arn)
    print(f"secret: {_mask(secret_arn)}")
    policy_arn = _upsert_policy(
        iam, policy_arn, policy_document(cluster_arn, secret_arn), account)
    print(f"policy: {_mask(policy_arn)}")
    user = _verify_login(cluster_arn, secret_arn, database)
    print(f"verified: {user} without BYPASSRLS")
    return Provisioned(secret_arn, policy_arn, user)


def _write_env(secret_arn: str) -> None:
    """Replace or append the one ``AURORA_WORKFLOW_SECRET_ARN`` line in ``.env``."""
    line = f"{ENV_KEY}={secret_arn}"
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    if any(entry.startswith(f"{ENV_KEY}=") for entry in lines):
        lines = [line if entry.startswith(f"{ENV_KEY}=") else entry for entry in lines]
    else:
        lines.append(line)
    ENV_FILE.write_text("\n".join(lines) + "\n")


def _require_account(sts, cluster_arn: str) -> None:
    """Stop before any write when the credentials belong to another account."""
    caller = sts.get_caller_identity()["Account"]
    expected = cluster_arn.split(":")[4]
    if caller != expected:
        raise SystemExit(
            f"credentials are for account {_mask_id(caller)} but the cluster is in "
            f"{_mask_id(expected)}; check AWS_PROFILE"
        )


def _mask_id(account: str) -> str:
    return account[-4:].rjust(12, "*")


def main() -> None:
    """Parse the CLI, run ``provision`` and optionally record the secret ARN."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="make the changes")
    parser.add_argument("--write-env", action="store_true",
                        help=f"write {ENV_KEY} into meridian/.env")
    args = parser.parse_args()
    load_dotenv(ENV_FILE)
    cluster_arn = os.environ["AURORA_CLUSTER_ARN"]
    region = cluster_arn.split(":")[3]
    _require_account(boto3.client("sts", region_name=region), cluster_arn)
    result = provision(
        sm=boto3.client("secretsmanager", region_name=region),
        iam=boto3.client("iam"),
        master=boto3.client("rds-data", region_name=region),
        cluster_arn=cluster_arn,
        master_secret_arn=os.environ["AURORA_SECRET_ARN"],
        database=os.getenv("AURORA_DATABASE", "meridian"),
        apply=args.apply,
    )
    if args.write_env and result.verified_user and result.secret_arn:
        _write_env(result.secret_arn)
    elif args.write_env:
        print(f"nothing written: {ENV_KEY} is only written after a verified --apply run")


if __name__ == "__main__":
    main()
