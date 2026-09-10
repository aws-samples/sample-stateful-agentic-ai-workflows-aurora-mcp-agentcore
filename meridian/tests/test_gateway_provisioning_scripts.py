"""Provisioning helpers publish parameters and bind the holds Lambda role to Alex."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scripts.bind_gateway_workload import role_subject
from scripts.publish_gateway_parameters import parameters_from_env


def test_parameters_map_the_env_to_ssm_names():
    params = parameters_from_env({
        "AURORA_CLUSTER_ARN": "arn:c",
        "AURORA_SECRET_ARN": "arn:s",
        "AURORA_DATABASE": "meridian",
    })
    assert params == {
        "/meridian/aurora/cluster_arn": "arn:c",
        "/meridian/aurora/secret_arn": "arn:s",
        "/meridian/aurora/database": "meridian",
    }


def test_missing_env_is_an_error():
    with pytest.raises(SystemExit, match="AURORA_SECRET_ARN"):
        parameters_from_env({"AURORA_CLUSTER_ARN": "arn:c"})


def test_role_subject_is_the_stable_role_id():
    iam = MagicMock()
    iam.get_role.return_value = {
        "Role": {"RoleId": "AROAEXAMPLE", "Arn": "arn:aws:iam::1:role/holds"}
    }
    assert role_subject(iam, "arn:aws:iam::1:role/holds") == (
        "AROAEXAMPLE",
        "arn:aws:iam::1:role/holds",
    )
    iam.get_role.assert_called_once_with(RoleName="holds")
