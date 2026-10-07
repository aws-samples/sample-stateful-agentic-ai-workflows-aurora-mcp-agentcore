"""Give the backend, gateway and identity logins passwords that only Secrets Manager knows.

Migration 018 creates meridian_backend, meridian_gateway and meridian_identity with NOLOGIN.
For each login this script:
1. generates a password and sends Postgres only its SCRAM-SHA-256 verifier; the plaintext
   password never reaches the database, its logs or the Data API request;
2. stores {"username", "password"} in that login's own Secrets Manager secret;
3. creates or updates the login's managed policy, which allows the Data API on this cluster
   and GetSecretValue on that one secret;
4. connects as the login and checks it is that role without BYPASSRLS.

Re-running rotates the password and repairs a half-finished run. During a rotation, between
ALTER ROLE and put_secret_value, anything holding the old secret fails. Without --apply it
only reports what it would do. Run from meridian/ with AWS_PROFILE set.
"""

import argparse
import asyncio
import json
import os
import re
import secrets
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db.rds_data_client import RDSDataClient  # noqa: E402
from scripts.provision_workflow_login import (  # noqa: E402
    DATA_API_ACTIONS,
    ENV_FILE,
    VERIFIER,
    scram_sha256_verifier,
)

MAX_POLICY_VERSIONS = 5
VERIFY_ATTEMPTS = 3
VERIFY_DELAY_SECONDS = 2


@dataclass(frozen=True)
class LoginSpec:
    """One login this script provisions.

    Attributes:
        key: The name used on the command line.
        role: The Postgres role migration 018 created.
        secret_name: The Secrets Manager secret holding the role's credential.
        policy_name: The managed policy that lets a workload read that secret.
        env_key: The meridian/.env line that records the secret's ARN.
        purpose: What the login serves, for the secret and policy descriptions.
    """

    key: str
    role: str
    secret_name: str
    policy_name: str
    env_key: str
    purpose: str


LOGINS: Dict[str, LoginSpec] = {
    spec.key: spec
    for spec in (
        LoginSpec("backend", "meridian_backend", "meridian/aurora/backend-login",
                  "MeridianBackendAuroraAccess", "AURORA_BACKEND_SECRET_ARN",
                  "the Meridian backend on App Runner"),
        LoginSpec("gateway", "meridian_gateway", "meridian/aurora/gateway-login",
                  "MeridianGatewayAuroraAccess", "AURORA_GATEWAY_SECRET_ARN",
                  "the MeridianHolds and meridian-semantic-trip-search Lambdas"),
        LoginSpec("identity", "meridian_identity", "meridian/aurora/identity-login",
                  "MeridianIdentityAuroraAccess", "AURORA_IDENTITY_SECRET_ARN",
                  "the Cognito pre-token-generation Lambda"),
    )
}


@dataclass(frozen=True)
class Provisioned:
    """What one login's run created or found.

    Attributes:
        role: The Postgres role.
        secret_arn: The login's Secrets Manager secret.
        policy_arn: The managed policy that grants read access to that secret.
        verified_user: The user a Data API call with the secret ran as, or None on a dry run.
    """

    role: str
    secret_arn: Optional[str]
    policy_arn: Optional[str]
    verified_user: Optional[str]


def mask_account(text: str) -> str:
    """Hide the 12-digit account id in an ARN before it is printed, as <acct>."""
    return re.sub(r":\d{12}:", ":<acct>:", text)


def mask_id(account: str) -> str:
    """Show only the last four digits of an account id."""
    return account[-4:].rjust(12, "*")


def policy_document(spec: LoginSpec, cluster_arn: str, secret_arn: str) -> Dict[str, Any]:
    """Return the managed policy for one login: the Data API on the cluster, one secret."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": f"{spec.key.capitalize()}DataApi", "Effect": "Allow",
             "Action": list(DATA_API_ACTIONS), "Resource": cluster_arn},
            {"Sid": f"{spec.key.capitalize()}LoginSecret", "Effect": "Allow",
             "Action": "secretsmanager:GetSecretValue", "Resource": secret_arn},
        ],
    }


def _find_secret_arn(sm, spec: LoginSpec) -> Optional[str]:
    """Return the login secret's ARN, or None when it does not exist."""
    try:
        return sm.describe_secret(SecretId=spec.secret_name).get("ARN") or None
    except ClientError as err:
        if err.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise


