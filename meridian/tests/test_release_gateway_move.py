"""The Gateway moves between iam and jwt in one complete update, with the grants and a read-back."""

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
    ROLE_NAME, Control, clients, current, fast, iam_client, lambda_client,
)

CONTROL = "bedrock-agentcore-control"


def target(mode="jwt", design=settings.BOTH):
    return rs.target(mode, design)


def snapshot(mode="iam", design=settings.BOTH, **extra):
    return gw.Snapshot(rs.GATEWAY_ID, current(mode, design, **extra))


def move(control, iam=None, lam=None, mode="jwt", design=settings.BOTH, before=None, **keywords):
    wanted = target(mode, design)
    before = before or gw.Snapshot(rs.GATEWAY_ID, control.before)
    return gw.apply(clients(control, iam, lam), wanted, before, **fast(), **keywords)


# ------------------------------------------------------------- the request


def test_the_jwt_update_resends_everything_it_replaces_and_changes_two_things():
    described = current("iam")

    arguments = gw.update_arguments(described, target("jwt"))

    expected = {key: described[key] for key in gw.KEPT_FIELDS if key in described}
    for key, value in expected.items():
        assert arguments[key] == value
    assert arguments["gatewayIdentifier"] == rs.GATEWAY_ID
    assert set(arguments) == {"gatewayIdentifier", "authorizerType", "authorizerConfiguration",
                              "interceptorConfigurations", *expected}
    assert arguments["authorizerType"] == "CUSTOM_JWT"
    assert arguments["authorizerConfiguration"] == rs.jwt_authorizer()
    assert arguments["interceptorConfigurations"] == [{
        "interceptor": {"lambda": {"arn": rs.INTERCEPTOR_ARN}},
        "interceptionPoints": ["REQUEST"], "inputConfiguration": {"passRequestHeaders": True}}]


def test_the_update_preserves_every_kept_field_the_service_model_allows():
    from botocore.session import get_session

    members = get_session().get_service_model(gw.CONTROL_SERVICE).operation_model(
        "UpdateGateway").input_shape.members
    resent = set(gw.KEPT_FIELDS) | set(gw.CHANGED_FIELDS) | {"gatewayIdentifier"}

    assert set(members) == resent


def test_the_update_is_a_valid_call_for_the_installed_service_model():
    arguments = gw.update_arguments(current("iam"), target("jwt"))

    assert violations(CONTROL, [("update_gateway", arguments)]) == []
    assert gw.request_problems(arguments) == []
    assert violations(CONTROL, [("update_gateway", gw.update_arguments(
        current("jwt"), target("iam")))]) == []


def test_a_request_the_service_model_rejects_is_reported():
    arguments = gw.update_arguments(current("iam"), target("jwt"))
    arguments["roleArn"] = 7

    assert gw.request_problems(arguments)


def test_the_update_does_not_alias_the_description_it_was_built_from():
    described = current("iam")
    arguments = gw.update_arguments(described, target("jwt"))
    arguments["protocolConfiguration"]["mcp"]["searchType"] = "changed"

    assert described["protocolConfiguration"]["mcp"]["searchType"] == "SEMANTIC"


def test_the_cedar_only_design_attaches_no_interceptor():
    arguments = gw.update_arguments(current("iam"), target("jwt", settings.CEDAR))

    assert arguments["authorizerType"] == "CUSTOM_JWT"
    assert "interceptorConfigurations" not in arguments


def test_the_iam_update_goes_back_to_the_iam_authorizer_and_omits_the_interceptor():
    arguments = gw.update_arguments(current("jwt"), target("iam"))

    assert arguments["authorizerType"] == "AWS_IAM"
    assert "authorizerConfiguration" not in arguments
    assert "interceptorConfigurations" not in arguments
    assert arguments["roleArn"] == rs.GATEWAY_ROLE


def test_optional_fields_the_gateway_does_not_have_are_not_sent():
    described = current("iam")
    del described["description"]
    del described["exceptionLevel"]

    arguments = gw.update_arguments(described, target("jwt"))

    assert "description" not in arguments and "exceptionLevel" not in arguments


def test_the_summary_shows_the_authorizer_clients_interceptor_and_headers():
    summary = gw.summarize(current("jwt"))

    assert summary == {"authorizerType": "CUSTOM_JWT", "allowedClients": [rs.CLIENT],
                       "interceptors": [{"arn": rs.INTERCEPTOR_ARN,
                                         "interceptionPoints": ["REQUEST"],
                                         "passRequestHeaders": True}]}
    assert gw.summarize(current("iam"))["interceptors"] == []


# ----------------------------------------------------------- the snapshot


