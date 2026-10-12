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
    "meridian-throwaway-abcdef12\n",
    "Meridian-Throwaway-abcdef12",
    "meridian-throwaway-abcdef12-x",
    " meridian-throwaway-abcdef12",
    "meridian-throwaway-\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18",
    "meridian-throwaway-abcdef12-meridian-aurora",
])
def test_anything_but_a_throwaway_name_is_refused(name):
    with pytest.raises(guards.HarnessRefusal, match="refusing"):
        guards.check_name(name)


@pytest.mark.parametrize("name", ["meridian-aurora", "meridianv2-meridian-aurora-abcde12345"])
def test_the_real_names_get_their_own_refusal(name):
    with pytest.raises(guards.HarnessRefusal, match="real Gateway or project"):
        guards.check_name(name)


def test_an_odd_name_is_not_called_the_real_gateway():
    with pytest.raises(guards.HarnessRefusal) as refused:
        guards.check_name("meridian-throwaway-abcdef12-x")
    assert "real Gateway" not in str(refused.value)


def test_a_bad_token_source_cannot_mint_an_unsafe_name():
    with pytest.raises(guards.HarnessRefusal, match="refusing"):
        guards.new_throwaway_name(lambda n: "meridianv2"[:n])
    with pytest.raises(guards.HarnessRefusal, match="refusing"):
        guards.new_throwaway_name(lambda n: "ZZZZ")


def test_the_deployment_target_comes_from_the_cluster_arn():
    assert guards.deployment_target({"AURORA_CLUSTER_ARN": CLUSTER}) == (ACCOUNT, "us-east-1")


@pytest.mark.parametrize("env", [
    {},
    {"AURORA_CLUSTER_ARN": ""},
    {"AURORA_CLUSTER_ARN": "arn:x"},
    {"AURORA_CLUSTER_ARN": "arn:aws:rds:us-east-1:\u0661\u0662\u0663\u0664\u0665\u0666"
                           "\u0667\u0668\u0669\u0660\u0661\u0662:cluster:meridian"},
    {"AURORA_CLUSTER_ARN": f"arn:aws:rds:us-east-1:{ACCOUNT}:db:x"},
    {"AURORA_CLUSTER_ARN": "arn:aws:rds:us-east-1:12345678901:cluster:meridian"},
])
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
