"""The committed AgentCore templates render into a deployable, account-specific config."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import render_agentcore_config as render_config

ACCOUNT = "123456789012"
CLUSTER_ARN = f"arn:aws:rds:us-west-2:{ACCOUNT}:cluster:meridian"
SECRET_ARN = f"arn:aws:secretsmanager:us-west-2:{ACCOUNT}:secret:meridian-AbC123"
WORKFLOW_SECRET_ARN = (
    f"arn:aws:secretsmanager:us-west-2:{ACCOUNT}:secret:meridian/aurora/workflow-login-XyZ789"
)
GATEWAY_SECRET_ARN = (
    f"arn:aws:secretsmanager:us-west-2:{ACCOUNT}:secret:meridian/aurora/gateway-login-GwY456"
)
GATEWAY_ID = "meridianv2-meridian-aurora-abcde12345"
POLICY_ENGINE_ID = "meridianv2_MeridianGovernance-abcde12345"
GATEWAY_ARN = f"arn:aws:bedrock-agentcore:us-west-2:{ACCOUNT}:gateway/{GATEWAY_ID}"
REPO = render_config.MERIDIAN_DIR.parent


def templates() -> tuple[dict, list]:
    config_dir = render_config.CONFIG_DIR
    spec = json.loads((config_dir / render_config.SPEC_TEMPLATE).read_text(encoding="utf-8"))
    targets = json.loads((config_dir / render_config.TARGETS_TEMPLATE).read_text(encoding="utf-8"))
    return spec, targets


def base_values() -> dict[str, str]:
    return render_config.account_values(
        {
            "AURORA_CLUSTER_ARN": CLUSTER_ARN,
            "AURORA_SECRET_ARN": SECRET_ARN,
            "AURORA_WORKFLOW_SECRET_ARN": WORKFLOW_SECRET_ARN,
            "AURORA_GATEWAY_SECRET_ARN": GATEWAY_SECRET_ARN,
        }
    )


def runtime_env(spec: dict, name: str = "MeridianConcierge") -> dict[str, str]:
    runtime = next(r for r in spec["runtimes"] if r["name"] == name)
    return {var["name"]: var["value"] for var in runtime["envVars"]}


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
        [SECRET_ARN, GATEWAY_SECRET_ARN],
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
            "AURORA_WORKFLOW_SECRET_ARN": WORKFLOW_SECRET_ARN,
            "AURORA_GATEWAY_SECRET_ARN": GATEWAY_SECRET_ARN,
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
          "AURORA_SECRET_ARN": SECRET_ARN.replace(ACCOUNT, "111122223333")},
         "both must belong to the deployment account"),
        ({"AURORA_CLUSTER_ARN": CLUSTER_ARN, "AURORA_SECRET_ARN": SECRET_ARN,
          "AWS_DEFAULT_REGION": "region"}, "not an AWS Region name"),
    ],
)
def test_malformed_inputs_fail_with_the_setting_to_fix(env: dict, message: str) -> None:
    with pytest.raises(render_config.ConfigError, match=message):
        render_config.account_values(
            {
                "AURORA_WORKFLOW_SECRET_ARN": WORKFLOW_SECRET_ARN,
                "AURORA_GATEWAY_SECRET_ARN": GATEWAY_SECRET_ARN,
                **env,
            }
        )


@pytest.mark.parametrize(
    "workflow_arn,message",
    [
        (f"arn:aws:secretsmanager:us-west-2:{ACCOUNT}:secret:workflow", "six-character suffix"),
        (WORKFLOW_SECRET_ARN.replace(ACCOUNT, "111122223333"), "both must belong"),
        (SECRET_ARN, "must differ from AURORA_SECRET_ARN"),
    ],
)
def test_a_bad_workflow_secret_names_the_setting_to_fix(workflow_arn: str, message: str) -> None:
    env = {
        "AURORA_CLUSTER_ARN": CLUSTER_ARN,
        "AURORA_SECRET_ARN": SECRET_ARN,
        "AURORA_WORKFLOW_SECRET_ARN": workflow_arn,
        "AURORA_GATEWAY_SECRET_ARN": GATEWAY_SECRET_ARN,
    }
    with pytest.raises(render_config.ConfigError, match=message):
        render_config.account_values(env)


@pytest.mark.parametrize(
    "gateway_arn,message",
    [
        ("", "provision_service_logins"),
        (f"arn:aws:secretsmanager:us-west-2:{ACCOUNT}:secret:gateway", "six-character suffix"),
        (GATEWAY_SECRET_ARN.replace(ACCOUNT, "111122223333"), "both must belong"),
        (SECRET_ARN, "must differ from AURORA_SECRET_ARN"),
        (WORKFLOW_SECRET_ARN, "must differ from AURORA_SECRET_ARN"),
    ],
)
def test_a_bad_gateway_secret_names_the_setting_to_fix(gateway_arn: str, message: str) -> None:
    env = {
        "AURORA_CLUSTER_ARN": CLUSTER_ARN,
        "AURORA_SECRET_ARN": SECRET_ARN,
        "AURORA_WORKFLOW_SECRET_ARN": WORKFLOW_SECRET_ARN,
        "AURORA_GATEWAY_SECRET_ARN": gateway_arn,
    }
    with pytest.raises(render_config.ConfigError, match=message):
        render_config.account_values(env)


def test_the_holds_policy_reads_both_the_master_and_the_gateway_secret() -> None:
    spec, _, _ = render_config.render(*templates(), base_values())
    holds = next(t for t in spec["agentCoreGateways"][0]["targets"] if t["name"] == "MeridianHolds")
    secrets = [s for s in holds["compute"]["iamPolicy"]["Statement"]
               if s["Action"] == ["secretsmanager:GetSecretValue"]]
    assert secrets[0]["Resource"] == [SECRET_ARN, GATEWAY_SECRET_ARN]


def test_the_workflow_runtime_gets_its_own_login_and_policy() -> None:
    spec, _, notes = render_config.render(*templates(), base_values())
    workflow = next(r for r in spec["runtimes"] if r["name"] == "MeridianWorkflow")
    env = {v["name"]: v["value"] for v in workflow["envVars"]}
    assert env["AURORA_SECRET_ARN"] == WORKFLOW_SECRET_ARN != SECRET_ARN
    assert workflow["additionalPolicies"] == [
        f"arn:aws:iam::{ACCOUNT}:policy/MeridianWorkflowAuroraAccess"]
    assert not [n for n in notes if "MeridianWorkflow" in n]


def test_render_refuses_to_run_without_the_workflow_login() -> None:
    with pytest.raises(render_config.ConfigError, match="provision_workflow_login"):
        render_config.account_values(
            {"AURORA_CLUSTER_ARN": CLUSTER_ARN, "AURORA_SECRET_ARN": SECRET_ARN})


def test_a_policy_engine_id_without_a_gateway_id_is_refused() -> None:
    values = {**base_values(), "POLICY_ENGINE_ID": POLICY_ENGINE_ID}

    with pytest.raises(render_config.ConfigError, match="needs the gateway ID"):
        render_config.render(*templates(), values)


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


@pytest.mark.parametrize(
    "state,message",
    [
        ([], "expected an object at the top level, found an array"),
        ({"targets": []}, "expected an object at targets, found an array"),
        ({"targets": {"default": "deployed"}}, "at targets.default, found a string"),
        (
            {"targets": {"default": {"resources": {"mcp": {"gateways": []}}}}},
            "at targets.default.resources.mcp.gateways, found an array",
        ),
        (
            {
                "targets": {
                    "default": {
                        "resources": {
                            "policyEngines": {"MeridianGovernance": {"policyEngineId": 7}}
                        }
                    }
                }
            },
            "targets.default.resources.policyEngines.MeridianGovernance.policyEngineId "
            "must be a string, found a number",
        ),
    ],
)
def test_unexpected_deployed_state_names_the_file_and_key(
    tmp_path: Path, state: object, message: str
) -> None:
    path = tmp_path / "deployed-state.json"
    path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(render_config.ConfigError) as raised:
        render_config.deployed_ids(path, "default")

    assert str(path) in str(raised.value)
    assert message in str(raised.value)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch meridian/ with the committed templates, an empty .env and no deploy yet."""
    config_dir = tmp_path / "meridian_agentcore" / "agentcore"
    config_dir.mkdir(parents=True)
    for name in (render_config.SPEC_TEMPLATE, render_config.TARGETS_TEMPLATE):
        shutil.copy(render_config.CONFIG_DIR / name, config_dir / name)
    monkeypatch.setattr(render_config, "MERIDIAN_DIR", tmp_path)
    monkeypatch.setattr(render_config, "CONFIG_DIR", config_dir)
    for name in ("AGENTCORE_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AURORA_CLUSTER_ARN", CLUSTER_ARN)
    monkeypatch.setenv("AURORA_SECRET_ARN", SECRET_ARN)
    monkeypatch.setenv("AURORA_WORKFLOW_SECRET_ARN", WORKFLOW_SECRET_ARN)
    monkeypatch.setenv("AURORA_GATEWAY_SECRET_ARN", GATEWAY_SECRET_ARN)
    real_stage = render_config.stage_workflow_runtime.stage
    monkeypatch.setattr(
        render_config.stage_workflow_runtime, "stage", lambda: real_stage(tmp_path / "bundle")
    )
    return config_dir


