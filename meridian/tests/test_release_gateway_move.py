"""The Gateway command attaches and detaches the interceptor; it never moves the authorizer.

CloudFormation and the UpdateGateway API both refuse to change an existing Gateway's authorizer
type, so a jwt Gateway is a new Gateway that the deploy creates. This command finds it by name,
writes the invoke grant, attaches the interceptor in one complete update and reads it back; the
revoke direction detaches it and removes the grant.
"""

from __future__ import annotations

import json
import stat
from datetime import datetime, timezone

import pytest

from scripts.identity_release import gateway_release as gw
from scripts.identity_release import settings
from tests import release_support as rs
from tests.aws_recorders import client_error, violations
from tests.gateway_release_support import (
    ROLE_NAME, Control, bare, clients, current, fast, iam_client, lambda_client, listing,
)

CONTROL = "bedrock-agentcore-control"
INTERCEPTORS = [{
    "interceptor": {"lambda": {"arn": rs.INTERCEPTOR_ARN}},
    "interceptionPoints": ["REQUEST"], "inputConfiguration": {"passRequestHeaders": True}}]


def target(mode="jwt", design=settings.BOTH):
    return rs.target(mode, design)


def snapshot(mode="jwt", design=settings.BOTH, **extra):
    return gw.Snapshot(rs.GATEWAY_ID, bare(mode, design, **extra))


def act(control, action, iam=None, lam=None, mode="jwt", design=settings.BOTH, before=None):
    wanted = target(mode, design)
    before = before or gw.Snapshot(rs.GATEWAY_ID, control.before)
    return gw.apply(clients(control, iam, lam), wanted, before, action, **fast())


# ------------------------------------------------------------ what each flag means


@pytest.mark.parametrize(("mode", "only", "design", "action"), [
    ("jwt", None, settings.BOTH, gw.FULL),
    ("jwt", None, settings.INTERCEPTOR, gw.FULL),
    ("jwt", "grant", settings.BOTH, gw.GRANT),
    ("jwt", "attach", settings.BOTH, gw.ATTACH),
    ("jwt", "revoke", settings.BOTH, gw.REVOKE),
    ("jwt", None, settings.CEDAR, gw.NONE),
    ("jwt", "revoke", settings.CEDAR, gw.REVOKE),
    ("iam", None, settings.BOTH, gw.REVOKE),
    ("iam", "revoke", settings.BOTH, gw.REVOKE),
])
def test_the_flags_and_the_mode_choose_one_action(mode, only, design, action):
    assert gw.resolve_action(mode, only, design) == action


@pytest.mark.parametrize(("mode", "only", "design"), [
    ("iam", "grant", settings.BOTH), ("iam", "attach", settings.BOTH),
    ("jwt", "grant", settings.CEDAR), ("jwt", "attach", settings.CEDAR),
])
def test_grant_and_attach_need_a_jwt_release_with_an_interceptor(mode, only, design):
    with pytest.raises(settings.ReleaseConfigError, match="--only"):
        gw.resolve_action(mode, only, design)


# ------------------------------------------------------------- the request


def test_the_attach_update_resends_everything_and_adds_only_the_interceptor():
    described = bare("jwt")

    arguments = gw.update_arguments(described, rs.INTERCEPTOR_ARN)

    expected = {key: described[key] for key in gw.KEPT_FIELDS if key in described}
    for key, value in expected.items():
        assert arguments[key] == value
    assert arguments["authorizerType"] == "CUSTOM_JWT"
    assert arguments["authorizerConfiguration"] == rs.jwt_authorizer()
    assert arguments["gatewayIdentifier"] == rs.GATEWAY_ID
    assert set(arguments) == {"gatewayIdentifier", "interceptorConfigurations", *expected}
    assert arguments["interceptorConfigurations"] == INTERCEPTORS


