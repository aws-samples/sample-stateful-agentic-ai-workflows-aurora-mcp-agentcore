"""`release_identity.py deploy`: the Gateway moves first, the stack deploys, the pin is read back.

CloudFormation cannot change a Gateway's authorizer type ("Authorizer type cannot be updated for
an existing gateway"), and the template cannot declare the interceptor. So the order is: the
UpdateGateway API moves the authorizer and attaches the interceptor, the deploy runs against a
template that already matches, and the Gateway is read back and re-attached if the deploy changed
it. The command refuses to deploy in any other order.

The rendered template keeps the Gateway on ``AWS_IAM`` in both modes (the deployed stack's value),
because CloudFormation compares the template with the stack template: the live Gateway is expected
to be CUSTOM_JWT in ``jwt`` mode while the template says AWS_IAM, and the deploy diff must plan no
Gateway authorizer change.
"""

from __future__ import annotations

import json

import pytest

from scripts import release_identity
from scripts.identity_release import deploy_order, settings
from tests import release_support as rs
from tests.gateway_release_support import current
from tests.test_release_gateway_cli import GatewayWorld
from tests.test_release_identity_cli import NOW, env

FLAG = settings.CONFIRM_FLAG
DEPLOY_ARGV = ["/opt/homebrew/bin/agentcore", "deploy", "-y"]
DIFF_ARGV = ["/opt/homebrew/bin/agentcore", "deploy", "--diff", "--json"]
RUNTIME_ONLY_PLAN = json.dumps({"resources": {"MeridianConcierge": {
    "type": "AWS::BedrockAgentCore::Runtime",
    "properties": {"AuthorizerConfiguration": {"old": None, "new": {"CustomJWTAuthorizer": {}}}}}}})
GATEWAY_PLAN = json.dumps({"resources": {"Gateway": {
    "type": "AWS::BedrockAgentCore::Gateway",
    "properties": {"AuthorizerType": {"old": "AWS_IAM", "new": "CUSTOM_JWT"}}}}})


def render(project, authorizer="AWS_IAM", clients=None, discovery=None):
    """Write the rendered ``agentcore.json`` the deploy would read.

    The default is what the render writes in both modes: the Gateway on the stack's ``AWS_IAM``.
    ``CUSTOM_JWT`` writes the old render that moved the Gateway authorizer.
    """
    pool = settings.cognito_settings(rs.COGNITO_ENV)
    gateway = {"name": "meridian-aurora", "authorizerType": authorizer}
    if authorizer == "CUSTOM_JWT":
        gateway["authorizerConfiguration"] = {"customJwtAuthorizer": {
            "discoveryUrl": discovery or pool.discovery_url,
            "allowedClients": clients if clients is not None else [rs.CLIENT]}}
    folder = project / "agentcore"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "agentcore.json").write_text(json.dumps({"agentCoreGateways": [gateway]}))
    return project


class Deploy:
    """A stand-in for the agentcore CLI: records argv and cwd, optionally changes the Gateway.

    The diff run (``deploy --diff --json``) answers with ``diff_output`` and ``diff_code`` and is
    recorded in ``diffs``; ``calls`` holds only the real deploy.
    """

    def __init__(self, world, code=0, output="deployed", reset=None, error=None,
                 diff_output=RUNTIME_ONLY_PLAN, diff_code=0):
        self.world, self.code, self.output = world, code, output
        self.reset, self.error = reset, error
        self.diff_output, self.diff_code = diff_output, diff_code
        self.calls, self.diffs = [], []

    def __call__(self, argv, cwd):
        if "--diff" in argv:
            self.diffs.append((list(argv), cwd))
            self.world.control.events.append("diff")
            return self.diff_code, self.diff_output
        self.calls.append((list(argv), cwd))
        self.world.control.events.append("deploy")
        if self.error:
            raise self.error
        if self.reset is not None:
            self.world.control.before = self.reset
        return self.code, self.output


def run(argv, world, tmp_path, runner, authorizer="AWS_IAM", environment=None, **render_args):
    project = render(tmp_path / "project", authorizer, **render_args)
    proof = tmp_path / "proof.json"
    proof.write_text(json.dumps(rs.receipt(NOW)))
    deps = release_identity.Dependencies(
        env=environment or env(), session=world.session, now=lambda: NOW,
        head_sha=lambda: rs.SHA, proof_path=proof, release_dir=tmp_path / "release",
        sleep=lambda seconds: None, agentcore_dir=project, run_command=runner)
    return release_identity.main(argv, deps)


def moved_world(**kwargs):
    """A Gateway already moved: Cognito authorizer, interceptor attached, grant written."""
    world = GatewayWorld("jwt", current("jwt"), installed=True, **kwargs)
    return world


