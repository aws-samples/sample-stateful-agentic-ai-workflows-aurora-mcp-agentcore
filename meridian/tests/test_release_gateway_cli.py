"""`release_identity.py gateway`: a dry run by default, guarded, complete and read back."""

from __future__ import annotations

import stat
from unittest.mock import Mock

import pytest

from scripts import release_identity
from scripts.identity_release import gateway_release as gw
from scripts.identity_release import settings
from tests import release_support as rs
from tests.gateway_release_support import Control, bare, current, iam_client, lambda_client
from tests.test_release_identity_cli import NOW, env

FLAG = settings.CONFIRM_FLAG


class GatewayWorld:
    """Recording fake clients for the services the gateway command may use."""

    def __init__(self, before="bare", *after, account=rs.ACCOUNT, installed=False, mode="jwt"):
        self.sts = Mock()
        self.sts.get_caller_identity.return_value = {"Account": account}
        first = {"bare": bare(mode), "attached": current(mode), "iam": current("iam")}[before]
        self.control = Control(first, *(after or (current("jwt"),)))
        self.iam = iam_client(installed=installed)
        self.lam = lambda_client()
        self.cfn = Mock()
        self.cfn.describe_stacks.return_value = {"Stacks": [{"Outputs": rs.identity_outputs()}]}
        self.built: list[str] = []

    def session(self, region):
        clients = {"sts": self.sts, "bedrock-agentcore-control": self.control, "iam": self.iam,
                   "lambda": self.lam, "cloudformation": self.cfn}

        def client(name, **kwargs):
            self.built.append(name)
            return clients[name]
        return Mock(client=client)


def run(argv, world, tmp_path, environment=None):
    deps = release_identity.Dependencies(
        env=environment or env(), session=world.session, now=lambda: NOW,
        head_sha=lambda: rs.SHA, proof_path=tmp_path / "no-proof.json",
        release_dir=tmp_path / "release", sleep=lambda seconds: None)
    return release_identity.main(argv, deps)


def test_the_dry_run_prints_before_and_after_and_writes_nothing(tmp_path, capsys):
    world = GatewayWorld()

    assert run(["gateway"], world, tmp_path) == 0

    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "before: authorizerType CUSTOM_JWT" in out and "after : authorizerType CUSTOM_JWT" in out
    assert "before: interceptor none" in out and "after : passRequestHeaders True" in out
    assert "resent unchanged: name, roleArn, description" in out
    assert "authorizerType, authorizerConfiguration" in out
    assert "would ensure: Gateway role" in out
    assert FLAG in out and rs.ACCOUNT not in out
    assert "--to jwt --apply" in out
    assert world.control.names() == ["list_gateways", "get_gateway"] and world.iam.calls == []
    assert world.lam.names() == ["get_function"]
    assert not (tmp_path / "release").exists()


def test_the_gateway_is_found_by_name_whatever_the_env_url_says(tmp_path, capsys):
    world = GatewayWorld()
    stale = env(AGENTCORE_GATEWAY_URL="https://gone-gw.gateway.bedrock-agentcore."
                                      f"{rs.REGION}.amazonaws.com/mcp")

    assert run(["gateway"], world, tmp_path, stale) == 0

    assert world.control.args("get_gateway") == [{"gatewayIdentifier": rs.GATEWAY_ID}]


def test_the_dry_run_with_a_blocker_says_what_would_refuse_and_exits_two(tmp_path, capsys):
    world = GatewayWorld()
    world.cfn.describe_stacks.return_value = {"Stacks": [{"Outputs": []}]}

    assert run(["gateway"], world, tmp_path) == 2

    assert "BLOCKED  Identity stack: has no output" in capsys.readouterr().out
    assert "update_gateway" not in world.control.names()


def test_the_dry_run_says_when_nothing_would_change(tmp_path, capsys):
    world = GatewayWorld("attached", installed=True)

    assert run(["gateway"], world, tmp_path) == 0

    assert "interceptor" in capsys.readouterr().out


def test_apply_without_the_confirmation_exits_three_before_any_client(tmp_path, capsys):
    world = GatewayWorld()

    assert run(["gateway", "--apply"], world, tmp_path) == 3

    assert world.built == [] and FLAG in capsys.readouterr().out


def test_an_abbreviated_flag_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        run(["gateway", "--app", FLAG], GatewayWorld(), tmp_path)

    assert stopped.value.code == 3


def test_the_removed_only_choice_move_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        run(["gateway", "--only", "move"], GatewayWorld(), tmp_path)

    assert stopped.value.code == 3