def test_the_detach_update_resends_the_authorizer_and_omits_the_interceptor():
    arguments = gw.update_arguments(current("jwt"), None)

    assert arguments["authorizerType"] == "CUSTOM_JWT"
    assert arguments["authorizerConfiguration"] == rs.jwt_authorizer()
    assert "interceptorConfigurations" not in arguments


def test_an_iam_gateway_is_resent_as_iam_without_an_authorizer_block():
    arguments = gw.update_arguments(current("iam"), None)

    assert arguments["authorizerType"] == "AWS_IAM"
    assert "authorizerConfiguration" not in arguments
    assert arguments["roleArn"] == rs.GATEWAY_ROLE


def test_the_update_preserves_every_field_the_service_model_allows():
    from botocore.session import get_session

    members = get_session().get_service_model(gw.CONTROL_SERVICE).operation_model(
        "UpdateGateway").input_shape.members
    resent = set(gw.KEPT_FIELDS) | set(gw.CHANGED_FIELDS) | {"gatewayIdentifier"}

    assert set(members) == resent


def test_the_updates_are_valid_calls_for_the_installed_service_model():
    attach = gw.update_arguments(bare("jwt"), rs.INTERCEPTOR_ARN)

    assert violations(CONTROL, [("update_gateway", attach)]) == []
    assert gw.request_problems(attach) == []
    assert violations(CONTROL, [("update_gateway", gw.update_arguments(
        current("iam"), None))]) == []


def test_a_request_the_service_model_rejects_is_reported():
    arguments = gw.update_arguments(bare("jwt"), rs.INTERCEPTOR_ARN)
    arguments["roleArn"] = 7

    assert gw.request_problems(arguments)


def test_the_update_does_not_alias_the_description_it_was_built_from():
    described = bare("jwt")
    arguments = gw.update_arguments(described, rs.INTERCEPTOR_ARN)
    arguments["protocolConfiguration"]["mcp"]["searchType"] = "changed"
    arguments["authorizerConfiguration"]["customJWTAuthorizer"]["allowedClients"].append("x")

    assert described["protocolConfiguration"]["mcp"]["searchType"] == "SEMANTIC"
    assert described["authorizerConfiguration"]["customJWTAuthorizer"]["allowedClients"] == [
        rs.CLIENT]


def test_optional_fields_the_gateway_does_not_have_are_not_sent():
    described = bare("jwt")
    del described["description"]
    del described["exceptionLevel"]

    arguments = gw.update_arguments(described, rs.INTERCEPTOR_ARN)

    assert "description" not in arguments and "exceptionLevel" not in arguments


def test_the_summary_shows_the_authorizer_clients_interceptor_and_headers():
    summary = gw.summarize(current("jwt"))

    assert summary == {"authorizerType": "CUSTOM_JWT", "allowedClients": [rs.CLIENT],
                       "interceptors": [{"arn": rs.INTERCEPTOR_ARN,
                                         "interceptionPoints": ["REQUEST"],
                                         "passRequestHeaders": True}]}
    assert gw.summarize(current("iam"))["interceptors"] == []


# ----------------------------------------------------------- finding the Gateway


def test_the_gateway_is_found_by_its_modes_name_not_by_an_id_in_the_settings():
    control = Control(bare("jwt"))

    found = gw.locate(control, target("jwt"))

    assert found.gateway_id == rs.GATEWAY_ID
    assert control.args("get_gateway") == [{"gatewayIdentifier": rs.GATEWAY_ID}]


def test_a_missing_gateway_says_which_name_and_what_builds_it():
    control = Control(bare("jwt"))
    control.list_gateways = lambda **kwargs: listing(("someone-else", "gw-x"))

    with pytest.raises(gw.GatewayError) as refused:
        gw.locate(control, target("jwt"))

    text = str(refused.value)
    assert "meridianv2-meridian-aurora-jwt" in text and "release_identity.py deploy" in text


# ----------------------------------------------------------- the snapshot


