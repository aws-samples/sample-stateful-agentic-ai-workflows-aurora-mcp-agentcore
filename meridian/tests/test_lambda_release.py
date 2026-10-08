"""The Lambdas read the meridian_gateway secret, and tightening removes the master one."""

from __future__ import annotations

import pytest
from botocore.exceptions import WaiterError

from scripts.identity_release import lambda_release as lambdas
from tests import release_support as rs
from tests.aws_recorders import client_error, violations
from tests.lambda_release_support import (
    GATEWAY, HOLDS_ARN, HOLDS_NAME, MASTER, LambdaClient, World, policy)

STAMP = "20261008T130000Z"
WHERE = {"account": rs.ACCOUNT, "region": rs.REGION}


def test_the_baseline_before_the_cutover_reads_the_master_secret_everywhere():
    world = World(ssm=MASTER, holds_grants=(MASTER, GATEWAY), semantic_env=MASTER,
                  semantic_grants=(MASTER,))

    assert world.check("master") == []


def test_after_the_cutover_the_parameter_and_the_semantic_environment_name_the_gateway_secret():
    world = World(holds_grants=(MASTER, GATEWAY), semantic_grants=(GATEWAY,))

    assert world.check("gateway") == []


def test_after_the_cutover_a_stale_parameter_or_environment_is_a_finding_each():
    world = World(ssm=MASTER, semantic_env=MASTER)

    found = world.check("gateway")

    assert len(found) == 2
    assert any(line.startswith("SSM /meridian/aurora/secret_arn:") for line in found)
    assert any("meridian-semantic-trip-search: AURORA_SECRET_ARN" in line for line in found)


def test_a_missing_parameter_is_a_finding_not_a_crash():
    world = World()
    world.ssm.failures["get_parameter"] = [client_error("ParameterNotFound")]

    assert [line.split(":")[0] for line in world.check("gateway")] == [
        "SSM /meridian/aurora/secret_arn"]


def test_a_role_that_cannot_read_the_gateway_secret_is_a_finding_before_anything_is_removed():
    world = World(holds_grants=(MASTER,), semantic_grants=(MASTER,))

    found = world.check("gateway")

    assert any("MeridianHolds" in line and "cannot read the meridian_gateway secret" in line
               for line in found)
    assert any(lambdas.SEMANTIC_FUNCTION in line and "cannot read" in line for line in found)


def test_tightened_means_no_role_can_still_read_the_master_secret():
    assert World(holds_grants=(GATEWAY,), semantic_grants=(GATEWAY,)).check("tightened") == []
    loose = World(holds_grants=(GATEWAY, MASTER), semantic_grants=(GATEWAY, MASTER))

    found = loose.check("tightened")

    assert len([line for line in found if "still can read the master login's secret" in line]) == 2


@pytest.mark.parametrize("grant", [
    "*",
    f"arn:aws:secretsmanager:{rs.REGION}:{rs.ACCOUNT}:secret:*",
    "arn:aws:secretsmanager:*:*:secret:meridian-*",
    f"arn:aws:secretsmanager:{rs.REGION}:{rs.ACCOUNT}:secret:meridian-AbC12?",
])
def test_a_wildcard_grant_counts_as_reading_the_master_secret(grant):
    world = World(holds_grants=(grant,), semantic_grants=(GATEWAY,))

    found = world.check("tightened")

    assert any("MeridianHolds" in line and "master" in line for line in found)


def test_a_pattern_that_misses_the_master_secret_is_not_a_finding():
    world = World(holds_grants=(f"arn:aws:secretsmanager:{rs.REGION}:{rs.ACCOUNT}:secret:"
                                "meridian/aurora/*",), semantic_grants=(GATEWAY,))

    assert world.check("tightened") == []


@pytest.mark.parametrize("action", ["secretsmanager:*", "secretsmanager:Get*", "*"])
def test_a_wildcard_action_counts_as_reading_the_secret(action):
    world = World(semantic_grants=(GATEWAY,))
    world.roles["AgentCore-meridianv2-MeridianHoldsRole"] = policy(MASTER, action=action)

    assert any("MeridianHolds" in line for line in world.check("tightened"))


