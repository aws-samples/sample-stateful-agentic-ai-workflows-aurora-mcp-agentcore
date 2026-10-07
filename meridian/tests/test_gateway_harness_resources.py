"""The harness creates what the plan says, switches the interceptor, and deletes all of it."""

from __future__ import annotations

import copy
import json
import zipfile
from io import BytesIO

import botocore.session
import pytest
from botocore import xform_name
from botocore.exceptions import ClientError
from botocore.validate import ParamValidator

from scripts.gateway_harness import resources as res
from scripts.gateway_harness import sources
from scripts.gateway_harness.guards import HarnessRefusal
from tests.gateway_harness_fakes import (
    ACCOUNT,
    ENGINE_ID,
    GATEWAY_ID,
    REGION,
    Fake,
    FakeControl,
    FakeIam,
    FakeLambda,
    client_error,
)

NAME = "meridian-throwaway-ab12cd34"
RUN_ID = "run-0123456789abcdef"
OWN = {res.RUN_TAG: RUN_ID}
OTHER = {res.RUN_TAG: "run-of-someone-else"}
TEMPLATE = json.loads(
    (sources.PRODUCTION_INTERCEPTOR.parents[2] / "agentcore.template.json").read_text()
)["policyEngines"][0]["policies"]
BINDING_TEMPLATE = next(p for p in TEMPLATE if p["name"] == res.BINDING_POLICY)["statement"]


def build(control=None, **overrides):
    clients = res.Clients(
        iam=FakeIam(), lambda_=FakeLambda(), control=control or FakeControl(), logs=Fake())
    config = res.HarnessConfig(NAME, ACCOUNT, REGION, "https://idp/.well-known/openid-configuration",
                               "client-web", BINDING_TEMPLATE, RUN_ID)
    saved = []
    ledger = res.Ledger(save=lambda entries: saved.append(list(entries)))
    harness = res.ThrowawayGateway(config, clients, ledger, sleep=lambda s: None,
                                   clock=iter(range(0, 10_000, 1)).__next__, **overrides)
    return harness, clients, saved


def test_a_live_gateway_refuses_a_name_that_is_not_a_throwaway():
    config = res.HarnessConfig("meridian-aurora", ACCOUNT, REGION, "u", "c", BINDING_TEMPLATE)
    with pytest.raises(HarnessRefusal):
        res.ThrowawayGateway(config, res.Clients(None, None, None, None), res.Ledger())


def test_the_plan_names_every_resource_and_calls_nothing():
    harness, clients, _ = build()
    plan = harness.plan()
    assert any(NAME in line for line in plan) and len(plan) == 9
    assert not clients.iam.calls and not clients.control.calls and not clients.lambda_.calls


def test_the_binding_rule_is_the_templates_with_the_echo_tool_and_the_gateway_substituted():
    config = res.HarnessConfig(NAME, ACCOUNT, REGION, "u", "c", BINDING_TEMPLATE)
    statement = res.harness_binding_statement(BINDING_TEMPLATE, config, GATEWAY_ID)
    assert 'AgentCore::Action::"EchoTarget___echo"' in statement
    assert f"gateway/{GATEWAY_ID}" in statement and "{{" not in statement
    assert "MeridianHolds" not in statement
    assert statement.count("principal.getTag") == BINDING_TEMPLATE.count("principal.getTag")


def test_a_template_rule_that_changed_shape_stops_the_harness():
    config = res.HarnessConfig(NAME, ACCOUNT, REGION, "u", "c", BINDING_TEMPLATE)
    with pytest.raises(HarnessRefusal, match="no longer lists the two production actions"):
        res.harness_binding_statement("forbid(principal, action, resource);", config, GATEWAY_ID)


def test_create_builds_the_gateway_with_the_interceptor_the_authorizer_and_the_engine():
    harness, clients, saved = build()
    live = harness.create()
    control = clients.control
    created = control.args("create_gateway")[0]
    assert created["authorizerType"] == "CUSTOM_JWT"
    assert created["authorizerConfiguration"]["customJWTAuthorizer"]["allowedClients"] == [
        "client-web"]
    assert created["interceptorConfigurations"][0]["inputConfiguration"] == {
        "passRequestHeaders": True}
    assert created["interceptorConfigurations"][0]["interceptionPoints"] == ["REQUEST"]
    target = control.args("create_gateway_target")[0]
    schema = target["targetConfiguration"]["mcp"]["lambda"]["toolSchema"]["inlinePayload"][0]
    assert schema["inputSchema"]["additionalProperties"] is False
    assert schema["inputSchema"]["required"] == ["travelerId"]
    assert target["credentialProviderConfigurations"] == [
        {"credentialProviderType": "GATEWAY_IAM_ROLE"}]
    policies = {p["name"]: p for p in control.args("create_policy")}
    assert policies[res.BINDING_POLICY]["validationMode"] == "FAIL_ON_ANY_FINDINGS"
    assert f"gateway/{live.gateway_id}" in (
        policies[res.BINDING_POLICY]["definition"]["cedar"]["statement"])
    update = control.args("update_gateway")[0]
    assert update["policyEngineConfiguration"] == {
        "arn": f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:policy-engine/{ENGINE_ID}",
        "mode": "ENFORCE"}
    assert update["interceptorConfigurations"] == created["interceptorConfigurations"]
    assert live.binding_policy_accepted is True
    assert [kind for kind, _ in saved[-1]] == [
        "iam-role", "iam-role", "lambda", "lambda", "gateway", "target", "policy-engine",
        "policy", "policy"]


