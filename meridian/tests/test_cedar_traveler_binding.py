"""The Cedar deny rule binds a hold or booking to the traveler claim, and the permits stay."""

from __future__ import annotations

import json

import cedarpy
import pytest

from scripts import render_agentcore_config as render_config
from tests.gateway_interceptor_support import load_interceptor

ACCOUNT = "123456789012"
GATEWAY_ID = "meridianv2-meridian-aurora-abcde12345"
GATEWAY = f"arn:aws:bedrock-agentcore:us-west-2:{ACCOUNT}:gateway/{GATEWAY_ID}"
HOLD = "MeridianHolds___create_courtesy_hold"
BOOKING = "MeridianHolds___confirm_booking"
SEARCH = "SemanticTripSearchLambda___semantic_trip_search"
BINDING = "meridian_traveler_binding"


def values(mode: str | None) -> dict[str, str]:
    env = {
        "AURORA_CLUSTER_ARN": f"arn:aws:rds:us-west-2:{ACCOUNT}:cluster:meridian",
        "AURORA_SECRET_ARN": f"arn:aws:secretsmanager:us-west-2:{ACCOUNT}:secret:meridian-AbC123",
        "AURORA_WORKFLOW_SECRET_ARN":
            f"arn:aws:secretsmanager:us-west-2:{ACCOUNT}:secret:meridian/wf-XyZ789",
        "AURORA_GATEWAY_SECRET_ARN":
            f"arn:aws:secretsmanager:us-west-2:{ACCOUNT}:secret:meridian/gw-GwY456",
        **({"MERIDIAN_AGENTCORE_AUTH": mode} if mode is not None else {}),
    }
    return {**render_config.account_values(env), "GATEWAY_ID": GATEWAY_ID}


def rendered(mode: str | None) -> dict:
    config_dir = render_config.CONFIG_DIR
    spec = json.loads((config_dir / render_config.SPEC_TEMPLATE).read_text())
    targets = json.loads((config_dir / render_config.TARGETS_TEMPLATE).read_text())
    return render_config.render(spec, targets, values(mode))[0]


def policies(mode: str | None) -> dict[str, str]:
    return {p["name"]: p["statement"] for p in rendered(mode)["policyEngines"][0]["policies"]}


def runtime_env(spec: dict, name: str) -> dict[str, str]:
    runtime = next(r for r in spec["runtimes"] if r["name"] == name)
    return {var["name"]: var["value"] for var in runtime["envVars"]}


@pytest.mark.parametrize("mode", [None, "iam", "jwt"])
def test_every_rendered_statement_parses_as_cedar(mode):
    for name, statement in policies(mode).items():
        assert cedarpy.format_policies(statement), name


def test_the_default_render_is_exactly_the_three_policies_deployed_today():
    assert list(policies(None)) == [
        "meridian_read_tools", "meridian_hold_governance", "meridian_booking_governance",
    ]
    assert policies(None) == policies("iam")


def test_the_jwt_render_adds_only_the_binding_and_leaves_the_permits_untouched():
    jwt, iam = policies("jwt"), policies("iam")
    assert list(jwt) == [*iam, BINDING]
    assert {name: jwt[name] for name in iam} == iam
    assert jwt[BINDING].startswith("forbid(")
    assert all(f'AgentCore::Gateway::"{GATEWAY}"' in statement for statement in jwt.values())


@pytest.mark.parametrize(("mode", "expected"), [(None, "iam"), ("iam", "iam"), ("jwt", "jwt")])
def test_both_runtimes_receive_the_mode(mode, expected):
    spec = rendered(mode)
    for runtime in ("MeridianConcierge", "MeridianWorkflow"):
        assert runtime_env(spec, runtime)["MERIDIAN_AGENTCORE_AUTH"] == expected


def test_a_mode_other_than_iam_or_jwt_is_refused_by_the_renderer():
    with pytest.raises(render_config.ConfigError, match="MERIDIAN_AGENTCORE_AUTH"):
        values("cognito")


def test_the_rule_names_exactly_the_tools_the_interceptor_pins():
    interceptor = load_interceptor()
    statement = policies("jwt")[BINDING]
    named = {part.split('"')[1] for part in statement.split("AgentCore::Action::")[1:]}
    assert named == set(interceptor.DEFAULT_PINNED_TOOLS)


def decide(statements, *, tool=HOLD, tag="trv_demo_decoy", arg="trv_demo_decoy", **overrides):
    entity_tags = {"traveler_id": tag} if tag is not None else {}
    entities = [
        {"uid": {"type": "AgentCore::OAuthUser", "id": "sub-1"}, "attrs": {}, "parents": [],
         "tags": entity_tags},
        {"uid": {"type": "AgentCore::Gateway", "id": GATEWAY}, "attrs": {}, "parents": []},
        {"uid": {"type": "AgentCore::Action", "id": tool}, "attrs": {}, "parents": []},
    ]
    arguments = {"travelerConfirmed": True, "holdMinutes": 60, "travelers": 2,
                 "totalCents": 10, "budgetCeilingCents": 100, **overrides}
    if arg is not None:
        arguments["travelerId"] = arg
    request = {
        "principal": 'AgentCore::OAuthUser::"sub-1"',
        "action": f'AgentCore::Action::"{tool}"',
        "resource": f'AgentCore::Gateway::"{GATEWAY}"',
        "context": {"input": arguments},
    }
    return cedarpy.is_authorized(request, "\n".join(statements.values()), entities).decision


ALLOW, DENY = cedarpy.Decision.Allow, cedarpy.Decision.Deny


def test_the_traveler_whose_token_it_is_may_hold():
    assert decide(policies("jwt")) == ALLOW


def test_a_decoy_token_naming_jordans_id_is_denied_by_the_rule_alone():
    statements = policies("jwt")
    assert decide(statements, arg="trv_meridian_demo") == DENY
    assert decide({k: v for k, v in statements.items() if k != BINDING},
                  arg="trv_meridian_demo") == ALLOW


@pytest.mark.parametrize(("tag", "arg"), [(None, "trv_demo_decoy"), ("trv_demo_decoy", None)])
def test_a_missing_claim_or_a_missing_argument_is_denied(tag, arg):
    assert decide(policies("jwt"), tag=tag, arg=arg) == DENY


def test_the_booking_tool_is_bound_the_same_way():
    statements = policies("jwt")
    assert decide(statements, tool=BOOKING, arg="trv_demo_decoy") == ALLOW
    assert decide(statements, tool=BOOKING, arg="trv_meridian_demo") == DENY


def test_the_existing_permits_still_refuse_what_they_refused_before():
    statements = policies("jwt")
    assert decide(statements, travelerConfirmed=False) == DENY
    assert decide(statements, totalCents=1000) == DENY
    assert decide(statements, holdMinutes=721) == DENY


def test_read_tools_are_unaffected_by_the_binding():
    request_args = {"query": "beach"}
    entities = [
        {"uid": {"type": "AgentCore::OAuthUser", "id": "sub-1"}, "attrs": {}, "parents": []},
        {"uid": {"type": "AgentCore::Gateway", "id": GATEWAY}, "attrs": {}, "parents": []},
        {"uid": {"type": "AgentCore::Action", "id": SEARCH}, "attrs": {}, "parents": []},
    ]
    request = {
        "principal": 'AgentCore::OAuthUser::"sub-1"',
        "action": f'AgentCore::Action::"{SEARCH}"',
        "resource": f'AgentCore::Gateway::"{GATEWAY}"',
        "context": {"input": request_args},
    }
    statements = "\n".join(policies("jwt").values())
    assert cedarpy.is_authorized(request, statements, entities).decision == ALLOW