def test_credentials_for_another_account_stop_before_any_other_client(tmp_path, capsys):
    world = GatewayWorld(account="999999999999")

    assert run(["gateway", "--apply", FLAG], world, tmp_path) == 2

    captured = capsys.readouterr()
    assert world.built == ["sts"] and "999999999999" not in captured.err
    assert "Traceback" not in captured.err


def test_apply_grants_attaches_and_reads_back(tmp_path, capsys):
    world = GatewayWorld()

    assert run(["gateway", "--apply", FLAG], world, tmp_path) == 0

    out = capsys.readouterr().out
    assert "put_role_policy" in world.iam.names()
    assert "update_gateway" in world.control.names()
    assert world.lam.names() == ["get_function"]
    assert "Gateway: interceptor attached" in out
    assert "OK  the Gateway has the interceptor and its role the grant" in out
    record = tmp_path / "release" / gw.OUTPUT_NAME
    assert stat.S_IMODE(record.stat().st_mode) == 0o600


def test_apply_refuses_before_any_write_when_a_precondition_fails(tmp_path, capsys):
    world = GatewayWorld()
    world.control.before = bare("jwt", policyEngineConfiguration={
        "arn": rs.ENGINE_ARN, "mode": "LOG_ONLY"})

    assert run(["gateway", "--apply", FLAG], world, tmp_path) == 2

    err = capsys.readouterr().err
    assert "precondition(s) not met" in err and "ENFORCE" in err
    assert world.iam.calls == [] and "update_gateway" not in world.control.names()


def test_apply_refuses_when_no_gateway_has_the_name(tmp_path, capsys):
    world = GatewayWorld()
    world.control.list_gateways = lambda **kwargs: {"items": []}

    assert run(["gateway", "--apply", FLAG], world, tmp_path) == 2

    err = capsys.readouterr().err
    assert "no Gateway named meridianv2-meridian-aurora-jwt exists" in err
    assert world.iam.calls == []


def test_apply_refuses_when_the_gateway_cannot_be_read_in_full(tmp_path, capsys):
    world = GatewayWorld()
    world.control.before = {"status": "READY", "name": world.control.before["name"],
                            "gatewayId": rs.GATEWAY_ID}

    assert run(["gateway", "--apply", FLAG], world, tmp_path) == 2

    assert "cannot be read in full" in capsys.readouterr().err
    assert world.iam.calls == []


def test_apply_reports_a_grant_that_vanished_as_drift(tmp_path, capsys):
    world = GatewayWorld()
    real_get = world.iam.get_role_policy
    reads = []

    def vanish(**kwargs):
        reads.append(kwargs)
        if len(reads) > 1:
            world.iam.document = None
        return real_get(**kwargs)
    world.iam.get_role_policy = vanish

    assert run(["gateway", "--apply", FLAG], world, tmp_path) == 1

    assert "DRIFT  Gateway role" in capsys.readouterr().out


def test_an_update_the_gateway_does_not_show_is_drift_with_exit_one(tmp_path, capsys):
    world = GatewayWorld("bare", bare("jwt"))

    assert run(["gateway", "--apply", FLAG], world, tmp_path) == 1

    assert "DRIFT  Gateway: no request interceptor is attached" in capsys.readouterr().out


def test_a_failed_update_exits_two_with_a_masked_reason(tmp_path, capsys):
    failed = current("jwt", status="UPDATE_UNSUCCESSFUL", statusReasons=[f"{rs.ACCOUNT} no"])
    world = GatewayWorld("bare", failed)

    assert run(["gateway", "--apply", FLAG], world, tmp_path) == 2

    err = capsys.readouterr().err
    assert "UPDATE_UNSUCCESSFUL" in err and rs.ACCOUNT not in err


def test_only_grant_writes_the_grants_and_leaves_the_gateway(tmp_path, capsys):
    world = GatewayWorld()

    assert run(["gateway", "--only", "grant", "--apply", FLAG], world, tmp_path) == 0

    assert "update_gateway" not in world.control.names()
    assert "put_role_policy" in world.iam.names() and world.lam.names() == ["get_function"]
    assert "OK  the grant is in place" in capsys.readouterr().out


def test_only_attach_without_the_grant_is_refused(tmp_path, capsys):
    world = GatewayWorld()

    assert run(["gateway", "--only", "attach", "--apply", FLAG], world, tmp_path) == 2

    assert "cannot invoke the interceptor yet" in capsys.readouterr().err
    assert "update_gateway" not in world.control.names()