def test_a_gateway_that_reads_in_full_is_snapshotted_as_a_copy():
    control = Control(current("iam"))

    taken = gw.read_snapshot(control, rs.GATEWAY_ID, target("jwt"))
    taken.described["name"] = "changed"

    assert control.before["name"] == "meridianv2-meridian-aurora"


@pytest.mark.parametrize("described,fragment", [
    ("not a mapping", "unreadable"),
    ({k: v for k, v in current().items() if k != "roleArn"}, "lacks roleArn"),
    (current(status="UPDATING"), "status is UPDATING"),
    (current(gatewayId="other"), "another Gateway"),
    (current(gatewayArn="arn:aws:bedrock-agentcore:eu-west-1:123456789012:gateway/x"),
     "account and Region"),
    (current(roleArn="arn:aws:iam::999999999999:role/r"), "role is not in the deployment"),
    (current(someNewSetting={"a": 1}), "could drop them: someNewSetting"),
])
def test_a_gateway_that_cannot_be_read_in_full_is_refused(described, fragment):
    with pytest.raises(gw.GatewayError, match=fragment):
        gw.read_snapshot(Control(described), rs.GATEWAY_ID, target("jwt"))


def test_a_new_field_with_no_value_does_not_block_the_snapshot():
    gw.read_snapshot(Control(current(someNewSetting=None)), rs.GATEWAY_ID, target("jwt"))


def test_the_response_metadata_boto3_adds_does_not_block_the_snapshot():
    metadata = {"RequestId": "r", "HTTPStatusCode": 200, "RetryAttempts": 0}

    gw.read_snapshot(Control(current(ResponseMetadata=metadata)), rs.GATEWAY_ID, target("jwt"))


# ----------------------------------------------------------------- the grants


def test_the_gateway_role_may_invoke_the_interceptor_and_nothing_else():
    policy = gw.invoke_policy(rs.INTERCEPTOR_ARN)

    assert policy["Statement"] == [{
        "Effect": "Allow", "Action": "lambda:InvokeFunction",
        "Resource": [rs.INTERCEPTOR_ARN, rs.INTERCEPTOR_ARN + ":*"]}]
    assert gw.role_name(rs.GATEWAY_ROLE) == ROLE_NAME


def test_no_lambda_resource_policy_is_read_or_written():
    lam = lambda_client()
    iam = iam_client(installed=False)

    gw.grant(clients(Control(current()), iam, lam), rs.GATEWAY_ROLE, target())
    gw.grant_findings(clients(Control(current()), iam, lam), rs.GATEWAY_ROLE, target())
    gw.revoke(clients(Control(current()), iam, lam), rs.GATEWAY_ROLE)

    assert lam.calls == []
    for name in ("PERMISSION_ID", "GATEWAY_PRINCIPAL", "permission_findings",
                 "_grant_permission"):
        assert not hasattr(gw, name)


def test_granting_twice_writes_the_same_role_policy_both_times():
    iam = iam_client(installed=False)
    both = clients(Control(current()), iam, lambda_client())
    gw.grant(both, rs.GATEWAY_ROLE, target())
    first = iam.args("put_role_policy")[0]

    gw.grant(both, rs.GATEWAY_ROLE, target())

    assert iam.args("put_role_policy") == [first, first]
    assert gw.grant_findings(both, rs.GATEWAY_ROLE, target()) == []


def test_the_grant_findings_name_a_missing_or_widened_role_policy():
    both = clients(Control(current()))
    assert gw.grant_findings(both, rs.GATEWAY_ROLE, target()) == []

    missing = clients(Control(current()), iam_client(installed=False), lambda_client())
    found = gw.grant_findings(missing, rs.GATEWAY_ROLE, target())
    assert len(found) == 1 and gw.INVOKE_POLICY_NAME in found[0]

    wide_iam = iam_client()
    wide_iam.document = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "lambda:*", "Resource": "*"}]}
    wide = clients(Control(current()), wide_iam, lambda_client())
    assert all("differs" in line for line in gw.grant_findings(
        wide, rs.GATEWAY_ROLE, target()))


