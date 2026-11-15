"""Give the meridian_rotator login a password only Secrets Manager knows.

Migration 034 creates meridian_rotator with NOLOGIN. The login is the one the password rotation
Lambdas connect as through the Data API (docs/OPERATIONS.md, "Rotate workload passwords
automatically"). This script does what provision_service_logins.py does for a workload login:
it sets LOGIN with a SCRAM-SHA-256 verifier, stores the credential in its own secret
(meridian/aurora/rotator-login), creates the managed policy MeridianRotatorAuroraAccess, and
connects as the login to check it is meridian_rotator without BYPASSRLS. Nothing rotates this
password: re-run the script to change it. Without --apply it only reports what it would do.
Run from meridian/ with AWS_PROFILE set.

Usage:
    python scripts/provision_rotation_login.py
    python scripts/provision_rotation_login.py --apply --i-understand-this-changes-aws --write-env
"""

import os
import sys
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.identity_release.usage import UsageParser  # noqa: E402
from scripts.provision_service_logins import (  # noqa: E402
    CLUSTER_ARN_SHAPE,
    CONFIRM_FLAG,
    ENV_FILE,
    LoginSpec,
    provision_login,
    redact,
    require_account,
    write_env,
)

ROTATOR = LoginSpec(
    key="rotator",
    role="meridian_rotator",
    secret_name="meridian/aurora/rotator-login",
    policy_name="MeridianRotatorAuroraAccess",
    env_key="AURORA_ROTATOR_SECRET_ARN",
    purpose="the password rotation Lambdas",
)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set; add it to meridian/.env or export it, then re-run")
    return value


def _run(apply: bool, write_env_line: bool) -> None:
    load_dotenv(ENV_FILE)
    cluster_arn = _require_env("AURORA_CLUSTER_ARN")
    if not CLUSTER_ARN_SHAPE.fullmatch(cluster_arn):
        raise SystemExit("AURORA_CLUSTER_ARN is not an Aurora cluster ARN; fix meridian/.env")
    master_secret_arn = _require_env("AURORA_SECRET_ARN") if apply else \
        os.environ.get("AURORA_SECRET_ARN", "")
    region = cluster_arn.split(":")[3]
    require_account(boto3.client("sts", region_name=region), cluster_arn)
    result = provision_login(
        ROTATOR,
        sm=boto3.client("secretsmanager", region_name=region),
        iam=boto3.client("iam"),
        master=boto3.client("rds-data", region_name=region),
        cluster_arn=cluster_arn,
        master_secret_arn=master_secret_arn,
        database=os.getenv("AURORA_DATABASE", "meridian"),
        apply=apply,
    )
    if write_env_line and result.verified_user and result.secret_arn:
        write_env(ENV_FILE, ROTATOR.env_key, result.secret_arn)


def main(argv: Optional[list] = None) -> None:
    """Parse the CLI and provision the rotation login."""
    parser = UsageParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="make the changes")
    parser.add_argument(CONFIRM_FLAG, action="store_true", dest="confirmed",
                        help="required with --apply: acknowledges that it changes AWS")
    parser.add_argument("--write-env", action="store_true",
                        help="write the secret ARN into meridian/.env after a verified run")
    args = parser.parse_args(argv)
    if args.apply and not args.confirmed:
        parser.error(f"--apply changes AWS (it sets the meridian_rotator password); add "
                     f"{CONFIRM_FLAG} to run it. Nothing was changed.")
    try:
        _run(args.apply, args.write_env)
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code", "unknown")
        raise SystemExit(
            f"{err.operation_name} failed ({code}): {redact(str(err))}; check AWS_PROFILE; "
            "if --apply had started, re-run it to repair a partial change"
        ) from None
    except BotoCoreError as err:
        raise SystemExit(
            f"could not reach AWS ({type(err).__name__}: {redact(str(err))}); check AWS_PROFILE "
            "and that its credentials have not expired, then re-run"
        ) from None


if __name__ == "__main__":
    main()