def test_only_grant_is_refused_for_a_design_without_the_interceptor(tmp_path, capsys):
    world = GatewayWorld()

    code = run(["gateway", "--only", "grant", "--apply", FLAG], world, tmp_path,
               env(MERIDIAN_GATEWAY_ENFORCEMENT="cedar"))

    assert code == 2 and "--only grant needs the jwt mode" in capsys.readouterr().err


def test_the_cedar_only_design_attaches_nothing(tmp_path, capsys):
    world = GatewayWorld()

    code = run(["gateway", "--apply", FLAG], world, tmp_path,
               env(MERIDIAN_GATEWAY_ENFORCEMENT="cedar"))

    assert code == 0
    assert "update_gateway" not in world.control.names() and world.iam.calls == []
    assert "needs no interceptor" in capsys.readouterr().out


def test_to_iam_cleans_the_old_gateway_and_needs_no_pool_or_proof(tmp_path, capsys):
    world = GatewayWorld("iam", installed=True)
    environment = {k: v for k, v in env().items() if not k.startswith("MERIDIAN_COGNITO")}

    code = run(["gateway", "--to", "iam", "--apply", FLAG], world, tmp_path, environment)

    assert code == 0
    assert world.control.args("get_gateway")[0] == {"gatewayIdentifier": rs.GATEWAY_ID}
    assert world.iam.names().count("delete_role_policy") == 1
    assert "update_gateway" not in world.control.names() and "cloudformation" not in world.built
    assert "OK  the Gateway has no interceptor and its role no grant" in capsys.readouterr().out


def test_revoke_on_the_jwt_gateway_detaches_then_removes_the_grant(tmp_path, capsys):
    world = GatewayWorld("attached", bare("jwt"), installed=True)

    code = run(["gateway", "--only", "revoke", "--apply", FLAG], world, tmp_path)

    assert code == 0
    assert "update_gateway" in world.control.names()
    assert world.iam.names().count("delete_role_policy") == 1
    assert "Gateway: interceptor detached" in capsys.readouterr().out


def test_the_dry_run_for_revoke_names_the_grant_it_would_remove(tmp_path, capsys):
    world = GatewayWorld("iam", installed=True)

    assert run(["gateway", "--to", "iam"], world, tmp_path) == 0

    out = capsys.readouterr().out
    assert "would remove: Gateway role" in out and gw.INVOKE_POLICY_NAME in out
    assert "--to iam --apply" in out
    assert world.iam.calls == []


def test_the_findings_are_printed_before_the_record_is_written(tmp_path, capsys, monkeypatch):
    world = GatewayWorld("bare", bare("jwt"))

    def unwritable(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(gw, "record", unwritable)

    code = run(["gateway", "--apply", FLAG], world, tmp_path)

    captured = capsys.readouterr()
    assert code == 1
    assert "DRIFT  Gateway: no request interceptor is attached" in captured.out
    assert "WARNING  the change record was not written" in captured.out
    assert "Traceback" not in captured.err


def test_a_clean_apply_whose_record_cannot_be_written_still_exits_zero(
        tmp_path, capsys, monkeypatch):
    world = GatewayWorld()

    def unwritable(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(gw, "record", unwritable)

    assert run(["gateway", "--apply", FLAG], world, tmp_path) == 0

    captured = capsys.readouterr()
    assert "OK  the Gateway has the interceptor" in captured.out
    assert "WARNING  the change record was not written" in captured.out


def test_the_dry_run_for_only_grant_shows_the_grant_and_not_a_gateway_change(
        tmp_path, capsys):
    world = GatewayWorld()

    assert run(["gateway", "--only", "grant"], world, tmp_path) == 0

    out = capsys.readouterr().out
    assert "would ensure: Gateway role" in out and "resent unchanged" not in out
    assert "--only grant --apply" in out


def test_the_dry_run_for_only_attach_does_not_promise_the_grant(tmp_path, capsys):
    world = GatewayWorld()

    assert run(["gateway", "--only", "attach"], world, tmp_path) == 0

    out = capsys.readouterr().out
    assert "after : passRequestHeaders True" in out
    assert "would ensure" not in out and "--only attach --apply" in out


def test_only_grant_reads_the_grant_back_and_reports_a_vanished_one(tmp_path, capsys):
    world = GatewayWorld()
    real_put = world.iam.put_role_policy

    def put_then_lose(**kwargs):
        real_put(**kwargs)
        world.iam.document = None
        return {}
    world.iam.put_role_policy = put_then_lose

    code = run(["gateway", "--only", "grant", "--apply", FLAG], world, tmp_path)

    assert code == 1 and "DRIFT  Gateway role" in capsys.readouterr().out