# ------------------------------------------------------------ the preconditions


class Deps:
    def __init__(self, tmp_path, **env):
        from tests.test_release_identity_cli import NOW, env as base_env
        self.env = base_env(**env)
        self.proof_path = tmp_path / "proof.json"
        self.now = lambda: NOW
        self.head_sha = lambda: rs.SHA


def ready_clients(control, **keywords):
    from unittest.mock import Mock
    cfn = Mock()
    cfn.describe_stacks.return_value = {"Stacks": [{"Outputs": rs.identity_outputs()}]}
    return clients(control, cfn=cfn, **keywords)


def found_for(tmp_path, control, action=gw.FULL, mode="jwt", design=settings.BOTH, **keywords):
    return gw.preconditions(ready_clients(control, **keywords), Deps(tmp_path),
                            target(mode, design), snapshot(mode, design), action)


def test_a_built_jwt_gateway_meets_every_precondition(tmp_path):
    assert found_for(tmp_path, Control(bare("jwt"))) == []


def test_a_gateway_that_already_carries_the_interceptor_still_meets_them(tmp_path):
    assert found_for(tmp_path, Control(current("jwt"))) == []


def test_the_backend_proof_is_not_this_commands_business_any_more(tmp_path):
    deps = Deps(tmp_path)

    assert not deps.proof_path.exists()
    assert gw.preconditions(ready_clients(Control(bare("jwt"))), deps, target(),
                            snapshot(), gw.FULL) == []


@pytest.mark.parametrize(("change", "fragment"), [
    ({"authorizerType": "AWS_IAM", "authorizerConfiguration": None}, "expected CUSTOM_JWT"),
    ({"status": "CREATING"}, "not READY"),
    ({"policyEngineConfiguration": None}, "policy engine"),
    ({"policyEngineConfiguration": {"arn": rs.ENGINE_ARN, "mode": "LOG_ONLY"}}, "ENFORCE"),
])
def test_a_gateway_that_is_not_a_finished_jwt_gateway_is_refused(tmp_path, change, fragment):
    described = bare("jwt", **change)
    gateway_snapshot = gw.Snapshot(rs.GATEWAY_ID, described)

    found = gw.preconditions(ready_clients(Control(described)), Deps(tmp_path), target(),
                             gateway_snapshot, gw.FULL)

    assert any(fragment in line for line in found), found


def test_each_missing_precondition_is_named(tmp_path):
    both = ready_clients(Control(bare("jwt")), lam=lambda_client(tags={"project": "other"}))
    both.cfn.describe_stacks.return_value = {"Stacks": [{"Outputs": []}]}

    found = gw.preconditions(both, Deps(tmp_path), target(), snapshot(), gw.FULL)

    text = "\n".join(found)
    assert "Identity stack: has no output" in text
    assert "exists without the release tags" in text


def raise_not_found(**kwargs):
    raise client_error("ResourceNotFoundException")


def test_an_interceptor_that_is_not_deployed_blocks_the_attach(tmp_path):
    lam = lambda_client()
    lam.get_function = raise_not_found

    found = found_for(tmp_path, Control(bare("jwt")), lam=lam)

    assert any("not deployed" in line for line in found)


def test_revoking_needs_no_pool_function_or_stack(tmp_path):
    control = Control(current("jwt"))
    lam = lambda_client(tags={})

    found = gw.preconditions(clients(control, lam=lam), Deps(tmp_path), target("iam"),
                             snapshot("iam"), gw.REVOKE)

    assert found == [] and lam.calls == []


def test_the_cedar_only_design_does_not_ask_for_the_function(tmp_path):
    lam = lambda_client(tags={})

    found = found_for(tmp_path, Control(bare("jwt", settings.CEDAR)), gw.NONE, "jwt",
                      settings.CEDAR, lam=lam)

    assert found == [] and "get_function" not in lam.names()


