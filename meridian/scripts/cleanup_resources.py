"""Plan cleanup of Meridian ancillary resources after its Aurora cluster is gone.

Run without flags to inspect the plan. --apply schedules secret deletion with
seven days of recovery and deletes the named subnet/security groups. Every AWS
failure exits nonzero. Never run against the shared presenter cluster.
"""
from __future__ import annotations

import argparse
import os

import boto3
from botocore.exceptions import ClientError
from rich.console import Console

console = Console()


def _optional(call, missing_codes, **kwargs):
    """Only a documented not-found response means a resource is absent."""
    try:
        return call(**kwargs)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in missing_codes:
            return None
        raise


def cleanup_resources(region: str = "us-east-1", *, apply: bool = False) -> int:
    """Plan or execute cleanup; preserve credentials while a cluster still exists."""
    rds = boto3.client("rds", region_name=region)
    cluster = _optional(
        rds.describe_db_clusters, {"DBClusterNotFoundFault"},
        DBClusterIdentifier="meridian-demo",
    )
    if cluster:
        raise RuntimeError(
            "meridian-demo still exists. Its credential and network resources must be retained."
        )
    secrets = boto3.client("secretsmanager", region_name=region)
    ec2 = boto3.client("ec2", region_name=region)
    secret = _optional(
        secrets.describe_secret, {"ResourceNotFoundException"},
        SecretId="meridian-demo-credentials",
    )
    subnet = _optional(
        rds.describe_db_subnet_groups, {"DBSubnetGroupNotFoundFault"},
        DBSubnetGroupName="meridian-demo-subnet-group",
    )
    groups = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": ["meridian-demo-sg"]}]
    )["SecurityGroups"]
    if len(groups) > 1:
        raise RuntimeError("Multiple meridian-demo-sg groups exist; inspect their VPC ownership.")

    actions = []
    if secret and not secret.get("DeletedDate"):
        actions.append(("Schedule secret deletion (7-day recovery)", secrets.delete_secret,
                        {"SecretId": secret["ARN"], "RecoveryWindowInDays": 7}))
    if subnet:
        actions.append(("Delete subnet group meridian-demo-subnet-group",
                        rds.delete_db_subnet_group,
                        {"DBSubnetGroupName": "meridian-demo-subnet-group"}))
    for group in groups:
        actions.append((f"Delete security group {group['GroupId']} in {group['VpcId']}",
                        ec2.delete_security_group, {"GroupId": group["GroupId"]}))
    for label, call, kwargs in actions:
        console.print(label)
        if apply:
            call(**kwargs)
    console.print("Cleanup completed." if apply else "Dry run only. Use --apply after reviewing ownership.")
    return len(actions)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    parser.add_argument("--apply", action="store_true", help="execute the reviewed cleanup plan")
    args = parser.parse_args()
    try:
        cleanup_resources(args.region, apply=args.apply)
    except (ClientError, RuntimeError) as exc:
        raise SystemExit(f"Cleanup incomplete: {exc}") from exc


if __name__ == "__main__":
    main()