def write_state(config_dir: Path, state: object) -> None:
    path = config_dir / render_config.DEPLOYED_STATE
    path.parent.mkdir()
    path.write_text(json.dumps(state), encoding="utf-8")


def deployed(gateway_id: str) -> dict:
    resources = {
        "mcp": {"gateways": {"meridian-aurora": {"gatewayId": gateway_id}}},
        "policyEngines": {"MeridianGovernance": {"policyEngineId": POLICY_ENGINE_ID}},
    }
    return {"targets": {"default": {"resources": resources}}}


def written(config_dir: Path) -> tuple[dict, list]:
    spec = json.loads((config_dir / render_config.SPEC_OUTPUT).read_text(encoding="utf-8"))
    targets = json.loads((config_dir / render_config.TARGETS_OUTPUT).read_text(encoding="utf-8"))
    return spec, targets


def test_main_writes_the_first_pass_before_any_deploy(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert render_config.main([]) == 0

    spec, targets = written(project)
    assert targets[0]["account"] == ACCOUNT
    assert spec["policyEngines"] == []
    assert "MERIDIAN_POLICY_ENGINE_ID" not in runtime_env(spec)
    out = capsys.readouterr().out
    assert f"account {ACCOUNT}, region us-west-2" in out
    assert "Deploy with `agentcore deploy -y`, then run this script again." in out


def test_main_stages_the_workflow_bundle_and_reports_its_modules(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert render_config.main([]) == 0

    manifest = tmp_path / "bundle" / "backend" / "BUNDLE_MANIFEST.json"
    modules = json.loads(manifest.read_text(encoding="utf-8"))
    assert f"staged {len(modules)} workflow modules" in capsys.readouterr().out


def test_main_completes_from_the_deployed_state_and_the_env_file(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("AURORA_CLUSTER_ARN")
    monkeypatch.delenv("AURORA_SECRET_ARN")
    monkeypatch.delenv("AURORA_WORKFLOW_SECRET_ARN")
    monkeypatch.delenv("AURORA_GATEWAY_SECRET_ARN")
    (render_config.MERIDIAN_DIR / ".env").write_text(
        f"AURORA_CLUSTER_ARN={CLUSTER_ARN}\nAURORA_SECRET_ARN={SECRET_ARN}\n"
        f"AURORA_WORKFLOW_SECRET_ARN={WORKFLOW_SECRET_ARN}\n"
        f"AURORA_GATEWAY_SECRET_ARN={GATEWAY_SECRET_ARN}\n"
        "AGENTCORE_REGION=eu-west-1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTCORE_REGION", "us-east-1")
    write_state(project, deployed(GATEWAY_ID))

    assert render_config.main([]) == 0

    spec, targets = written(project)
    assert targets[0]["region"] == "us-east-1"
    assert runtime_env(spec)["MERIDIAN_GATEWAY_ID"] == GATEWAY_ID
    assert runtime_env(spec)["MERIDIAN_POLICY_ENGINE_ID"] == POLICY_ENGINE_ID
    assert capsys.readouterr().out.endswith("Configuration complete.\n")


def test_main_flags_override_the_deployed_state(project: Path) -> None:
    write_state(project, deployed("meridianv2-meridian-aurora-zyxwv98765"))

    assert render_config.main(["--gateway-id", GATEWAY_ID]) == 0

    spec, _ = written(project)
    assert runtime_env(spec)["MERIDIAN_GATEWAY_ID"] == GATEWAY_ID
    assert runtime_env(spec)["MERIDIAN_POLICY_ENGINE_ID"] == POLICY_ENGINE_ID
    assert all(GATEWAY_ID in p["statement"] for p in spec["policyEngines"][0]["policies"])


@pytest.mark.parametrize(
    "args,state,message",
    [
        (["--policy-engine-id", POLICY_ENGINE_ID], None, "needs the gateway ID"),
        ([], {"targets": {"default": []}}, "expected an object at targets.default"),
    ],
)
def test_main_refuses_to_write_an_inconsistent_config(
    project: Path,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    state: object,
    message: str,
) -> None:
    if state is not None:
        write_state(project, state)

    assert render_config.main(args) == 1

    assert message in capsys.readouterr().err
    assert not (project / render_config.SPEC_OUTPUT).exists()
    assert not (project / render_config.TARGETS_OUTPUT).exists()


def test_templates_hold_no_account_or_deployment_specific_value() -> None:
    for name in (render_config.SPEC_TEMPLATE, render_config.TARGETS_TEMPLATE):
        text = (render_config.CONFIG_DIR / name).read_text(encoding="utf-8")
        assert not re.search(r"\d{12}", text), name
        assert not re.search(r"arn:aws[a-z-]*:[a-z0-9-]+:[a-z]{2}-[a-z]+-\d", text), name


@pytest.mark.skipif(
    not (REPO / ".git").exists(),
    reason="asks git check-ignore; this copy has no .git (for example, a ZIP download)",
)
def test_rendered_files_are_gitignored() -> None:
    for name in (render_config.SPEC_OUTPUT, render_config.TARGETS_OUTPUT):
        rendered = render_config.CONFIG_DIR / name
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", "--no-index", str(rendered)],
            cwd=render_config.CONFIG_DIR,
            check=False,
        )
        assert result.returncode == 0, f"{rendered} must be gitignored"


# ------------------------------------------------------------------ jwt mode

POOL_ID = "us-east-1_AbCdEfGhI"
CLIENT_ID = "exampleclientid123"
DISCOVERY_URL = (
    f"https://cognito-idp.us-west-2.amazonaws.com/{POOL_ID}/.well-known/openid-configuration"
)
COGNITO_ENV = {
    "MERIDIAN_COGNITO_REGION": "us-west-2",
    "MERIDIAN_COGNITO_USER_POOL_ID": POOL_ID,
    "MERIDIAN_COGNITO_APP_CLIENT_ID": CLIENT_ID,
}
JWT_FIXTURE = (
    render_config.CONFIG_DIR / "cdk" / "test" / "fixtures" / "jwt-spec.json"
)


def env_for(**extra: str) -> dict[str, str]:
    return {
        "AURORA_CLUSTER_ARN": CLUSTER_ARN,
        "AURORA_SECRET_ARN": SECRET_ARN,
        "AURORA_WORKFLOW_SECRET_ARN": WORKFLOW_SECRET_ARN,
        "AURORA_GATEWAY_SECRET_ARN": GATEWAY_SECRET_ARN,
        **extra,
    }


def jwt_spec(**extra: str) -> dict:
    env = env_for(MERIDIAN_AGENTCORE_AUTH="jwt", **COGNITO_ENV, **extra)
    values = {**render_config.account_values(env), "GATEWAY_ID": GATEWAY_ID,
              "POLICY_ENGINE_ID": POLICY_ENGINE_ID}
    return render_config.render(*templates(), values)[0]


def policy_names(spec: dict) -> list[str]:
    return [p["name"] for p in spec["policyEngines"][0]["policies"]]


def test_jwt_mode_gives_both_runtimes_the_authorizer_and_the_header_allowlist() -> None:
    spec = jwt_spec()

    assert [r["name"] for r in spec["runtimes"]] == ["MeridianConcierge", "MeridianWorkflow"]
    for runtime in spec["runtimes"]:
        assert runtime["authorizerType"] == "CUSTOM_JWT"
        assert runtime["authorizerConfiguration"] == {
            "customJwtAuthorizer": {"discoveryUrl": DISCOVERY_URL, "allowedClients": [CLIENT_ID]}
        }
        assert runtime["requestHeaderAllowlist"] == ["Authorization"]


def test_jwt_mode_gives_the_gateway_the_same_authorizer() -> None:
    gateway = jwt_spec()["agentCoreGateways"][0]

    assert gateway["authorizerType"] == "CUSTOM_JWT"
    assert gateway["authorizerConfiguration"] == {
        "customJwtAuthorizer": {"discoveryUrl": DISCOVERY_URL, "allowedClients": [CLIENT_ID]}
    }


def test_the_authorizers_check_the_client_id_claim_and_never_an_audience() -> None:
    spec = jwt_spec()
    blocks = [r["authorizerConfiguration"] for r in spec["runtimes"]]
    blocks.append(spec["agentCoreGateways"][0]["authorizerConfiguration"])

    for block in blocks:
        assert set(block["customJwtAuthorizer"]) == {"discoveryUrl", "allowedClients"}
    assert "allowedAudience" not in json.dumps(spec)


def test_the_iam_render_has_no_authorizer_and_no_allowlist() -> None:
    spec, _, _ = render_config.render(*templates(), base_values())

    for runtime in spec["runtimes"]:
        assert not {"authorizerType", "authorizerConfiguration", "requestHeaderAllowlist"} & set(
            runtime)
    gateway = spec["agentCoreGateways"][0]
    assert gateway["authorizerType"] == "AWS_IAM" and "authorizerConfiguration" not in gateway


def test_jwt_mode_without_the_pool_settings_is_refused_naming_them() -> None:
    with pytest.raises(render_config.ConfigError) as refused:
        render_config.account_values(env_for(MERIDIAN_AGENTCORE_AUTH="jwt"))

    assert "MERIDIAN_COGNITO_USER_POOL_ID" in str(refused.value)
    assert "MERIDIAN_COGNITO_APP_CLIENT_ID" in str(refused.value)


def test_iam_mode_does_not_need_the_pool_settings() -> None:
    assert render_config.account_values(env_for())["MERIDIAN_AGENTCORE_AUTH"] == "iam"


def test_the_default_enforcement_renders_the_binding_rule() -> None:
    assert policy_names(jwt_spec())[-1] == "meridian_traveler_binding"


def test_cedar_enforcement_keeps_the_binding_rule() -> None:
    spec = jwt_spec(MERIDIAN_GATEWAY_ENFORCEMENT="cedar")

    assert policy_names(spec)[-1] == "meridian_traveler_binding"


def test_interceptor_only_enforcement_leaves_the_binding_rule_out() -> None:
    spec = jwt_spec(MERIDIAN_GATEWAY_ENFORCEMENT="interceptor")

    assert policy_names(spec) == [
        "meridian_read_tools", "meridian_hold_governance", "meridian_booking_governance"]


def test_an_unknown_enforcement_design_is_a_render_error() -> None:
    with pytest.raises(render_config.ConfigError, match="MERIDIAN_GATEWAY_ENFORCEMENT"):
        render_config.account_values(
            env_for(MERIDIAN_AGENTCORE_AUTH="jwt", MERIDIAN_GATEWAY_ENFORCEMENT="neither",
                    **COGNITO_ENV))


def test_a_bad_mode_is_still_a_render_error_with_the_same_words() -> None:
    with pytest.raises(render_config.ConfigError, match="must be 'iam' or 'jwt', not 'true'"):
        render_config.identity_mode({"MERIDIAN_AGENTCORE_AUTH": "true"})


def test_main_reports_the_mode_and_the_enforcement_design(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    for name, value in COGNITO_ENV.items():
        monkeypatch.setenv(name, value)

    assert render_config.main([]) == 0

    out = capsys.readouterr().out
    assert "AgentCore identity mode: jwt" in out
    assert "Gateway enforcement: both" in out


def test_the_cdk_fixture_is_the_jwt_render_with_the_cdk_test_values() -> None:
    """cdk.test.ts synthesizes this file, so a render change must change it too.

    Regenerate with ``MERIDIAN_UPDATE_FIXTURES=1`` and this test's name in ``-k``.
    """
    cdk_values = {
        "cluster": "arn:aws:rds:us-east-1:123456789012:cluster:meridian",
        "secret": "arn:aws:secretsmanager:us-east-1:123456789012:secret:meridian-AbC123",
        "workflow": "arn:aws:secretsmanager:us-east-1:123456789012:secret:"
                    "meridian/aurora/workflow-login-XyZ789",
        "gateway": "arn:aws:secretsmanager:us-east-1:123456789012:secret:"
                   "meridian/aurora/gateway-login-GwY456",
    }
    env = {
        "AURORA_CLUSTER_ARN": cdk_values["cluster"],
        "AURORA_SECRET_ARN": cdk_values["secret"],
        "AURORA_WORKFLOW_SECRET_ARN": cdk_values["workflow"],
        "AURORA_GATEWAY_SECRET_ARN": cdk_values["gateway"],
        "MERIDIAN_AGENTCORE_AUTH": "jwt",
        "MERIDIAN_COGNITO_REGION": "us-east-1",
        "MERIDIAN_COGNITO_USER_POOL_ID": POOL_ID,
        "MERIDIAN_COGNITO_APP_CLIENT_ID": CLIENT_ID,
    }
    values = {
        **render_config.account_values(env),
        "GATEWAY_ID": "meridianv2-meridian-aurora-abcde12345",
        "POLICY_ENGINE_ID": "meridianv2_MeridianGovernance-abcde12345",
    }
    spec = render_config.render(*templates(), values)[0]

    if os.environ.get("MERIDIAN_UPDATE_FIXTURES") == "1":
        JWT_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        JWT_FIXTURE.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    assert json.loads(JWT_FIXTURE.read_text(encoding="utf-8")) == spec
