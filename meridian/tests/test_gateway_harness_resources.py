"""The harness creates what the plan says, switches the interceptor, and deletes all of it."""

from __future__ import annotations

import copy
import json
import zipfile
from io import BytesIO

import botocore.session
import pytest
from botocore import xform_name
from botocore.exceptions import ClientError, EndpointConnectionError
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
    ledger = res.Ledger(save=lambda payload: saved.append(copy.deepcopy(payload)))
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
    assert set(schema["inputSchema"]) == {"type", "properties", "required"}
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
    assert [kind for kind, _ in saved[-1]["entries"]] == [
        "iam-role", "iam-role", "lambda", "lambda", "gateway", "target", "policy-engine",
        "policy", "policy"]


def test_each_create_is_in_the_ledger_before_the_next_call():
    harness, _, saved = build()
    harness.create()
    counts = [len(payload["entries"]) for payload in saved]
    assert counts == sorted(counts) and counts[0] == 0 and counts[-1] == 9


def test_the_ledger_persists_the_run_id_and_is_saved_before_the_first_create():
    seen = []
    harness, clients, saved = build()
    harness.ledger.save = lambda payload: seen.append(
        (copy.deepcopy(payload), len(clients.iam.calls) + len(clients.control.calls)))
    harness.create()
    first, calls_then = seen[0]
    assert first == {"run_id": RUN_ID, "entries": []} and calls_then == 0
    assert all(payload["run_id"] == RUN_ID for payload, _ in seen)
    assert seen[-1][0]["entries"][0] == ["iam-role", f"{NAME}-lambda"]


def entry_exists_when(harness, client, operation):
    """Make ``operation`` raise, and report the ledger entries that existed at that moment."""
    at_call = []

    def explode(**kwargs):
        at_call.append(list(harness.ledger.entries))
        raise client_error("AccessDenied")

    client.answers[operation] = explode
    with pytest.raises(ClientError):
        harness.create()
    return at_call[0]


def test_the_role_is_in_the_ledger_before_create_role_is_called():
    harness, clients, _ = build()
    at_call = entry_exists_when(harness, clients.iam, "create_role")
    assert at_call == [("iam-role", f"{NAME}-lambda")]
    assert harness.ledger.entries == [("iam-role", f"{NAME}-lambda")]


def test_the_function_is_in_the_ledger_before_create_function_is_called():
    harness, clients, _ = build()
    at_call = entry_exists_when(harness, clients.lambda_, "create_function")
    assert ("lambda", f"{NAME}-echo") in at_call
    assert harness.ledger.entries[-1] == ("lambda", f"{NAME}-echo")


def test_a_create_that_left_nothing_behind_is_gone_not_a_failure_at_teardown():
    harness, clients, _ = build()
    entry_exists_when(harness, clients.iam, "create_role")
    harness.teardown()
    assert clients.iam.names().count("delete_role") == 0


def test_the_roles_trust_only_their_service_in_this_account():
    harness, clients, _ = build()
    harness.create()
    calls = {c["RoleName"]: c for c in clients.iam.args("create_role")}
    lambda_trust = json.loads(calls[f"{NAME}-lambda"]["AssumeRolePolicyDocument"])["Statement"][0]
    assert lambda_trust["Condition"] == {"StringEquals": {"aws:SourceAccount": ACCOUNT}}
    gateway_trust = json.loads(calls[f"{NAME}-gateway"]["AssumeRolePolicyDocument"])["Statement"][0]
    assert gateway_trust["Condition"] == {
        "StringEquals": {"aws:SourceAccount": ACCOUNT},
        "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:gateway/*"}}
    policies = {c["RoleName"]: json.loads(c["PolicyDocument"])
                for c in clients.iam.args("put_role_policy")}
    functions = policies[f"{NAME}-gateway"]["Statement"][0]["Resource"]
    assert functions == [f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{NAME}-{s}"
                         for s in ("echo", "interceptor")]


