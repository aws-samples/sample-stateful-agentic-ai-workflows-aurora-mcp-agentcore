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
