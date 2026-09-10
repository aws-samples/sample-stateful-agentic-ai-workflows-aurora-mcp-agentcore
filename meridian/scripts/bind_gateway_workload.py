#!/usr/bin/env python3
"""Grant the MeridianHolds Lambda's execution role access to Alex's traveler record.

The gateway Lambda is a workload like the FastAPI backend: before it sets a
traveler scope it must hold an active row in traveler_identity_bindings. The
subject is the role's stable RoleId, which is what sts:GetCallerIdentity returns
as the first part of UserId inside the function.

Usage:
    cd meridian
    python scripts/bind_gateway_workload.py [--function meridianv2-MeridianHolds]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.bind_current_identity import bind  # noqa: E402

load_dotenv()
DEFAULT_FUNCTION = "meridianv2-MeridianHolds"


def role_subject(iam, role_arn: str) -> tuple[str, str]:
    """Return (RoleId, role ARN) for the role behind a Lambda function."""
    role = iam.get_role(RoleName=role_arn.rsplit("/", 1)[-1])["Role"]
    return role["RoleId"], role["Arn"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--function", default=DEFAULT_FUNCTION)
    args = parser.parse_args()
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    role_arn = boto3.client("lambda", region_name=region).get_function_configuration(
        FunctionName=args.function
    )["Role"]
    subject_id, principal = role_subject(boto3.client("iam"), role_arn)
    bind(
        boto3.client("rds-data", region_name=region),
        provider="aws_iam",
        subject_id=subject_id,
        principal=principal,
    )


if __name__ == "__main__":
    main()