def test_the_lambda_role_writes_only_to_log_groups_of_this_throwaway():
    harness, clients, _ = build()
    harness.create()
    assert clients.iam.args("attach_role_policy") == []
    policy = next(json.loads(c["PolicyDocument"]) for c in clients.iam.args("put_role_policy")
                  if c["RoleName"] == f"{NAME}-lambda")
    group = f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/lambda/{NAME}-*"
    for statement in policy["Statement"]:
        assert statement["Effect"] == "Allow" and statement["Resource"] in (
            [group, group + ":*"], [group], group)
    actions = {a for st in policy["Statement"] for a in (
        [st["Action"]] if isinstance(st["Action"], str) else st["Action"])}
    assert actions == {"logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"}


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
    harness.set_interceptor_mode("bad_type")
    update = clients.lambda_.args("update_function_configuration")[0]
    assert update["FunctionName"] == f"{NAME}-interceptor"
    assert update["Environment"]["Variables"]["HARNESS_MODE"] == "bad_type"
    assert update["Environment"]["Variables"]["PINNED_TOOLS"] == "EchoTarget___echo"


@pytest.mark.parametrize("mode", ["pin", "bad_type", "drop_required", "refuse", "off"])
def test_every_valid_interceptor_mode_is_accepted(mode):
    harness, clients, _ = build()
    harness.create()
    harness.set_interceptor_mode(mode)
    assert clients.lambda_.args("update_function_configuration")[0]["Environment"]["Variables"][
        "HARNESS_MODE"] == mode


def test_detaching_updates_the_gateway_without_interceptors_and_reports_none_left():
    harness, clients, _ = build()
    harness.create()
    assert harness.detach_interceptor() == 0
    updates = clients.control.args("update_gateway")
    assert len(updates) == 2 and "interceptorConfigurations" in updates[0]
    detach = updates[-1]
    assert "interceptorConfigurations" not in detach
    assert detach["authorizerType"] == "CUSTOM_JWT" and detach["roleArn"] == updates[0]["roleArn"]
    assert detach["policyEngineConfiguration"] == updates[0]["policyEngineConfiguration"]
    assert clients.control.names()[-4:] == [
        "get_gateway", "update_gateway", "get_gateway", "get_gateway"]


def gateway_reads(clients, counts):
    """Make get_gateway report READY with the next interceptor count from ``counts``."""
    remaining = iter(counts)
    last = [counts[-1]]

    def read(**kwargs):
        count = next(remaining, last[0])
        return {"status": "READY", "interceptorConfigurations": [{}] * count}

    clients.control.get_gateway = read


def test_detaching_reports_the_interceptors_the_gateway_still_has_after_the_deadline():
    harness, clients, _ = build()
    harness.create()
    gateway_reads(clients, [1])
    assert harness.detach_interceptor() == 1


def test_detaching_without_an_interceptor_to_start_with_fails_instead_of_passing():
    harness, clients, _ = build()
    harness.create()
    gateway_reads(clients, [0])
    with pytest.raises(res.HarnessFailure, match="no interceptor attached before"):
        harness.detach_interceptor()
    assert len(clients.control.args("update_gateway")) == 1, "the update was never sent"


def test_the_read_back_waits_for_an_update_that_has_not_started_yet():
    harness, clients, _ = build()
    harness.create()
    gateway_reads(clients, [1, 1, 1, 0])
    assert harness.detach_interceptor() == 0


def test_the_read_back_gives_up_when_the_deadline_passes():
    harness, clients, _ = build()
    harness.create()
    gateway_reads(clients, [1])
    sleeps = []
    harness._sleep = sleeps.append
    assert harness.detach_interceptor(settle_seconds=30) == 1
    assert sleeps and sum(sleeps) >= 25


