"""An IAM Runtime reaches the Gateway only while its role may call InvokeGateway.

The jwt deploy removes that statement from both Runtime roles and the UpdateAgentRuntime rollback
does not put it back. Only an IAM render deployed with `agentcore deploy -y` does, so `check`,
the snapshot and the rollback all read the roles.
"""

from __future__ import annotations


import pytest

from scripts import release_identity
from scripts.identity_release import restore_hops, runtime_roles
from tests import release_support as rs
from tests import snapshot_support as ss
from tests.aws_recorders import Recorder
from tests.test_release_identity_cli import NOW, env
from tests.test_release_rollback import context, go, released

GATEWAY_ARN = f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:gateway/{rs.GATEWAY_ID}"
OTHER_GATEWAY = f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:gateway/someone-else"
RUNTIMES = {name: rs.runtime(name, "iam") for name in rs.RUNTIME_IDS}


def iam_with(statement):
    document = {"Version": "2012-10-17", "Statement": [statement]}
    return Recorder({"list_role_policies": {"PolicyNames": ["p"]},
                     "get_role_policy": {"PolicyDocument": document},
                     "list_attached_role_policies": {"AttachedPolicies": []}})


def allow(action="bedrock-agentcore:InvokeGateway", resource=GATEWAY_ARN, effect="Allow"):
    return {"Effect": effect, "Action": action, "Resource": resource}


# ------------------------------------------------------------------- the reader


@pytest.mark.parametrize("statement", [
    allow(), allow("bedrock-agentcore:*"), allow(resource="*"),
    allow(resource=[OTHER_GATEWAY, GATEWAY_ARN]), allow(resource=f"{GATEWAY_ARN[:-8]}*")])
def test_a_role_that_may_invoke_this_gateway_has_no_finding(statement):
    assert runtime_roles.findings(iam_with(statement), RUNTIMES, GATEWAY_ARN) == []


@pytest.mark.parametrize("statement", [
    allow(resource=OTHER_GATEWAY), allow("bedrock-agentcore:InvokeAgentRuntime"),
    allow("s3:*")])
def test_a_role_without_the_grant_is_a_finding_per_runtime(statement):
    found = runtime_roles.findings(iam_with(statement), RUNTIMES, GATEWAY_ARN)

    assert [line.split(":")[0] for line in found] == [
        "Runtime MeridianConcierge", "Runtime MeridianWorkflow"]
    assert all("InvokeGateway" in line and "agentcore deploy -y" in line for line in found)


def test_a_role_with_no_policy_at_all_is_a_finding():
    iam = Recorder({"list_role_policies": {"PolicyNames": []},
                    "list_attached_role_policies": {"AttachedPolicies": []}})

    assert len(runtime_roles.findings(iam, RUNTIMES, GATEWAY_ARN)) == 2


def test_a_runtime_without_a_role_is_a_finding_not_a_crash():
    found = runtime_roles.findings(iam_with(allow()), {"MeridianConcierge": {}}, GATEWAY_ARN)

    assert found == ["Runtime MeridianConcierge: has no role to read, so InvokeGateway cannot be "
                     "checked"]


# ---------------------------------------------------------------------- check


class World:
    def __init__(self, statement=None):
        from tests.test_release_identity_cli import World as Base
        self.base = Base("iam")
        self.base.iam = iam_with(statement) if statement else Recorder({
            "list_role_policies": {"PolicyNames": []},
            "list_attached_role_policies": {"AttachedPolicies": []}})

    def session(self, region):
        return self.base.session(region)


def check(world, tmp_path, *argv):
    deps = release_identity.Dependencies(
        env=env(), session=world.session, now=lambda: NOW, head_sha=lambda: rs.SHA,
        proof_path=tmp_path / "none.json", release_dir=tmp_path / "release",
        sleep=lambda seconds: None)
    return release_identity.main(["check", *argv, "--skip-service"], deps)


def test_check_expect_iam_reports_a_runtime_role_that_lost_invoke_gateway(tmp_path, capsys):
    assert check(World(), tmp_path, "--expect", "iam") == 1

    out = capsys.readouterr().out
    assert "DRIFT  Runtime MeridianConcierge: its role cannot call bedrock-agentcore:" in out
    assert "DRIFT  Runtime MeridianWorkflow: its role cannot call" in out


def test_check_expect_iam_is_ok_when_the_roles_may_invoke_the_gateway(tmp_path, capsys):
    assert check(World(allow()), tmp_path, "--expect", "iam") == 0

    assert "OK  every hop reports iam" in capsys.readouterr().out


