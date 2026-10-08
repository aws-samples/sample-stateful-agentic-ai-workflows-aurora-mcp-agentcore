"""A replaced Gateway is built in stages, because the stack cannot hold two of them at once.

CloudFormation cannot change an existing Gateway's authorizer type, and the AgentCore CDK
constructs cannot hold two Gateways with the same target names (a target's Lambda has a fixed
physical name and its outputs one construct id). So the jwt Gateway is created under a new name
in four passes that replay the first deployment: the Gateway with the targets that own no fixed
name, the rest of the targets, the Cedar engine and rules, then the Runtimes' engine variable.
The stage is read from the CLI's deployed state, never guessed.
"""

from __future__ import annotations

import pytest

from scripts.identity_release import stages
from scripts.identity_release.stages import DeployedIds

GATEWAY, HOLDS, ENGINE = "gw-1", "target-1", "engine-1"


@pytest.mark.parametrize(("ids", "expected"), [
    (DeployedIds(None, None, None), stages.GATEWAY),
    (DeployedIds(None, HOLDS, ENGINE), stages.GATEWAY),
    (DeployedIds(GATEWAY, None, None), stages.TARGETS),
    (DeployedIds(GATEWAY, None, ENGINE), stages.TARGETS),
    (DeployedIds(GATEWAY, HOLDS, None), stages.GOVERNANCE),
    (DeployedIds(GATEWAY, HOLDS, ENGINE), stages.COMPLETE),
])
def test_the_stage_is_the_first_one_whose_resource_the_state_lacks(ids, expected):
    assert stages.stage_for(ids) == expected


def test_a_stale_engine_id_never_lifts_a_stage_past_a_missing_gateway_or_target():
    assert stages.stage_for(DeployedIds("", "", "engine")) == stages.GATEWAY
    assert stages.stage_for(DeployedIds("gw", "", "engine")) == stages.TARGETS


def test_the_stages_are_ordered_and_each_says_what_it_renders():
    assert stages.STAGES == (stages.GATEWAY, stages.TARGETS, stages.GOVERNANCE, stages.COMPLETE)
    assert [stages.renders_holds(s) for s in stages.STAGES] == [False, True, True, True]
    assert [stages.renders_engine(s) for s in stages.STAGES] == [False, False, True, True]
    assert [stages.renders_gateway_id(s) for s in stages.STAGES] == [False, True, True, True]
    assert [stages.renders_engine_id(s) for s in stages.STAGES] == [False, False, False, True]


def test_the_next_stage_follows_in_order_and_complete_has_none():
    assert [stages.next_stage(s) for s in stages.STAGES] == [
        stages.TARGETS, stages.GOVERNANCE, stages.COMPLETE, None]


def test_deployed_ids_read_the_cli_state_for_the_named_gateway():
    state = {"targets": {"default": {"resources": {
        "mcp": {"gateways": {"meridian-aurora-jwt": {
            "gatewayId": GATEWAY, "targets": {"MeridianHolds": {"targetId": HOLDS}}}}},
        "policyEngines": {"MeridianGovernance": {"policyEngineId": ENGINE}}}}}}

    found = stages.deployed_ids(state, "default", "meridian-aurora-jwt")
    other = stages.deployed_ids(state, "default", "meridian-aurora")

    assert found == DeployedIds(GATEWAY, HOLDS, ENGINE)
    assert other == DeployedIds(None, None, ENGINE)
    assert stages.stage_for(other) == stages.GATEWAY


def test_deployed_ids_of_an_empty_or_odd_state_are_all_absent():
    assert stages.deployed_ids({}, "default", "g") == DeployedIds(None, None, None)
    assert stages.deployed_ids({"targets": []}, "default", "g") == DeployedIds(None, None, None)


def spec(*, holds=True, engines=True, engine_variable=True):
    targets = [{"name": "SemanticTripSearchLambda"}]
    if holds:
        targets.append({"name": "MeridianHolds"})
    variables = [{"name": "MERIDIAN_POLICY_MODE", "value": "ENFORCE"}]
    if engine_variable:
        variables.append({"name": "MERIDIAN_POLICY_ENGINE_ID", "value": "engine-1"})
    return {"agentCoreGateways": [{"name": "g", "targets": targets}],
            "policyEngines": [{"name": "MeridianGovernance", "policies": []}] if engines else [],
            "runtimes": [{"name": "MeridianConcierge", "envVars": variables}]}


@pytest.mark.parametrize(("rendered", "expected"), [
    (spec(holds=False, engines=False, engine_variable=False), stages.GATEWAY),
    (spec(holds=True, engines=False, engine_variable=False), stages.TARGETS),
    (spec(holds=True, engines=True, engine_variable=False), stages.GOVERNANCE),
    (spec(), stages.COMPLETE),
])
def test_a_rendered_spec_says_which_stage_it_is(rendered, expected):
    assert stages.stage_of_render(rendered) == expected


def test_a_spec_with_no_gateway_or_runtime_reads_as_the_first_stage():
    assert stages.stage_of_render({}) == stages.GATEWAY
    assert stages.stage_of_render({"agentCoreGateways": "x", "runtimes": None}) == stages.GATEWAY