def test_a_gateway_that_reads_in_full_is_snapshotted_as_a_copy():
    control = Control(current("iam"))

    taken = gw.read_snapshot(control, rs.GATEWAY_ID, target("jwt"))
    taken.described["name"] = "changed"

    assert control.before["name"] == "meridian-aurora"


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


def test_an_update_that_would_not_reach_the_wanted_state_is_a_plan_problem():
    engine = {"arn": rs.ENGINE_ARN, "mode": "LOG_ONLY"}
    engine_off = current("iam", policyEngineConfiguration=engine)

    problems = gw.plan_problems(gw.Snapshot(rs.GATEWAY_ID, engine_off), target("jwt"))

    assert problems == ["the update would leave: Gateway: the policy engine is not attached in "
                        "ENFORCE mode"]
    assert gw.plan_problems(snapshot("iam"), target("jwt")) == []


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
    def __init__(self, tmp_path, with_proof=True, **env):
        from tests.test_release_identity_cli import NOW, env as base_env
        self.env = base_env(**env)
        self.proof_path = tmp_path / "proof.json"
        if with_proof:
            self.proof_path.write_text(json.dumps(rs.receipt(NOW)))
        self.now = lambda: NOW
        self.head_sha = lambda: rs.SHA


def ready_clients(control, **keywords):
    from unittest.mock import Mock
    cfn = Mock()
    cfn.describe_stacks.return_value = {"Stacks": [{"Outputs": rs.identity_outputs()}]}
    return clients(control, cfn=cfn, **keywords)


def test_a_release_that_meets_every_precondition_has_no_blockers(tmp_path):
    control = Control(current("iam"))

    found = gw.preconditions(ready_clients(control), Deps(tmp_path), target(), snapshot())

    assert found == []


def test_each_missing_precondition_is_named(tmp_path):
    control = Control(current("iam"))
    both = ready_clients(control, lam=lambda_client(tags={"project": "other"}))
    both.cfn.describe_stacks.return_value = {"Stacks": [{"Outputs": []}]}

    found = gw.preconditions(both, Deps(tmp_path, with_proof=False), target(), snapshot())

    text = "\n".join(found)
    assert "Identity stack: has no output" in text
    assert "Backend login proof: none recorded" in text
    assert "exists without the release tags" in text


def raise_not_found(**kwargs):
    raise client_error("ResourceNotFoundException")


def test_an_interceptor_that_is_not_deployed_blocks_the_move(tmp_path):
    lam = lambda_client()
    lam.get_function = raise_not_found

    found = gw.preconditions(ready_clients(Control(current()), lam=lam), Deps(tmp_path),
                             target(), snapshot())

    assert any("not deployed" in line for line in found)


def test_the_iam_move_needs_no_pool_proof_or_function(tmp_path):
    control = Control(current("jwt"))

    found = gw.preconditions(clients(control), Deps(tmp_path, with_proof=False),
                             target("iam"), snapshot("jwt"))

    assert found == []


def test_the_cedar_only_design_does_not_ask_for_the_function(tmp_path):
    lam = lambda_client(tags={})

    found = gw.preconditions(ready_clients(Control(current()), lam=lam), Deps(tmp_path),
                             target("jwt", settings.CEDAR), snapshot("iam", settings.CEDAR))

    assert found == []
    assert "get_function" not in lam.names()


# --------------------------------------------------------------- applying


def test_moving_to_jwt_grants_first_then_updates_then_reads_back():
    order: list[str] = []
    control = Control(current("iam"), current("jwt", status="UPDATING"), current("jwt"),
                      events=order)
    iam = iam_client(installed=False, events=order)
    lam = lambda_client()

    result = move(control, iam, lam)

    assert order == ["grant", "update"]
    assert iam.args("put_role_policy")[0]["RoleName"] == ROLE_NAME
    assert iam.args("put_role_policy")[0]["PolicyName"] == gw.INVOKE_POLICY_NAME
    assert json.loads(iam.args("put_role_policy")[0]["PolicyDocument"]) == gw.invoke_policy(
        rs.INTERCEPTOR_ARN)
    assert lam.calls == []
    assert "Gateway: updated to jwt" in result.notes
    assert result.findings == [] and result.after["authorizerType"] == "CUSTOM_JWT"
    assert result.before.described == current("iam")
    assert violations("iam", iam.calls) == []
    sent = control.args("update_gateway")[0]
    assert violations(CONTROL, [("update_gateway", sent)]) == []


def test_the_update_sent_equals_the_planned_request_so_unchanged_fields_are_exact():
    control = Control(current("iam"), current("jwt"))

    move(control, iam_client(), lambda_client())

    assert control.args("update_gateway") == [gw.update_arguments(current("iam"), target("jwt"))]


def test_a_gateway_that_already_matches_is_not_updated_again_and_keeps_its_grants():
    control = Control(current("jwt"))

    result = move(control, iam_client(), lambda_client())

    assert "update_gateway" not in control.names()
    assert "Gateway: unchanged, already jwt" in result.notes