def test_each_create_is_in_the_ledger_before_the_next_call():
    harness, _, saved = build()
    harness.create()
    assert [len(entries) for entries in saved] == list(range(1, 10))


def test_the_roles_trust_only_their_service_in_this_account():
    harness, clients, _ = build()
    harness.create()
    for call in clients.iam.args("create_role"):
        trust = json.loads(call["AssumeRolePolicyDocument"])["Statement"][0]
        assert trust["Condition"] == {"StringEquals": {"aws:SourceAccount": ACCOUNT}}
    policy = json.loads(clients.iam.args("put_role_policy")[0]["PolicyDocument"])
    functions = policy["Statement"][0]["Resource"]
    assert functions == [f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{NAME}-{s}"
                         for s in ("echo", "interceptor")]


def test_the_interceptor_package_carries_the_production_file_byte_for_byte():
    archive = zipfile.ZipFile(BytesIO(sources.interceptor_package()))
    assert sorted(archive.namelist()) == ["interceptor_wrapper.py", "lambda_function.py"]
    assert archive.read("lambda_function.py") == sources.PRODUCTION_INTERCEPTOR.read_bytes()
    assert zipfile.ZipFile(BytesIO(sources.echo_package())).namelist() == ["echo_target.py"]


def test_a_rejected_binding_policy_is_recorded_not_raised():
    harness, _, _ = build(control=FakeControl(binding_status="CREATE_FAILED"))
    live = harness.create()
    assert live.binding_policy_accepted is False
    assert "CREATE_FAILED" in live.binding_policy_reason and "unknown tag" in (
        live.binding_policy_reason)


