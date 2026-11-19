"""Do not mistake AWS-owned encryption for unencrypted storage."""
import pytest

from scripts.provision_preflight import storage_encryption


@pytest.mark.parametrize("mode,legacy,encrypted,migrate", [
    ("sse-rds", False, True, False),
    ("sse-kms", True, True, False),
    ("none", False, False, True),
])
def test_encryption_type_is_authoritative(mode, legacy, encrypted, migrate):
    result = storage_encryption({"StorageEncryptionType": mode, "StorageEncrypted": legacy})
    assert result["encryptedAtRest"] is encrypted
    assert result["encryptionMigrationNeeded"] is migrate


@pytest.mark.parametrize("legacy", [False, True])
def test_legacy_flag_alone_does_not_establish_current_encryption_mode(legacy):
    with pytest.raises(ValueError, match="StorageEncryptionType unavailable"):
        storage_encryption({"StorageEncrypted": legacy})


from botocore.exceptions import ClientError  # noqa: E402

from scripts.provision_preflight import assess_rds_quotas  # noqa: E402

QUOTA_NAMES = ("DBClusters", "DBInstances", "DBSubnetGroups", "DBClusterParameterGroups")


class StubRds:
    """An RDS inventory: quota usage plus, optionally, one existing cluster's members."""

    def __init__(self, instances_left: int, members: list[str] | None = None) -> None:
        self.instances_left, self.members = instances_left, members

    def describe_account_attributes(self) -> dict:
        quotas = [{"AccountQuotaName": name, "Used": 1, "Max": 1000} for name in QUOTA_NAMES]
        quotas[1] = {"AccountQuotaName": "DBInstances", "Used": 40 - self.instances_left, "Max": 40}
        return {"AccountQuotas": quotas}

    def describe_db_clusters(self, DBClusterIdentifier: str) -> dict:
        if self.members is None:
            raise ClientError({"Error": {"Code": "DBClusterNotFoundFault", "Message": "no"}},
                              "DescribeDBClusters")
        return {"DBClusters": [{"DBClusterMembers": [
            {"DBInstanceIdentifier": name} for name in self.members]}]}


def test_first_creation_with_one_instance_slot_fails():
    with pytest.raises(ValueError, match="2 DB instances"):
        assess_rds_quotas(StubRds(instances_left=1), "meridian")


def test_first_creation_with_two_instance_slots_passes():
    assert assess_rds_quotas(StubRds(instances_left=2), "meridian")


def test_an_existing_stack_with_both_instances_needs_no_instance_slot():
    stub = StubRds(instances_left=0, members=["writer", "reader"])
    assert assess_rds_quotas(stub, "meridian")


def test_an_existing_stack_missing_its_reader_needs_exactly_one_slot():
    assert assess_rds_quotas(StubRds(instances_left=1, members=["writer"]), "meridian")
    with pytest.raises(ValueError, match="1 DB instance"):
        assess_rds_quotas(StubRds(instances_left=0, members=["writer"]), "meridian")


def test_no_cluster_identifier_is_treated_as_first_creation():
    with pytest.raises(ValueError, match="2 DB instances"):
        assess_rds_quotas(StubRds(instances_left=1, members=["writer", "reader"]), None)


import sys  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from scripts import provision_preflight  # noqa: E402


def _blocked(operation: str, code: str = "ValidationError") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "no"}}, operation)


class _Client:
    """A client whose named calls raise and whose other calls answer with plain data."""

    def __init__(self, answers: dict) -> None:
        self.answers = answers

    def __getattr__(self, name: str):
        def call(**_kwargs):
            answer = self.answers.get(name, {})
            if isinstance(answer, Exception):
                raise answer
            return answer
        return call


