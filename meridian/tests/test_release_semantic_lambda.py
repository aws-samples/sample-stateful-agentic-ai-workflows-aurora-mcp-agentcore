"""`release_identity.py semantic-lambda` grants the role the read, then moves the variable."""

from __future__ import annotations

import pytest

from scripts import release_identity
from scripts.identity_release import semantic_lambda as semantic
from scripts.identity_release import settings
from tests import release_support as rs
from tests.aws_recorders import client_error, violations
from tests.lambda_release_support import GATEWAY, MASTER, SEMANTIC_ROLE, World
from tests.test_lambda_release_cli import NOW, env

APPLY = [settings.CONFIRM_FLAG, "--apply"]
POLICY = semantic.POLICY_NAME


class Iam:
    """A stateful IAM fake: inline policies by (role, name), enough for the grant reader."""

    def __init__(self, policies=None):
        self.policies = dict(policies or {})
        self.calls = []

    def names(self):
        return [name for name, _ in self.calls]

    def get_role_policy(self, RoleName, PolicyName):
        self.calls.append(("get_role_policy", {"RoleName": RoleName, "PolicyName": PolicyName}))
        if (RoleName, PolicyName) not in self.policies:
            raise client_error("NoSuchEntity", "no policy", "GetRolePolicy")
        return {"PolicyDocument": self.policies[(RoleName, PolicyName)]}

    def put_role_policy(self, RoleName, PolicyName, PolicyDocument):
        import json

        self.calls.append(("put_role_policy", {"RoleName": RoleName, "PolicyName": PolicyName,
                                               "PolicyDocument": PolicyDocument}))
        self.policies[(RoleName, PolicyName)] = json.loads(PolicyDocument)

    def delete_role_policy(self, RoleName, PolicyName):
        self.calls.append(("delete_role_policy", {"RoleName": RoleName,
                                                  "PolicyName": PolicyName}))
        del self.policies[(RoleName, PolicyName)]

    def list_role_policies(self, RoleName):
        return {"PolicyNames": [n for r, n in self.policies if r == RoleName]}

    def list_attached_role_policies(self, RoleName):
        return {"AttachedPolicies": []}

    def args(self, operation):
        return [kwargs for name, kwargs in self.calls if name == operation]


def world(*, semantic_env=MASTER, policies=None, extra_variables=None):
    built = World(semantic_env=semantic_env)
    built.configs[semantic.FUNCTION]["Environment"]["Variables"].update(extra_variables or {})
    built.configs[semantic.FUNCTION]["RevisionId"] = "rev-1"
    built.iam = Iam(policies)
    return built


def run(argv, built, tmp_path, environment=None):
    return release_identity.main(
        ["semantic-lambda", *argv],
        release_identity.Dependencies(
            env=environment or env(), session=built.session, now=lambda: NOW,
            release_dir=tmp_path / "release", sleep=lambda seconds: None))


def test_a_dry_run_reads_and_writes_nothing(tmp_path, capsys):
    built = world()

    assert run([], built, tmp_path) == 0

    out = capsys.readouterr().out
    assert "DRY RUN" in out and POLICY in out and "would be added" in out
    assert "would be set to the gateway login's secret" in out
    assert settings.CONFIRM_FLAG in out and rs.ACCOUNT not in out
    assert "update_function_configuration" not in built.lam.names()
    assert "put_role_policy" not in built.iam.names()


def test_apply_without_the_confirmation_is_refused_before_any_call(tmp_path, capsys):
    built = world()

    assert run(["--apply"], built, tmp_path) == 3

    assert "REFUSED" in capsys.readouterr().out
    assert not built.sts.calls and not built.lam.calls and not built.iam.calls


def test_the_grant_is_written_before_the_variable_moves(tmp_path, capsys):
    built = world(extra_variables={"OTHER": "kept", "REGION": "us-east-1"})
    order = []
    original = built.update
    built.update = lambda FunctionName, **kw: (order.append("lambda"), original(
        FunctionName, **kw))[1]
    built.lam.answers["update_function_configuration"] = built.update
    put = built.iam.put_role_policy
    built.iam.put_role_policy = lambda **kw: (order.append("iam"), put(**kw))[1]

    assert run(APPLY, built, tmp_path) == 0

    assert order == ["iam", "lambda"]
    document = built.iam.policies[(SEMANTIC_ROLE, POLICY)]
    assert document == semantic.grant_document(GATEWAY)
    update = built.lam.args("update_function_configuration")[0]
    assert update["Environment"]["Variables"] == {
        "AURORA_SECRET_ARN": GATEWAY, "OTHER": "kept", "REGION": "us-east-1"}
    assert update["RevisionId"] == "rev-1"
    assert "OK  the meridian-semantic-trip-search Lambda is at the gateway stage" in (
        capsys.readouterr().out)


def test_a_second_apply_changes_nothing(tmp_path, capsys):
    built = world()
    run(APPLY, built, tmp_path)
    puts, updates = len(built.iam.args("put_role_policy")), len(
        built.lam.args("update_function_configuration"))

    assert run(APPLY, built, tmp_path) == 0

    assert len(built.iam.args("put_role_policy")) == puts
    assert len(built.lam.args("update_function_configuration")) == updates