def test_the_dry_run_lists_the_order_and_deploys_nothing(tmp_path, capsys):
    world = moved_world()
    runner = Deploy(world)

    assert run(["deploy"], world, tmp_path, runner) == 0

    out = capsys.readouterr().out
    assert "DRY RUN" in out and runner.calls == []
    assert "/opt/homebrew/bin/agentcore deploy -y" in out
    assert "re-attach" in out and FLAG in out and rs.ACCOUNT not in out
    assert "deploy --diff --json" in out and "AWS_IAM on purpose" in out
    assert runner.diffs == []
    assert "update_gateway" not in world.control.names()


def test_a_gateway_still_on_iam_blocks_the_deploy_and_names_the_gateway_command(tmp_path, capsys):
    world = GatewayWorld("iam")
    runner = Deploy(world)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2

    err = capsys.readouterr().err
    assert runner.calls == []
    assert "CloudFormation" in err and "Authorizer type cannot be updated" in err
    assert "release_identity.py gateway --to jwt --apply" in err
    assert "authorizer is AWS_IAM, expected CUSTOM_JWT" in err
    assert runner.diffs == []
    assert "update_gateway" not in world.control.names()


def test_the_dry_run_with_a_blocker_exits_two_and_says_blocked(tmp_path, capsys):
    world = GatewayWorld("iam")

    assert run(["deploy"], world, tmp_path, Deploy(world)) == 2

    assert "BLOCKED" in capsys.readouterr().out


def test_a_gateway_without_the_interceptor_blocks_the_deploy(tmp_path, capsys):
    world = GatewayWorld("jwt", current("jwt"), installed=True)
    world.control.before = current("jwt", settings.CEDAR)
    runner = Deploy(world)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2

    assert runner.calls == []
    assert "no request interceptor is attached" in capsys.readouterr().err


def test_a_render_that_moves_the_gateway_authorizer_blocks_the_deploy(tmp_path, capsys):
    world = moved_world()
    runner = Deploy(world)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner, authorizer="CUSTOM_JWT") == 2

    err = capsys.readouterr().err
    assert runner.calls == [] and runner.diffs == []
    assert "Rendered config" in err and "render_agentcore_config.py" in err


def test_a_jwt_deploy_goes_ahead_when_the_live_gateway_is_jwt_and_the_template_says_iam(
        tmp_path, capsys):
    """The documented divergence: live CUSTOM_JWT, rendered AWS_IAM is the EXPECTED state."""
    world = moved_world()
    runner = Deploy(world)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 0

    assert len(runner.calls) == 1 and "update_gateway" not in world.control.names()


def test_a_live_gateway_pool_that_is_not_this_pools_blocks_the_deploy(tmp_path, capsys):
    other = current("jwt")
    other["authorizerConfiguration"]["customJWTAuthorizer"]["allowedClients"] = ["someone-else"]
    world = moved_world()
    world.control.before = other
    runner = Deploy(world)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2

    assert runner.calls == [] and "allowedClients" in capsys.readouterr().err


def test_a_missing_render_is_reported_with_the_command_that_writes_it(tmp_path, capsys):
    world = moved_world()
    runner = Deploy(world)
    deps = release_identity.Dependencies(
        env=env(), session=world.session, now=lambda: NOW, head_sha=lambda: rs.SHA,
        proof_path=tmp_path / "none.json", release_dir=tmp_path / "release",
        agentcore_dir=tmp_path / "nowhere", run_command=runner)

    assert release_identity.main(["deploy", "--apply", FLAG], deps) == 2

    assert "render_agentcore_config.py" in capsys.readouterr().err
    assert runner.calls == []


def test_apply_without_the_confirmation_exits_three_before_any_client(tmp_path, capsys):
    world = moved_world()
    runner = Deploy(world)

    assert run(["deploy", "--apply"], world, tmp_path, runner) == 3

    assert world.built == [] and runner.calls == [] and FLAG in capsys.readouterr().out


def test_a_deploy_that_leaves_the_gateway_alone_is_read_back_and_not_updated(tmp_path, capsys):
    world = moved_world()
    runner = Deploy(world)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 0

    out = capsys.readouterr().out
    assert runner.calls == [(DEPLOY_ARGV, tmp_path / "project")]
    assert runner.diffs == [(DIFF_ARGV, tmp_path / "project")]
    assert world.control.events == ["diff", "deploy"]
    assert "update_gateway" not in world.control.names()
    assert "left the Gateway as the release wants" in out


