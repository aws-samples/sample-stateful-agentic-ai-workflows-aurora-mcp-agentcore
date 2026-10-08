"""`release_identity.py gateway`: a dry run by default, guarded, complete and read back."""

from __future__ import annotations

import json
import stat
from unittest.mock import Mock

import pytest

from scripts import release_identity
from scripts.identity_release import gateway_release as gw
from scripts.identity_release import settings
from tests import release_support as rs
from tests.gateway_release_support import Control, current, iam_client, lambda_client
from tests.test_release_identity_cli import NOW, env

FLAG = settings.CONFIRM_FLAG


class GatewayWorld:
    """Recording fake clients for the services the gateway command may use."""

    def __init__(self, before="iam", *after, account=rs.ACCOUNT, permitted=False, installed=False):
        self.sts = Mock()
        self.sts.get_caller_identity.return_value = {"Account": account}
        self.control = Control(current(before), *(after or (current("jwt"),)))
        self.iam = iam_client(installed=installed)
        self.lam = lambda_client(permitted=permitted)
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


def run(argv, world, tmp_path, environment=None, with_proof=True):
    proof = tmp_path / "proof.json"
    if with_proof:
        proof.write_text(json.dumps(rs.receipt(NOW)))
    deps = release_identity.Dependencies(
        env=environment or env(), session=world.session, now=lambda: NOW,
        head_sha=lambda: rs.SHA, proof_path=proof, release_dir=tmp_path / "release",
        sleep=lambda seconds: None)
    return release_identity.main(argv, deps)


def test_the_dry_run_prints_before_and_after_and_writes_nothing(tmp_path, capsys):
    world = GatewayWorld()

    assert run(["gateway"], world, tmp_path) == 0

    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "before: authorizerType AWS_IAM" in out and "after : authorizerType CUSTOM_JWT" in out
    assert f"after : allowedClients ['{rs.CLIENT}']" in out
    assert "before: interceptor none" in out and "after : passRequestHeaders True" in out
    assert "resent unchanged: name, roleArn, description" in out
    assert "would change: Gateway: authorizer is AWS_IAM" in out
    assert "would ensure: Gateway role" in out and "would ensure: Interceptor Lambda" in out
    assert FLAG in out and rs.ACCOUNT not in out
    assert world.control.names() == ["get_gateway"] and world.iam.calls == []
    assert world.lam.names() == ["get_function"]
    assert not (tmp_path / "release").exists()


def test_the_dry_run_with_a_blocker_says_what_would_refuse_and_exits_one(tmp_path, capsys):
    world = GatewayWorld()

    assert run(["gateway"], world, tmp_path, with_proof=False) == 1

    assert "BLOCKED  Backend login proof: none recorded" in capsys.readouterr().out
    assert "update_gateway" not in world.control.names()


def test_the_dry_run_says_when_nothing_would_change(tmp_path, capsys):
    world = GatewayWorld("jwt", permitted=True, installed=True)

    assert run(["gateway"], world, tmp_path) == 0

    assert "already matches" in capsys.readouterr().out


def test_apply_without_the_confirmation_exits_three_before_any_client(tmp_path, capsys):
    world = GatewayWorld()

    assert run(["gateway", "--apply"], world, tmp_path) == 3

    assert world.built == [] and FLAG in capsys.readouterr().out


def test_an_abbreviated_flag_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        run(["gateway", "--app", FLAG], GatewayWorld(), tmp_path)

    assert stopped.value.code == 3


def test_credentials_for_another_account_stop_before_any_other_client(tmp_path, capsys):
    world = GatewayWorld(account="999999999999")

    assert run(["gateway", "--apply", FLAG], world, tmp_path) == 2

    captured = capsys.readouterr()
    assert world.built == ["sts"] and "999999999999" not in captured.err
    assert "Traceback" not in captured.err


def test_apply_grants_updates_and_reads_back(tmp_path, capsys):
    world = GatewayWorld()

    assert run(["gateway", "--apply", FLAG], world, tmp_path) == 0

    out = capsys.readouterr().out
    assert "put_role_policy" in world.iam.names()
    assert "update_gateway" in world.control.names() and "add_permission" in world.lam.names()
    assert "Gateway: updated to jwt" in out and "OK  the Gateway reports jwt" in out
    record = tmp_path / "release" / gw.OUTPUT_NAME
    assert stat.S_IMODE(record.stat().st_mode) == 0o600


def test_apply_refuses_before_any_write_when_a_precondition_fails(tmp_path, capsys):
    world = GatewayWorld()

    assert run(["gateway", "--apply", FLAG], world, tmp_path, with_proof=False) == 2

    err = capsys.readouterr().err
    assert "precondition(s) not met" in err and "Backend login proof" in err
    assert world.iam.calls == [] and "update_gateway" not in world.control.names()
    assert "add_permission" not in world.lam.names()


def test_apply_refuses_when_the_gateway_cannot_be_read_in_full(tmp_path, capsys):
    world = GatewayWorld()
    world.control.before = {"status": "READY"}

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
    world = GatewayWorld("iam", current("iam"))

    assert run(["gateway", "--apply", FLAG], world, tmp_path) == 1

    assert "DRIFT  Gateway: authorizer is AWS_IAM" in capsys.readouterr().out


def test_a_failed_update_exits_two_with_a_masked_reason(tmp_path, capsys):
    failed = current("iam", status="UPDATE_UNSUCCESSFUL", statusReasons=[f"{rs.ACCOUNT} no"])
    world = GatewayWorld("iam", failed)

    assert run(["gateway", "--apply", FLAG], world, tmp_path) == 2

    err = capsys.readouterr().err
    assert "UPDATE_UNSUCCESSFUL" in err and rs.ACCOUNT not in err


def test_only_grant_writes_the_grants_and_leaves_the_gateway(tmp_path, capsys):
    world = GatewayWorld()

    assert run(["gateway", "--only", "grant", "--apply", FLAG], world, tmp_path) == 0

    assert "update_gateway" not in world.control.names()
    assert "put_role_policy" in world.iam.names() and "add_permission" in world.lam.names()


def test_only_grant_is_refused_for_a_design_without_the_interceptor(tmp_path, capsys):
    world = GatewayWorld()

    code = run(["gateway", "--only", "grant", "--apply", FLAG], world, tmp_path,
               env(MERIDIAN_GATEWAY_ENFORCEMENT="cedar"))

    assert code == 2 and "needs the jwt mode" in capsys.readouterr().err


def test_to_iam_is_the_rollback_and_needs_no_pool_or_proof(tmp_path, capsys):
    world = GatewayWorld("jwt", current("iam"), permitted=True, installed=True)
    environment = {k: v for k, v in env().items() if not k.startswith("MERIDIAN_COGNITO")}

    code = run(["gateway", "--to", "iam", "--apply", FLAG], world, tmp_path, environment,
               with_proof=False)

    assert code == 0
    assert world.iam.names().count("delete_role_policy") == 1
    assert "Gateway: updated to iam" in capsys.readouterr().out
