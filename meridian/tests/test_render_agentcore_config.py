"""The committed AgentCore templates render into a deployable, account-specific config."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from scripts import render_agentcore_config as render_config

ACCOUNT = "123456789012"
CLUSTER_ARN = f"arn:aws:rds:us-west-2:{ACCOUNT}:cluster:meridian"
SECRET_ARN = f"arn:aws:secretsmanager:us-west-2:{ACCOUNT}:secret:meridian-AbC123"
GATEWAY_ID = "meridianv2-meridian-aurora-abcde12345"
POLICY_ENGINE_ID = "meridianv2_MeridianGovernance-abcde12345"
GATEWAY_ARN = f"arn:aws:bedrock-agentcore:us-west-2:{ACCOUNT}:gateway/{GATEWAY_ID}"


def templates() -> tuple[dict, list]:
    config_dir = render_config.CONFIG_DIR
    spec = json.loads((config_dir / render_config.SPEC_TEMPLATE).read_text(encoding="utf-8"))
    targets = json.loads((config_dir / render_config.TARGETS_TEMPLATE).read_text(encoding="utf-8"))
    return spec, targets


def base_values() -> dict[str, str]:
    return render_config.account_values(
        {"AURORA_CLUSTER_ARN": CLUSTER_ARN, "AURORA_SECRET_ARN": SECRET_ARN}
    )


def runtime_env(spec: dict) -> dict[str, str]:
    return {var["name"]: var["value"] for var in spec["runtimes"][0]["envVars"]}


def test_complete_render_fills_every_account_and_deployment_value() -> None:
    values = {**base_values(), "GATEWAY_ID": GATEWAY_ID, "POLICY_ENGINE_ID": POLICY_ENGINE_ID}

    spec, targets, notes = render_config.render(*templates(), values)

    assert notes == []
    assert targets == [
        {
            "name": "default",
            "description": "Meridian demo deployment target",
            "account": ACCOUNT,
            "region": "us-west-2",
        }
    ]
    assert runtime_env(spec)["MERIDIAN_GATEWAY_ID"] == GATEWAY_ID
    assert runtime_env(spec)["MERIDIAN_POLICY_ENGINE_ID"] == POLICY_ENGINE_ID
    gateway = spec["agentCoreGateways"][0]
    assert gateway["policyEngineConfiguration"] == {
        "policyEngineName": "MeridianGovernance",
        "mode": "ENFORCE",
    }
    assert gateway["targets"][0]["lambdaFunctionArn"]["lambdaArn"] == (
        f"arn:aws:lambda:us-west-2:{ACCOUNT}:function:meridian-semantic-trip-search"
    )
    resources = [s["Resource"] for s in gateway["targets"][1]["compute"]["iamPolicy"]["Statement"]]
    assert resources == [
        f"arn:aws:ssm:us-west-2:{ACCOUNT}:parameter/meridian/aurora/*",
        CLUSTER_ARN,
        SECRET_ARN,
    ]
    policies = spec["policyEngines"][0]["policies"]
    assert [p["name"] for p in policies] == [
        "meridian_read_tools",
        "meridian_hold_governance",
        "meridian_booking_governance",
    ]
    assert all(f'AgentCore::Gateway::"{GATEWAY_ARN}"' in p["statement"] for p in policies)
    assert "{{" not in json.dumps(spec) + json.dumps(targets)


def test_first_deployment_renders_without_policies_that_name_the_gateway() -> None:
    spec, _, notes = render_config.render(*templates(), base_values())

    assert spec["policyEngines"] == []
    assert "policyEngineConfiguration" not in spec["agentCoreGateways"][0]
    assert "MERIDIAN_GATEWAY_ID" not in runtime_env(spec)
    assert "MERIDIAN_POLICY_ENGINE_ID" not in runtime_env(spec)
    assert runtime_env(spec)["MERIDIAN_POLICY_MODE"] == "ENFORCE"
    assert len(notes) == 3


def test_second_deployment_adds_policies_before_the_engine_id_exists() -> None:
    values = {**base_values(), "GATEWAY_ID": GATEWAY_ID}

    spec, _, notes = render_config.render(*templates(), values)

    assert len(spec["policyEngines"][0]["policies"]) == 3
    assert runtime_env(spec)["MERIDIAN_GATEWAY_ID"] == GATEWAY_ID
    assert "MERIDIAN_POLICY_ENGINE_ID" not in runtime_env(spec)
    assert notes == [
        "runtime MeridianConcierge: left out MERIDIAN_POLICY_ENGINE_ID (not deployed yet)"
    ]


def test_account_and_region_come_from_the_cluster_arn_unless_a_region_is_set() -> None:
    assert base_values()["AWS_ACCOUNT_ID"] == ACCOUNT
    assert base_values()["AWS_REGION"] == "us-west-2"
    values = render_config.account_values(
        {
            "AURORA_CLUSTER_ARN": CLUSTER_ARN,
            "AURORA_SECRET_ARN": SECRET_ARN,
            "AWS_DEFAULT_REGION": "eu-west-1",
            "AGENTCORE_REGION": "us-east-1",
        }
    )
    assert values["AWS_REGION"] == "us-east-1"


@pytest.mark.parametrize(
    "env,message",
    [
        ({"AURORA_SECRET_ARN": SECRET_ARN}, "AURORA_CLUSTER_ARN"),
        ({"AURORA_CLUSTER_ARN": "arn:aws:rds:region:account-id:cluster:x",
          "AURORA_SECRET_ARN": SECRET_ARN}, "AURORA_CLUSTER_ARN"),
        ({"AURORA_CLUSTER_ARN": CLUSTER_ARN,
          "AURORA_SECRET_ARN": f"arn:aws:secretsmanager:us-west-2:{ACCOUNT}:secret:meridian"},
         "six-character suffix"),
        ({"AURORA_CLUSTER_ARN": CLUSTER_ARN,
          "AURORA_SECRET_ARN": SECRET_ARN.replace(ACCOUNT, "210987654321")},
         "both must belong to the deployment account"),
        ({"AURORA_CLUSTER_ARN": CLUSTER_ARN, "AURORA_SECRET_ARN": SECRET_ARN,
          "AWS_DEFAULT_REGION": "region"}, "not an AWS Region name"),
    ],
)
def test_malformed_inputs_fail_with_the_setting_to_fix(env: dict, message: str) -> None:
    with pytest.raises(render_config.ConfigError, match=message):
        render_config.account_values(env)


def test_a_required_placeholder_without_a_value_is_refused() -> None:
    values = base_values()
    del values["AURORA_SECRET_ARN"]

    with pytest.raises(render_config.ConfigError, match="AURORA_SECRET_ARN"):
        render_config.render(*templates(), values)


def test_deployed_ids_come_from_the_cli_deployment_state(tmp_path: Path) -> None:
    state = tmp_path / "deployed-state.json"
    assert render_config.deployed_ids(state, "default") == {}
    state.write_text(
        json.dumps(
            {
                "targets": {
                    "default": {
                        "resources": {
                            "mcp": {"gateways": {"meridian-aurora": {"gatewayId": GATEWAY_ID}}},
                            "policyEngines": {
                                "MeridianGovernance": {"policyEngineId": POLICY_ENGINE_ID}
                            },
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert render_config.deployed_ids(state, "default") == {
        "GATEWAY_ID": GATEWAY_ID,
        "POLICY_ENGINE_ID": POLICY_ENGINE_ID,
    }
    assert render_config.deployed_ids(state, "other") == {}
    state.write_text("{", encoding="utf-8")
    with pytest.raises(render_config.ConfigError, match="not valid JSON"):
        render_config.deployed_ids(state, "default")


def test_templates_hold_no_account_or_deployment_specific_value() -> None:
    for name in (render_config.SPEC_TEMPLATE, render_config.TARGETS_TEMPLATE):
        text = (render_config.CONFIG_DIR / name).read_text(encoding="utf-8")
        assert not re.search(r"\d{12}", text), name
        assert not re.search(r"arn:aws[a-z-]*:[a-z0-9-]+:[a-z]{2}-[a-z]+-\d", text), name


def test_rendered_files_are_gitignored() -> None:
    for name in (render_config.SPEC_OUTPUT, render_config.TARGETS_OUTPUT):
        rendered = render_config.CONFIG_DIR / name
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", "--no-index", str(rendered)],
            cwd=render_config.CONFIG_DIR,
            check=False,
        )
        assert result.returncode == 0, f"{rendered} must be gitignored"