def test_a_grant_for_another_action_is_not_a_read_of_the_secret():
    world = World(semantic_grants=(GATEWAY,))
    world.roles["AgentCore-meridianv2-MeridianHoldsRole"] = policy(
        MASTER, action="secretsmanager:DescribeSecret")

    assert any("cannot read the meridian_gateway" in line for line in world.check("gateway"))


def test_a_customer_managed_policy_attached_to_the_role_is_read_too():
    extra = f"arn:aws:iam::{rs.ACCOUNT}:policy/Extra"
    world = World(holds_grants=(GATEWAY,), semantic_grants=(GATEWAY,),
                  holds_attached=[{"PolicyArn": extra},
                                  {"PolicyArn": "arn:aws:iam::aws:policy/AdministratorAccess"}])

    found = world.check("tightened")

    assert len(found) == 1 and "MeridianHolds" in found[0]
    assert world.iam.args("get_policy") == [{"PolicyArn": extra}]


def test_a_missing_semantic_function_is_a_finding_not_a_crash():
    world = World()
    original = world.lam.answers["get_function_configuration"]

    def answer(FunctionName):
        if FunctionName == lambdas.SEMANTIC_FUNCTION:
            raise client_error("ResourceNotFoundException")
        return original(FunctionName)

    world.lam.answers["get_function_configuration"] = answer

    assert world.check("gateway") == [f"Lambda {lambdas.SEMANTIC_FUNCTION}: does not exist"]


def test_an_unknown_stage_is_refused():
    with pytest.raises(ValueError, match="stage"):
        World().check("later")


def test_the_check_makes_only_the_listed_read_calls_and_they_match_the_service_models():
    world = World(holds_attached=[{"PolicyArn": f"arn:aws:iam::{rs.ACCOUNT}:policy/Extra"}])

    world.check("tightened")

    assert set(world.ssm.names()) == {"get_parameter"}
    assert set(world.lam.names()) == {"get_function_configuration"}
    assert set(world.iam.names()) == {
        "list_role_policies", "get_role_policy", "list_attached_role_policies", "get_policy",
        "get_policy_version"}
    assert set(world.control.names()) == {"list_gateway_targets", "get_gateway_target"}
    for service, client in (("ssm", world.ssm), ("lambda", world.lam), ("iam", world.iam),
                            ("bedrock-agentcore-control", world.control)):
        assert violations(service, client.calls) == []


def test_the_holds_function_is_found_through_the_gateway_target():
    world = World()

    assert lambdas.holds_function_arn(world.control, rs.GATEWAY_ID) == HOLDS_ARN
    assert world.control.args("get_gateway_target") == [
        {"gatewayIdentifier": rs.GATEWAY_ID, "targetId": "T2"}]
    world.control.answers["list_gateway_targets"] = {"items": []}
    with pytest.raises(lambdas.LambdaError, match="MeridianHolds"):
        lambdas.holds_function_arn(world.control, rs.GATEWAY_ID)


def test_the_target_list_is_followed_across_pages():
    world = World()
    pages = iter([{"items": [{"name": "Other", "targetId": "T1"}], "nextToken": "n1"},
                  {"items": [{"name": "MeridianHolds", "targetId": "T9"}]}])
    world.control.answers["list_gateway_targets"] = lambda **kwargs: next(pages)

    assert lambdas.holds_function_arn(world.control, rs.GATEWAY_ID) == HOLDS_ARN
    assert world.control.args("list_gateway_targets")[1]["nextToken"] == "n1"
    assert violations("bedrock-agentcore-control", world.control.calls) == []


# ------------------------------------------------------------------ remedies


def test_a_stale_semantic_environment_gets_the_exact_manual_step():
    steps = lambdas.remedies(World(semantic_env=MASTER).check("gateway"), "gateway")

    assert len(steps) == 1 and steps[0].startswith("MANUAL STEP")
    assert "AURORA_GATEWAY_SECRET_ARN" in steps[0] and lambdas.SEMANTIC_FUNCTION in steps[0]
    assert "replaces the whole environment" in steps[0]