# --------------------------------------------------------------- applying


def test_attaching_grants_first_then_updates_then_reads_back():
    order: list[str] = []
    control = Control(bare("jwt"), current("jwt", status="UPDATING"), current("jwt"),
                      events=order)
    iam = iam_client(installed=False, events=order)
    lam = lambda_client()

    result = act(control, gw.FULL, iam, lam)

    assert order == ["grant", "update"]
    put = iam.args("put_role_policy")[0]
    assert put["RoleName"] == ROLE_NAME and put["PolicyName"] == gw.INVOKE_POLICY_NAME
    assert json.loads(put["PolicyDocument"]) == gw.invoke_policy(rs.INTERCEPTOR_ARN)
    assert lam.calls == []
    assert "Gateway: interceptor attached" in result.notes
    assert result.findings == [] and result.after["interceptorConfigurations"] == INTERCEPTORS
    assert violations("iam", iam.calls) == []


def test_the_update_sent_equals_the_planned_request_so_unchanged_fields_are_exact():
    control = Control(bare("jwt"), current("jwt"))

    act(control, gw.FULL, iam_client(), lambda_client())

    assert control.args("update_gateway") == [
        gw.update_arguments(bare("jwt"), rs.INTERCEPTOR_ARN)]


def test_a_gateway_that_already_has_the_interceptor_is_not_updated_again():
    control = Control(current("jwt"))

    result = act(control, gw.FULL, iam_client(), lambda_client())

    assert "update_gateway" not in control.names()
    assert "Gateway: interceptor already attached" in result.notes


def test_the_apply_refuses_when_the_gateway_changed_after_it_was_read():
    control = Control(bare("jwt", description="someone edited this"))

    with pytest.raises(gw.GatewayError, match="changed after it was read"):
        act(control, gw.FULL, before=snapshot("jwt"))

    assert "update_gateway" not in control.names()


def test_an_attach_whose_grant_is_missing_is_refused_before_the_update():
    control = Control(bare("jwt"), current("jwt"))

    with pytest.raises(gw.GatewayError, match="cannot invoke the interceptor yet"):
        act(control, gw.ATTACH, iam_client(installed=False), lambda_client())

    assert "update_gateway" not in control.names()


def test_grant_alone_writes_the_grant_and_does_not_touch_the_gateway():
    control = Control(bare("jwt"))
    iam, lam = iam_client(installed=False), lambda_client()

    result = act(control, gw.GRANT, iam, lam)

    assert "update_gateway" not in control.names() and result.after is None
    assert iam.names().count("put_role_policy") == 1 and lam.calls == []


def test_attach_alone_does_not_write_the_grant():
    control = Control(bare("jwt"), current("jwt"))
    iam, lam = iam_client(), lambda_client()

    act(control, gw.ATTACH, iam, lam)

    assert "put_role_policy" not in iam.names() and lam.calls == []
    assert "update_gateway" in control.names()


def test_waiting_for_the_update_stops_on_a_failure_and_says_why():
    control = Control(bare("jwt"), current("jwt", status="UPDATE_UNSUCCESSFUL",
                                           statusReasons=[f"role {rs.ACCOUNT} lacks access"]))

    with pytest.raises(gw.GatewayError) as failed:
        act(control, gw.FULL, iam_client(), lambda_client())

    assert "UPDATE_UNSUCCESSFUL" in str(failed.value) and rs.ACCOUNT not in str(failed.value)


def test_waiting_for_the_update_gives_up_after_the_timeout():
    control = Control(bare("jwt"), current("jwt", status="UPDATING"))

    with pytest.raises(gw.GatewayError, match="still UPDATING"):
        act(control, gw.FULL, iam_client(), lambda_client())


