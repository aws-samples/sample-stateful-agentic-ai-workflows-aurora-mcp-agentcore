"""The read-back checks name every hop that does not report the configuration the mode needs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from scripts.identity_release import preflight, settings
from tests import release_support as rs

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def gateway_findings(described, mode="jwt", design=settings.BOTH):
    return preflight.check_gateway(described, rs.target(mode, design))


def runtime_findings(described, mode="jwt", name="MeridianConcierge"):
    return preflight.check_runtime(name, described, rs.target(mode))


# ------------------------------------------------------------------- Gateway


@pytest.mark.parametrize("design", [settings.BOTH, settings.CEDAR, settings.INTERCEPTOR])
def test_a_jwt_gateway_that_matches_its_design_has_no_findings(design):
    assert gateway_findings(rs.gateway("jwt", design), "jwt", design) == []


def test_an_iam_gateway_in_iam_mode_has_no_findings():
    assert gateway_findings(rs.gateway("iam"), "iam") == []


@pytest.mark.parametrize(("path", "value", "word"), [
    ("status", "UPDATING", "READY"),
    ("authorizerType", "AWS_IAM", "CUSTOM_JWT"),
    ("authorizerConfiguration.customJWTAuthorizer.allowedClients", ["other"], "allowedClients"),
    ("authorizerConfiguration.customJWTAuthorizer.allowedClients", [], "allowedClients"),
    ("authorizerConfiguration.customJWTAuthorizer.discoveryUrl", "https://x/.well-known/o",
     "discoveryUrl"),
    ("authorizerConfiguration.customJWTAuthorizer.allowedAudience", ["aud"], "allowedAudience"),
    ("interceptorConfigurations", None, "no request interceptor"),
    ("policyEngineConfiguration.mode", "LOG_ONLY", "ENFORCE"),
])
def test_each_jwt_gateway_deviation_is_one_named_finding(path, value, word):
    found = gateway_findings(rs.mutated(rs.gateway("jwt"), path, value))

    assert len(found) == 1 and found[0].startswith("Gateway: ") and word in found[0], found


def test_an_interceptor_that_cannot_see_the_token_is_a_finding():
    broken = rs.mutated(rs.gateway("jwt"), "interceptorConfigurations", [{
        "interceptor": {"lambda": {"arn": rs.INTERCEPTOR_ARN}},
        "interceptionPoints": ["REQUEST"], "inputConfiguration": {"passRequestHeaders": False}}])

    assert any("passRequestHeaders" in line for line in gateway_findings(broken))


def test_the_wrong_interceptor_function_or_point_is_a_finding():
    wrong_function = rs.mutated(rs.gateway("jwt"), "interceptorConfigurations", [{
        "interceptor": {"lambda": {"arn": "arn:aws:lambda:us-east-1:123456789012:function:x"}},
        "interceptionPoints": ["REQUEST"], "inputConfiguration": {"passRequestHeaders": True}}])
    wrong_point = rs.mutated(rs.gateway("jwt"), "interceptorConfigurations", [{
        "interceptor": {"lambda": {"arn": rs.INTERCEPTOR_ARN}},
        "interceptionPoints": ["REQUEST", "RESPONSE"], "inputConfiguration": {
            "passRequestHeaders": True}}])

    assert any("function" in line for line in gateway_findings(wrong_function))
    assert any("REQUEST" in line for line in gateway_findings(wrong_point))


def test_two_interceptors_are_a_finding():
    config = rs.gateway("jwt")["interceptorConfigurations"][0]
    doubled = rs.mutated(rs.gateway("jwt"), "interceptorConfigurations", [config, config])

    assert any("exactly one" in line for line in gateway_findings(doubled))


def test_the_cedar_only_design_expects_no_interceptor_on_the_gateway():
    attached = rs.gateway("jwt", settings.BOTH)

    found = gateway_findings(attached, "jwt", settings.CEDAR)

    assert len(found) == 1 and "interceptor" in found[0]


def test_an_iam_gateway_must_be_iam_with_no_interceptor():
    assert any("AWS_IAM" in line for line in gateway_findings(rs.gateway("jwt"), "iam"))
    leftover = rs.mutated(rs.gateway("iam"), "interceptorConfigurations", [{"interceptor": {}}])
    assert any("interceptor" in line for line in gateway_findings(leftover, "iam"))


# ------------------------------------------------------------------- Runtimes


@pytest.mark.parametrize("name", ["MeridianConcierge", "MeridianWorkflow"])
def test_a_jwt_runtime_that_matches_has_no_findings(name):
    assert runtime_findings(rs.runtime(name, "jwt"), "jwt", name) == []


@pytest.mark.parametrize("iam_env", [False, True])
def test_an_iam_runtime_has_no_findings_with_or_without_the_mode_variable(iam_env):
    assert runtime_findings(rs.runtime("MeridianConcierge", "iam", iam_env=iam_env), "iam") == []


@pytest.mark.parametrize(("path", "value", "word"), [
    ("status", "UPDATING", "READY"),
    ("authorizerConfiguration", None, "JWT authorizer"),
    ("authorizerConfiguration.customJWTAuthorizer.allowedClients", ["other"], "allowedClients"),
    ("authorizerConfiguration.customJWTAuthorizer.allowedAudience", ["aud"], "allowedAudience"),
    ("requestHeaderConfiguration", None, "Authorization"),
    ("requestHeaderConfiguration.requestHeaderAllowlist", ["X-Other"], "Authorization"),
    ("requestHeaderConfiguration.requestHeaderAllowlist", ["Authorization", "X-Other"],
     "Authorization"),
    ("environmentVariables.MERIDIAN_AGENTCORE_AUTH", "iam", "MERIDIAN_AGENTCORE_AUTH"),
    ("environmentVariables.MERIDIAN_AGENTCORE_AUTH", None, "MERIDIAN_AGENTCORE_AUTH"),
])
def test_each_jwt_runtime_deviation_is_one_finding_naming_the_runtime(path, value, word):
    found = runtime_findings(rs.mutated(rs.runtime("MeridianWorkflow", "jwt"), path, value),
                             "jwt", "MeridianWorkflow")

    assert len(found) == 1, found
    assert found[0].startswith("Runtime MeridianWorkflow: ") and word in found[0]


def test_an_iam_runtime_must_not_carry_an_authorizer_or_the_allowlist():
    found = runtime_findings(rs.runtime("MeridianConcierge", "jwt"), "iam")

    assert any("JWT authorizer" in line for line in found)
    assert any("Authorization" in line for line in found)
    assert any("MERIDIAN_AGENTCORE_AUTH" in line for line in found)


# ------------------------------------------------------------------- Cedar


@pytest.mark.parametrize(("mode", "design", "wanted"), [
    ("jwt", settings.BOTH, True), ("jwt", settings.CEDAR, True),
    ("jwt", settings.INTERCEPTOR, False), ("iam", settings.BOTH, False)])
def test_the_binding_rule_is_present_only_when_the_design_uses_it(mode, design, wanted):
    names = rs.BASE_POLICIES + ([rs.BINDING_POLICY] if wanted else [])
    assert preflight.check_policies(names, rs.target(mode, design)) == []
    flipped = rs.BASE_POLICIES + ([] if wanted else [rs.BINDING_POLICY])
    found = preflight.check_policies(flipped, rs.target(mode, design))
    assert len(found) == 1 and rs.BINDING_POLICY in found[0]


def test_a_missing_base_policy_is_a_finding_in_every_mode():
    found = preflight.check_policies([rs.BINDING_POLICY], rs.target("jwt"))

    assert any("meridian_hold_governance" in line for line in found)


# ------------------------------------------------------------ service environment


def test_a_jwt_service_environment_with_the_pool_and_backend_login_has_no_findings():
    assert preflight.check_service_environment(rs.jwt_service_variables(), {}, rs.target()) == []


@pytest.mark.parametrize(("key", "value", "word"), [
    ("MERIDIAN_AGENTCORE_AUTH", "iam", "MERIDIAN_AGENTCORE_AUTH"),
    ("MERIDIAN_COGNITO_APP_CLIENT_ID", "other", "MERIDIAN_COGNITO_APP_CLIENT_ID"),
    ("MERIDIAN_COGNITO_USER_POOL_ID", None, "MERIDIAN_COGNITO_USER_POOL_ID"),
    ("ENVIRONMENT", "development", "ENVIRONMENT"),
    ("MERIDIAN_API_TOKEN", "t", "MERIDIAN_API_TOKEN"),
    ("MERIDIAN_ALLOW_INSECURE_LOCALHOST", "1", "MERIDIAN_ALLOW_INSECURE_LOCALHOST"),
    ("AURORA_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123456789012:secret:master-AbC123",
     "meridian_backend"),
])
def test_each_service_environment_deviation_is_one_finding(key, value, word):
    variables = rs.jwt_service_variables()
    if value is None:
        variables.pop(key)
    else:
        variables[key] = value

    found = preflight.check_service_environment(variables, {}, rs.target())

    assert len(found) == 1 and word in found[0], found


def test_the_shared_token_secret_reference_is_a_finding_in_jwt_mode():
    found = preflight.check_service_environment(
        rs.jwt_service_variables(), {"MERIDIAN_API_TOKEN": "arn:secret"}, rs.target())

    assert len(found) == 1 and "MERIDIAN_API_TOKEN" in found[0]


def test_an_iam_service_environment_must_carry_no_sign_in_setting():
    iam = rs.target("iam")
    assert preflight.check_service_environment({"AWS_REGION": "us-east-1"}, {}, iam) == []
    found = preflight.check_service_environment(rs.jwt_service_variables(), {}, rs.target("iam"))
    assert any("MERIDIAN_COGNITO_USER_POOL_ID" in line for line in found)


# -------------------------------------------------------------- identity stack


def test_identity_outputs_must_exist_and_name_the_configured_pool_and_client():
    pool = settings.cognito_settings(rs.COGNITO_ENV)
    outputs = {"UserPoolId": rs.POOL, "AppClientId": rs.CLIENT, "HostedUiDomain": "d.auth.x",
               "Issuer": pool.issuer}
    assert preflight.identity_findings(outputs, pool) == []
    found = preflight.identity_findings({"UserPoolId": "other", "AppClientId": rs.CLIENT}, pool)
    assert any("HostedUiDomain" in line and "Issuer" in line for line in found)
    assert any("UserPoolId" in line and "differs" in line for line in found)


# ----------------------------------------------------------------- the proof


def write_proof(tmp_path, **fields):
    path = tmp_path / "backend-login-proof.json"
    path.write_text(json.dumps({
        "ok": True, "login": "meridian_backend", "at": NOW.isoformat(), **fields}))
    return path


def test_a_recent_passing_backend_login_proof_has_no_findings(tmp_path):
    assert preflight.check_backend_login_proof(write_proof(tmp_path), NOW) == []


@pytest.mark.parametrize(("fields", "word"), [
    ({"ok": False}, "did not pass"),
    ({"login": "meridian_admin"}, "meridian_backend"),
    ({"at": (NOW - timedelta(days=8)).isoformat()}, "older than"),
    ({"at": "yesterday"}, "unreadable"),
])
def test_a_proof_that_failed_names_the_wrong_login_or_is_old_is_a_finding(tmp_path, fields, word):
    found = preflight.check_backend_login_proof(write_proof(tmp_path, **fields), NOW)

    assert len(found) == 1 and word in found[0], found


def test_a_missing_or_unreadable_proof_is_a_finding_that_names_the_command(tmp_path):
    missing = preflight.check_backend_login_proof(tmp_path / "none.json", NOW)
    assert len(missing) == 1 and "prove_backend_login.py" in missing[0]
    broken = tmp_path / "broken.json"
    broken.write_text("{")
    assert "unreadable" in preflight.check_backend_login_proof(broken, NOW)[0]


# ------------------------------------------------------------- reading the hops


def fake_control(gateway=None, runtimes=None, pages=None):
    control = Mock()
    control.get_gateway.return_value = gateway or rs.gateway("jwt")
    described = runtimes or {rs.RUNTIME_IDS[n]: rs.runtime(n) for n in rs.RUNTIME_IDS}
    control.get_agent_runtime.side_effect = lambda agentRuntimeId: described[agentRuntimeId]
    pages = pages or [{"policies": rs.policies(rs.BASE_POLICIES), "nextToken": "n"},
                      {"policies": rs.policies([rs.BINDING_POLICY])}]
    control.list_policies.side_effect = lambda **kwargs: pages[1 if "nextToken" in kwargs else 0]
    return control


def test_the_state_is_read_from_the_gateway_both_runtimes_and_every_policy_page():
    control = fake_control()

    state = preflight.read_state(control, rs.GATEWAY_ID, rs.RUNTIME_IDS)

    assert state.gateway["gatewayId"] == rs.GATEWAY_ID
    assert set(state.runtimes) == {"MeridianConcierge", "MeridianWorkflow"}
    assert state.policy_names == rs.BASE_POLICIES + [rs.BINDING_POLICY]
    control.list_policies.assert_any_call(policyEngineId="meridianv2_Engine-abc")


def test_a_policy_that_is_not_active_does_not_count():
    pages = [{"policies": [{"name": rs.BINDING_POLICY, "status": "CREATING"}]}]

    state = preflight.read_state(fake_control(pages=pages), rs.GATEWAY_ID, rs.RUNTIME_IDS)

    assert state.policy_names == []


def test_a_gateway_with_no_policy_engine_has_no_policies():
    control = fake_control(gateway=rs.mutated(rs.gateway("jwt"), "policyEngineConfiguration", None))

    state = preflight.read_state(control, rs.GATEWAY_ID, rs.RUNTIME_IDS)

    assert state.policy_names == []
    control.list_policies.assert_not_called()


def test_the_hop_findings_cover_the_gateway_the_runtimes_and_the_rules():
    state = preflight.read_state(fake_control(), rs.GATEWAY_ID, rs.RUNTIME_IDS)
    assert preflight.hop_findings(state, rs.target()) == []

    stale = {rs.RUNTIME_IDS["MeridianConcierge"]: rs.runtime("MeridianConcierge", "iam"),
             rs.RUNTIME_IDS["MeridianWorkflow"]: rs.runtime("MeridianWorkflow", "jwt")}
    state = preflight.read_state(
        fake_control(gateway=rs.gateway("iam"), runtimes=stale,
                     pages=[{"policies": rs.policies(rs.BASE_POLICIES)}]),
        rs.GATEWAY_ID, rs.RUNTIME_IDS)
    found = preflight.hop_findings(state, rs.target())
    assert [line.split(":")[0] for line in found].count("Gateway") >= 1
    assert any(line.startswith("Runtime MeridianConcierge: ") for line in found)
    assert not any(line.startswith("Runtime MeridianWorkflow: ") for line in found)
    assert any(rs.BINDING_POLICY in line for line in found)


def test_the_hop_ids_come_from_the_backend_environment():
    ids = preflight.hop_ids({
        "AGENTCORE_GATEWAY_URL": f"https://{rs.GATEWAY_ID}.gateway.bedrock-agentcore."
                                 f"{rs.REGION}.amazonaws.com/mcp",
        "AGENTCORE_RUNTIME_ARN": f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:runtime/"
                                 + rs.RUNTIME_IDS["MeridianConcierge"],
        "AGENTCORE_WORKFLOW_RUNTIME_ARN": f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:"
                                          "runtime/" + rs.RUNTIME_IDS["MeridianWorkflow"],
    })

    assert ids == (rs.GATEWAY_ID, rs.RUNTIME_IDS)


@pytest.mark.parametrize("missing", ["AGENTCORE_GATEWAY_URL", "AGENTCORE_RUNTIME_ARN",
                                     "AGENTCORE_WORKFLOW_RUNTIME_ARN"])
def test_a_missing_hop_setting_is_named(missing):
    env = {"AGENTCORE_GATEWAY_URL": "https://g.gateway.x/mcp",
           "AGENTCORE_RUNTIME_ARN": "arn:aws:bedrock-agentcore:r:a:runtime/c",
           "AGENTCORE_WORKFLOW_RUNTIME_ARN": "arn:aws:bedrock-agentcore:r:a:runtime/w"}
    del env[missing]

    with pytest.raises(settings.ReleaseConfigError, match=missing):
        preflight.hop_ids(env)


def test_refusing_prints_one_line_per_finding_and_exits_nonzero(capsys):
    with pytest.raises(SystemExit) as stopped:
        preflight.refuse_if_any(["Gateway: a", "Runtime X: b"], "jwt")

    text = str(stopped.value)
    assert "refusing: 2 hops do not report jwt" in text
    assert "  Gateway: a" in text and "  Runtime X: b" in text
    preflight.refuse_if_any([], "jwt")


# ------------------------------------------------------------ the target and settings


def test_the_target_follows_the_mode_and_the_design():
    env = {**rs.COGNITO_ENV, "MERIDIAN_GATEWAY_ENFORCEMENT": "cedar"}

    cedar = preflight.target_for("jwt", env, rs.ACCOUNT, rs.REGION)
    both = preflight.target_for("jwt", rs.COGNITO_ENV, rs.ACCOUNT, rs.REGION)
    iam = preflight.target_for("iam", {}, rs.ACCOUNT, rs.REGION)

    assert cedar.design == settings.CEDAR and cedar.interceptor_arn is None
    assert both.interceptor_arn == rs.INTERCEPTOR_ARN and both.cognito.client_id == rs.CLIENT
    assert iam.cognito is None and iam.interceptor_arn is None


@pytest.mark.parametrize("arn", ["", "arn:aws:s3:::bucket", "arn:aws:rds:us-east-1:12:cluster:x"])
def test_a_deployment_target_needs_an_aurora_cluster_arn(arn):
    with pytest.raises(settings.ReleaseConfigError, match="AURORA_CLUSTER_ARN"):
        settings.deployment_target({"AURORA_CLUSTER_ARN": arn})


def test_the_deployment_target_is_the_clusters_account_and_region():
    arn = f"arn:aws:rds:{rs.REGION}:{rs.ACCOUNT}:cluster:meridian"

    assert settings.deployment_target({"AURORA_CLUSTER_ARN": arn}) == (rs.ACCOUNT, rs.REGION)


# ------------------------------------------------- fixtures against the botocore model


def _unknown_members(shape, value, path):
    """Dotted paths in ``value`` that the service model's ``shape`` does not declare."""
    if shape.type_name == "structure" and isinstance(value, dict):
        found = [f"{path}.{key}" for key in value if key not in shape.members]
        for key, child in value.items():
            if key in shape.members:
                found += _unknown_members(shape.members[key], child, f"{path}.{key}")
        return found
    if shape.type_name == "list" and isinstance(value, list):
        return [p for item in value for p in _unknown_members(shape.member, item, f"{path}[]")]
    return []


def _output_shape(operation):
    import botocore.session

    model = botocore.session.get_session().get_service_model("bedrock-agentcore-control")
    return model.operation_model(operation).output_shape


@pytest.mark.parametrize("mode", ["iam", "jwt"])
def test_the_gateway_fixture_uses_only_members_the_service_model_declares(mode):
    shape = _output_shape("GetGateway")

    assert _unknown_members(shape, rs.gateway(mode), "gateway") == []
    assert rs.gateway(mode)["authorizerType"] in shape.members["authorizerType"].enum


def test_the_runtime_and_policy_fixtures_use_only_declared_members():
    runtime = _output_shape("GetAgentRuntime")
    listing = _output_shape("ListPolicies")

    for name in rs.RUNTIME_IDS:
        assert _unknown_members(runtime, rs.runtime(name, "jwt"), name) == []
    assert _unknown_members(listing, {"policies": rs.policies(["a"])}, "policies") == []