def test_a_deploy_that_detaches_the_interceptor_is_followed_by_the_reattach(tmp_path, capsys):
    world = moved_world()
    runner = Deploy(world, reset=current("jwt", settings.CEDAR))

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 0

    out = capsys.readouterr().out
    assert world.control.events == ["diff", "deploy", "update"]
    sent = [kwargs for name, kwargs in world.control.calls if name == "update_gateway"]
    assert len(sent) == 1 and sent[0]["authorizerType"] == "CUSTOM_JWT"
    assert sent[0]["interceptorConfigurations"][0]["interceptor"]["lambda"]["arn"] \
        == rs.INTERCEPTOR_ARN
    assert "the deploy changed the Gateway" in out and "OK  the Gateway reports jwt" in out


def test_a_deploy_that_reverts_the_authorizer_is_reported_and_restored(tmp_path, capsys):
    world = moved_world()
    runner = Deploy(world, reset=current("iam"))

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 0

    out = capsys.readouterr().out
    assert "authorizer is AWS_IAM, expected CUSTOM_JWT" in out
    assert world.control.events == ["diff", "deploy", "update"]


def test_a_failed_deploy_stops_without_touching_the_gateway_and_masks_the_output(
        tmp_path, capsys):
    world = moved_world()
    runner = Deploy(world, code=1, output=f"stack in {rs.ACCOUNT} rolled back")

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2

    captured = capsys.readouterr()
    assert "update_gateway" not in world.control.names()
    assert rs.ACCOUNT not in captured.out + captured.err and "<acct>" in captured.out
    assert "rolls the stack back" in captured.err
    assert "check --skip-service" in captured.err


