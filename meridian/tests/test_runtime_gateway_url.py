"""A Runtime finds the Gateway under whichever URL variable the CDK wired.

The CDK names the variable after the Gateway: the IAM Gateway's is
``AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL`` and the token Gateway's (a new resource, because an
existing Gateway's authorizer type cannot change) is ``AGENTCORE_GATEWAY_MERIDIAN_AURORA_JWT_URL``.
A deployed stack holds one Gateway, so a Runtime has exactly one; the code reads either, without
branching on the mode.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from scripts.identity_release import settings

APP = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app"
IAM_VARIABLE = "AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL"
JWT_VARIABLE = "AGENTCORE_GATEWAY_MERIDIAN_AURORA_JWT_URL"
IAM_URL = "https://iam-gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
JWT_URL = "https://jwt-gw.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"


def load(folder: str, module: str):
    spec = importlib.util.spec_from_file_location(
        f"runtime_url_{folder}_{module}", APP / folder / f"{module}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture(params=[("MeridianConcierge", "gateway_auth"), ("MeridianWorkflow", "gateway_url")],
                ids=["concierge", "workflow"])
def module(request):
    return load(*request.param)


def test_the_variables_are_the_ones_the_release_tools_name(module):
    assert module.IAM_URL_VARIABLE == IAM_VARIABLE == settings.gateway_url_variable("iam")
    assert module.JWT_URL_VARIABLE == JWT_VARIABLE == settings.gateway_url_variable("jwt")


def test_the_iam_variable_is_used_when_it_is_the_only_one(module):
    assert module.gateway_url({IAM_VARIABLE: IAM_URL}) == IAM_URL


def test_the_jwt_variable_is_used_when_it_is_the_only_one(module):
    assert module.gateway_url({JWT_VARIABLE: JWT_URL}) == JWT_URL


def test_the_jwt_variable_wins_when_both_are_set(module):
    assert module.gateway_url({IAM_VARIABLE: IAM_URL, JWT_VARIABLE: JWT_URL}) == JWT_URL


@pytest.mark.parametrize("environment", [{}, {IAM_VARIABLE: ""}, {JWT_VARIABLE: "  "}])
def test_no_url_is_refused_naming_both_variables(module, environment):
    with pytest.raises(RuntimeError) as refused:
        module.gateway_url(environment)

    assert IAM_VARIABLE in str(refused.value) and JWT_VARIABLE in str(refused.value)


def test_an_empty_jwt_variable_falls_back_to_the_iam_one(module):
    assert module.gateway_url({JWT_VARIABLE: "", IAM_VARIABLE: IAM_URL}) == IAM_URL


def test_the_workflow_maps_the_gateway_url_for_the_staged_backend_modules():
    workflow = load("MeridianWorkflow", "gateway_url")
    environment = {JWT_VARIABLE: JWT_URL}

    workflow.map_gateway_url(environment)

    assert environment["AGENTCORE_GATEWAY_URL"] == JWT_URL


def test_the_workflow_keeps_a_backend_url_that_is_already_set_even_with_no_variable():
    workflow = load("MeridianWorkflow", "gateway_url")
    environment = {"AGENTCORE_GATEWAY_URL": "https://set"}

    workflow.map_gateway_url(environment)

    assert environment == {"AGENTCORE_GATEWAY_URL": "https://set"}


def test_the_workflow_refuses_to_start_with_no_url_at_all():
    workflow = load("MeridianWorkflow", "gateway_url")

    with pytest.raises(RuntimeError, match=JWT_VARIABLE):
        workflow.map_gateway_url({})
