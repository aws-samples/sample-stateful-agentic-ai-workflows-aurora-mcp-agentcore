"""The rotation login is provisioned like a workload login, behind the same confirm flag."""

import pytest

from scripts import provision_rotation_login as rot
from scripts import provision_service_logins as prov


def test_the_spec_names_the_rotator_role_and_its_own_secret_and_policy():
    spec = rot.ROTATOR
    assert spec.role == "meridian_rotator"
    assert spec.secret_name == "meridian/aurora/rotator-login"
    assert spec.policy_name == "MeridianRotatorAuroraAccess"
    assert spec.env_key == "AURORA_ROTATOR_SECRET_ARN"
    assert spec.key not in prov.LOGINS


def test_apply_without_the_confirm_flag_changes_nothing_and_exits_3(monkeypatch, capsys):
    def refuse(*_a, **_k):
        raise AssertionError("AWS must not be reached")

    monkeypatch.setattr(rot.boto3, "client", refuse)
    with pytest.raises(SystemExit) as stopped:
        rot.main(["--apply"])
    assert stopped.value.code == 3
    assert "Nothing was changed" in capsys.readouterr().err


def test_an_abbreviated_flag_is_refused(monkeypatch):
    monkeypatch.setattr(rot.boto3, "client", lambda *_a, **_k: pytest.fail("AWS reached"))
    with pytest.raises(SystemExit) as stopped:
        rot.main(["--app"])
    assert stopped.value.code == 3


def test_the_policy_allows_the_data_api_on_the_cluster_and_one_secret():
    cluster = "arn:aws:rds:us-east-1:111122223333:cluster:c1"
    secret = "arn:aws:secretsmanager:us-east-1:111122223333:secret:meridian/aurora/rotator-X"
    document = prov.policy_document(rot.ROTATOR, cluster, secret)
    resources = [statement["Resource"] for statement in document["Statement"]]
    assert resources == [cluster, secret]