def test_a_missing_cli_is_a_clear_error_not_a_traceback(tmp_path, capsys, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(deploy_order.subprocess, "run", missing)
    world = moved_world()

    assert run(["deploy", "--apply", FLAG], world, tmp_path, deploy_order.run_command) == 2

    err = capsys.readouterr().err
    assert "/opt/homebrew/bin/agentcore" in err and "Traceback" not in err
    assert "update_gateway" not in world.control.names()


def test_a_deploy_that_outlives_its_timeout_says_the_stack_may_still_be_updating(
        tmp_path, capsys, monkeypatch):
    def slow(*args, **kwargs):
        raise deploy_order.subprocess.TimeoutExpired(cmd="agentcore", timeout=1)

    monkeypatch.setattr(deploy_order.subprocess, "run", slow)

    assert run(["deploy", "--apply", FLAG], moved_world(), tmp_path,
               deploy_order.run_command) == 2

    assert "may still be updating" in capsys.readouterr().err


def test_the_real_runner_passes_the_argv_and_cwd_through_and_joins_both_streams(
        tmp_path, monkeypatch):
    seen = {}

    def fake(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return deploy_order.subprocess.CompletedProcess(argv, 3, stdout="out\n", stderr="err\n")

    monkeypatch.setattr(deploy_order.subprocess, "run", fake)

    assert deploy_order.run_command(DEPLOY_ARGV, tmp_path) == (3, "out\nerr\n")
    assert seen["argv"] == DEPLOY_ARGV and seen["cwd"] == tmp_path
    assert seen["capture_output"] is True and seen["timeout"] > 60


def test_the_iam_direction_needs_the_iam_gateway_first(tmp_path, capsys):
    world = GatewayWorld("jwt", current("iam"), installed=True)
    runner = Deploy(world)

    code = run(["deploy", "--to", "iam", "--apply", FLAG], world, tmp_path, runner,
               authorizer="AWS_IAM")

    assert code == 2 and runner.calls == []
    assert "release_identity.py gateway --to iam --apply" in capsys.readouterr().err


def test_the_iam_direction_deploys_once_the_gateway_is_back_on_iam(tmp_path, capsys):
    world = GatewayWorld("iam", current("iam"))
    runner = Deploy(world)

    code = run(["deploy", "--to", "iam", "--apply", FLAG], world, tmp_path, runner,
               authorizer="AWS_IAM")

    assert code == 0 and len(runner.calls) == 1
    assert "left the Gateway as the release wants" in capsys.readouterr().out


def test_the_order_findings_are_pure_and_name_each_reason():
    rendered = {"authorizerType": "AWS_IAM"}

    found = deploy_order.ordering_findings(rendered, current("iam"), rs.target("jwt"))

    assert any("authorizer is AWS_IAM, expected CUSTOM_JWT" in line for line in found)
    assert any("release_identity.py gateway --to jwt --apply" in line for line in found)
    assert deploy_order.ordering_findings(rendered, current("jwt"), rs.target("jwt")) == []
    assert deploy_order.ordering_findings(rendered, current("iam"), rs.target("iam")) == []


@pytest.mark.parametrize("rendered", [
    {"authorizerType": "CUSTOM_JWT"},
    {"authorizerType": "AWS_IAM", "authorizerConfiguration": {"customJwtAuthorizer": {}}}])
def test_a_rendered_gateway_that_is_not_the_stacks_iam_gateway_is_a_finding(rendered):
    found = deploy_order.ordering_findings(rendered, current("jwt"), rs.target("jwt"))

    assert len(found) == 1 and "Rendered config" in found[0]


# ----------------------------------------------------------- the deploy diff gate


def test_a_plan_that_changes_the_gateway_authorizer_stops_before_the_deploy(tmp_path, capsys):
    world = moved_world()
    runner = Deploy(world, diff_output=GATEWAY_PLAN)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2

    err = capsys.readouterr().err
    assert runner.calls == [] and len(runner.diffs) == 1
    assert "changes the Gateway authorizer" in err and "release_identity.py gateway" in err
    assert "update_gateway" not in world.control.names()


def test_a_plan_that_changes_only_the_runtime_authorizers_goes_ahead(tmp_path):
    world = moved_world()
    runner = Deploy(world, diff_output=RUNTIME_ONLY_PLAN)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 0
    assert len(runner.calls) == 1


def test_a_plan_in_text_form_that_names_the_gateway_authorizer_stops_the_deploy(
        tmp_path, capsys):
    text = ("[~] AWS::BedrockAgentCore::Gateway Gateway\n"
            " └─ [~] AuthorizerType\n     ├─ [-] AWS_IAM\n     └─ [+] CUSTOM_JWT\n")
    world = moved_world()
    runner = Deploy(world, diff_output=text)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2

    assert runner.calls == [] and "AuthorizerType" in capsys.readouterr().err


def test_a_text_plan_that_changes_only_a_runtime_goes_ahead(tmp_path):
    text = ("[~] AWS::BedrockAgentCore::Runtime Concierge\n"
            " └─ [~] AuthorizerConfiguration\n")
    world = moved_world()
    runner = Deploy(world, diff_output=text)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 0


def test_a_gateway_authorizer_configuration_change_alone_stops_the_deploy(tmp_path):
    plan = json.dumps({"resources": {"Gateway": {
        "type": "AWS::BedrockAgentCore::Gateway",
        "properties": {"AuthorizerConfiguration": {"old": None, "new": {}}}}}})
    world = moved_world()
    runner = Deploy(world, diff_output=plan)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2
    assert runner.calls == []


def test_a_diff_that_fails_cannot_clear_the_deploy(tmp_path, capsys):
    world = moved_world()
    runner = Deploy(world, diff_output=f"synth failed in {rs.ACCOUNT}", diff_code=1)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2

    captured = capsys.readouterr()
    assert runner.calls == [] and "cannot be checked" in captured.err
    assert rs.ACCOUNT not in captured.out + captured.err


def test_the_dry_run_accepts_a_live_jwt_gateway_under_an_iam_template(tmp_path, capsys):
    """Live CUSTOM_JWT against a template that says AWS_IAM is the expected state."""
    world = moved_world()
    project = render(tmp_path / "project")
    assert json.loads((project / "agentcore" / "agentcore.json").read_text())[
        "agentCoreGateways"][0]["authorizerType"] == "AWS_IAM"

    code = run(["deploy"], world, tmp_path, Deploy(world))

    assert code == 0 and "BLOCKED" not in capsys.readouterr().out


def test_one_update_call_carries_the_authorizer_the_clients_and_the_interceptor(tmp_path):
    world = GatewayWorld("iam", current("jwt"))

    assert run(["gateway", "--apply", FLAG], world, tmp_path, Deploy(world)) == 0

    sent = [kwargs for name, kwargs in world.control.calls if name == "update_gateway"]
    assert len(sent) == 1
    pool = sent[0]["authorizerConfiguration"]["customJWTAuthorizer"]
    assert sent[0]["authorizerType"] == "CUSTOM_JWT"
    assert pool == {"discoveryUrl": rs.target().cognito.discovery_url,
                    "allowedClients": [rs.CLIENT]}
    assert [i["interceptor"]["lambda"]["arn"] for i in sent[0]["interceptorConfigurations"]] \
        == [rs.INTERCEPTOR_ARN]
    assert world.control.events == ["update"]


def test_a_failed_deploy_says_the_site_stays_down_until_a_retry_or_the_rollback(
        tmp_path, capsys):
    world = moved_world()

    assert run(["deploy", "--apply", FLAG], world, tmp_path, Deploy(world, code=1)) == 2

    err = capsys.readouterr().err
    assert "still expect IAM while the Gateway expects the token" in err
    assert "release_identity.py rollback" in err


def test_the_authorizer_refusal_after_the_gateway_matched_is_named_as_the_wrong_assumption(
        tmp_path, capsys):
    world = moved_world()
    runner = Deploy(world, code=1, output=f"CREATE_FAILED: {deploy_order.CLI_ERROR}")

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2

    err = capsys.readouterr().err
    assert "even though the live Gateway already matched the render" in err
    assert "compares the template with its own previous state" in err
    assert "Do not retry" in err and "owner decision" in err