def _store_secret(sm, spec: LoginSpec, password: str, existing_arn: Optional[str]) -> str:
    """Create the login secret, or write a new value into the existing one."""
    value = json.dumps({"username": spec.role, "password": password})
    if existing_arn is None:
        created = sm.create_secret(
            Name=spec.secret_name,
            SecretString=value,
            Description=f"Aurora login for {spec.purpose}",
            Tags=[{"Key": "project", "Value": "meridian"}],
        )
        return created["ARN"]
    sm.put_secret_value(SecretId=spec.secret_name, SecretString=value)
    return existing_arn


def _set_login(master, spec: LoginSpec, cluster_arn: str, master_secret_arn: str,
               database: str, verifier: str) -> None:
    """Enable LOGIN on the role with ``verifier`` as its password. Never logs the SQL."""
    if not VERIFIER.fullmatch(verifier):
        raise SystemExit("refusing to send a malformed SCRAM verifier to Postgres")
    master.execute_statement(
        resourceArn=cluster_arn,
        secretArn=master_secret_arn,
        database=database,
        sql=f"ALTER ROLE {spec.role} LOGIN PASSWORD '{verifier}'",
    )


def _find_policy_arn(iam, spec: LoginSpec, account: str) -> Optional[str]:
    """Return the managed policy's ARN, or None when it does not exist."""
    arn = f"arn:aws:iam::{account}:policy/{spec.policy_name}"
    try:
        policy = iam.get_policy(PolicyArn=arn).get("Policy")
    except ClientError as err:
        if err.response["Error"]["Code"] == "NoSuchEntity":
            return None
        raise
    return policy["Arn"] if policy else None