def test_an_update_the_read_back_does_not_see_is_reported_not_raised():
    control = Control(bare("jwt"), bare("jwt"))

    result = act(control, gw.FULL, iam_client(), lambda_client())

    assert any("no request interceptor" in line for line in result.findings)
    assert "Gateway: update sent" in result.notes


def test_a_read_back_that_is_ready_before_it_is_updating_is_waited_out():
    control = Control(bare("jwt"), bare("jwt"), current("jwt", status="UPDATING"),
                      current("jwt"))

    result = act(control, gw.FULL, iam_client(), lambda_client())

    assert result.findings == []


def test_the_cedar_only_design_attaches_nothing_and_says_so():
    control = Control(bare("jwt", settings.CEDAR))
    iam, lam = iam_client(), lambda_client()

    result = act(control, gw.NONE, iam, lam, design=settings.CEDAR)

    assert iam.calls == [] and lam.calls == [] and "update_gateway" not in control.names()
    assert result.findings == [] and any("no interceptor" in note for note in result.notes)


def test_revoking_detaches_first_then_removes_the_grant():
    order: list[str] = []
    control = Control(current("jwt"), bare("jwt"), events=order)
    iam, lam = iam_client(events=order), lambda_client()

    result = act(control, gw.REVOKE, iam, lam)

    assert order == ["update", "revoke"]
    sent = control.args("update_gateway")[0]
    assert "interceptorConfigurations" not in sent
    assert sent["authorizerType"] == "CUSTOM_JWT"
    assert sent["authorizerConfiguration"] == rs.jwt_authorizer()
    assert iam.args("delete_role_policy") == [
        {"RoleName": ROLE_NAME, "PolicyName": gw.INVOKE_POLICY_NAME}]
    assert lam.calls == []
    assert any("removed" in note for note in result.notes) and result.findings == []
    assert violations("iam", iam.calls) == []


def test_revoking_an_iam_gateway_only_removes_the_leftover_grant():
    order: list[str] = []
    control = Control(current("iam"), events=order)
    iam = iam_client(events=order)

    result = act(control, gw.REVOKE, iam, mode="iam")

    assert order == ["revoke"] and "update_gateway" not in control.names()
    assert result.findings == []


def test_a_grant_that_is_already_gone_is_not_an_error_on_revoke():
    control = Control(current("iam"))

    result = act(control, gw.REVOKE, iam_client(installed=False), mode="iam")

    assert result.findings == []


def test_a_revoke_whose_interceptor_stays_attached_keeps_the_grant():
    control = Control(current("jwt"), current("jwt"))
    iam = iam_client()

    result = act(control, gw.REVOKE, iam, lambda_client())

    assert any("interceptor" in line for line in result.findings)
    assert iam.args("delete_role_policy") == []


def test_the_record_is_private_and_holds_masked_summaries_only(tmp_path):
    control = Control(bare("jwt"), current("jwt"))
    result = act(control, gw.FULL, iam_client(), lambda_client())
    at = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

    path = gw.record(tmp_path / "out", result, target(), gw.FULL, at)

    assert path.name == "gateway-interceptor.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    text = path.read_text()
    assert rs.ACCOUNT not in text and "<acct>" in text
    document = json.loads(text)
    assert document["after"]["interceptors"][0]["arn"].endswith(settings.INTERCEPTOR_FUNCTION)
    assert document["action"] == "full"


def test_a_grant_is_present_whatever_it_says_and_absent_when_there_is_none():
    assert gw.grant_present(iam_client(installed=True), rs.GATEWAY_ROLE) is True
    assert gw.grant_present(iam_client(installed=False), rs.GATEWAY_ROLE) is False


def test_any_other_error_reading_the_grant_is_not_swallowed():
    iam = iam_client()

    def denied(**kwargs):
        raise client_error("AccessDenied")
    iam.get_role_policy = denied

    with pytest.raises(Exception, match="AccessDenied"):
        gw.grant_present(iam, rs.GATEWAY_ROLE)
