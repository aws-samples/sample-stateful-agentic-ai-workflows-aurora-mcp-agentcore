"""Create the two sign-in users, keep their passwords in two places, and bind them to travelers.

Jordan Morgan is the main traveler. Jordan Lee is the decoy: a second real person who
can sign in and must be refused everything that belongs to Jordan Morgan. For each user this:
1. creates the Cognito user, or resets the existing one, with a generated password;
2. stores {"username", "password"} in Secrets Manager under meridian/cognito/<key>;
3. stores the password in the macOS Keychain (service meridian-cognito), through
   ``security -i`` so it never appears in a process list;
4. writes the binding row (identity_provider='cognito', subject_id=<sub>) that the pre-token
   trigger reads, as the master login, which is the only login allowed to grant.

Before anything is changed, every chosen user's traveler must exist in ``travelers`` (the binding
has a foreign key on it), and an existing user's sub must not already hold an active cognito
binding to a different traveler; either problem stops the run with the fix named. A reset keeps the
user's ``sub``, so it does not refresh ``name`` or ``picture`` (change those in the console or with
admin_update_user_attributes) and it does not revoke extra bindings the same sub holds, which is
why the second check refuses to continue instead of leaving them.

The password is printed nowhere and written to no file. Without --apply it reports what it
would do. Run from meridian/ with AWS_PROFILE set, after the identity stack is deployed and
scripts/sync_cognito_env.py --write has put the pool id in meridian/.env.
"""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.provision_service_logins import (  # noqa: E402
    ENV_FILE,
    mask_account,
    redact,
    require_account,
)

PROVIDER = "cognito"
KEYCHAIN_SERVICE = "meridian-cognito"
GRANTED_BY = "scripts/seed_cognito_users.py"


@dataclass(frozen=True)
class SeedUser:
    """One person who can sign in.

    Attributes:
        key: The name used on the command line and in the secret name.
        email: The Cognito username, because the pool signs in by email.
        name: The display name carried in the ID token.
        traveler_id: The traveler the binding row grants.
        picture: A site-relative photo URL for the ID token, or None for initials.
        seed_script: The script that creates the traveler row this user is bound to.
    """

    key: str
    email: str
    name: str
    traveler_id: str
    picture: Optional[str]
    seed_script: str

    @property
    def secret_name(self) -> str:
        return f"meridian/cognito/{self.key}"


USERS: Dict[str, SeedUser] = {
    user.key: user
    for user in (
        SeedUser("jordan", "jordan.morgan@example.com", "Jordan Morgan",
                 "trv_meridian_demo", "/travel/jordan-morgan.jpg", "scripts/seed_data.py"),
        SeedUser("decoy", "jordan.lee@example.com", "Jordan Lee", "trv_demo_decoy", None,
                 "scripts/apply_rls_force_and_decoy.py"),
    )
}


def generate_password() -> str:
    """A 23-character password: 19 random characters plus a suffix that meets the pool policy."""
    return secrets.token_urlsafe(14) + "-Aa1"


def binding_id(subject: str, traveler_id: str) -> str:
    """The stable id of the binding between one Cognito subject and one traveler."""
    digest = hashlib.sha256(f"{PROVIDER}:{subject}:{traveler_id}".encode()).hexdigest()[:16]
    return f"bind_{digest}"


class PartialSeedError(Exception):
    """An AWS call failed after Cognito's password for ``user_key`` may have changed."""

    def __init__(self, user_key: str, cause: Exception):
        super().__init__(user_key)
        self.user_key, self.cause = user_key, cause


def repair_hint(user_key: str) -> str:
    """What to run when the new password may be stored nowhere safe."""
    return (f"Cognito's password for {user_key} is currently stored nowhere safe; "
            f"re-run with --apply to reset and store it again "
            f"(scripts/seed_cognito_users.py --user {user_key} --apply)")


def _attributes(user: SeedUser) -> list:
    attributes = [
        {"Name": "email", "Value": user.email},
        {"Name": "email_verified", "Value": "true"},
        {"Name": "name", "Value": user.name},
    ]
    if user.picture:
        attributes.append({"Name": "picture", "Value": user.picture})
    return attributes


def find_user(idp, pool_id: str, user: SeedUser) -> Optional[Dict[str, Any]]:
    """The existing Cognito user, or None."""
    try:
        return idp.admin_get_user(UserPoolId=pool_id, Username=user.email)
    except ClientError as err:
        if err.response["Error"]["Code"] == "UserNotFoundException":
            return None
        raise