@pytest.mark.parametrize("mode", ["extra_argument", "", "PIN", "pin; refuse"])
def test_an_unknown_interceptor_mode_is_refused_before_any_call(mode):
    harness, clients, _ = build()
    harness.create()
    before = len(clients.lambda_.calls)
    with pytest.raises(HarnessRefusal, match="interceptor mode"):
        harness.set_interceptor_mode(mode)
    assert len(clients.lambda_.calls) == before


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
    assert [c["logGroupName"] for c in clients.logs.args("delete_log_group")] == [
        f"/aws/lambda/{NAME}-echo"]


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


def test_every_call_matches_the_installed_service_models():
    harness, clients, _ = build()
    harness.create()
    harness.set_interceptor_mode("refuse")
    harness.teardown()
    for service, operation, kwargs in recorded_calls(clients):
        report = validate(service, operation, kwargs)
        assert not report.has_errors(), f"{service}.{operation}: {report.generate_report()}"


def test_the_update_resends_the_gateway_description():
    harness, clients, _ = build()
    harness.create()
    created = clients.control.args("create_gateway")[0]
    update = clients.control.args("update_gateway")[0]
    assert update["description"] == created["description"]


def test_the_plan_does_not_claim_additional_properties():
    harness, _, _ = build()
    assert not [line for line in harness.plan() if "additionalProperties" in line]


def all_calls(clients):
    return len(recorded_calls(clients))


def test_an_invalid_request_creates_nothing(monkeypatch):
    bad = [{"name": "echo", "description": "x", "inputSchema": {
        "type": "object", "additionalProperties": False}}]
    monkeypatch.setattr(res, "ECHO_SCHEMA", bad)
    harness, clients, saved = build()
    with pytest.raises(HarnessRefusal, match="preflight.*create_gateway_target"):
        harness.create()
    assert all_calls(clients) == 0
    assert harness.ledger.entries == []


def test_a_request_the_client_model_rejects_creates_nothing():
    harness, clients, _ = build()
    consulted = []
    real = botocore.session.get_session().get_service_model("bedrock-agentcore-control")

    class RejectingModel:
        def operation_model(self, name):
            consulted.append(name)
            return real.operation_model("DeleteGateway")

    clients.control.meta = type("Meta", (), {"service_model": RejectingModel()})()
    with pytest.raises(HarnessRefusal, match="preflight"):
        harness.create()
    assert "CreateGateway" in consulted and all_calls(clients) == 0


def test_preflight_checks_every_service_before_the_first_call():
    harness, clients, saved = build()
    harness.create()
    assert saved[0] == {"run_id": RUN_ID, "entries": []}
    assert clients.iam.calls[0][0] == "create_role"


def test_a_template_that_changed_shape_creates_nothing():
    config = res.HarnessConfig(
        NAME, ACCOUNT, REGION, "u", "c", "forbid(principal, action, resource);", RUN_ID)
    clients = res.Clients(iam=FakeIam(), lambda_=FakeLambda(), control=FakeControl(), logs=Fake())
    harness = res.ThrowawayGateway(config, clients, res.Ledger(), sleep=lambda s: None)
    with pytest.raises(HarnessRefusal, match="no longer lists"):
        harness.create()
    assert all_calls(clients) == 0


def test_teardown_continues_past_errors_that_are_not_client_errors():
    harness, clients, _ = build()
    harness.create()
    clients.control.delete_gateway_target = lambda **kw: (_ for _ in ()).throw(
        EndpointConnectionError(endpoint_url="https://gw.example"))
    with pytest.raises(res.TeardownIncomplete, match="target .*EndpointConnectionError"):
        harness.teardown()
    assert clients.lambda_.names().count("delete_function") == 2
    assert clients.iam.names().count("delete_role") == 2
    assert len(clients.logs.args("delete_log_group")) == 2


@pytest.mark.parametrize("error", [KeyError("Configuration"), TypeError("bad"),
                                   AttributeError("no attribute"), IndexError("empty")])