def test_a_role_that_is_not_assumable_yet_is_retried():
    harness, clients, _ = build()
    attempts = []

    def create_function(**kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise client_error("InvalidParameterValueException",
                               "The role defined for the function cannot be assumed by Lambda.")
        return {}

    clients.lambda_.answers["create_function"] = create_function
    harness.create()
    assert len(attempts) >= 3


def test_another_lambda_error_is_not_retried():
    harness, clients, _ = build()
    clients.lambda_.answers["create_function"] = lambda **kw: (_ for _ in ()).throw(
        client_error("AccessDeniedException", "denied"))
    with pytest.raises(ClientError):
        harness.create()
    assert len(clients.lambda_.args("create_function")) == 1


def test_the_interceptor_mode_is_switched_with_a_function_configuration_update():
    harness, clients, _ = build()
    harness.create()
    harness.set_interceptor_mode("extra_argument")
    update = clients.lambda_.args("update_function_configuration")[0]
    assert update["FunctionName"] == f"{NAME}-interceptor"
    assert update["Environment"]["Variables"]["HARNESS_MODE"] == "extra_argument"
    assert update["Environment"]["Variables"]["PINNED_TOOLS"] == "EchoTarget___echo"


def test_a_failed_status_stops_with_the_reason():
    with pytest.raises(res.HarnessFailure, match="gateway ended in FAILED: no role"):
        res.wait_for(lambda: ("FAILED", "no role"), "READY", what="gateway", sleep=lambda s: None)
    ticks = iter(range(0, 1000, 100))
    with pytest.raises(res.HarnessFailure, match="still CREATING after 300 s"):
        res.wait_for(lambda: ("CREATING", ""), "READY", what="gateway", sleep=lambda s: None,
                     clock=lambda: next(ticks))


def test_teardown_deletes_targets_then_gateway_then_policies_then_engine_then_the_rest():
    harness, clients, _ = build()
    harness.create()
    harness.teardown()
    order = clients.control.names()
    deletes = [name for name in order if name.startswith("delete_")]
    assert deletes == ["delete_gateway_target", "delete_gateway", "delete_policy",
                       "delete_policy", "delete_policy_engine"]
    assert clients.lambda_.names().count("delete_function") == 2
    assert clients.iam.names().count("delete_role") == 2
    assert {call["logGroupName"] for call in clients.logs.args("delete_log_group")} == {
        f"/aws/lambda/{NAME}-echo", f"/aws/lambda/{NAME}-interceptor"}
    assert clients.iam.args("detach_role_policy") and clients.iam.args("delete_role_policy")


def test_teardown_after_a_half_finished_create_deletes_only_what_exists():
    harness, clients, _ = build()
    harness.ledger.add("iam-role", f"{NAME}-lambda")
    clients.iam.tags[f"{NAME}-lambda"] = res.tag_list(RUN_ID)
    harness.teardown()
    assert clients.control.names() == []
    assert clients.iam.names().count("delete_role") == 1


def test_things_already_gone_are_not_failures_but_other_errors_are_reported():
    harness, clients, _ = build()
    harness.ledger.add("lambda", f"{NAME}-echo")
    harness.ledger.add("lambda", f"{NAME}-interceptor")
    clients.lambda_.tags.update({f"{NAME}-echo": OWN, f"{NAME}-interceptor": OWN})
    clients.lambda_.answers["delete_function"] = lambda FunctionName: (_ for _ in ()).throw(
        client_error("ResourceNotFoundException") if FunctionName.endswith("echo")
        else client_error("AccessDeniedException"))
    with pytest.raises(res.TeardownIncomplete) as left:
        harness.teardown()
    assert f"lambda {NAME}-interceptor: AccessDeniedException" in str(left.value)
    assert f"{NAME}-echo:" not in str(left.value)
    assert len(clients.logs.args("delete_log_group")) == 2


def test_every_taggable_create_carries_the_run_tag():
    harness, clients, _ = build()
    harness.create()
    assert all(call["Tags"] == res.tag_list(RUN_ID) for call in clients.iam.args("create_role"))
    assert all(call["Tags"] == OWN for call in clients.lambda_.args("create_function"))
    assert clients.control.args("create_gateway")[0]["tags"] == OWN
    assert clients.control.args("create_policy_engine")[0]["tags"] == OWN


def test_a_ledger_for_another_run_is_refused_before_anything_is_called():
    config = res.HarnessConfig(NAME, ACCOUNT, REGION, "u", "c", BINDING_TEMPLATE, RUN_ID)
    ledger = res.Ledger(run_id="run-of-someone-else")
    with pytest.raises(HarnessRefusal, match="different run"):
        res.ThrowawayGateway(config, res.Clients(None, None, None, None), ledger)


def test_the_ledger_takes_the_run_id_of_the_config():
    harness, _, _ = build()
    assert harness.ledger.run_id == RUN_ID


def delete_ops(calls):
    return [name for name in calls.names() if name.startswith("delete_")]


def test_teardown_reads_the_tag_before_each_delete():
    harness, clients, _ = build()
    harness.create()
    harness.teardown()
    for client, read, delete in (
        (clients.iam, "list_role_tags", "delete_role"),
        (clients.lambda_, "list_tags", "delete_function"),
        (clients.control, "list_tags_for_resource", "delete_gateway"),
        (clients.control, "list_tags_for_resource", "delete_policy_engine"),
    ):
        names = client.names()
        assert names.index(read) < names.index(delete)


def test_teardown_never_lists_resources_to_find_what_to_delete():
    harness, clients, _ = build()
    harness.create()
    harness.teardown()
    everything = clients.iam.names() + clients.lambda_.names() + clients.control.names()
    assert not [name for name in everything if name.startswith(("list_gateways", "list_policy",
                                                                 "list_functions", "list_roles"))]
    assert not clients.logs.names().count("describe_log_groups")


MISMATCHES = {
    "role": lambda c: c.iam.tags.__setitem__(
        f"{NAME}-gateway", [{"Key": res.RUN_TAG, "Value": "run-of-someone-else"}]),
    "role without the tag": lambda c: c.iam.tags.__setitem__(f"{NAME}-gateway", []),
    "lambda": lambda c: c.lambda_.tags.__setitem__(f"{NAME}-echo", OTHER),
    "gateway": lambda c: c.control.tags.__setitem__(f"gateway/{GATEWAY_ID}", OTHER),
    "gateway without the tag": lambda c: c.control.tags.__setitem__(f"gateway/{GATEWAY_ID}", {}),
    "engine": lambda c: c.control.tags.__setitem__(f"policy-engine/{ENGINE_ID}", OTHER),
}
DELETE_COUNTS = {"role": ("iam", "delete_role", 1),
                 "role without the tag": ("iam", "delete_role", 1),
                 "lambda": ("lambda_", "delete_function", 1),
                 "gateway": ("control", "delete_gateway", 0),
                 "gateway without the tag": ("control", "delete_gateway", 0),
                 "engine": ("control", "delete_policy_engine", 0)}


@pytest.mark.parametrize("case", MISMATCHES)
def test_teardown_refuses_a_resource_whose_run_tag_is_not_this_runs(case):
    harness, clients, _ = build()
    harness.create()
    MISMATCHES[case](clients)
    with pytest.raises(res.TeardownIncomplete, match="not tagged for this run"):
        harness.teardown()
    client, operation, expected = DELETE_COUNTS[case]
    assert getattr(clients, client).names().count(operation) == expected


def test_a_refused_gateway_keeps_its_targets_and_a_refused_engine_keeps_its_policies():
    harness, clients, _ = build()
    harness.create()
    clients.control.tags[f"gateway/{GATEWAY_ID}"] = OTHER
    clients.control.tags[f"policy-engine/{ENGINE_ID}"] = OTHER
    with pytest.raises(res.TeardownIncomplete):
        harness.teardown()
    assert delete_ops(clients.control) == []


def test_a_refused_lambda_keeps_its_log_group():
    harness, clients, _ = build()
    harness.create()
    clients.lambda_.tags[f"{NAME}-echo"] = OTHER
    with pytest.raises(res.TeardownIncomplete):
        harness.teardown()
    assert [call["logGroupName"] for call in clients.logs.args("delete_log_group")] == [
        f"/aws/lambda/{NAME}-interceptor"]


def test_a_tag_that_cannot_be_read_is_reported_and_nothing_is_deleted():
    harness, clients, _ = build()
    harness.create()
    clients.iam.list_role_tags = lambda **kw: (_ for _ in ()).throw(
        client_error("AccessDenied"))
    with pytest.raises(res.TeardownIncomplete, match="iam-role .*: AccessDenied"):
        harness.teardown()
    assert clients.iam.names().count("delete_role") == 0


def test_a_ledger_name_that_does_not_belong_to_this_throwaway_is_never_touched():
    harness, clients, _ = build()
    harness.ledger.add("iam-role", "meridian-aurora-runtime")
    harness.ledger.add("lambda", "meridian-throwaway-00000000-echo")
    with pytest.raises(res.TeardownIncomplete, match="does not belong to"):
        harness.teardown()
    assert clients.iam.calls == [] and clients.lambda_.calls == [] and clients.logs.calls == []


def test_a_resource_that_stays_in_a_failed_delete_state_is_reported():
    harness, clients, _ = build()
    harness.create()
    clients.control.get_gateway = lambda **kw: {"status": "DELETE_FAILED",
                                                "statusReasons": ["still in use"]}
    with pytest.raises(res.TeardownIncomplete, match=f"gateway {GATEWAY_ID}: DELETE_FAILED"):
        harness.teardown()


def test_a_service_reason_is_scrubbed_of_account_ids():
    control = FakeControl(binding_status="CREATE_FAILED")
    control.get_policy = lambda **kw: {
        "status": "CREATE_FAILED" if kw["policyId"].startswith(res.BINDING_POLICY) else "ACTIVE",
        "statusReasons": [f"no access in {ACCOUNT}"]}
    harness, _, _ = build(control=control)
    live = harness.create()
    assert ACCOUNT not in live.binding_policy_reason and "<acct>" in live.binding_policy_reason


def recorded_calls(clients):
    pairs = (("iam", clients.iam), ("lambda", clients.lambda_),
             ("bedrock-agentcore-control", clients.control), ("logs", clients.logs))
    return [(service, name, kwargs) for service, fake in pairs for name, kwargs in fake.calls]


def validate(service, operation, kwargs):
    model = botocore.session.get_session().get_service_model(service)
    pascal = next(op for op in model.operation_names if xform_name(op) == operation)
    shape = model.operation_model(pascal).input_shape
    return ParamValidator().validate(kwargs, shape)


def without_additional_properties(kwargs):
    clean = copy.deepcopy(kwargs)
    for tool in clean["targetConfiguration"]["mcp"]["lambda"]["toolSchema"]["inlinePayload"]:
        tool["inputSchema"].pop("additionalProperties")
    return clean


def test_every_call_matches_the_installed_service_models():
    harness, clients, _ = build()
    harness.create()
    harness.set_interceptor_mode("refuse")
    harness.teardown()
    for service, operation, kwargs in recorded_calls(clients):
        if operation == "create_gateway_target":
            kwargs = without_additional_properties(kwargs)
        report = validate(service, operation, kwargs)
        assert not report.has_errors(), f"{service}.{operation}: {report.generate_report()}"


def test_the_installed_gateway_model_has_no_additional_properties_for_a_tool_schema():
    harness, clients, _ = build()
    harness.create()
    target = clients.control.args("create_gateway_target")[0]
    report = validate("bedrock-agentcore-control", "create_gateway_target", target)
    assert "additionalProperties" in report.generate_report()