def _run_preflight(monkeypatch, *, bootstrap, role) -> dict:
    subnets = {"Subnets": [
        {"SubnetId": f"subnet-{zone}", "AvailabilityZone": f"us-east-1{zone}", "VpcId": "vpc-1",
         "State": "available", "AvailableIpAddressCount": 50} for zone in "ab"]}
    quotas = {"AccountQuotas": [{"AccountQuotaName": name, "Used": 0, "Max": 100}
                                for name in provision_preflight.QUOTA_NAMES]}
    clients = {
        "sts": _Client({"get_caller_identity": {"Account": "123456789012", "Arn": "arn:x:user/u"}}),
        "ec2": _Client({"describe_subnets": subnets}),
        "rds": _Client({"describe_orderable_db_instance_options": {
            "OrderableDBInstanceOptions": [{}]}, "describe_account_attributes": quotas}),
        "iam": _Client({"get_role": role}),
        "cloudformation": _Client({"describe_stacks": bootstrap}),
    }
    monkeypatch.setattr(provision_preflight.boto3, "Session",
                        lambda **kw: SimpleNamespace(client=lambda name, **k: clients[name]))
    args = SimpleNamespace(account="123456789012", region="us-east-1", vpc_id="vpc-1",
                           subnet_ids=["subnet-a", "subnet-b"], engine_version="18.6",
                           cluster_identifier=None, source_cluster=None)
    return provision_preflight.inspect(args)


def test_each_failed_prerequisite_carries_its_recovery_instruction(monkeypatch):
    report = _run_preflight(
        monkeypatch, bootstrap=_blocked("DescribeStacks"),
        role=_blocked("GetRole", "NoSuchEntity"))

    by_name = {c["check"]: c for c in report["checks"]}
    assert report["readyForRehearsal"] is False
    assert "cdk bootstrap aws://<account-id>/<region>" in by_name["cdk_bootstrap"]["recovery"]
    assert "create-service-linked-role --aws-service-name rds.amazonaws.com" in (
        by_name["rds_service_linked_role"]["recovery"])
    assert by_name["cdk_bootstrap"]["status"] == by_name["rds_service_linked_role"]["status"] == (
        "BLOCKED")


def test_a_passing_prerequisite_carries_no_recovery_instruction(monkeypatch):
    stack = {"Stacks": [{"StackStatus": "CREATE_COMPLETE", "Outputs": []}]}
    report = _run_preflight(monkeypatch, bootstrap=stack, role={"Role": {"Arn": "arn:x:role/r"}})

    by_name = {c["check"]: c for c in report["checks"]}
    assert by_name["cdk_bootstrap"]["status"] == by_name["rds_service_linked_role"]["status"] == (
        "PASS")
    assert "recovery" not in by_name["cdk_bootstrap"]


def test_an_incomplete_bootstrap_fails_with_the_same_recovery(monkeypatch):
    stack = {"Stacks": [{"StackStatus": "ROLLBACK_COMPLETE", "Outputs": []}]}
    report = _run_preflight(monkeypatch, bootstrap=stack, role={"Role": {"Arn": "arn:x:role/r"}})

    check = next(c for c in report["checks"] if c["check"] == "cdk_bootstrap")
    assert check["status"] == "FAIL" and "cdk bootstrap" in check["recovery"]


def test_the_recovery_lines_are_printed_beside_the_failure(monkeypatch, capsys, tmp_path):
    report = _run_preflight(
        monkeypatch, bootstrap=_blocked("DescribeStacks"), role=_blocked("GetRole", "NoSuchEntity"))
    monkeypatch.setattr(provision_preflight, "inspect", lambda args: report)
    monkeypatch.setattr(sys, "argv", ["preflight", "--account", "123456789012", "--region", "r",
                                      "--vpc-id", "v", "--subnet-ids", "a",
                                      "--engine-version", "18.6"])

    assert provision_preflight.main() == 1

    err = capsys.readouterr().err
    assert "BLOCKED cdk_bootstrap (ValidationError): Bootstrap CDK once" in err
    assert "BLOCKED rds_service_linked_role (NoSuchEntity)" in err
