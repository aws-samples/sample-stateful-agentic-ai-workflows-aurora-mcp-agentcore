"""`release_identity.py lambdas` checks the Lambdas read-only and restarts the holds Lambda."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts import release_identity
from scripts.identity_release import lambda_release, settings
from tests import release_support as rs
from tests.aws_recorders import client_error
from tests.lambda_release_support import GATEWAY, HOLDS_ROLE, MASTER, World, policy

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
CLUSTER = f"arn:aws:rds:{rs.REGION}:{rs.ACCOUNT}:cluster:meridian"
RESTART = ["lambdas", "--restart-holds"]


def env(**extra):
    return {
        "AURORA_CLUSTER_ARN": CLUSTER, "AURORA_SECRET_ARN": MASTER,
        "AURORA_GATEWAY_SECRET_ARN": GATEWAY,
        "AGENTCORE_GATEWAY_URL": f"https://{rs.GATEWAY_ID}.gateway.bedrock-agentcore."
                                 f"{rs.REGION}.amazonaws.com/mcp",
        "AGENTCORE_RUNTIME_ARN": f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:runtime/"
                                 + rs.RUNTIME_IDS["MeridianConcierge"],
        "AGENTCORE_WORKFLOW_RUNTIME_ARN": f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:"
                                          "runtime/" + rs.RUNTIME_IDS["MeridianWorkflow"],
        **extra,
    }


def run(argv, world, tmp_path, environment=None):
    deps = release_identity.Dependencies(
        env=environment or env(), session=world.session, now=lambda: NOW,
        head_sha=lambda: rs.SHA, proof_path=tmp_path / "none.json",
        release_dir=tmp_path / "release", sleep=lambda seconds: None)
    return release_identity.main(argv, deps)


def test_the_lambdas_command_reports_ok_at_the_stage_the_secrets_are_at(tmp_path, capsys):
    assert run(["lambdas"], World(), tmp_path) == 0

    assert "OK  the Lambdas are at the gateway stage" in capsys.readouterr().out


def test_the_lambdas_command_lists_what_has_not_moved_and_exits_one(tmp_path, capsys):
    world = World(ssm=MASTER, semantic_env=MASTER)

    assert run(["lambdas", "--expect", "gateway"], world, tmp_path) == 1

    out = capsys.readouterr().out
    assert "DRIFT  SSM /meridian/aurora/secret_arn:" in out
    assert "MANUAL STEP" in out and "publish_gateway_parameters.py --gateway-login" in out


def test_the_tightened_stage_fails_while_a_role_can_still_read_the_master_secret(
        tmp_path, capsys):
    world = World()
    world.roles[HOLDS_ROLE] = policy(GATEWAY, MASTER)

    assert run(["lambdas", "--expect", "tightened"], world, tmp_path) == 1

    assert "still can read the master login's secret" in capsys.readouterr().out


def test_the_read_back_never_writes_and_checks_the_account_first(tmp_path):
    world = World()

    run(["lambdas", "--expect", "tightened"], world, tmp_path)

    assert world.sts.names() == ["get_caller_identity"]
    assert "update_function_configuration" not in world.lam.names()
    assert set(world.lam.names()) == {"get_function_configuration"}


def test_credentials_for_another_account_stop_everything_with_exit_two(tmp_path, capsys):
    world = World()
    world.sts.answers["get_caller_identity"] = {"Account": "999999999999"}

    assert run(RESTART + ["--apply", settings.CONFIRM_FLAG], world, tmp_path) == 2

    assert not (world.lam.calls or world.ssm.calls or world.iam.calls or world.control.calls)
    err = capsys.readouterr().err
    assert "999999999999" not in err and "Traceback" not in err


def test_missing_secrets_in_the_env_exit_two_naming_the_keys(tmp_path, capsys):
    environment = env()
    del environment["AURORA_GATEWAY_SECRET_ARN"]

    assert run(["lambdas"], World(), tmp_path, environment) == 2

    assert "AURORA_GATEWAY_SECRET_ARN" in capsys.readouterr().err


def test_restarting_the_holds_lambda_is_a_dry_run_without_both_flags(tmp_path, capsys):
    world = World()

    assert run(RESTART, world, tmp_path) == 0

    assert "update_function_configuration" not in world.lam.names()
    out = capsys.readouterr().out
    assert "DRY RUN" in out and settings.CONFIRM_FLAG in out


def test_restarting_the_holds_lambda_needs_the_confirmation_to_apply(tmp_path, capsys):
    world = World()

    assert run(RESTART + ["--apply"], world, tmp_path) == 3

    assert "update_function_configuration" not in world.lam.names()
    assert not world.sts.calls and not world.lam.calls


def test_apply_without_restart_holds_is_refused(tmp_path, capsys):
    world = World()

    assert run(["lambdas", "--apply", settings.CONFIRM_FLAG], world, tmp_path) == 3

    assert "update_function_configuration" not in world.lam.names()
    assert "REFUSED" in capsys.readouterr().out


def test_the_confirmed_restart_changes_the_marker_on_the_holds_function(tmp_path, capsys):
    world = World()

    code = run(RESTART + ["--apply", settings.CONFIRM_FLAG], world, tmp_path)

    assert code == 0
    update = world.lam.args("update_function_configuration")[0]
    assert update["Environment"]["Variables"]["MERIDIAN_COLD_START"] == "20261008T120000Z"
    assert "restarted" in capsys.readouterr().out


def test_a_function_that_is_not_the_holds_function_exits_two_and_is_not_changed(
        tmp_path, capsys):
    world = World()
    other = f"arn:aws:lambda:{rs.REGION}:{rs.ACCOUNT}:function:someone-elses"
    world.control.answers["get_gateway_target"] = {"targetConfiguration": {"mcp": {"lambda": {
        "lambdaArn": other}}}}

    assert run(RESTART + ["--apply", settings.CONFIRM_FLAG], world, tmp_path) == 2

    assert "update_function_configuration" not in world.lam.names()
    assert "not an AgentCore" in capsys.readouterr().err


def test_the_dry_run_also_refuses_a_foreign_function(tmp_path, capsys):
    world = World()
    world.control.answers["get_gateway_target"] = {"targetConfiguration": {"mcp": {"lambda": {
        "lambdaArn": f"arn:aws:lambda:{rs.REGION}:999999999999:function:x"}}}}

    assert run(RESTART, world, tmp_path) == 2

    assert "999999999999" not in capsys.readouterr().err


def test_an_aws_failure_during_the_restart_is_masked_with_no_traceback(tmp_path, capsys):
    world = World()
    world.lam.failures["update_function_configuration"] = [
        client_error("AccessDeniedException", f"User arn:aws:iam::{rs.ACCOUNT}:user/x denied")]

    assert run(RESTART + ["--apply", settings.CONFIRM_FLAG], world, tmp_path) == 2

    err = capsys.readouterr().err
    assert "AccessDeniedException" in err and rs.ACCOUNT not in err and "Traceback" not in err


def test_abbreviated_flags_are_not_accepted(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        run(["lambdas", "--restart"], World(), tmp_path)

    assert stopped.value.code == 3


def test_an_unknown_stage_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        run(["lambdas", "--expect", "later"], World(), tmp_path)

    assert stopped.value.code == 3


def test_the_exit_codes_the_command_returns_are_the_documented_table():
    assert (lambda_release.OK, lambda_release.DRIFT, lambda_release.REFUSED) == (
        0, release_identity.EXIT_DRIFT, release_identity.EXIT_REFUSED)
