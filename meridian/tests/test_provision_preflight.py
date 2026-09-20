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
