#!/usr/bin/env python3
"""Grant the published backend's App Runner instance role access to Alex's traveler record.

The FastAPI backend behind CloudFront runs on App Runner with the instance role
from the MeridianWebRoles stack. Like the gateway Lambda, it is a workload:
before it sets a traveler scope it must hold an active row in
traveler_identity_bindings, keyed by the role's stable RoleId (the first part of
the UserId that sts:GetCallerIdentity returns inside the container). Without
this grant every Phase 4 and Phase 5 request on the published site fails with
"aws_iam subject is not authorized for traveler trv_meridian_demo".

Usage:
    cd meridian
    python scripts/bind_web_backend_role.py [--stack MeridianWebRoles]
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
from scripts.bind_gateway_workload import role_subject  # noqa: E402

load_dotenv()

DEFAULT_STACK = "MeridianWebRoles"
OUTPUT_KEY = "InstanceRoleArn"


def instance_role_arn(cloudformation, stack: str) -> str:
    """The App Runner instance role ARN the roles stack exports."""
    outputs = cloudformation.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
    for output in outputs:
        if output["OutputKey"] == OUTPUT_KEY:
            return output["OutputValue"]
    raise SystemExit(f"Stack {stack} has no {OUTPUT_KEY} output; deploy it with scripts/publish.py")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", default=DEFAULT_STACK)
    args = parser.parse_args()
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    role_arn = instance_role_arn(boto3.client("cloudformation", region_name=region), args.stack)
    subject_id, principal = role_subject(boto3.client("iam"), role_arn)
    bind(
        boto3.client("rds-data", region_name=region),
        provider="aws_iam",
        subject_id=subject_id,
        principal=principal,
    )
    print(f"Bound {subject_id} ({principal}) to trv_meridian_demo")


if __name__ == "__main__":
    main()