def test_any_error_on_one_entry_is_reported_and_the_rest_is_deleted(error):
    harness, clients, _ = build()
    harness.create()
    real = clients.lambda_.delete_function
    clients.lambda_.delete_function = lambda **kw: (
        (_ for _ in ()).throw(error) if kw["FunctionName"].endswith("-echo") else real(**kw))
    with pytest.raises(res.TeardownIncomplete, match=f"lambda .*-echo: {type(error).__name__}"):
        harness.teardown()
    assert clients.control.names().count("delete_gateway") == 1
    assert clients.iam.names().count("delete_role") == 2
    assert len(clients.logs.args("delete_log_group")) == 1  # only the deleted function's group


def test_a_log_group_delete_failing_with_an_unexpected_error_is_reported():
    harness, clients, _ = build()
    harness.create()
    clients.logs.delete_log_group = lambda **kw: (_ for _ in ()).throw(KeyError("logGroup"))
    with pytest.raises(res.TeardownIncomplete, match="log group .*KeyError"):
        harness.teardown()
    assert clients.iam.names().count("delete_role") == 2


def test_a_malformed_ledger_entry_is_reported_and_the_rest_is_deleted():
    harness, clients, _ = build()
    harness.create()
    harness.ledger.entries.append(("policy", "no-slash-here"))
    with pytest.raises(res.TeardownIncomplete, match="policy no-slash-here"):
        harness.teardown()
    assert clients.iam.names().count("delete_role") == 2


def test_a_log_group_delete_that_cannot_reach_the_service_is_reported():
    harness, clients, _ = build()
    harness.create()
    clients.logs.delete_log_group = lambda **kw: (_ for _ in ()).throw(
        EndpointConnectionError(endpoint_url="https://logs.example"))
    with pytest.raises(res.TeardownIncomplete, match="log group .*EndpointConnectionError"):
        harness.teardown()
    assert clients.iam.names().count("delete_role") == 2


def test_an_empty_ledger_loaded_from_a_file_is_refused_without_an_aws_call():
    harness, clients, _ = build()
    with pytest.raises(HarnessRefusal, match="ledger"):
        harness.teardown(from_file=True)
    assert all_calls(clients) == 0


def test_a_loaded_ledger_with_entries_tears_down():
    harness, clients, _ = build()
    harness.ledger.entries.append(("iam-role", f"{NAME}-lambda"))
    clients.iam.tags[f"{NAME}-lambda"] = res.tag_list(RUN_ID)
    harness.teardown(from_file=True)
    assert clients.iam.names().count("delete_role") == 1


def test_the_gateway_tag_is_read_again_just_before_it_is_deleted():
    harness, clients, _ = build()
    harness.create()
    real_delete = clients.control.delete_gateway_target

    def delete_then_retag(**kwargs):
        real_delete(**kwargs)
        clients.control.tags[f"gateway/{GATEWAY_ID}"] = OTHER

    clients.control.delete_gateway_target = delete_then_retag
    with pytest.raises(res.TeardownIncomplete, match="not tagged for this run"):
        harness.teardown()
    assert clients.control.names().count("delete_gateway") == 0


def test_the_engine_tag_is_read_again_just_before_it_is_deleted():
    harness, clients, _ = build()
    harness.create()
    real_delete = clients.control.delete_policy

    def delete_then_retag(**kwargs):
        real_delete(**kwargs)
        clients.control.tags[f"policy-engine/{ENGINE_ID}"] = OTHER

    clients.control.delete_policy = delete_then_retag
    with pytest.raises(res.TeardownIncomplete, match="not tagged for this run"):
        harness.teardown()
    assert clients.control.names().count("delete_policy_engine") == 0


def test_a_log_group_is_deleted_only_for_a_function_that_was_deleted_or_gone():
    harness, clients, _ = build()
    harness.create()
    clients.lambda_.answers["delete_function"] = lambda FunctionName: (_ for _ in ()).throw(
        client_error("AccessDeniedException"))
    with pytest.raises(res.TeardownIncomplete):
        harness.teardown()
    assert clients.logs.args("delete_log_group") == []
