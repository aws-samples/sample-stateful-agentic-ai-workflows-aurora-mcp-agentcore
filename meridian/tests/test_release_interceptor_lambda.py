"""The interceptor function is deployed with a role that can only write its own logs."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import stat
import urllib.parse
import zipfile

import pytest
from botocore.exceptions import WaiterError

from scripts.identity_release import interceptor_lambda as deploy
from scripts.identity_release import settings
from tests import release_support as rs
from tests.aws_recorders import Recorder, Waiters, client_error, violations

PACKAGE = deploy.package()
SHA = base64.b64encode(hashlib.sha256(PACKAGE).digest()).decode()
ROLE_ARN = f"arn:aws:iam::{rs.ACCOUNT}:role/{settings.INTERCEPTOR_FUNCTION}"
OURS = dict(deploy.TAGS)
OTHER = {"project": "someone-else"}


class FakeLambda(Recorder, Waiters):
    def __init__(self, **kwargs):
        Recorder.__init__(self, **kwargs)
        Waiters.__init__(self)


class StuckLambda(FakeLambda):
    def get_waiter(self, name):
        class Waiter:
            def wait(self, **kwargs):
                raise WaiterError(name, "Waiter encountered a terminal failure state", {})

        return Waiter()


def desired():
    return deploy.desired(rs.ACCOUNT, rs.REGION, settings.cognito_settings(rs.COGNITO_ENV))


def function(tags=None, **changes):
    configuration = {
        "FunctionName": settings.INTERCEPTOR_FUNCTION, "State": "Active", "CodeSha256": SHA,
        "Handler": "lambda_function.lambda_handler", "Runtime": "python3.13", "Role": ROLE_ARN,
        "Timeout": 5, "MemorySize": 128,
        "Environment": {"Variables": desired().environment},
        "FunctionArn": rs.INTERCEPTOR_ARN,
    }
    configuration.update(changes)
    return {"Configuration": configuration, "Tags": OURS if tags is None else tags}


def role_answer(tags=None):
    pairs = [{"Key": k, "Value": v} for k, v in (OURS if tags is None else tags).items()]
    return {"Role": {"Arn": ROLE_ARN, "Tags": pairs,
                     "AssumeRolePolicyDocument": json.loads(desired().trust_policy)}}


def iam_with(role=True, policy=None, tags=None):
    answers = {"get_role": role_answer(tags),
               "get_role_policy": {"PolicyDocument": policy or desired().role_policy},
               "list_attached_role_policies": {"AttachedPolicies": []},
               "list_role_policies": {"PolicyNames": [deploy.POLICY_NAME]},
               "create_role": {"Role": {"Arn": ROLE_ARN}}}
    failures = {} if role else {"get_role": client_error("NoSuchEntity")}
    return Recorder(answers, failures)


def lambda_with(existing=None, **kwargs):
    answers = {"create_function": function()["Configuration"],
               "get_function": existing or function()}
    failures = {} if existing is not None else {"get_function": client_error(
        "ResourceNotFoundException")}
    return FakeLambda(answers=answers, failures=failures, **kwargs)


# --------------------------------------------------------------------- package


def test_the_package_is_the_production_interceptor_alone_and_is_reproducible():
    with zipfile.ZipFile(io.BytesIO(PACKAGE)) as archive:
        assert archive.namelist() == ["lambda_function.py"]
        shipped = archive.read("lambda_function.py")
    assert shipped == deploy.SOURCE.read_bytes()
    assert deploy.package() == PACKAGE
    with zipfile.ZipFile(io.BytesIO(PACKAGE)) as archive:
        assert archive.infolist()[0].compress_type == zipfile.ZIP_DEFLATED
    assert desired().code == PACKAGE


# --------------------------------------------------------------------- desired


def test_the_function_checks_the_clients_and_issuer_as_well_as_the_gateway_does():
    wanted = desired()

    assert wanted.environment == {
        "EXPECTED_CLIENT_ID": rs.CLIENT,
        "EXPECTED_ISSUER": settings.cognito_settings(rs.COGNITO_ENV).issuer}
    assert wanted.function_arn == rs.INTERCEPTOR_ARN and wanted.role_arn == ROLE_ARN
    assert wanted.code_sha256 == SHA


def test_the_pinned_tools_override_is_never_set_because_it_replaces_the_defaults():
    assert "PINNED_TOOLS" not in desired().environment


def test_the_role_can_be_assumed_only_by_lambda_in_this_account():
    trust = json.loads(desired().trust_policy)

    statement = trust["Statement"][0]
    assert statement["Principal"] == {"Service": "lambda.amazonaws.com"}
    assert statement["Condition"] == {"StringEquals": {"aws:SourceAccount": rs.ACCOUNT}}


def test_the_role_policy_writes_this_functions_logs_and_touches_nothing_else():
    policy = desired().role_policy
    text = json.dumps(policy)

    actions = sorted(a for s in policy["Statement"] for a in ([s["Action"]] if isinstance(
        s["Action"], str) else s["Action"]))
    assert actions == ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    assert all(f"log-group:/aws/lambda/{settings.INTERCEPTOR_FUNCTION}" in r
               for s in policy["Statement"] for r in ([s["Resource"]] if isinstance(
                   s["Resource"], str) else s["Resource"]))
    for forbidden in ("rds", "secretsmanager", "ssm", "bedrock", '"*"'):
        assert forbidden not in text


def test_the_plan_names_every_resource_and_makes_no_call():
    lines = deploy.plan(desired())

    assert any(settings.INTERCEPTOR_FUNCTION in line and "IAM role" in line for line in lines)
    assert any("Lambda function" in line for line in lines)
    assert not any(rs.ACCOUNT in line for line in lines)


def test_everything_is_tagged_with_a_stable_name_and_the_release_run():
    assert OURS == {"project": "meridian", "meridian-component": "gateway-interceptor",
                    "meridian-release": "b2b-identity"}


# ----------------------------------------------------------------------- apply


def test_a_first_apply_creates_the_role_then_the_function_and_waits():
    iam, lam = iam_with(role=False), lambda_with(existing=None)

    deploy.apply(iam, lam, desired(), sleep=lambda s: None)

    assert iam.names()[:2] == ["get_role", "create_role"]
    assert "put_role_policy" in iam.names()
    assert iam.args("create_role")[0]["Tags"] == [{"Key": k, "Value": v} for k, v in OURS.items()]
    created = lam.args("create_function")[0]
    assert created["Role"] == ROLE_ARN and created["Handler"] == "lambda_function.lambda_handler"
    assert created["Environment"] == {"Variables": desired().environment}
    assert created["Code"] == {"ZipFile": PACKAGE} and created["Tags"] == OURS
    assert lam.waited[-1][0] == "function_active_v2"
    assert "Delay" in lam.waited[-1][1]["WaiterConfig"]
    assert violations("iam", iam.calls) == [] and violations("lambda", lam.calls) == []


def test_a_new_role_that_lambda_cannot_assume_yet_is_retried():
    cannot = client_error("InvalidParameterValueException",
                          "The role defined for the function cannot be assumed by Lambda.")
    lam = lambda_with(existing=None)
    lam.failures["create_function"] = [cannot, cannot]
    slept = []

    deploy.apply(iam_with(role=False), lam, desired(), sleep=slept.append)

    assert lam.names().count("create_function") == 3 and slept == [5, 5]


def test_a_role_that_never_becomes_assumable_stops_with_the_reason():
    cannot = client_error("InvalidParameterValueException", "cannot be assumed by Lambda")
    lam = lambda_with(existing=None)
    lam.failures["create_function"] = [cannot] * 20

    with pytest.raises(deploy.DeployError, match="cannot be assumed"):
        deploy.apply(iam_with(role=False), lam, desired(), sleep=lambda s: None)


def test_another_create_error_is_not_retried():
    lam = lambda_with(existing=None)
    lam.failures["create_function"] = [client_error("AccessDeniedException")]

    with pytest.raises(Exception, match="AccessDenied"):
        deploy.apply(iam_with(role=False), lam, desired(), sleep=lambda s: None)
    assert lam.names().count("create_function") == 1


def test_applying_again_with_nothing_changed_writes_no_function_and_no_role():
    iam, lam = iam_with(), lambda_with(existing=function())

    notes = deploy.apply(iam, lam, desired(), sleep=lambda s: None)

    assert "create_role" not in iam.names() and "create_function" not in lam.names()
    assert "update_function_code" not in lam.names()
    assert "update_function_configuration" not in lam.names()
    assert any("unchanged" in note for note in notes)


def test_changed_code_is_uploaded_and_changed_settings_are_updated():
    stale = function(CodeSha256="old", Timeout=3,
                     Environment={"Variables": {"EXPECTED_CLIENT_ID": "other"}})
    lam = lambda_with(existing=stale)

    deploy.apply(iam_with(), lam, desired(), sleep=lambda s: None)

    assert lam.args("update_function_code")[0]["ZipFile"] == PACKAGE
    config = lam.args("update_function_configuration")[0]
    assert config["Timeout"] == 5 and config["Environment"] == {"Variables": desired().environment}
    assert [w[0] for w in lam.waited].count("function_updated_v2") == 2
    assert violations("lambda", lam.calls) == []


def test_an_existing_role_gets_its_trust_and_policy_rewritten_every_time():
    iam = iam_with()

    deploy.apply(iam, lambda_with(existing=function()), desired(), sleep=lambda s: None)

    assert iam.names().count("update_assume_role_policy") == 1
    assert iam.args("put_role_policy")[0]["PolicyName"] == deploy.POLICY_NAME
    assert violations("iam", iam.calls) == []


def test_a_missing_role_error_from_the_trust_update_does_not_create_a_role():
    iam = iam_with()
    iam.failures["update_assume_role_policy"] = [client_error("NoSuchEntity")]

    with pytest.raises(Exception, match="NoSuchEntity"):
        deploy.apply(iam, lambda_with(existing=function()), desired(), sleep=lambda s: None)
    assert "create_role" not in iam.names() and "put_role_policy" not in iam.names()


def test_a_role_without_our_tags_is_never_modified():
    iam, lam = iam_with(tags=OTHER), lambda_with(existing=function())

    with pytest.raises(deploy.DeployError, match="not tagged"):
        deploy.apply(iam, lam, desired(), sleep=lambda s: None)
    assert iam.names() == ["get_role"] and lam.calls == []


def test_a_function_without_our_tags_is_never_modified():
    iam, lam = iam_with(), lambda_with(existing=function(tags=OTHER, CodeSha256="old"))

    with pytest.raises(deploy.DeployError, match="not tagged"):
        deploy.apply(iam, lam, desired(), sleep=lambda s: None)
    assert lam.names() == ["get_function"]


def test_a_wait_that_never_ends_is_a_deploy_error_not_a_traceback():
    lam = StuckLambda(answers={"get_function": function(CodeSha256="old")})

    with pytest.raises(deploy.DeployError, match="function_updated_v2"):
        deploy.apply(iam_with(), lam, desired(), sleep=lambda s: None)


def test_an_update_still_in_flight_is_waited_out_before_new_code_is_uploaded():
    lam = lambda_with(existing=function(CodeSha256="old", LastUpdateStatus="InProgress"))

    deploy.apply(iam_with(), lam, desired(), sleep=lambda s: None)

    assert lam.waited[0][0] == "function_updated_v2"
    assert lam.names().index("get_function") < lam.names().index("update_function_code")
    assert lam.names().count("get_function") == 2


def test_a_pending_function_is_waited_until_active_before_any_update():
    lam = lambda_with(existing=function(CodeSha256="old", State="Pending"))

    deploy.apply(iam_with(), lam, desired(), sleep=lambda s: None)

    assert lam.waited[0][0] == "function_active_v2"
    assert lam.names().index("update_function_code") > 0


@pytest.mark.parametrize("change", [
    {"Layers": [{"Arn": "arn:aws:lambda:us-east-1:123456789012:layer:x:1"}]},
    {"VpcConfig": {"VpcId": "vpc-0abc", "SubnetIds": ["subnet-1"],
                   "SecurityGroupIds": ["sg-1"]}},
    {"MemorySize": 512},
])
def test_drifted_layers_vpc_and_memory_are_put_back(change):
    lam = lambda_with(existing=function(**change))

    deploy.apply(iam_with(), lam, desired(), sleep=lambda s: None)

    config = lam.args("update_function_configuration")[0]
    assert config["Layers"] == [] and config["MemorySize"] == 128
    assert config["VpcConfig"] == {"SubnetIds": [], "SecurityGroupIds": []}
    assert violations("lambda", lam.calls) == []


def test_drifted_architectures_are_put_back_with_the_code_upload():
    lam = lambda_with(existing=function(Architectures=["arm64"]))

    deploy.apply(iam_with(), lam, desired(), sleep=lambda s: None)

    assert lam.args("update_function_code")[0]["Architectures"] == ["x86_64"]
    assert violations("lambda", lam.calls) == []


def test_a_new_role_that_iam_does_not_show_yet_is_retried_for_the_policy():
    iam = iam_with(role=False)
    iam.failures["put_role_policy"] = [client_error("NoSuchEntity")]
    slept = []

    deploy.apply(iam, lambda_with(existing=None), desired(), sleep=slept.append)

    assert iam.names().count("put_role_policy") == 2 and slept == [5]


def test_a_new_role_that_never_shows_up_stops_with_the_reason():
    iam = iam_with(role=False)
    iam.failures["put_role_policy"] = [client_error("NoSuchEntity")] * 20

    with pytest.raises(deploy.DeployError, match="not visible yet"):
        deploy.apply(iam, lambda_with(existing=None), desired(), sleep=lambda s: None)


def test_a_policy_write_to_an_existing_role_does_not_retry_no_such_entity():
    iam = iam_with()
    iam.failures["put_role_policy"] = [client_error("NoSuchEntity")]

    with pytest.raises(Exception, match="NoSuchEntity"):
        deploy.apply(iam, lambda_with(existing=function()), desired(), sleep=lambda s: None)
    assert iam.names().count("put_role_policy") == 1


def test_the_role_tags_are_reread_before_each_write_and_a_change_stops_the_writes():
    iam = iam_with()
    answers = iter([role_answer(), role_answer(), role_answer(OTHER)])
    iam.answers["get_role"] = lambda **kwargs: next(answers)

    with pytest.raises(deploy.DeployError, match="not tagged"):
        deploy.apply(iam, lambda_with(existing=function()), desired(), sleep=lambda s: None)
    assert iam.names().count("update_assume_role_policy") == 1
    assert "put_role_policy" not in iam.names()


def test_the_teardown_plan_names_the_log_group_and_the_manual_delete_command():
    text = "\n".join(deploy.teardown_plan())

    name = f"/aws/lambda/{settings.INTERCEPTOR_FUNCTION}"
    assert "left in place" in text
    assert f"aws logs delete-log-group --log-group-name {name}" in text


# ------------------------------------------------------------------- read back


def test_a_deployed_function_that_matches_has_no_findings():
    assert deploy.read_back(iam_with(), lambda_with(existing=function()), desired()) == []


@pytest.mark.parametrize(("change", "word"), [
    ({"State": "Failed"}, "State"),
    ({"CodeSha256": "old"}, "code"),
    ({"Handler": "other.handler"}, "Handler"),
    ({"Runtime": "python3.9"}, "Runtime"),
    ({"Role": "arn:aws:iam::123456789012:role/other"}, "role"),
    ({"Environment": {"Variables": {}}}, "environment"),
    ({"Environment": {"Variables": {**desired().environment, "PINNED_TOOLS": "echo"}}},
     "environment"),
    ({"Timeout": 60}, "Timeout"),
    ({"MemorySize": 3008}, "MemorySize"),
    ({"Layers": [{"Arn": "arn:aws:lambda:us-east-1:123456789012:layer:x:1"}]}, "Layers"),
    ({"Architectures": ["arm64"]}, "Architectures"),
    ({"VpcConfig": {"VpcId": "vpc-0abc", "SubnetIds": ["subnet-1"]}}, "VpcConfig"),
])
def test_each_deviation_of_the_function_is_one_finding(change, word):
    found = deploy.read_back(iam_with(), lambda_with(existing=function(**change)), desired())

    assert len(found) == 1 and word in found[0], found


def test_reserved_concurrency_is_a_finding():
    described = function()
    described["Concurrency"] = {"ReservedConcurrentExecutions": 0}

    found = deploy.read_back(iam_with(), lambda_with(existing=described), desired())

    assert len(found) == 1 and "reserved concurrency" in found[0]


def test_a_trust_policy_that_differs_is_a_finding_even_when_url_encoded():
    wider = json.loads(desired().trust_policy)
    wider["Statement"][0]["Principal"] = {"Service": "ec2.amazonaws.com"}
    iam = iam_with()
    iam.answers["get_role"]["Role"]["AssumeRolePolicyDocument"] = wider

    found = deploy.read_back(iam, lambda_with(existing=function()), desired())
    assert len(found) == 1 and "trust policy" in found[0]

    iam.answers["get_role"]["Role"]["AssumeRolePolicyDocument"] = urllib.parse.quote(
        desired().trust_policy)
    assert deploy.read_back(iam, lambda_with(existing=function()), desired()) == []


def test_a_missing_function_is_a_finding():
    found = deploy.read_back(iam_with(), lambda_with(existing=None), desired())

    assert found == [f"Lambda {settings.INTERCEPTOR_FUNCTION}: does not exist"]


def test_missing_tags_are_findings():
    found = deploy.read_back(
        iam_with(tags=OTHER), lambda_with(existing=function(tags=OTHER)), desired())

    assert len(found) == 2 and all("tag" in line for line in found)


def test_a_role_with_extra_permissions_is_a_finding_even_if_the_function_is_fine():
    loose = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "rds-data:ExecuteStatement", "Resource": "*"}]}
    found = deploy.read_back(iam_with(policy=loose), lambda_with(existing=function()), desired())
    assert any("policy" in line for line in found)

    attached = iam_with()
    attached.answers["list_attached_role_policies"] = {
        "AttachedPolicies": [{"PolicyName": "AdministratorAccess"}]}
    found = deploy.read_back(attached, lambda_with(existing=function()), desired())
    assert any("AdministratorAccess" in line for line in found)

    extra = iam_with()
    extra.answers["list_role_policies"] = {"PolicyNames": [deploy.POLICY_NAME, "more"]}
    assert any("more" in line for line in deploy.read_back(
        extra, lambda_with(existing=function()), desired()))


# -------------------------------------------------------------------- teardown


def test_teardown_deletes_the_function_then_the_role_and_rereads_the_tag_each_time():
    iam, lam = iam_with(), lambda_with(existing=function())
    iam.answers["list_attached_role_policies"] = {
        "AttachedPolicies": [{"PolicyName": "x", "PolicyArn": "arn:aws:iam::aws:policy/x"}]}

    notes = deploy.teardown(iam, lam)

    assert lam.names() == ["get_function", "delete_function"]
    assert iam.names() == ["get_role", "list_attached_role_policies", "get_role",
                           "detach_role_policy", "list_role_policies", "get_role",
                           "delete_role_policy", "get_role", "delete_role"]
    assert notes[0].endswith("deleted") and notes[-1].endswith("deleted")
    assert violations("iam", iam.calls) == [] and violations("lambda", lam.calls) == []


def test_teardown_refuses_a_function_or_role_without_our_tags_and_deletes_nothing():
    lam = lambda_with(existing=function(tags=OTHER))
    with pytest.raises(deploy.DeployError, match="not tagged"):
        deploy.teardown(iam_with(), lam)
    assert "delete_function" not in lam.names()

    iam = iam_with(tags=OTHER)
    with pytest.raises(deploy.DeployError, match="not tagged"):
        deploy.teardown(iam, lambda_with(existing=None))
    assert not [n for n in iam.names() if n.startswith(("delete", "detach"))]


def test_teardown_of_something_already_gone_changes_nothing():
    iam, lam = iam_with(role=False), lambda_with(existing=None)

    notes = deploy.teardown(iam, lam)

    assert all("already gone" in note for note in notes)
    assert not [n for n in iam.names() + lam.names() if n.startswith("delete")]


def test_teardown_stops_when_the_tag_vanishes_between_the_reads():
    iam = iam_with()
    answers = iter([role_answer(), role_answer(OTHER)])
    iam.answers["get_role"] = lambda **kwargs: next(answers)

    with pytest.raises(deploy.DeployError, match="not tagged"):
        deploy.teardown(iam, lambda_with(existing=None))
    assert "delete_role" not in iam.names()


# --------------------------------------------------------------------- outputs


def test_the_outputs_file_is_private_and_holds_no_account_id_or_secret(tmp_path):
    target = tmp_path / "release"

    path = deploy.record_outputs(target, desired(), "2026-10-08T12:00:00+00:00")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    text = path.read_text()
    assert rs.ACCOUNT not in text and ("e" + "yJ") not in text and rs.CLIENT not in text
    saved = json.loads(text)
    assert saved["function_name"] == settings.INTERCEPTOR_FUNCTION
    assert saved["code_sha256"] == SHA and saved["tags"] == OURS
    deploy.record_outputs(target, desired(), "later")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_removing_the_outputs_is_quiet_when_there_are_none(tmp_path):
    deploy.remove_outputs(tmp_path)
    deploy.record_outputs(tmp_path, desired(), "now")
    deploy.remove_outputs(tmp_path)

    assert list(tmp_path.iterdir()) == []