def test_check_in_jwt_mode_does_not_read_the_runtime_roles(tmp_path):
    from tests.test_release_identity_cli import World as Base, proof
    base = Base("jwt")
    deps = release_identity.Dependencies(
        env=env(), session=base.session, now=lambda: NOW, head_sha=lambda: rs.SHA,
        proof_path=proof(tmp_path), release_dir=tmp_path / "release", sleep=lambda s: None)

    assert release_identity.main(["check", "--skip-service"], deps) == 0
    assert base.iam.calls == []


# ------------------------------------------------------------------- snapshot


def test_a_snapshot_taken_with_a_broken_role_carries_the_finding_as_baseline(tmp_path):
    world = ss.SnapWorld(tmp_path, mode="iam")
    world.role_policies["MeridianWorkflow"] = None

    saved = ss.taken(world)

    assert any("MeridianWorkflow: its role cannot call" in line
               for line in saved["baselineFindings"])


def test_a_healthy_iam_snapshot_has_no_role_finding(tmp_path):
    saved = ss.taken(ss.SnapWorld(tmp_path, mode="iam"))

    assert not [line for line in saved["baselineFindings"] if "InvokeGateway" in line]


def test_a_jwt_snapshot_does_not_ask_for_the_grant(tmp_path):
    saved = ss.taken(ss.SnapWorld(tmp_path, mode="jwt"))

    assert not [line for line in saved["baselineFindings"] if "InvokeGateway" in line]


# -------------------------------------------------------------------- rollback


def test_the_rollback_runs_the_gateway_before_the_stack_and_names_the_stack_step():
    names = [step.name for step in restore_hops.steps(sorted(rs.RUNTIME_IDS))]

    assert names.index("gateway") < names.index("agentcore stack")
    assert "cedar rules" not in names
    stack = next(step for step in restore_hops.steps(["a"]) if step.name == "agentcore stack")
    assert "gateway" in stack.needs


def test_after_the_jwt_deploy_the_rollback_reports_the_missing_grant_and_the_exact_steps(tmp_path):
    world, saved, _ = released(tmp_path)

    outcome, said = go(world, saved)

    manual = {r.name: r for r in outcome.results if r.status == "manual"}
    assert set(manual) == {"roles stack", "agentcore stack"}
    text = "\n".join(manual["agentcore stack"].lines)
    assert "Runtime MeridianConcierge: its role cannot call bedrock-agentcore:InvokeGateway" in text
    assert "Runtime MeridianWorkflow: its role cannot call" in text
    assert "MERIDIAN_AGENTCORE_AUTH=iam python scripts/render_agentcore_config.py" in text
    assert "/opt/homebrew/bin/agentcore deploy -y" in text
    assert "only after the gateway step above" in text
    assert "CloudFormation cannot change the authorizer type back" in text
    assert "check --expect iam --service-arn" in text
    assert rs.ACCOUNT not in "\n".join(said) and outcome.code == 1


def test_the_gateway_is_restored_through_the_api_before_the_manual_deploy_is_printed(tmp_path):
    world, saved, _ = released(tmp_path)

    outcome, said = go(world, saved)

    assert world.gateway["authorizerType"] == "AWS_IAM"
    order = [r.name for r in outcome.results]
    assert order.index("gateway") < order.index("agentcore stack")


def test_once_the_iam_render_is_deployed_the_rollback_reads_complete(tmp_path):
    world, saved, before = released(tmp_path)
    go(world, saved)
    world.deploy_iam_render()

    outcome, said = go(world, saved)

    manual = [r.name for r in outcome.results if r.status == "manual"]
    assert manual == ["roles stack"] or manual == []
    stack = next(r for r in outcome.results if r.name == "agentcore stack")
    assert stack.status == "unchanged"


def test_the_stack_step_is_skipped_when_the_gateway_step_failed(tmp_path):
    world, saved, _ = released(tmp_path)
    world.failures["update_gateway"] = ss.client_error("AccessDeniedException")

    outcome, _ = go(world, saved)

    statuses = {r.name: r.status for r in outcome.results}
    assert statuses["gateway"] == "failed" and statuses["agentcore stack"] == "skipped"


def test_a_jwt_snapshot_does_not_demand_the_grant_back(tmp_path):
    world, saved, _ = released(tmp_path, mode="jwt")
    world.role_policies = {name: None for name in rs.RUNTIME_IDS}
    stack = next(step for step in restore_hops.steps(sorted(rs.RUNTIME_IDS))
                 if step.name == "agentcore stack")

    assert not [line for line in stack.check(context(world, saved))
                if "InvokeGateway" in line]


def test_a_dry_run_prints_the_same_steps_and_changes_nothing(tmp_path):
    world, saved, _ = released(tmp_path)

    outcome, said = go(world, saved, apply=False)

    assert world.writes() == []
    assert any("agentcore deploy -y" in line for line in said)
    assert outcome.code == 0
