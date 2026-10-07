"""Provisioning helpers publish parameters and bind the holds Lambda role to Jordan."""

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


def test_the_gateway_login_replaces_only_the_secret_parameter():
    params = parameters_from_env(
        {
            "AURORA_CLUSTER_ARN": "arn:c",
            "AURORA_SECRET_ARN": "arn:master",
            "AURORA_GATEWAY_SECRET_ARN": "arn:gateway",
            "AURORA_DATABASE": "meridian",
        },
        gateway_login=True,
    )
    assert params == {
        "/meridian/aurora/cluster_arn": "arn:c",
        "/meridian/aurora/secret_arn": "arn:gateway",
        "/meridian/aurora/database": "meridian",
    }


def test_the_default_still_publishes_the_master_secret_when_a_gateway_login_exists():
    params = parameters_from_env(
        {
            "AURORA_CLUSTER_ARN": "arn:c",
            "AURORA_SECRET_ARN": "arn:master",
            "AURORA_GATEWAY_SECRET_ARN": "arn:gateway",
            "AURORA_DATABASE": "meridian",
        }
    )
    assert params["/meridian/aurora/secret_arn"] == "arn:master"


def test_the_gateway_login_flag_without_the_login_is_an_error():
    env = {"AURORA_CLUSTER_ARN": "arn:c", "AURORA_SECRET_ARN": "arn:s",
           "AURORA_DATABASE": "meridian"}
    with pytest.raises(SystemExit, match="AURORA_GATEWAY_SECRET_ARN"):
        parameters_from_env(env, gateway_login=True)
