"""`release_identity.py check` reads every hop back and exits non-zero on any drift."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from scripts import release_identity
from scripts.identity_release import settings
from tests import release_support as rs

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
CLUSTER = f"arn:aws:rds:{rs.REGION}:{rs.ACCOUNT}:cluster:meridian"
SERVICE_ARN = f"arn:aws:apprunner:{rs.REGION}:{rs.ACCOUNT}:service/meridian-web/abc123"
IDENTITY_OUTPUTS = [
    {"OutputKey": "UserPoolId", "OutputValue": rs.POOL},
    {"OutputKey": "AppClientId", "OutputValue": rs.CLIENT},
    {"OutputKey": "HostedUiDomain", "OutputValue": "d.auth.us-east-1.amazoncognito.com"},
    {"OutputKey": "Issuer", "OutputValue": settings.cognito_settings(rs.COGNITO_ENV).issuer},
]


def env(**extra):
    return {
        "AURORA_CLUSTER_ARN": CLUSTER, "MERIDIAN_AGENTCORE_AUTH": "jwt", **rs.COGNITO_ENV,
        "AGENTCORE_GATEWAY_URL": f"https://{rs.GATEWAY_ID}.gateway.bedrock-agentcore."
                                 f"{rs.REGION}.amazonaws.com/mcp",
        "AGENTCORE_RUNTIME_ARN": f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:runtime/"
                                 + rs.RUNTIME_IDS["MeridianConcierge"],
        "AGENTCORE_WORKFLOW_RUNTIME_ARN": f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:"
                                          "runtime/" + rs.RUNTIME_IDS["MeridianWorkflow"],
        **extra,
    }


class World:
    """Recording fake clients for every service `check` may read."""

    def __init__(self, mode="jwt", account=rs.ACCOUNT, service=None):
        self.sts = Mock()
        self.sts.get_caller_identity.return_value = {"Account": account}
        self.control = Mock()
        self.control.get_gateway.return_value = rs.gateway(mode)
        runtimes = {rs.RUNTIME_IDS[n]: rs.runtime(n, mode) for n in rs.RUNTIME_IDS}
        self.control.get_agent_runtime.side_effect = lambda agentRuntimeId: runtimes[agentRuntimeId]
        names = rs.BASE_POLICIES + ([rs.BINDING_POLICY] if mode == "jwt" else [])
        self.control.list_policies.return_value = {"policies": rs.policies(names)}
        self.cfn = Mock()
        self.cfn.describe_stacks.return_value = {"Stacks": [{"Outputs": IDENTITY_OUTPUTS}]}
        self.apprunner = Mock()
        self.apprunner.describe_service.return_value = {"Service": service or {}}

    def session(self, region):
        clients = {"sts": self.sts, "bedrock-agentcore-control": self.control,
                   "cloudformation": self.cfn, "apprunner": self.apprunner}
        return Mock(client=lambda name, **kwargs: clients[name])


def proof(tmp_path, ok=True):
    path = tmp_path / "backend-login-proof.json"
    path.write_text(json.dumps({"ok": ok, "login": "meridian_backend", "at": NOW.isoformat()}))
    return path


def run(argv, world, tmp_path, environment=None, with_proof=True):
    deps = release_identity.Dependencies(
        env=environment or env(), session=world.session, now=lambda: NOW,
        proof_path=proof(tmp_path) if with_proof else tmp_path / "none.json")
    return release_identity.main(argv, deps)


def test_a_release_that_matches_its_mode_reports_ok_and_exits_zero(tmp_path, capsys):
    assert run(["check"], World("jwt"), tmp_path) == 0

    assert "OK" in capsys.readouterr().out


def test_every_drifted_hop_is_printed_and_the_exit_is_one(tmp_path, capsys):
    assert run(["check"], World("iam"), tmp_path) == 1

    out = capsys.readouterr().out
    assert "DRIFT  Gateway: authorizer is AWS_IAM, expected CUSTOM_JWT" in out
    assert "DRIFT  Runtime MeridianConcierge: has no JWT authorizer" in out
    assert "DRIFT  Runtime MeridianWorkflow: " in out
    assert "OK" not in out


def test_expect_iam_reads_the_baseline_even_when_the_env_says_jwt(tmp_path, capsys):
    assert run(["check", "--expect", "iam"], World("iam"), tmp_path, with_proof=False) == 0

    assert "every hop reports iam" in capsys.readouterr().out


def test_the_jwt_check_also_wants_the_identity_stack_and_the_backend_login_proof(tmp_path, capsys):
    world = World("jwt")
    world.cfn.describe_stacks.return_value = {"Stacks": [{"Outputs": []}]}

    assert run(["check"], world, tmp_path, with_proof=False) == 1

    out = capsys.readouterr().out
    assert "Identity stack: has no output" in out
    assert "Backend login proof: none recorded" in out


def test_credentials_for_another_account_stop_before_any_read(tmp_path):
    world = World("jwt", account="999999999999")

    with pytest.raises(SystemExit) as stopped:
        run(["check"], world, tmp_path)

    assert "<acct>" in str(stopped.value) and "999999999999" not in str(stopped.value)
    world.control.get_gateway.assert_not_called()


def test_a_missing_setting_exits_two_and_says_which(tmp_path, capsys):
    environment = env()
    del environment["AGENTCORE_WORKFLOW_RUNTIME_ARN"]

    assert run(["check"], World("jwt"), tmp_path, environment) == 2

    assert "AGENTCORE_WORKFLOW_RUNTIME_ARN" in capsys.readouterr().err


def test_a_bad_mode_exits_two(tmp_path, capsys):
    assert run(["check"], World("jwt"), tmp_path, env(MERIDIAN_AGENTCORE_AUTH="true")) == 2

    assert "MERIDIAN_AGENTCORE_AUTH" in capsys.readouterr().err


def live_service(variables, secrets=None):
    return {"SourceConfiguration": {"ImageRepository": {"ImageConfiguration": {
        "RuntimeEnvironmentVariables": variables, "RuntimeEnvironmentSecrets": secrets or {}}}}}


def test_the_live_service_environment_is_checked_when_the_service_is_named(tmp_path, capsys):
    good = World("jwt", service=live_service(rs.jwt_service_variables()))
    assert run(["check", "--service-arn", SERVICE_ARN], good, tmp_path) == 0
    good.apprunner.describe_service.assert_called_once_with(ServiceArn=SERVICE_ARN)

    bad = World("jwt", service=live_service(
        rs.jwt_service_variables(), {"MERIDIAN_API_TOKEN": "arn:secret"}))
    assert run(["check", "--service-arn", SERVICE_ARN], bad, tmp_path) == 1
    assert "Service: MERIDIAN_API_TOKEN is still a secret reference" in capsys.readouterr().out


def test_without_a_service_arn_the_service_is_reported_as_not_checked(tmp_path, capsys):
    assert run(["check"], World("jwt"), tmp_path) == 0

    assert "not checked" in capsys.readouterr().out


def test_the_cedar_only_design_expects_no_interceptor(tmp_path, capsys):
    world = World("jwt")
    world.control.get_gateway.return_value = rs.gateway("jwt", settings.CEDAR)

    assert run(["check"], world, tmp_path, env(MERIDIAN_GATEWAY_ENFORCEMENT="cedar")) == 0
    assert run(["check"], world, tmp_path, env()) == 1
    assert "no request interceptor" in capsys.readouterr().out


def test_an_aws_read_failure_exits_two_without_a_traceback_or_an_account_id(tmp_path, capsys):
    from botocore.exceptions import ClientError

    world = World("jwt")
    denied = ClientError(
        {"Error": {"Code": "AccessDeniedException",
                   "Message": f"User arn:aws:iam::{rs.ACCOUNT}:user/x may not read"}},
        "GetGateway")
    world.control.get_gateway.side_effect = denied

    assert run(["check"], world, tmp_path) == 2

    err = capsys.readouterr().err
    assert "AccessDeniedException" in err and "Traceback" not in err and rs.ACCOUNT not in err


def test_check_makes_only_read_calls(tmp_path):
    world = World("jwt")

    run(["check"], world, tmp_path)

    called = {c[0] for mock in (world.control, world.cfn, world.apprunner, world.sts)
              for c in mock.method_calls}
    assert called <= {"get_gateway", "get_agent_runtime", "list_policies", "describe_stacks",
                      "describe_service", "get_caller_identity"}