def upsert_user(idp, pool_id: str, user: SeedUser, password: str) -> str:
    """Create or reset the user with ``password`` and return its ``sub``."""
    if find_user(idp, pool_id, user) is None:
        idp.admin_create_user(
            UserPoolId=pool_id, Username=user.email, UserAttributes=_attributes(user),
            MessageAction="SUPPRESS",
        )
    idp.admin_set_user_password(
        UserPoolId=pool_id, Username=user.email, Password=password, Permanent=True)
    return sub_of(idp.admin_get_user(UserPoolId=pool_id, Username=user.email))


def sub_of(cognito_user: Dict[str, Any]) -> str:
    """The ``sub`` attribute of an admin_get_user response."""
    return next(a["Value"] for a in cognito_user["UserAttributes"] if a["Name"] == "sub")


def store_secret(sm, user: SeedUser, password: str) -> None:
    """Create the user's secret, or write the new password into the existing one."""
    value = json.dumps({"username": user.email, "password": password})
    try:
        sm.describe_secret(SecretId=user.secret_name)
    except ClientError as err:
        if err.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        sm.create_secret(
            Name=user.secret_name, SecretString=value,
            Description=f"Cognito sign-in for {user.name}",
            Tags=[{"Key": "project", "Value": "meridian"}],
        )
        return
    sm.put_secret_value(SecretId=user.secret_name, SecretString=value)


def store_keychain(user: SeedUser, password: str,
                   run: Callable[..., Any] = subprocess.run) -> None:
    """Put the password in the macOS Keychain without placing it in any argument list."""
    if sys.platform != "darwin":
        raise SystemExit("The macOS Keychain is the second home of the sign-in passwords; "
                         "run this script on the presenter's Mac")
    if not all(c.isalnum() or c in "-_" for c in password):
        raise SystemExit("refusing to quote an unexpected password alphabet")
    command = (f"add-generic-password -U -s {KEYCHAIN_SERVICE} -a {user.email} "
               f"-l 'Meridian sign-in for {user.name}' -w '{password}'\n")
    done = run(["security", "-i"], input=command, text=True, capture_output=True, check=False)
    if done.returncode != 0:
        raise SystemExit(f"the Keychain refused the {user.key} password (exit {done.returncode}); "
                         f"{repair_hint(user.key)}")
    found = run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", user.email],
                text=True, capture_output=True, check=False)
    if found.returncode != 0:
        raise SystemExit(
            f"Keychain item for {user.key} missing after add; Cognito and Secrets Manager "
            f"already hold the new password, fix the Keychain and re-run "
            f"scripts/seed_cognito_users.py --user {user.key} --apply")


def _select(rds, conn: Dict[str, str], sql: str, **values: str) -> list:
    """The first column of every row ``sql`` returns, as strings."""
    response = rds.execute_statement(
        **conn, sql=sql,
        parameters=[{"name": k, "value": {"stringValue": v}} for k, v in values.items()])
    return [row[0]["stringValue"] for row in response.get("records", [])]


def require_traveler(rds, conn: Dict[str, str], user: SeedUser) -> None:
    """Stop when the traveler row is missing, before anything is created for the user."""
    found = _select(rds, conn, "SELECT traveler_id FROM travelers WHERE traveler_id = :traveler_id",
                    traveler_id=user.traveler_id)
    if not found:
        raise SystemExit(
            f"traveler {user.traveler_id} for {user.key} is not in the travelers table; run "
            f"{user.seed_script} first, then re-run this script")


def require_no_other_binding(rds, conn: Dict[str, str], user: SeedUser, subject: str) -> None:
    """Stop when the sub is already active on a traveler other than the intended one."""
    others = _select(
        rds, conn,
        "SELECT traveler_id FROM traveler_identity_bindings WHERE identity_provider = :provider "
        "AND subject_id = :subject_id AND status = 'active' AND traveler_id <> :traveler_id",
        provider=PROVIDER, subject_id=subject, traveler_id=user.traveler_id)
    if others:
        raise SystemExit(
            f"{user.key}'s Cognito user already has an active binding to {', '.join(others)} as "
            f"well as {user.traveler_id}, and a reset would not revoke it. Revoke the extra "
            "binding (set status = 'revoked' in traveler_identity_bindings as the master login), "
            "then re-run")


def preflight(user: SeedUser, *, idp, rds, pool_id: str, cluster_arn: str,
              master_secret_arn: str, database: str) -> None:
    """Read-only checks that must pass before the user is created or reset."""
    conn = {"resourceArn": cluster_arn, "secretArn": master_secret_arn, "database": database}
    require_traveler(rds, conn, user)
    existing = find_user(idp, pool_id, user)
    if existing is not None:
        require_no_other_binding(rds, conn, user, sub_of(existing))