def test_the_apply_refuses_when_the_gateway_changed_after_it_was_read():
    control = Control(current("iam", description="someone edited this"))

    with pytest.raises(gw.GatewayError, match="changed after it was read"):
        move(control, before=snapshot("iam"))

    assert "update_gateway" not in control.names()


def test_a_move_whose_grants_are_missing_is_refused_before_the_update():
    control = Control(current("iam"), current("jwt"))

    with pytest.raises(gw.GatewayError, match="cannot invoke the interceptor yet"):
        move(control, iam_client(installed=False), lambda_client(), only="move")

    assert "update_gateway" not in control.names()


def test_only_grant_writes_the_grants_and_does_not_touch_the_gateway():
    control = Control(current("iam"))
    iam, lam = iam_client(installed=False), lambda_client()

    result = move(control, iam, lam, only="grant")

    assert "update_gateway" not in control.names() and result.after is None
    assert iam.names().count("put_role_policy") == 1 and lam.calls == []


def test_only_move_does_not_write_the_grants():
    control = Control(current("iam"), current("jwt"))
    iam, lam = iam_client(), lambda_client()

    move(control, iam, lam, only="move")

    assert "put_role_policy" not in iam.names() and lam.calls == []
    assert "update_gateway" in control.names()


def test_waiting_for_the_update_stops_on_a_failure_and_says_why():
    control = Control(current("iam"), current("iam", status="UPDATE_UNSUCCESSFUL",
                                              statusReasons=[f"role {rs.ACCOUNT} lacks access"]))

    with pytest.raises(gw.GatewayError) as failed:
        move(control, iam_client(), lambda_client())

    assert "UPDATE_UNSUCCESSFUL" in str(failed.value) and rs.ACCOUNT not in str(failed.value)


def test_waiting_for_the_update_gives_up_after_the_timeout():
    control = Control(current("iam"), current("iam", status="UPDATING"))

    with pytest.raises(gw.GatewayError, match="still UPDATING"):
        move(control, iam_client(), lambda_client())


def test_an_update_the_read_back_does_not_see_is_reported_not_raised():
    control = Control(current("iam"), current("iam"))

    result = move(control, iam_client(), lambda_client())

    assert any("expected CUSTOM_JWT" in line for line in result.findings)
    assert "Gateway: update sent for jwt" in result.notes


def test_a_read_back_that_is_ready_before_it_is_updating_is_waited_out():
    control = Control(current("iam"), current("iam"), current("iam", status="UPDATING"),
                      current("jwt"))

    result = move(control, iam_client(), lambda_client())

    assert result.findings == []


def test_the_cedar_only_design_grants_nothing_and_revokes_leftovers():
    control = Control(current("iam"), current("jwt", settings.CEDAR))
    iam, lam = iam_client(), lambda_client()

    result = move(control, iam, lam, design=settings.CEDAR)

    assert "put_role_policy" not in iam.names() and lam.calls == []
    assert result.findings == []
    assert "delete_role_policy" in iam.names() and lam.calls == []


def test_moving_back_to_iam_updates_then_removes_both_grants():
    order: list[str] = []
    control = Control(current("jwt"), current("iam"), events=order)
    iam, lam = iam_client(events=order), lambda_client()

    result = move(control, iam, lam, mode="iam")

    assert order == ["update", "revoke"]
    assert iam.args("delete_role_policy") == [
        {"RoleName": ROLE_NAME, "PolicyName": gw.INVOKE_POLICY_NAME}]
    assert lam.calls == []
    assert any("removed" in note for note in result.notes) and result.findings == []
    assert violations("iam", iam.calls) == []


def test_a_grant_that_is_already_gone_is_not_an_error_on_rollback():
    control = Control(current("jwt"), current("iam"))

    result = move(control, iam_client(installed=False), lambda_client(), mode="iam")

    assert result.findings == []


def test_a_rollback_whose_interceptor_stays_attached_keeps_the_grants():
    control = Control(current("jwt"), current("iam", interceptorConfigurations=[{"x": 1}]))
    iam, lam = iam_client(), lambda_client()

    result = move(control, iam, lam, mode="iam")

    assert any("interceptor" in line for line in result.findings)
    assert iam.args("delete_role_policy") == [] and lam.calls == []


def test_the_record_is_private_and_holds_masked_summaries_only(tmp_path):
    control = Control(current("iam"), current("jwt"))
    result = move(control, iam_client(), lambda_client())
    at = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

    path = gw.record(tmp_path / "out", result, target(), at)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    text = path.read_text()
    assert rs.ACCOUNT not in text and "<acct>" in text
    assert json.loads(text)["after"]["authorizerType"] == "CUSTOM_JWT"