def test_a_stale_parameter_points_at_the_publisher_with_the_gateway_flag():
    steps = lambdas.remedies(World(ssm=MASTER).check("gateway"), "gateway")

    assert len(steps) == 1 and "publish_gateway_parameters.py --gateway-login" in steps[0]
    master = lambdas.remedies(World().check("master"), "master")
    assert all("--gateway-login" not in step for step in master)


def test_nothing_to_fix_means_no_steps():
    assert lambdas.remedies([], "gateway") == []


# -------------------------------------------------------------------- restart


def restart(world, arn=HOLDS_ARN, **where):
    return lambdas.restart_holds(world.lam, arn, STAMP, **{**WHERE, **where})


def test_restarting_the_holds_function_adds_a_marker_and_keeps_its_environment():
    world = World()

    note = restart(world)

    update = world.lam.args("update_function_configuration")[0]
    assert update["Environment"] == {"Variables": {"EXISTING": "1", "MERIDIAN_COLD_START": STAMP}}
    waiter = ("function_updated_v2", {"FunctionName": HOLDS_ARN, "WaiterConfig": lambdas.WAIT})
    assert world.lam.waited == [waiter, waiter]
    assert "restarted" in note
    assert violations("lambda", world.lam.calls) == []
    assert lambdas.WAIT["Delay"] * lambdas.WAIT["MaxAttempts"] <= 300


def test_the_update_is_conditional_on_the_revision_that_was_read():
    world = World()
    world.configs[HOLDS_ARN]["RevisionId"] = "rev-1"

    restart(world)

    assert world.lam.args("update_function_configuration")[0]["RevisionId"] == "rev-1"
    assert violations("lambda", world.lam.calls) == []


@pytest.mark.parametrize("arn, where, word", [
    (HOLDS_ARN.replace(rs.ACCOUNT, "999999999999"), {}, "outside this account"),
    (HOLDS_ARN.replace(rs.REGION, "eu-west-1"), {}, "outside this account"),
    (HOLDS_ARN, {"account": "999999999999"}, "outside this account"),
    (HOLDS_ARN + ":prod", {}, "outside this account"),
    (HOLDS_ARN.replace(HOLDS_NAME, "someone-elses-function"), {}, "not an AgentCore"),
    (HOLDS_ARN.replace(HOLDS_NAME, "AgentCore-x-MeridianHolds1-extra"), {}, "not an AgentCore"),
])
def test_a_function_that_is_not_this_projects_holds_function_is_never_changed(arn, where, word):
    world = World()

    with pytest.raises(lambdas.LambdaError, match=word):
        restart(world, arn, **where)

    assert "update_function_configuration" not in world.lam.names()


def test_a_missing_holds_function_is_refused_not_a_crash():
    world = World()
    world.lam.failures["get_function_configuration"] = [client_error("ResourceNotFoundException")]

    with pytest.raises(lambdas.LambdaError, match="does not exist"):
        restart(world)


def test_a_holds_function_whose_role_is_in_another_account_is_never_changed():
    world = World()
    world.configs[HOLDS_ARN]["Role"] = "arn:aws:iam::999999999999:role/x"

    with pytest.raises(lambdas.LambdaError, match="role"):
        restart(world)

    assert "update_function_configuration" not in world.lam.names()


def test_a_wait_that_runs_out_is_an_error_naming_the_bound_not_a_traceback():
    world = World()

    class Stuck(LambdaClient):
        def get_waiter(self, name):
            class Waiter:
                def wait(self, **kwargs):
                    raise WaiterError(name, "Max attempts exceeded", {})

            return Waiter()

    world.lam = Stuck(answers=world.lam.answers)

    with pytest.raises(lambdas.LambdaError, match="did not settle within 120 seconds"):
        restart(world)

    assert "update_function_configuration" not in world.lam.names()


def test_a_marker_that_did_not_land_is_an_error():
    world = World()
    world.lam.answers["update_function_configuration"] = lambda FunctionName, **changes: {}

    with pytest.raises(lambdas.LambdaError, match="marker"):
        restart(world)
