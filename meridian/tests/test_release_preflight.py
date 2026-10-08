"""The read-back checks name every hop that does not report the configuration the mode needs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from scripts.identity_release import preflight, settings
from scripts.identity_release.preflight import Target
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


def test_an_iam_gateway_that_keeps_a_jwt_authorizer_configuration_is_a_finding():
    leftover = rs.mutated(rs.gateway("iam"), "authorizerConfiguration", rs.jwt_authorizer())

    found = gateway_findings(leftover, "iam")

    assert len(found) == 1 and "authorizerConfiguration" in found[0] and "iam" in found[0].lower()


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
    assert preflight.check_policies(rs.policy_modes(names), rs.target(mode, design)) == []
    flipped = rs.BASE_POLICIES + ([] if wanted else [rs.BINDING_POLICY])
    found = preflight.check_policies(rs.policy_modes(flipped), rs.target(mode, design))
    assert len(found) == 1 and rs.BINDING_POLICY in found[0]


def test_a_missing_base_policy_is_a_finding_in_every_mode():
    found = preflight.check_policies(rs.policy_modes([rs.BINDING_POLICY]), rs.target("jwt"))

    assert any("meridian_hold_governance" in line for line in found)


@pytest.mark.parametrize("name", [*rs.BASE_POLICIES, rs.BINDING_POLICY])
@pytest.mark.parametrize("mode", ["LOG_ONLY", None, "SHADOW"])
def test_a_required_rule_that_is_not_enforcing_is_a_finding(name, mode):
    states = rs.policy_modes(rs.BASE_POLICIES + [rs.BINDING_POLICY])
    states[name] = mode

    found = preflight.check_policies(states, rs.target("jwt"))

    assert len(found) == 1 and name in found[0] and "ACTIVE" in found[0], found


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


def identity_outputs(**changes):
    outputs = {o["OutputKey"]: o["OutputValue"] for o in rs.identity_outputs()}
    outputs.update(changes)
    return outputs


def test_the_issuer_output_must_be_the_configured_pools_issuer():
    pool = settings.cognito_settings(rs.COGNITO_ENV)

    found = preflight.identity_findings(identity_outputs(Issuer="https://other/issuer"), pool)

    assert len(found) == 1 and "Issuer" in found[0] and "differs" in found[0]


def test_the_hosted_ui_domain_must_match_the_sites_setting_when_one_is_given():
    pool = settings.cognito_settings(rs.COGNITO_ENV)
    outputs = identity_outputs()

    assert preflight.identity_findings(outputs, pool, rs.HOSTED_UI_DOMAIN) == []
    assert preflight.identity_findings(outputs, pool) == []
    for wrong in ("other.auth.example.com", ""):
        found = preflight.identity_findings(outputs, pool, wrong)
        assert len(found) == 1 and "HostedUiDomain" in found[0] and "VITE_COGNITO" in found[0]


def test_identity_outputs_must_exist_and_name_the_configured_pool_and_client():
    pool = settings.cognito_settings(rs.COGNITO_ENV)
    outputs = identity_outputs()
    assert preflight.identity_findings(outputs, pool) == []
    found = preflight.identity_findings({"UserPoolId": "other", "AppClientId": rs.CLIENT}, pool)
    assert any("HostedUiDomain" in line and "Issuer" in line for line in found)
    assert any("UserPoolId" in line and "differs" in line for line in found)


# ----------------------------------------------------------------- the proof


def write_proof(tmp_path, **fields):
    path = tmp_path / "backend-login-proof.json"
    path.write_text(json.dumps(rs.receipt(NOW, **fields)))
    return path


def proof_findings(path, now=NOW, sha=rs.SHA, mode="jwt"):
    return preflight.check_backend_login_proof(path, rs.target(mode), sha, now)


def test_a_recent_passing_receipt_bound_to_this_release_has_no_findings(tmp_path):
    assert proof_findings(write_proof(tmp_path)) == []


def test_the_receipt_schema_is_the_documented_one():
    assert tuple(settings.PROOF_FIELDS) == (
        "ok", "at", "account", "region", "user_pool_id", "git_sha", "login", "checks")
    assert set(rs.receipt(NOW)) == set(settings.PROOF_FIELDS)


@pytest.mark.parametrize(("fields", "word"), [
    ({"ok": False}, "did not pass"),
    ({"ok": "no"}, "did not pass"),
    ({"ok": 1}, "did not pass"),
    ({"ok": None}, "did not pass"),
    ({"login": "meridian_admin"}, "meridian_backend"),
    ({"at": (NOW - timedelta(days=8)).isoformat()}, "older than"),
    ({"at": "yesterday"}, "unreadable"),
    ({"at": 12345}, "unreadable"),
    ({"at": "2026-10-08T12:00:00"}, "time zone"),
    ({"at": (NOW + timedelta(days=1)).isoformat()}, "in the future"),
    ({"at": (NOW + timedelta(minutes=6)).isoformat()}, "in the future"),
    ({"account": "999999999999"}, "account"),
    ({"region": "eu-west-1"}, "region"),
    ({"user_pool_id": "us-east-1_Other"}, "user_pool_id"),
    ({"git_sha": "f" * 40}, "git_sha"),
    ({"checks": {}}, "checks"),
    ({"checks": {"warm": True, "recovery": False}}, "recovery"),
    ({"checks": {"warm": "yes"}}, "warm"),
    ({"checks": ["warm"]}, "checks"),
])
def test_a_receipt_that_is_bad_or_for_another_release_is_one_finding(tmp_path, fields, word):
    found = proof_findings(write_proof(tmp_path, **fields))

    assert len(found) == 1 and word in found[0], found
    assert "prove_backend_login.py" in found[0]


@pytest.mark.parametrize("field", ["account", "region", "user_pool_id", "git_sha", "checks",
                                   "at", "ok", "login"])
def test_a_receipt_missing_a_field_is_a_finding(tmp_path, field):
    receipt = rs.receipt(NOW)
    del receipt[field]
    path = tmp_path / "backend-login-proof.json"
    path.write_text(json.dumps(receipt))

    found = proof_findings(path)

    assert found and all(isinstance(line, str) for line in found)
    assert any(field in line or "did not pass" in line or "unreadable" in line for line in found)


def test_a_few_minutes_of_clock_skew_is_tolerated(tmp_path):
    ahead = write_proof(tmp_path, at=(NOW + timedelta(minutes=4)).isoformat())

    assert proof_findings(ahead) == []


def test_the_receipt_is_compared_with_the_head_it_is_given(tmp_path):
    path = write_proof(tmp_path)

    assert any("git_sha" in line for line in proof_findings(path, sha="1" * 40))


def test_a_receipt_wrong_in_several_ways_reports_each(tmp_path):
    path = write_proof(tmp_path, account="999999999999", region="eu-west-1")

    found = proof_findings(path)

    assert len(found) == 2


def test_a_naive_timestamp_is_a_finding_not_a_type_error(tmp_path):
    path = write_proof(tmp_path, at="2026-10-08T11:59:00")

    assert proof_findings(path) == [
        "Backend login proof: its timestamp has no time zone; run "
        f"`python scripts/prove_backend_login.py --apply {settings.CONFIRM_FLAG}` before the "
        "release"]


def test_a_missing_or_unreadable_proof_is_a_finding_that_names_the_command(tmp_path):
    missing = proof_findings(tmp_path / "none.json")
    assert len(missing) == 1 and "prove_backend_login.py" in missing[0]
    broken = tmp_path / "broken.json"
    broken.write_text("{")
    assert "unreadable" in proof_findings(broken)[0]
    listed = tmp_path / "listed.json"
    listed.write_text("[1]")
    assert "did not pass" in proof_findings(listed)[0]


def test_the_proof_is_only_defined_for_a_target_with_a_pool(tmp_path):
    found = preflight.check_backend_login_proof(
        write_proof(tmp_path), rs.target("iam"), rs.SHA, NOW)

    assert len(found) == 1 and "pool" in found[0]


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
    assert state.policies == rs.policy_modes(rs.BASE_POLICIES + [rs.BINDING_POLICY])
    control.list_policies.assert_any_call(policyEngineId="meridianv2_Engine-abc")


def test_a_policy_that_is_not_active_does_not_count():
    pages = [{"policies": [{"name": rs.BINDING_POLICY, "status": "CREATING"}]}]

    state = preflight.read_state(fake_control(pages=pages), rs.GATEWAY_ID, rs.RUNTIME_IDS)

    assert state.policies == {}


def test_a_gateway_with_no_policy_engine_has_no_policies():
    control = fake_control(gateway=rs.mutated(rs.gateway("jwt"), "policyEngineConfiguration", None))

    state = preflight.read_state(control, rs.GATEWAY_ID, rs.RUNTIME_IDS)

    assert state.policies == {}
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
    assert "refusing: 2 findings against the jwt release:" in text
    assert "  Gateway: a" in text and "  Runtime X: b" in text
    preflight.refuse_if_any([], "jwt")
    with pytest.raises(SystemExit) as one:
        preflight.refuse_if_any(["Gateway: a"], "jwt")
    assert "refusing: 1 finding against the jwt release:" in str(one.value)


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


def _output_shape(operation, service="bedrock-agentcore-control"):
    import botocore.session

    model = botocore.session.get_session().get_service_model(service)
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


def test_the_policy_modes_are_the_ones_the_service_model_declares():
    summary = _output_shape("ListPolicies").members["policies"].member
    modes = summary.members["enforcementMode"].enum

    assert "ACTIVE" in modes and "LOG_ONLY" in modes
    assert rs.policies(["a"])[0]["enforcementMode"] in modes


def test_the_app_runner_and_identity_stack_fixtures_use_only_declared_members():
    described = _output_shape("DescribeService", "apprunner")
    stacks = _output_shape("DescribeStacks", "cloudformation")
    service = rs.app_runner_service({"A": "b"}, {"C": "arn:d"})

    assert _unknown_members(described, {"Service": service}, "service") == []
    assert _unknown_members(
        stacks, {"Stacks": [{"Outputs": rs.identity_outputs()}]}, "stacks") == []


def test_the_scope_and_claim_members_exist_on_both_authorizers():
    gateway = _output_shape("GetGateway").members["authorizerConfiguration"]
    runtime = _output_shape("GetAgentRuntime").members["authorizerConfiguration"]

    for shape in (gateway, runtime):
        members = shape.members["customJWTAuthorizer"].members
        assert "allowedScopes" in members and "customClaims" in members


# --------------------------------------------------------- more deviations and guards


@pytest.mark.parametrize("member", ["allowedScopes", "customClaims"])
def test_scopes_and_claims_on_the_gateway_or_a_runtime_are_findings(member):
    path = f"authorizerConfiguration.customJWTAuthorizer.{member}"
    extra = [{"inboundTokenClaimName": "x"}] if member == "customClaims" else ["openid"]

    gateway = gateway_findings(rs.mutated(rs.gateway("jwt"), path, extra))
    runtime = runtime_findings(rs.mutated(rs.runtime("MeridianConcierge", "jwt"), path, extra))

    assert len(gateway) == 1 and member in gateway[0] and gateway[0].startswith("Gateway: ")
    assert len(runtime) == 1 and member in runtime[0]
    empty = rs.mutated(rs.gateway("jwt"), path, [])
    assert gateway_findings(empty) == []


@pytest.mark.parametrize("value", ["jwt", "JWT", "true", "", "cognito"])
def test_an_iam_service_environment_must_not_declare_the_jwt_mode(value):
    found = preflight.check_service_environment(
        {"MERIDIAN_AGENTCORE_AUTH": value}, {}, rs.target("iam"))

    assert len(found) == 1 and "MERIDIAN_AGENTCORE_AUTH" in found[0]


@pytest.mark.parametrize("value", [None, "iam"])
def test_an_iam_service_environment_may_leave_the_mode_unset_or_iam(value):
    variables = {} if value is None else {"MERIDIAN_AGENTCORE_AUTH": value}

    assert preflight.check_service_environment(variables, {}, rs.target("iam")) == []


def test_the_backend_login_must_not_be_the_master_secret_even_when_both_settings_agree():
    variables = {**rs.jwt_service_variables(), "AURORA_SECRET_ARN": rs.MASTER_SECRET,
                 "AURORA_BACKEND_SECRET_ARN": rs.MASTER_SECRET}

    found = preflight.check_service_environment(variables, {}, rs.target())

    assert len(found) == 1 and "master" in found[0], found


def test_the_backend_login_must_be_the_backend_secret_the_release_expects():
    other = rs.BACKEND_SECRET.replace("backend-AbC123", "backend-Other9")
    variables = {**rs.jwt_service_variables(), "AURORA_SECRET_ARN": other,
                 "AURORA_BACKEND_SECRET_ARN": other}

    found = preflight.check_service_environment(variables, {}, rs.target())

    assert len(found) == 1 and "expected" in found[0], found


def test_without_a_known_master_or_backend_secret_only_the_two_settings_are_compared():
    bare = Target(mode="jwt", design=settings.BOTH, cognito=rs.target().cognito,
                  interceptor_arn=rs.INTERCEPTOR_ARN)

    assert preflight.check_service_environment(rs.jwt_service_variables(), {}, bare) == []


# ------------------------------------------------------------------ the target


def test_a_jwt_target_without_a_pool_cannot_be_built():
    with pytest.raises(settings.ReleaseConfigError, match="pool"):
        Target(mode="jwt", design=settings.BOTH)


def test_a_target_with_an_unknown_mode_cannot_be_built():
    with pytest.raises(settings.ReleaseConfigError, match="mode"):
        Target(mode="maybe", design=settings.BOTH)


def test_the_target_carries_the_account_region_and_secrets_from_the_settings():
    env = {**rs.COGNITO_ENV, "AURORA_SECRET_ARN": rs.MASTER_SECRET,
           "AURORA_BACKEND_SECRET_ARN": rs.BACKEND_SECRET}

    target = preflight.target_for("jwt", env, rs.ACCOUNT, rs.REGION)

    assert (target.account, target.region) == (rs.ACCOUNT, rs.REGION)
    assert target.master_secret_arn == rs.MASTER_SECRET
    assert target.backend_secret_arn == rs.BACKEND_SECRET


# --------------------------------------------- garbage in, findings out, never a raise

GARBAGE = [None, 7, "text", [], ["x"], {}, {"x": 1}, [None]]
NESTED_GATEWAY = [
    ("authorizerConfiguration", [None, [], "x", {"customJWTAuthorizer": []},
                                 {"customJWTAuthorizer": "x"}, {"customJWTAuthorizer": None}]),
    ("interceptorConfigurations", [{}, ["x"], "x", [None], [{"interceptor": []}],
                                   [{"interceptor": {"lambda": "x"}}],
                                   [{"interceptionPoints": "REQUEST"}], 5]),
    ("policyEngineConfiguration", ["x", [], 5]),
    ("status", [[], {}, 5]),
]
NESTED_RUNTIME = [
    ("authorizerConfiguration", [None, [], "x", {"customJWTAuthorizer": "x"}]),
    ("requestHeaderConfiguration", ["x", [], {"requestHeaderAllowlist": "Authorization"},
                                    {"requestHeaderAllowlist": 5}]),
    ("environmentVariables", ["x", [], 5, {"MERIDIAN_AGENTCORE_AUTH": []}]),
]


def assert_findings(found):
    assert isinstance(found, list) and found and all(isinstance(x, str) for x in found), found


@pytest.mark.parametrize("garbage", GARBAGE)
@pytest.mark.parametrize("mode", ["iam", "jwt"])
def test_check_gateway_turns_garbage_into_findings(garbage, mode):
    assert_findings(preflight.check_gateway(garbage, rs.target(mode)))


@pytest.mark.parametrize(("key", "values"), NESTED_GATEWAY)
@pytest.mark.parametrize("mode", ["iam", "jwt"])
def test_check_gateway_turns_nested_garbage_into_findings(key, values, mode):
    for value in values:
        found = preflight.check_gateway({**rs.gateway(mode), key: value}, rs.target(mode))
        assert isinstance(found, list) and all(isinstance(line, str) for line in found)
        if mode == "jwt":
            assert_findings(found)


@pytest.mark.parametrize("garbage", GARBAGE)
@pytest.mark.parametrize("mode", ["iam", "jwt"])
def test_check_runtime_turns_garbage_into_findings(garbage, mode):
    assert_findings(preflight.check_runtime("MeridianWorkflow", garbage, rs.target(mode)))


@pytest.mark.parametrize(("key", "values"), NESTED_RUNTIME)
def test_check_runtime_turns_nested_garbage_into_findings(key, values):
    for value in values:
        broken = {**rs.runtime("MeridianConcierge", "jwt"), key: value}
        assert_findings(preflight.check_runtime("MeridianConcierge", broken, rs.target("jwt")))


@pytest.mark.parametrize("garbage", [None, 7, "text", ["meridian_read_tools"], {"x": 1},
                                     {"meridian_read_tools": []}])
def test_check_policies_turns_garbage_into_findings(garbage):
    assert_findings(preflight.check_policies(garbage, rs.target("jwt")))


@pytest.mark.parametrize("garbage", [None, 7, "text", [], ["x"]])
@pytest.mark.parametrize("mode", ["iam", "jwt"])
def test_check_service_environment_turns_garbage_into_findings(garbage, mode):
    good = rs.jwt_service_variables() if mode == "jwt" else {}

    assert_findings(preflight.check_service_environment(garbage, {}, rs.target(mode)))
    assert_findings(preflight.check_service_environment(good, garbage, rs.target(mode)))


@pytest.mark.parametrize("garbage", [None, 7, "text", [], ["x"], {}, {"UserPoolId": []}])
def test_identity_findings_turns_garbage_into_findings(garbage):
    pool = settings.cognito_settings(rs.COGNITO_ENV)

    assert_findings(preflight.identity_findings(garbage, pool))
    assert_findings(preflight.identity_findings(identity_outputs(), None))


@pytest.mark.parametrize("garbage", ["null", "7", '"text"', "[]", "[1]", "{}", '{"ok": true}',
                                     '{"ok": true, "at": []}', '{"ok": true, "checks": 5}'])
def test_the_proof_check_turns_garbage_into_findings(tmp_path, garbage):
    path = tmp_path / "backend-login-proof.json"
    path.write_text(garbage)

    assert_findings(proof_findings(path))


def test_the_proof_check_survives_a_path_that_is_a_directory(tmp_path):
    assert_findings(proof_findings(tmp_path))


def test_the_hop_checks_survive_a_state_of_garbage():
    state = preflight.HopState(gateway=None, runtimes={"MeridianConcierge": None}, policies=None)

    assert_findings(preflight.hop_findings(state, rs.target()))


# ------------------------------------------------------- reading the policy pages


def test_a_policy_summary_without_a_name_is_skipped_not_fatal():
    pages = [{"policies": [{"status": "ACTIVE", "enforcementMode": "ACTIVE"}, "x", None,
                           *rs.policies(rs.BASE_POLICIES)]}]

    state = preflight.read_state(fake_control(pages=pages), rs.GATEWAY_ID, rs.RUNTIME_IDS)

    assert state.policies == rs.policy_modes(rs.BASE_POLICIES)


def test_a_log_only_rule_is_carried_with_its_mode_and_reported():
    pages = [{"policies": rs.policies(rs.BASE_POLICIES[:2]) + rs.policies(
        [rs.BASE_POLICIES[2], rs.BINDING_POLICY], "LOG_ONLY")}]

    state = preflight.read_state(fake_control(pages=pages), rs.GATEWAY_ID, rs.RUNTIME_IDS)
    found = preflight.hop_findings(state, rs.target())

    assert state.policies[rs.BINDING_POLICY] == "LOG_ONLY"
    assert len(found) == 2 and all("LOG_ONLY" in line for line in found)


def test_a_page_token_that_repeats_stops_the_read_instead_of_looping():
    control = Mock()
    control.get_gateway.return_value = rs.gateway("jwt")
    control.get_agent_runtime.side_effect = lambda agentRuntimeId: rs.runtime("MeridianWorkflow")
    control.list_policies.return_value = {"policies": [], "nextToken": "same"}

    with pytest.raises(settings.ReleaseConfigError, match="nextToken"):
        preflight.read_state(control, rs.GATEWAY_ID, rs.RUNTIME_IDS)

    assert control.list_policies.call_count == 2


# --------------------------------------------------- where the hops say they are

HOP_ENV = {
    "AGENTCORE_GATEWAY_URL": f"https://{rs.GATEWAY_ID}.gateway.bedrock-agentcore."
                             f"{rs.REGION}.amazonaws.com/mcp",
    "AGENTCORE_RUNTIME_ARN": f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:runtime/c",
    "AGENTCORE_WORKFLOW_RUNTIME_ARN": f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:"
                                      "runtime/w",
}


def test_hop_settings_in_the_targets_account_and_region_have_no_findings():
    assert preflight.check_hop_locations(HOP_ENV, rs.target()) == []


@pytest.mark.parametrize(("key", "value", "word"), [
    ("AGENTCORE_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:999999999999:runtime/c",
     "account"),
    ("AGENTCORE_WORKFLOW_RUNTIME_ARN", "arn:aws:bedrock-agentcore:eu-west-1:123456789012:runtime/w",
     "Region"),
    ("AGENTCORE_RUNTIME_ARN", "not-an-arn", "ARN"),
    ("AGENTCORE_GATEWAY_URL", "https://g.gateway.bedrock-agentcore.eu-west-1.amazonaws.com/mcp",
     "Region"),
    ("AGENTCORE_GATEWAY_URL", "https://example.com/mcp", "Gateway URL"),
])
def test_a_hop_setting_for_another_account_or_region_is_a_finding(key, value, word):
    found = preflight.check_hop_locations({**HOP_ENV, key: value}, rs.target())

    assert len(found) == 1 and key in found[0] and word in found[0], found
    assert "999999999999" not in found[0]


def test_unset_hop_settings_are_left_to_hop_ids():
    assert preflight.check_hop_locations({}, rs.target()) == []


def test_the_service_arn_must_name_meridian_web_in_the_targets_account_and_region():
    assert preflight.check_service_arn(rs.SERVICE_ARN, rs.target()) == []
    for wrong in (rs.SERVICE_ARN.replace(rs.ACCOUNT, "999999999999"),
                  rs.SERVICE_ARN.replace(rs.REGION, "eu-west-1"),
                  "arn:aws:apprunner:us-east-1:123456789012:service/other/abc", "x", None):
        found = preflight.check_service_arn(wrong, rs.target())
        assert len(found) == 1 and "--service-arn" in found[0], wrong
        assert "999999999999" not in found[0]


# ------------------------------------------------------- the App Runner description


def test_an_image_based_service_yields_its_plain_and_secret_environment():
    service = rs.app_runner_service({"A": "b"}, {"C": "arn:d"})

    assert preflight.image_environment(service) == ({"A": "b"}, {"C": "arn:d"})


@pytest.mark.parametrize("service", [
    {"SourceConfiguration": {"CodeRepository": {"RepositoryUrl": "https://x"}}},
    {"SourceConfiguration": {}}, {}, None, "x", [],
    {"SourceConfiguration": {"ImageRepository": "x"}},
])
def test_a_service_that_is_not_image_based_has_no_image_environment(service):
    assert preflight.image_environment(service) is None


def test_an_image_service_with_no_environment_has_empty_ones():
    service = {"SourceConfiguration": {"ImageRepository": {"ImageIdentifier": "x"}}}

    assert preflight.image_environment(service) == ({}, {})


# ----------------------------------------------- the replaced Gateway, found by name


@pytest.mark.parametrize("mode", ["iam", "jwt"])
def test_each_modes_gateway_must_carry_that_modes_name(mode):
    wrong = rs.mutated(rs.gateway(mode), "name", settings.gateway_physical_name(
        "iam" if mode == "jwt" else "jwt"))

    found = gateway_findings(wrong, mode)

    assert len(found) == 1 and found[0].startswith("Gateway: name is ")
    assert settings.gateway_physical_name(mode) in found[0]
    assert "sync_agentcore_env.py --write" in found[0]


def test_a_gateway_with_no_name_is_a_name_finding():
    found = gateway_findings(rs.mutated(rs.gateway("jwt"), "name", None))

    assert len(found) == 1 and "name" in found[0]


def test_the_target_knows_its_modes_gateway_name():
    assert rs.target("jwt").gateway_name == "meridianv2-meridian-aurora-jwt"
    assert rs.target("iam").gateway_name == "meridianv2-meridian-aurora"


def test_a_gateway_without_its_interceptor_is_fine_while_the_release_is_still_building():
    bare = rs.mutated(rs.gateway("jwt"), "interceptorConfigurations", None)

    assert preflight.check_gateway(bare, rs.target("jwt"), expect_interceptor=False) == []
    assert any("no request interceptor" in line for line in gateway_findings(bare))


def test_an_attached_interceptor_is_still_checked_when_it_is_not_required_yet():
    wrong = rs.mutated(rs.gateway("jwt"), "interceptorConfigurations", [{
        "interceptor": {"lambda": {"arn": "arn:aws:lambda:us-east-1:123456789012:function:x"}},
        "interceptionPoints": ["REQUEST"], "inputConfiguration": {"passRequestHeaders": True}}])

    found = preflight.check_gateway(wrong, rs.target("jwt"), expect_interceptor=False)

    assert len(found) == 1 and "traveler-pin" in found[0]


def gateway_list(*named, token=None):
    page = {"items": [{"gatewayId": gid, "name": name} for name, gid in named]}
    if token:
        page["nextToken"] = token
    return page


def test_a_gateway_is_found_by_its_exact_name_across_pages():
    control = Mock()
    control.list_gateways.side_effect = [
        gateway_list(("meridianv2-meridian-aurora-jwt-old", "gw-a"), token="next"),
        gateway_list(("meridianv2-meridian-aurora-jwt", "gw-b"))]

    assert preflight.find_gateway_id(control, "meridianv2-meridian-aurora-jwt") == "gw-b"
    assert control.list_gateways.call_count == 2


def test_a_gateway_that_does_not_exist_is_none_and_a_prefix_is_not_a_match():
    control = Mock()
    control.list_gateways.return_value = gateway_list(("meridianv2-meridian-aurora-jwt", "gw-b"))

    assert preflight.find_gateway_id(control, "meridianv2-meridian-aurora") is None


def test_two_gateways_with_one_name_are_refused_not_guessed():
    control = Mock()
    control.list_gateways.return_value = gateway_list(("same", "gw-a"), ("same", "gw-b"))

    with pytest.raises(settings.ReleaseConfigError, match="more than one"):
        preflight.find_gateway_id(control, "same")


def test_a_repeating_gateway_page_token_stops_the_search():
    control = Mock()
    control.list_gateways.return_value = gateway_list(("x", "gw-a"), token="same")

    with pytest.raises(settings.ReleaseConfigError, match="nextToken"):
        preflight.find_gateway_id(control, "wanted")


def test_a_gateway_that_the_settings_name_but_no_longer_exists_says_how_to_refresh():
    from botocore.exceptions import ClientError

    control = fake_control()
    control.get_gateway.side_effect = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "gone"}}, "GetGateway")

    with pytest.raises(settings.ReleaseConfigError, match="sync_agentcore_env.py --write"):
        preflight.read_state(control, rs.GATEWAY_ID, rs.RUNTIME_IDS)


def test_any_other_gateway_read_error_is_not_swallowed():
    from botocore.exceptions import ClientError

    control = fake_control()
    control.get_gateway.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "GetGateway")

    with pytest.raises(ClientError):
        preflight.read_state(control, rs.GATEWAY_ID, rs.RUNTIME_IDS)


# ---------------------------------------- the Runtimes point at the live Gateway


def bound_state(mode="jwt", **changes):
    gateway = rs.gateway(mode)
    runtimes = {name: rs.runtime(name, mode) for name in rs.RUNTIME_IDS}
    for path, value in changes.items():
        name, _, key = path.partition("__")
        runtimes[name] = rs.mutated(runtimes[name], f"environmentVariables.{key}", value)
    return preflight.HopState(gateway=gateway, runtimes=runtimes,
                              policies=rs.policy_modes(rs.BASE_POLICIES + [rs.BINDING_POLICY]))


@pytest.mark.parametrize("mode", ["iam", "jwt"])
def test_runtimes_bound_to_the_live_gateway_have_no_binding_findings(mode):
    assert preflight.binding_findings(bound_state(mode), rs.target(mode)) == []


def test_a_runtime_that_still_carries_the_replaced_gateways_url_is_a_finding():
    state = bound_state(MeridianConcierge__AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL="https://old")

    found = preflight.binding_findings(state, rs.target("jwt"))

    assert len(found) == 1 and found[0].startswith("Runtime MeridianConcierge: ")
    assert "AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL" in found[0] and "replaced" in found[0]


def test_a_runtime_whose_gateway_url_is_not_the_live_gateways_is_a_finding():
    state = bound_state(MeridianWorkflow__AGENTCORE_GATEWAY_MERIDIAN_AURORA_JWT_URL="https://x")

    found = preflight.binding_findings(state, rs.target("jwt"))

    assert len(found) == 1 and "MeridianWorkflow" in found[0] and "URL" in found[0]


@pytest.mark.parametrize("variable", ["MERIDIAN_GATEWAY_ID", "MERIDIAN_POLICY_ENGINE_ID"])
@pytest.mark.parametrize("value", ["other-id", None])
def test_a_runtime_with_the_wrong_or_missing_gateway_or_engine_id_is_a_finding(variable, value):
    state = bound_state(**{f"MeridianConcierge__{variable}": value})

    found = preflight.binding_findings(state, rs.target("jwt"))

    assert len(found) == 1 and variable in found[0]


def test_a_gateway_with_no_engine_asks_nothing_of_the_runtimes_engine_variable():
    state = bound_state()
    state.gateway = rs.mutated(state.gateway, "policyEngineConfiguration", None)
    for name in state.runtimes:
        state.runtimes[name] = rs.mutated(
            state.runtimes[name], "environmentVariables.MERIDIAN_POLICY_ENGINE_ID", None)

    found = preflight.binding_findings(state, rs.target("jwt"))

    assert found == []


def test_the_binding_check_survives_a_state_of_garbage():
    state = preflight.HopState(gateway=None, runtimes={"MeridianConcierge": None}, policies=None)

    assert preflight.binding_findings(state, rs.target()) == []


def test_the_hop_findings_include_the_binding_findings():
    state = bound_state(MeridianConcierge__MERIDIAN_GATEWAY_ID="other-id")

    found = preflight.hop_findings(state, rs.target("jwt"))

    assert len(found) == 1 and "MERIDIAN_GATEWAY_ID" in found[0]


# ------------------------------------------- a Gateway built before its engine exists


def test_a_gateway_without_its_engine_is_fine_before_the_governance_stage():
    bare = rs.mutated(rs.gateway("jwt"), "policyEngineConfiguration", None)

    assert preflight.check_gateway(
        bare, rs.target("jwt"), expect_interceptor=False, expect_engine=False) == []
    assert any("policy engine" in line for line in gateway_findings(bare))


def test_an_engine_that_is_attached_is_still_checked_when_it_is_not_required_yet():
    loose = rs.mutated(rs.gateway("jwt"), "policyEngineConfiguration.mode", "LOG_ONLY")

    found = preflight.check_gateway(
        loose, rs.target("jwt"), expect_interceptor=False, expect_engine=False)

    assert len(found) == 1 and "ENFORCE" in found[0]


def test_the_authorizer_check_is_public_and_judges_a_block_against_the_pool():
    good = {"discoveryUrl": rs.jwt_authorizer()["customJWTAuthorizer"]["discoveryUrl"],
            "allowedClients": [rs.CLIENT]}

    assert preflight.check_authorizer("X", good, rs.target("jwt")) == []
    assert preflight.check_authorizer("X", {**good, "allowedClients": []}, rs.target("jwt")) == [
        "X: allowedClients is not exactly the web app client"]