def write_binding(rds, cluster_arn: str, master_secret_arn: str, database: str,
                  user: SeedUser, subject: str) -> None:
    """Grant the Cognito subject its one traveler, as the master login."""
    rds.execute_statement(
        resourceArn=cluster_arn, secretArn=master_secret_arn, database=database,
        sql=(
            "INSERT INTO traveler_identity_bindings (binding_id, identity_provider, subject_id, "
            "traveler_id, status, granted_by) VALUES (:binding_id, :provider, :subject_id, "
            ":traveler_id, 'active', :granted_by) "
            "ON CONFLICT (identity_provider, subject_id, traveler_id) DO UPDATE SET "
            "status = 'active', granted_by = EXCLUDED.granted_by, expires_at = NULL"
        ),
        parameters=[
            {"name": "binding_id", "value": {"stringValue": binding_id(subject, user.traveler_id)}},
            {"name": "provider", "value": {"stringValue": PROVIDER}},
            {"name": "subject_id", "value": {"stringValue": subject}},
            {"name": "traveler_id", "value": {"stringValue": user.traveler_id}},
            {"name": "granted_by", "value": {"stringValue": GRANTED_BY}},
        ],
    )


def seed_user(user: SeedUser, *, idp, sm, rds, pool_id: str, cluster_arn: str,
              master_secret_arn: str, database: str, apply: bool,
              keychain: Optional[Callable[[SeedUser, str], None]] = None,
              password: Optional[str] = None) -> Optional[str]:
    """Seed one user. Returns the Cognito sub, or None on a dry run."""
    exists = find_user(idp, pool_id, user) is not None
    if not apply:
        verb = "reset" if exists else "create"
        print(f"{user.key}: would {verb} {user.email}, store {user.secret_name} and a Keychain "
              f"item, bind {PROVIDER} -> {user.traveler_id}")
        return None
    password = password or generate_password()
    try:
        subject = upsert_user(idp, pool_id, user, password)
        store_secret(sm, user, password)
        (keychain or store_keychain)(user, password)
        write_binding(rds, cluster_arn, master_secret_arn, database, user, subject)
    except (ClientError, BotoCoreError) as err:
        raise PartialSeedError(user.key, err) from None
    print(f"{user.key}: {'reset' if exists else 'created'}, bound {PROVIDER} -> "
          f"{user.traveler_id}, sub ends {subject[-4:]}")
    return subject


def _run(args: argparse.Namespace) -> None:
    load_dotenv(ENV_FILE)
    cluster_arn = os.environ["AURORA_CLUSTER_ARN"]
    pool_id = os.environ.get("MERIDIAN_COGNITO_USER_POOL_ID", "")
    if not pool_id:
        raise SystemExit("MERIDIAN_COGNITO_USER_POOL_ID is not set; deploy the identity stack "
                         "and run scripts/sync_cognito_env.py --write")
    region = cluster_arn.split(":")[3]
    require_account(boto3.client("sts", region_name=region), cluster_arn)
    print(f"cluster: {mask_account(cluster_arn)}")
    master_secret_arn = os.environ["AURORA_SECRET_ARN"]
    idp = boto3.client("cognito-idp", region_name=region)
    sm = boto3.client("secretsmanager", region_name=region)
    rds = boto3.client("rds-data", region_name=region)
    database = os.getenv("AURORA_DATABASE", "meridian")
    chosen = list(USERS.values()) if args.user == "all" else [USERS[args.user]]
    for user in chosen:
        preflight(user, idp=idp, rds=rds, pool_id=pool_id, cluster_arn=cluster_arn,
                  master_secret_arn=master_secret_arn, database=database)
    for user in chosen:
        seed_user(
            user, idp=idp, sm=sm, rds=rds, pool_id=pool_id, cluster_arn=cluster_arn,
            master_secret_arn=master_secret_arn, database=database, apply=args.apply,
        )


def _aws_message(err: Exception) -> str:
    """A redacted one-line account of an AWS failure."""
    if isinstance(err, ClientError):
        code = err.response.get("Error", {}).get("Code", "unknown")
        return (f"{err.operation_name} failed ({code}): {redact(str(err))}; "
                "check AWS_PROFILE and that the profile has permission for this call")
    return f"AWS call failed: {redact(str(err))}; check AWS_PROFILE, the region and the network"


def main(argv: Optional[list] = None) -> None:
    """Parse the CLI and seed the chosen users."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--user", choices=[*USERS, "all"], default="all")
    parser.add_argument("--apply", action="store_true", help="make the changes")
    args = parser.parse_args(argv)
    try:
        _run(args)
    except PartialSeedError as err:
        raise SystemExit(f"{_aws_message(err.cause)}; {repair_hint(err.user_key)}") from None
    except (ClientError, BotoCoreError) as err:
        raise SystemExit(f"{_aws_message(err)}; re-run to repair a partly seeded user") from None
    except KeyError as err:
        raise SystemExit(
            f"unexpected response or setting missing {redact(str(err))}; re-run to repair"
        ) from None


if __name__ == "__main__":
    main()
