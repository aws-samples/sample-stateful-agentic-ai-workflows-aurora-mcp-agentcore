"""The harness refuses the real Gateway, odd names and the wrong account before any AWS call."""

import pytest

from scripts.gateway_harness import guards

ACCOUNT = "123456789012"
CLUSTER = f"arn:aws:rds:us-east-1:{ACCOUNT}:cluster:meridian"


def test_a_fresh_name_is_eight_hex_digits_after_the_prefix():
    name = guards.new_throwaway_name(lambda n: "ab12cd34"[: n * 2])
    assert name == "meridian-throwaway-ab12cd34"
    assert guards.check_name(name) == name


@pytest.mark.parametrize("name", [
    "meridian-aurora",
    "meridianv2-meridian-aurora-abcde12345",
    "meridian-throwaway-meridianv2",
    "meridian-throwaway-",
    "meridian-throwaway-ABCDEF12",
    "meridian-throwaway-1234567",
    "meridian-throwaway-123456789",
    "other-throwaway-12345678",
    "",
])
def test_anything_but_a_throwaway_name_is_refused(name):
    with pytest.raises(guards.HarnessRefusal, match="refusing"):
        guards.check_name(name)


def test_the_deployment_target_comes_from_the_cluster_arn():
    assert guards.deployment_target({"AURORA_CLUSTER_ARN": CLUSTER}) == (ACCOUNT, "us-east-1")


@pytest.mark.parametrize("env", [{}, {"AURORA_CLUSTER_ARN": ""}, {"AURORA_CLUSTER_ARN": "arn:x"}])
def test_a_missing_cluster_arn_is_refused(env):
    with pytest.raises(guards.HarnessRefusal, match="AURORA_CLUSTER_ARN"):
        guards.deployment_target(env)


def test_the_matching_account_and_region_pass():
    guards.check_caller(ACCOUNT, "us-east-1", (ACCOUNT, "us-east-1"))


def test_another_account_is_refused_without_printing_either_id():
    with pytest.raises(guards.HarnessRefusal) as refused:
        guards.check_caller("999999999999", "us-east-1", (ACCOUNT, "us-east-1"))
    assert "<acct>" in str(refused.value)
    assert "999999999999" not in str(refused.value) and ACCOUNT not in str(refused.value)


def test_another_region_is_refused():
    with pytest.raises(guards.HarnessRefusal, match="us-west-2"):
        guards.check_caller(ACCOUNT, "us-west-2", (ACCOUNT, "us-east-1"))