@pytest.mark.parametrize("environment", [
    {"Error": {"ErrorCode": "KMSAccessDenied", "Message": "x"}},
    {},
    None,
])
def test_an_environment_that_cannot_be_read_in_full_is_refused_unchanged(
        tmp_path, capsys, environment):
    built = world()
    if environment is None:
        del built.configs[semantic.FUNCTION]["Environment"]
    else:
        built.configs[semantic.FUNCTION]["Environment"] = environment

    assert run(APPLY, built, tmp_path) == 2

    assert "cannot be read in full" in capsys.readouterr().err
    assert "update_function_configuration" not in built.lam.names()
    assert "put_role_policy" not in built.iam.names()


def test_a_variable_that_names_neither_login_is_refused(tmp_path, capsys):
    built = world(semantic_env=f"arn:aws:secretsmanager:{rs.REGION}:{rs.ACCOUNT}:secret:other-x")

    assert run(APPLY, built, tmp_path) == 2

    assert "neither the master nor the gateway" in capsys.readouterr().err
    assert not built.iam.args("put_role_policy")


def test_a_policy_of_that_name_that_is_not_ours_is_never_overwritten(tmp_path, capsys):
    other = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "*", "Resource": "*"}]}
    built = world(policies={(SEMANTIC_ROLE, POLICY): other})

    assert run(APPLY, built, tmp_path) == 2

    assert "did not write" in capsys.readouterr().err
    assert built.iam.policies[(SEMANTIC_ROLE, POLICY)] == other
    assert "update_function_configuration" not in built.lam.names()


def test_a_role_in_another_account_is_refused(tmp_path, capsys):
    built = world()
    built.configs[semantic.FUNCTION]["Role"] = "arn:aws:iam::999999999999:role/x"

    assert run(APPLY, built, tmp_path) == 2

    err = capsys.readouterr().err
    assert "not in this account" in err and "999999999999" not in err
    assert not built.iam.args("put_role_policy")


def test_a_missing_function_is_refused(tmp_path, capsys):
    built = world()
    built.lam.failures["get_function_configuration"] = [
        client_error("ResourceNotFoundException", "none", "GetFunctionConfiguration")]

    assert run(APPLY, built, tmp_path) == 2

    assert "does not exist" in capsys.readouterr().err


def test_the_wrong_account_is_refused_before_any_lambda_or_iam_call(tmp_path, capsys):
    built = world()
    built.sts.answers["get_caller_identity"] = {"Account": "999999999999"}

    assert run(APPLY, built, tmp_path) == 2

    assert not built.lam.calls and not built.iam.calls
    assert "999999999999" not in capsys.readouterr().err


def test_an_environment_that_differs_after_the_update_fails_the_command(tmp_path, capsys):
    built = world()
    built.lam.answers["update_function_configuration"] = lambda FunctionName, **kw: None

    assert run(APPLY, built, tmp_path) == 2

    assert "not the one sent" in capsys.readouterr().err


def test_going_back_restores_the_master_value_and_keeps_the_grant(tmp_path, capsys):
    built = world(semantic_env=GATEWAY, policies={
        (SEMANTIC_ROLE, POLICY): semantic.grant_document(GATEWAY)})

    assert run(["--to", "master", *APPLY], built, tmp_path) == 0

    update = built.lam.args("update_function_configuration")[0]
    assert update["Environment"]["Variables"]["AURORA_SECRET_ARN"] == MASTER
    assert not built.iam.args("delete_role_policy")


def test_remove_grant_deletes_only_the_marked_policy_after_the_variable_is_back(tmp_path):
    built = world(semantic_env=GATEWAY, policies={
        (SEMANTIC_ROLE, POLICY): semantic.grant_document(GATEWAY)})

    assert run(["--to", "master", "--remove-grant", *APPLY], built, tmp_path) == 0

    assert (SEMANTIC_ROLE, POLICY) not in built.iam.policies
    assert built.iam.args("delete_role_policy") == [
        {"RoleName": SEMANTIC_ROLE, "PolicyName": POLICY}]


def test_removing_an_unmarked_policy_is_refused(tmp_path, capsys):
    other = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "*", "Resource": "*"}]}
    built = world(semantic_env=GATEWAY, policies={(SEMANTIC_ROLE, POLICY): other})

    assert run(["--to", "master", "--remove-grant", *APPLY], built, tmp_path) == 2

    assert built.iam.policies[(SEMANTIC_ROLE, POLICY)] == other


def test_remove_grant_without_going_back_is_a_refusal(tmp_path, capsys):
    built = world()

    assert run(["--remove-grant", *APPLY], built, tmp_path) == 3

    assert not built.lam.calls


def test_abbreviated_flags_are_a_usage_error(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        run(["--rem"], world(), tmp_path)

    assert stopped.value.code == 3


def test_a_failing_write_is_masked_and_leaves_the_variable_alone(tmp_path, capsys):
    built = world()
    built.iam.put_role_policy = lambda **kw: (_ for _ in ()).throw(client_error(
        "AccessDenied", f"User arn:aws:iam::{rs.ACCOUNT}:user/x is not allowed"))

    assert run(APPLY, built, tmp_path) == 2

    err = capsys.readouterr().err
    assert rs.ACCOUNT not in err and "Traceback" not in err
    assert "update_function_configuration" not in built.lam.names()


def test_the_calls_match_the_botocore_models(tmp_path):
    built = world()
    run(APPLY, built, tmp_path)

    assert violations("iam", built.iam.calls) == []
    assert violations("lambda", built.lam.calls) == []
