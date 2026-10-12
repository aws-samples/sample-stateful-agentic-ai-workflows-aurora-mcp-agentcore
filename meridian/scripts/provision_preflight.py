#!/usr/bin/env python3
"""Read-only Aurora provisioning prerequisites; never claims a fresh-account deployment passed."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

CONFIG = Config(connect_timeout=10, read_timeout=30, retries={"mode": "standard", "total_max_attempts": 3})


def storage_encryption(cluster: dict) -> dict:
    """The legacy boolean alone misses encryption with an AWS-owned key."""
    mode = cluster.get("StorageEncryptionType")
    if mode not in {"none", "sse-rds", "sse-kms"}:
        raise ValueError("StorageEncryptionType unavailable; use a current RDS SDK/API before assessing encryption")
    return {"type": mode, "encryptedAtRest": mode != "none",
            "legacyStorageEncrypted": cluster.get("StorageEncrypted"),
            "kmsKeyArn": cluster.get("KmsKeyId"), "encryptionMigrationNeeded": mode == "none"}


def inspect(args) -> dict:
    session = boto3.Session(region_name=args.region)
    identity = session.client("sts", config=CONFIG).get_caller_identity()
    if identity["Account"] != args.account:
        raise ValueError("AWS identity differs from --account; no further calls made")
    checks = []

    def check(name, function):
        try:
            detail = function()
            checks.append({"check": name, "status": "PASS", "detail": detail})
        except ClientError as exc:
            checks.append({"check": name, "status": "BLOCKED", "error": exc.response["Error"]["Code"]})
        except ValueError as exc:
            checks.append({"check": name, "status": "FAIL", "error": str(exc)})

    rds = session.client("rds", config=CONFIG)
    ec2 = session.client("ec2", config=CONFIG)
    iam = session.client("iam", config=CONFIG)
    cfn = session.client("cloudformation", config=CONFIG)

    def subnets():
        found = ec2.describe_subnets(SubnetIds=args.subnet_ids)["Subnets"]
        if len({s["AvailabilityZone"] for s in found}) < 2 or any(s["VpcId"] != args.vpc_id or s["State"] != "available" for s in found):
            raise ValueError("Need available subnets in two AZs of the requested VPC")
        if any(s["AvailableIpAddressCount"] < 4 for s in found):
            raise ValueError("Subnet has fewer than four free addresses")
        return [{"id": s["SubnetId"], "az": s["AvailabilityZone"], "freeAddresses": s["AvailableIpAddressCount"]} for s in found]

    def engine():
        options = rds.describe_orderable_db_instance_options(Engine="aurora-postgresql", EngineVersion=args.engine_version,
                                                           DBInstanceClass="db.serverless")["OrderableDBInstanceOptions"]
        if not options:
            raise ValueError("Requested Aurora version is not orderable as db.serverless in this region")
        return {"engineVersion": args.engine_version, "class": "db.serverless"}

    def quotas():
        relevant = {"DBClusters", "DBInstances", "DBSubnetGroups", "DBClusterParameterGroups"}
        values = [a for a in rds.describe_account_attributes()["AccountQuotas"] if a["AccountQuotaName"] in relevant]
        if {a["AccountQuotaName"] for a in values} != relevant:
            raise ValueError("RDS did not return all required account quotas")
        if any(a["Used"] + 1 > a["Max"] for a in values):
            raise ValueError("Insufficient RDS quota for one isolated cluster/writer/subnet group/parameter group")
        return values

    def bootstrap():
        stack = cfn.describe_stacks(StackName="CDKToolkit")["Stacks"][0]
        outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
        if stack["StackStatus"] not in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}:
            raise ValueError("CDK bootstrap is not complete")
        return {"version": outputs.get("BootstrapVersion"), "status": stack["StackStatus"]}

    def service_role():
        return {"arn": iam.get_role(RoleName="AWSServiceRoleForRDS")["Role"]["Arn"]}

    def deployer():
        arn = identity["Arn"]
        if ":assumed-role/" not in arn:
            raise ValueError("IAM simulation in this preflight requires an assumed role; review deployer permissions manually")
        role = iam.get_role(RoleName=arn.split("/")[-2])["Role"]
        bootstrap_role = f"arn:aws:iam::{args.account}:role/cdk-hnb659fds-cfn-exec-role-{args.account}-{args.region}"
        evaluation = iam.simulate_principal_policy(PolicySourceArn=role["Arn"], ActionNames=["iam:PassRole"],
            ResourceArns=[bootstrap_role], ContextEntries=[{"ContextKeyName": "iam:PassedToService",
                "ContextKeyValues": ["cloudformation.amazonaws.com"], "ContextKeyType": "string"}])["EvaluationResults"]
        if any(result["EvalDecision"] != "allowed" for result in evaluation):
            raise ValueError("IAM simulation did not allow PassRole for the CDK execution role")
        return {"role": role["Arn"], "permissionsBoundary": role.get("PermissionsBoundary", {}).get("PermissionsBoundaryArn"),
                "passRoleSimulation": "allowed", "limitation": "Simulation does not prove SCP/session policy or CloudFormation execution permissions"}

    check("network", subnets)
    check("orderable_engine", engine)
    check("rds_quotas", quotas)
    check("rds_service_linked_role", service_role)
    check("cdk_bootstrap", bootstrap)
    check("deployer_pass_role", deployer)
    if args.source_cluster:
        check("source_encryption", lambda: storage_encryption(
            rds.describe_db_clusters(DBClusterIdentifier=args.source_cluster)["DBClusters"][0]))
    return {"observedAt": datetime.now(timezone.utc).isoformat(), "account": args.account, "region": args.region,
            "checks": checks, "readyForRehearsal": all(c["status"] == "PASS" for c in checks),
            "notProven": ["Fresh-account deployment, rollback and teardown", "Organization SCPs and deployment session policies",
                          "Data API region/version availability and pgvector schema bootstrap on a new cluster",
                          "App Runner eligibility: closed to new customers from March 31, 2026", "Event load/capacity and cost approval"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--vpc-id", required=True)
    parser.add_argument("--subnet-ids", nargs="+", required=True)
    parser.add_argument("--engine-version", required=True)
    parser.add_argument("--source-cluster", help="Inspect an existing cluster's actual encryption type before planning a migration")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = inspect(args)
    except (ValueError, ClientError) as exc:
        print(str(exc) if isinstance(exc, ValueError) else exc.response["Error"]["Code"], file=sys.stderr)
        return 1
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered)
    return 0 if report["readyForRehearsal"] else 1


if __name__ == "__main__":
    sys.exit(main())