def _upsert_policy(iam, spec: LoginSpec, existing_arn: Optional[str],
                   document: Dict[str, Any], account: str) -> str:
    """Create the managed policy, or add a default version to the existing one."""
    body = json.dumps(document)
    if existing_arn is None:
        created = iam.create_policy(
            PolicyName=spec.policy_name,
            PolicyDocument=body,
            Description=f"Data API access to the Meridian cluster and the {spec.role} secret",
        )
        arn = created["Policy"]["Arn"]
        if arn.split(":")[4] != account:
            raise SystemExit(
                f"created policy landed in account {mask_id(arn.split(':')[4])} "
                f"instead of {mask_id(account)}; stop and check AWS_PROFILE"
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
    """Query as the login, retrying while a new secret becomes readable."""
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


def _verify_login(spec: LoginSpec, cluster_arn: str, secret_arn: str, database: str, *,
                  sleep: Callable[[float], None] = time.sleep) -> str:
    """Run a Data API query with the login's secret and return the user it ran as."""
    row = _query_as_login(cluster_arn, secret_arn, database, sleep)
    if not row or row["u"] != spec.role or row["b"] is not False:
        raise SystemExit(
            f"the new secret did not connect as {spec.role} without BYPASSRLS; "
            "re-run this script to repair"
        )
    return row["u"]


def _print_plan(spec: LoginSpec, cluster_arn: str, secret_arn: Optional[str],
                policy_arn: Optional[str]) -> None:
    print(f"role:   {spec.role} (enable LOGIN with a SCRAM verifier)")
    print(f"secret: {spec.secret_name} ({'update' if secret_arn else 'create'})")
    print(f"policy: {spec.policy_name} ({'new version' if policy_arn else 'create'})")
    print(f"cluster: {mask_account(cluster_arn)}")
    print("dry run: pass --apply to make these changes")


def provision_login(spec: LoginSpec, *, sm, iam, master, cluster_arn: str, database: str,
                    apply: bool, master_secret_arn: str = "",
                    password: Optional[str] = None) -> Provisioned:
    """Provision one login, its secret and its policy, then verify the login.

    Each step is idempotent and ordered so that a failure is repaired by a re-run: the
    verifier is set first, then the secret is overwritten with the matching password, then
    the policy is written, then the login is exercised.
    """
    if apply and not master_secret_arn:
        raise ValueError("master_secret_arn is required to set the login's verifier")
    account = cluster_arn.split(":")[4]
    secret_arn = _find_secret_arn(sm, spec)
    policy_arn = _find_policy_arn(iam, spec, account)
    if not apply:
        _print_plan(spec, cluster_arn, secret_arn, policy_arn)
        return Provisioned(spec.role, secret_arn, policy_arn, None)
    password = password or secrets.token_urlsafe(32)
    _set_login(master, spec, cluster_arn, master_secret_arn, database,
               scram_sha256_verifier(password))
    secret_arn = _store_secret(sm, spec, password, secret_arn)
    print(f"secret: {mask_account(secret_arn)}")
    policy_arn = _upsert_policy(
        iam, spec, policy_arn, policy_document(spec, cluster_arn, secret_arn), account)
    print(f"policy: {mask_account(policy_arn)}")
    user = _verify_login(spec, cluster_arn, secret_arn, database)
    print(f"verified: {user} without BYPASSRLS")
    return Provisioned(spec.role, secret_arn, policy_arn, user)


def write_env(env_file: Path, env_key: str, secret_arn: str) -> None:
    """Replace or append the one ``env_key`` line in ``env_file``, atomically."""
    line = f"{env_key}={secret_arn}"
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    if any(entry.startswith(f"{env_key}=") for entry in lines):
        lines = [line if entry.startswith(f"{env_key}=") else entry for entry in lines]
    else:
        lines.append(line)
    mode = stat.S_IMODE(env_file.stat().st_mode) if env_file.exists() else 0o600
    handle, temp_name = tempfile.mkstemp(dir=env_file.parent, prefix=f".{env_file.name}.")
    try:
        with os.fdopen(handle, "w") as temp:
            temp.write("\n".join(lines) + "\n")
        os.chmod(temp_name, mode)
        os.replace(temp_name, env_file)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def require_account(sts, cluster_arn: str) -> None:
    """Stop before any write when the credentials belong to another account."""
    caller = sts.get_caller_identity()["Account"]
    expected = cluster_arn.split(":")[4]
    if caller != expected:
        raise SystemExit(
            f"credentials are for account {mask_id(caller)} but the cluster is in "
            f"{mask_id(expected)}; check AWS_PROFILE"
        )


def redact(text: str) -> str:
    """Hide every 12-digit account id in ``text``."""
    return re.sub(r"(?<!\d)\d{12}(?!\d)", "<acct>", mask_account(text))


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set; add it to meridian/.env or export it, then re-run")
    return value


def _run(args: argparse.Namespace) -> None:
    load_dotenv(ENV_FILE)
    cluster_arn = _require_env("AURORA_CLUSTER_ARN")
    master_secret_arn = _require_env("AURORA_SECRET_ARN") if args.apply else \
        os.environ.get("AURORA_SECRET_ARN", "")
    region = cluster_arn.split(":")[3]
    require_account(boto3.client("sts", region_name=region), cluster_arn)
    chosen = list(LOGINS.values()) if args.login == "all" else [LOGINS[args.login]]
    for spec in chosen:
        result = provision_login(
            spec,
            sm=boto3.client("secretsmanager", region_name=region),
            iam=boto3.client("iam"),
            master=boto3.client("rds-data", region_name=region),
            cluster_arn=cluster_arn,
            master_secret_arn=master_secret_arn,
            database=os.getenv("AURORA_DATABASE", "meridian"),
            apply=args.apply,
        )
        if args.write_env and result.verified_user and result.secret_arn:
            write_env(ENV_FILE, spec.env_key, result.secret_arn)
        elif args.write_env:
            print(f"nothing written: {spec.env_key} is only written after a verified --apply run")


def main(argv: Optional[list] = None) -> None:
    """Parse the CLI, provision the chosen logins and optionally record their secret ARNs."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--login", choices=[*LOGINS, "all"], default="all",
                        help="which login to provision (default: all three)")
    parser.add_argument("--apply", action="store_true", help="make the changes")
    parser.add_argument("--write-env", action="store_true",
                        help="write each secret ARN into meridian/.env after a verified run")
    args = parser.parse_args(argv)
    try:
        _run(args)
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code", "unknown")
        raise SystemExit(
            f"{err.operation_name} failed ({code}): {redact(str(err))}; "
            "check AWS_PROFILE and that the profile has permission for this call"
        ) from None
    except KeyError as err:
        raise SystemExit(
            f"unexpected response or setting missing {redact(str(err))}; re-run to repair"
        ) from None


if __name__ == "__main__":
    main()
