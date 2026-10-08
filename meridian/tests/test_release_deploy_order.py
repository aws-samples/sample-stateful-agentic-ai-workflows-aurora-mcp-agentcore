"""`release_identity.py deploy`: one build stage at a time, checked against the live Gateway.

CloudFormation cannot change a Gateway's authorizer type and the AgentCore CDK constructs cannot
hold two Gateways with the same target names, so a mode switch replaces the Gateway in four
passes (see ``scripts/identity_release/stages.py``). Each ``deploy`` runs one pass, after
checking that the render, the CLI's deployed state and the live Gateway agree on which pass it
is, that the plan is the one that pass should have, and, for the first pass, that everything the
irreversible replacement needs is in place.
"""

from __future__ import annotations

import json
import pytest

from scripts import release_identity
from scripts.identity_release import deploy_order, snapshot, stages, settings
from tests import release_support as rs
from tests.aws_recorders import Recorder, client_error
from tests.gateway_release_support import bare, current
from tests.test_release_identity_cli import NOW, env

FLAG = settings.CONFIRM_FLAG
DEPLOY_ARGV = ["/opt/homebrew/bin/agentcore", "deploy", "-y"]
DIFF_ARGV = ["/opt/homebrew/bin/agentcore", "deploy", "--diff", "--json"]
GATEWAY_TYPE = "AWS::BedrockAgentCore::Gateway"
STATUS = '{"success":true,"targetName":"default"}'
NEW_JWT = f"[+] {GATEWAY_TYPE} Mcp/GatewayMeridianAuroraJwt/Resource McpGatewayMeridianAuroraJwt1"
OLD_IAM = f"[-] {GATEWAY_TYPE} Mcp/GatewayMeridianAurora/Resource McpGatewayMeridianAuroraAB"
NEW_IAM = f"[+] {GATEWAY_TYPE} Mcp/GatewayMeridianAurora/Resource McpGatewayMeridianAuroraAB"
OLD_JWT = f"[-] {GATEWAY_TYPE} Mcp/GatewayMeridianAuroraJwt/Resource McpGatewayMeridianAuroraJwt1"
RUNTIME_PLAN = ("[~] AWS::BedrockAgentCore::Runtime App/MeridianConcierge/Resource Mer1\n"
                " └─ [~] AuthorizerConfiguration\n")
JWT_REPLACES_IAM = f"Resources\n{NEW_JWT}\n{OLD_IAM}\n{RUNTIME_PLAN}{STATUS}"
IAM_REPLACES_JWT = f"Resources\n{NEW_IAM}\n{OLD_JWT}\n{RUNTIME_PLAN}{STATUS}"
QUIET_PLAN = f"Resources\n{RUNTIME_PLAN}{STATUS}"
OTHER = {"jwt": "iam", "iam": "jwt"}


def gateway_of(mode, **extra):
    """The mode's Gateway as the control plane describes it, with its own id and role."""
    return bare(mode, gatewayId=f"gw-{mode}",
                roleArn=f"arn:aws:iam::{rs.ACCOUNT}:role/role-{mode}", **extra)


class Cloud:
    """The control plane's Gateways, by id, changed by the stand-in deploy."""

    def __init__(self, *modes, **extras):
        self.gateways = {f"gw-{mode}": gateway_of(mode, **extras.get(mode, {})) for mode in modes}
        self.calls = []

    def list_gateways(self, **kwargs):
        self.calls.append("list_gateways")
        return {"items": [{"gatewayId": gid, "name": g["name"]}
                          for gid, g in self.gateways.items()]}

    def get_gateway(self, gatewayIdentifier):
        self.calls.append("get_gateway")
        return dict(self.gateways[gatewayIdentifier])

    def update_gateway(self, **kwargs):
        raise AssertionError("deploy must never update a Gateway itself")


class Roles:
    """Inline policies by role name, like IAM answers get_role_policy."""

    def __init__(self, **policies):
        self.policies = policies
        self.calls = []

    def get_role_policy(self, RoleName, PolicyName):
        self.calls.append(RoleName)
        if PolicyName not in self.policies.get(RoleName, ()):
            raise client_error("NoSuchEntity")
        return {"PolicyDocument": {}}


class World:
    def __init__(self, mode="jwt", *live, account=rs.ACCOUNT, roles=None, lam=None, **extras):
        from unittest.mock import Mock
        self.mode = mode
        self.sts = Mock()
        self.sts.get_caller_identity.return_value = {"Account": account}
        self.cloud = Cloud(*live, **extras)
        self.iam = Roles(**(roles or {}))
        self.cfn = Mock()
        self.cfn.describe_stacks.return_value = {"Stacks": [{"Outputs": rs.identity_outputs()}]}
        self.lam = lam or _interceptor_lambda()
        self.built = []

    def session(self, region):
        from unittest.mock import Mock
        clients = {"sts": self.sts, "bedrock-agentcore-control": self.cloud, "iam": self.iam,
                   "cloudformation": self.cfn, "lambda": self.lam}

        def client(name, **kwargs):
            self.built.append(name)
            return clients[name]
        return Mock(client=client)


def _interceptor_lambda():
    from tests.gateway_release_support import lambda_client
    return lambda_client()


class Cli(Recorder):
    """A stand-in for the agentcore CLI: answers the diff, and a deploy creates the Gateway."""

    def __init__(self, world, plan=QUIET_PLAN, diff_code=0, code=0, output="deployed",
                 creates=None, removes=None, error=None):
        super().__init__()
        self.world, self.plan, self.diff_code = world, plan, diff_code
        self.code, self.output, self.creates, self.removes = code, output, creates, removes
        self.error = error
        self.diffs, self.deploys, self.events = [], [], []

    def __call__(self, argv, cwd):
        if "--diff" in argv:
            self.diffs.append((list(argv), cwd))
            self.events.append("diff")
            return self.diff_code, self.plan
        self.deploys.append((list(argv), cwd))
        self.events.append("deploy")
        if self.error:
            raise self.error
        if self.code == 0:
            if self.removes:
                self.world.cloud.gateways.pop(f"gw-{self.removes}", None)
            if self.creates:
                self.world.cloud.gateways[f"gw-{self.creates}"] = gateway_of(self.creates)
        return self.code, self.output


def spec(mode="jwt", stage=stages.COMPLETE, **changes):
    gateway = {
        "name": settings.gateway_logical_name(mode),
        "authorizerType": "CUSTOM_JWT" if mode == "jwt" else "AWS_IAM",
        "targets": [{"name": "SemanticTripSearchLambda"}]
        + ([{"name": settings.HOLDS_TARGET}] if stages.renders_holds(stage) else [])}
    if mode == "jwt":
        pool = settings.cognito_settings(rs.COGNITO_ENV)
        gateway["authorizerConfiguration"] = {"customJwtAuthorizer": {
            "discoveryUrl": pool.discovery_url, "allowedClients": [rs.CLIENT]}}
    gateway.update(changes)
    variables = [{"name": "MERIDIAN_POLICY_MODE", "value": "ENFORCE"}]
    if stages.renders_engine_id(stage):
        variables.append({"name": "MERIDIAN_POLICY_ENGINE_ID", "value": "e"})
    return {"agentCoreGateways": [gateway],
            "policyEngines": [{"name": "MeridianGovernance", "policies": []}]
            if stages.renders_engine(stage) else [],
            "runtimes": [{"name": "MeridianConcierge", "envVars": variables}]}


def state(mode="jwt", stage=stages.COMPLETE):
    gateway: dict = {}
    if stage != stages.GATEWAY:
        gateway = {"gatewayId": f"gw-{mode}", "targets": {"SemanticTripSearchLambda": {
            "targetId": "t1"}}}
        if stage in (stages.GOVERNANCE, stages.COMPLETE):
            gateway["targets"][settings.HOLDS_TARGET] = {"targetId": "t2"}
    resources: dict = {"mcp": {"gateways": {settings.gateway_logical_name(mode): gateway}
                               if gateway else {}}}
    if stage == stages.COMPLETE:
        resources["policyEngines"] = {"MeridianGovernance": {"policyEngineId": "e"}}
    return {"targets": {"default": {"resources": resources}}}


def write_snapshot(release_dir, mode):
    document = {
        "schema": snapshot.SCHEMA, "takenAt": "2026-10-08T11:00:00+00:00", "commit": rs.SHA,
        "account": rs.ACCOUNT, "region": rs.REGION, "mode": mode, "gateway": {},
        "runtimes": {}, "service": {"ServiceArn": rs.SERVICE_ARN}, "site": {}, "roles": {},
        "lambdas": {}, "policies": {}, "baselineFindings": [], "redacted": [], "complete": True}
    snapshot.write(document, release_dir, NOW)


def project(tmp_path, rendered=None, deployed=None, targets=True):
    folder = tmp_path / "project" / "agentcore"
    (folder / ".cli").mkdir(parents=True, exist_ok=True)
    if rendered is not None:
        (folder / "agentcore.json").write_text(
            rendered if isinstance(rendered, str) else json.dumps(rendered))
    if deployed not in (None, False):
        (folder / ".cli" / "deployed-state.json").write_text(
            deployed if isinstance(deployed, str) else json.dumps(deployed))
    if targets:
        (folder / "aws-targets.json").write_text(json.dumps([{"name": "default"}]))
    return tmp_path / "project"


def run(argv, world, tmp_path, runner, rendered=None, deployed=None, snap="auto",
        proof=True, environment=None, mode=None, stage=stages.COMPLETE):
    mode = mode or world.mode
    root = project(tmp_path, spec(mode, stage) if rendered is None else rendered,
                   state(mode, stage) if deployed is None else deployed)
    release = tmp_path / "release"
    if snap == "auto":
        snap = OTHER[mode]
    if snap:
        write_snapshot(release, snap)
    receipt = tmp_path / "proof.json"
    if proof:
        receipt.write_text(json.dumps(rs.receipt(NOW)))
    deps = release_identity.Dependencies(
        env=environment or env(MERIDIAN_AGENTCORE_AUTH=mode), session=world.session,
        now=lambda: NOW, head_sha=lambda: rs.SHA, proof_path=receipt, release_dir=release,
        sleep=lambda seconds: None, agentcore_dir=root, run_command=runner)
    return release_identity.main(argv, deps)


def replacement(mode="jwt", **kwargs):
    """A world at the first stage: the other mode's Gateway is live, this mode's is not."""
    return World(mode, OTHER[mode], **kwargs)


# ------------------------------------------------------------------ dry runs


def test_the_dry_run_lists_the_preflight_and_deploys_nothing(tmp_path, capsys):
    world = replacement()
    runner = Cli(world)

    code = run(["deploy"], world, tmp_path, runner, stage=stages.GATEWAY)

    out = capsys.readouterr().out
    assert code == 0 and "DRY RUN" in out and runner.deploys == [] and runner.diffs == []
    assert "stage gateway (1 of 4)" in out
    assert "/opt/homebrew/bin/agentcore deploy -y" in out and "deploy --diff --json" in out
    assert FLAG in out and rs.ACCOUNT not in out
    assert "BLOCKED" not in out


def test_the_dry_run_with_a_blocker_exits_two_and_says_blocked(tmp_path, capsys):
    world = replacement()

    assert run(["deploy"], world, tmp_path, Cli(world), stage=stages.GATEWAY, proof=False) == 2

    assert "BLOCKED  Backend login proof: none recorded" in capsys.readouterr().out


def test_apply_without_the_confirmation_exits_three_before_any_client(tmp_path, capsys):
    world = replacement()

    assert run(["deploy", "--apply"], world, tmp_path, Cli(world), stage=stages.GATEWAY) == 3

    assert world.built == [] and FLAG in capsys.readouterr().out


def test_credentials_for_another_account_stop_before_any_read(tmp_path, capsys):
    world = replacement(account="999999999999")
    runner = Cli(world)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GATEWAY) == 2

    captured = capsys.readouterr()
    assert world.built == ["sts"] and "999999999999" not in captured.err
    assert runner.deploys == []


# --------------------------------------------------------- the first stage


def test_the_first_stage_replaces_the_iam_gateway_with_the_jwt_one(tmp_path, capsys):
    world = replacement()
    runner = Cli(world, JWT_REPLACES_IAM, creates="jwt", removes="iam")

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GATEWAY)

    out = capsys.readouterr().out
    assert code == 0
    assert runner.events == ["diff", "deploy"]
    assert runner.diffs[0][0] == DIFF_ARGV and runner.deploys[0][0] == DEPLOY_ARGV
    assert runner.deploys[0][1] == tmp_path / "project"
    assert "Stage gateway deployed (1 of 4)" in out
    assert "python scripts/render_agentcore_config.py" in out
    assert "python scripts/sync_agentcore_env.py --write" in out
    assert f"python scripts/release_identity.py deploy --to jwt --apply {FLAG}" in out


def test_the_first_stage_back_to_iam_replaces_the_jwt_gateway(tmp_path, capsys):
    world = replacement("iam")
    runner = Cli(world, IAM_REPLACES_JWT, creates="iam", removes="jwt")

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GATEWAY)

    assert code == 0 and runner.events == ["diff", "deploy"]


def test_the_iam_first_stage_needs_no_pool_proof_or_interceptor(tmp_path):
    world = replacement("iam", lam=None)
    runner = Cli(world, IAM_REPLACES_JWT, creates="iam", removes="jwt")
    environment = {k: v for k, v in env(MERIDIAN_AGENTCORE_AUTH="iam").items()
                   if not k.startswith("MERIDIAN_COGNITO")}

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GATEWAY,
               proof=False, environment=environment) == 0


@pytest.mark.parametrize(("kwargs", "fragment"), [
    ({"proof": False}, "Backend login proof: none recorded"),
    ({"snap": None}, "no complete snapshot"),
    ({"snap": "jwt"}, "snapshot is of the jwt release"),
])
def test_each_missing_first_stage_precondition_is_named_and_nothing_runs(
        tmp_path, capsys, kwargs, fragment):
    world = replacement()
    runner = Cli(world, JWT_REPLACES_IAM)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GATEWAY,
               **kwargs) == 2

    assert fragment in capsys.readouterr().err
    assert runner.diffs == [] and runner.deploys == []


def test_an_identity_stack_without_outputs_blocks_the_first_stage(tmp_path, capsys):
    world = replacement()
    world.cfn.describe_stacks.return_value = {"Stacks": [{"Outputs": []}]}

    assert run(["deploy", "--apply", FLAG], world, tmp_path, Cli(world),
               stage=stages.GATEWAY) == 2

    assert "Identity stack: has no output" in capsys.readouterr().err


def test_an_interceptor_that_is_not_deployed_blocks_the_first_stage(tmp_path, capsys):
    world = replacement()

    def missing(**kwargs):
        raise client_error("ResourceNotFoundException")
    world.lam.get_function = missing

    assert run(["deploy", "--apply", FLAG], world, tmp_path, Cli(world),
               stage=stages.GATEWAY) == 2

    assert "Interceptor Lambda: not deployed" in capsys.readouterr().err


def test_the_cedar_only_design_does_not_ask_for_the_interceptor(tmp_path):
    world = replacement()
    world.lam.get_function = lambda **kwargs: (_ for _ in ()).throw(AssertionError("called"))
    runner = Cli(world, JWT_REPLACES_IAM, creates="jwt", removes="iam")

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GATEWAY,
               environment=env(MERIDIAN_GATEWAY_ENFORCEMENT="cedar")) == 0


def test_a_grant_left_on_the_replaced_gateways_role_blocks_it_with_the_command(tmp_path, capsys):
    from scripts.identity_release.gateway_release import INVOKE_POLICY_NAME
    world = replacement(roles={"role-iam": [INVOKE_POLICY_NAME]})
    runner = Cli(world, JWT_REPLACES_IAM)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GATEWAY) == 2

    err = capsys.readouterr().err
    assert "MeridianTravelerPinInvoke" in err
    assert f"python scripts/release_identity.py gateway --to iam --apply {FLAG}" in err
    assert runner.deploys == []


def test_an_interceptor_still_attached_to_the_replaced_jwt_gateway_names_the_revoke(
        tmp_path, capsys):
    world = replacement("iam")
    world.cloud.gateways["gw-jwt"] = current(
        "jwt", gatewayId="gw-jwt", roleArn=gateway_of("jwt")["roleArn"])

    assert run(["deploy", "--apply", FLAG], world, tmp_path, Cli(world),
               stage=stages.GATEWAY) == 2

    err = capsys.readouterr().err
    assert "interceptor attached" in err
    assert f"gateway --to jwt --only revoke --apply {FLAG}" in err


def test_a_first_ever_build_has_nothing_to_replace_and_needs_no_snapshot(tmp_path):
    world = World("iam")
    runner = Cli(world, f"Resources\n{NEW_IAM}\n{STATUS}", creates="iam")

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GATEWAY,
               snap=None, environment=env(MERIDIAN_AGENTCORE_AUTH="iam")) == 0


# --------------------------------------------------- the render and the state


@pytest.mark.parametrize(("changes", "fragment"), [
    ({"name": "meridian-aurora"}, "named 'meridian-aurora', expected 'meridian-aurora-jwt'"),
    ({"authorizerType": "AWS_IAM"}, "authorizer is AWS_IAM, expected CUSTOM_JWT"),
    ({"authorizerConfiguration": {"customJwtAuthorizer": {
        "discoveryUrl": "https://x/.well-known/o", "allowedClients": [rs.CLIENT]}}},
     "discoveryUrl"),
    ({"authorizerConfiguration": {"customJwtAuthorizer": {
        "discoveryUrl": settings.cognito_settings(rs.COGNITO_ENV).discovery_url,
        "allowedClients": ["other"]}}}, "allowedClients"),
])
def test_a_rendered_gateway_that_is_not_the_modes_blocks_the_deploy(
        tmp_path, capsys, changes, fragment):
    world = World("jwt", "jwt")
    runner = Cli(world)

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner,
               rendered=spec("jwt", **changes))

    err = capsys.readouterr().err
    assert code == 2 and fragment in err and "Rendered config" in err
    assert "render_agentcore_config.py" in err
    assert runner.diffs == [] and runner.deploys == []


def test_an_iam_render_with_a_token_authorizer_block_blocks_the_deploy(tmp_path, capsys):
    world = World("iam", "iam")
    rendered = spec("iam", authorizerConfiguration={"customJwtAuthorizer": {}})

    assert run(["deploy", "--apply", FLAG], world, tmp_path, Cli(world), rendered=rendered,
               environment=env(MERIDIAN_AGENTCORE_AUTH="iam")) == 2

    assert "authorizerConfiguration" in capsys.readouterr().err


def test_a_render_and_a_state_at_different_stages_block_the_deploy(tmp_path, capsys):
    world = World("jwt", "jwt")
    runner = Cli(world)

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner,
               rendered=spec("jwt", stages.TARGETS), deployed=state("jwt", stages.COMPLETE))

    err = capsys.readouterr().err
    assert code == 2 and "the render is stage targets" in err and "stage complete" in err
    assert "run python scripts/render_agentcore_config.py again" in err
    assert runner.deploys == []


def test_a_stale_state_that_reads_as_the_first_stage_while_the_gateway_lives_is_refused(
        tmp_path, capsys):
    world = World("jwt", "jwt")
    runner = Cli(world)

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GATEWAY)

    err = capsys.readouterr().err
    assert code == 2 and "already exists" in err and "would delete" in err
    assert runner.deploys == []


@pytest.mark.parametrize("stage", [stages.TARGETS, stages.GOVERNANCE, stages.COMPLETE])
def test_a_later_stage_needs_the_gateway_to_exist(tmp_path, capsys, stage):
    world = World("jwt")
    runner = Cli(world)

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stage)

    err = capsys.readouterr().err
    assert code == 2 and "no Gateway named meridianv2-meridian-aurora-jwt" in err
    assert runner.deploys == []


def test_an_unreadable_render_or_state_says_what_to_run(tmp_path, capsys):
    world = World("jwt", "jwt")

    assert run(["deploy"], world, tmp_path, Cli(world), rendered="not json", snap=None) == 2
    assert "render_agentcore_config.py" in capsys.readouterr().err
    assert run(["deploy"], world, tmp_path, Cli(world), deployed="{broken", snap=None) == 2
    assert "deployed-state.json" in capsys.readouterr().err


def test_a_render_with_two_gateways_is_refused(tmp_path, capsys):
    world = World("jwt", "jwt")
    both = spec("jwt")
    both["agentCoreGateways"].append(both["agentCoreGateways"][0])

    assert run(["deploy"], world, tmp_path, Cli(world), rendered=both) == 2

    assert "exactly one Gateway" in capsys.readouterr().err


def test_a_missing_state_file_is_the_first_stage(tmp_path, capsys):
    world = replacement()
    runner = Cli(world, JWT_REPLACES_IAM, creates="jwt", removes="iam")

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner,
               rendered=spec("jwt", stages.GATEWAY), deployed=False)

    assert code == 0 and runner.events == ["diff", "deploy"]


# ----------------------------------------------------------------- the plan gate


def test_a_plan_that_does_not_replace_the_gateway_blocks_the_first_stage(tmp_path, capsys):
    world = replacement()
    runner = Cli(world, QUIET_PLAN)

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GATEWAY)

    err = capsys.readouterr().err
    assert code == 2 and "deploy plan is not safe to apply" in err
    assert "add exactly one Gateway" in err
    assert runner.diffs and runner.deploys == []


def test_a_plan_that_changes_the_authorizer_in_place_blocks_every_stage(tmp_path, capsys):
    world = World("jwt", "jwt")
    plan = (f"Resources\n[~] {GATEWAY_TYPE} Mcp/GatewayMeridianAuroraJwt/Resource G1\n"
            " ├─ [~] AuthorizerType\n")
    runner = Cli(world, plan)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2

    assert "authorizer type in place" in capsys.readouterr().err
    assert runner.deploys == []


def test_a_diff_that_failed_blocks_the_deploy(tmp_path, capsys):
    world = World("jwt", "jwt")
    runner = Cli(world, "boom", diff_code=1)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2

    assert "exited 1" in capsys.readouterr().err and runner.deploys == []


def test_a_later_stage_whose_plan_deletes_the_holds_lambda_is_refused(tmp_path, capsys):
    world = World("jwt", "jwt")
    plan = "Resources\n[-] AWS::Lambda::Function Mcp/Holds/Function F1\n" + STATUS
    runner = Cli(world, plan)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2

    assert "deletes AWS::Lambda::Function" in capsys.readouterr().err
    assert runner.deploys == []


# ------------------------------------------------------- deploying, reading back


def test_a_later_stage_deploys_once_after_the_plan_and_reads_the_gateway_back(tmp_path, capsys):
    world = World("jwt", "jwt")
    runner = Cli(world)

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.COMPLETE)

    out = capsys.readouterr().out
    assert code == 0 and runner.events == ["diff", "deploy"]
    assert "Stage complete deployed (4 of 4)" in out
    assert f"python scripts/release_identity.py gateway --to jwt --apply {FLAG}" in out
    assert "python scripts/sync_agentcore_env.py --write" in out
    assert "release_identity.py check --skip-service" in out


def test_the_complete_stage_for_iam_has_no_interceptor_to_attach(tmp_path, capsys):
    world = World("iam", "iam")

    code = run(["deploy", "--apply", FLAG], world, tmp_path, Cli(world),
               environment=env(MERIDIAN_AGENTCORE_AUTH="iam"))

    out = capsys.readouterr().out
    assert code == 0 and "gateway --to jwt" not in out and "sync_agentcore_env.py" in out


def test_a_middle_stage_points_at_the_next_render(tmp_path, capsys):
    world = World("jwt", "jwt")

    assert run(["deploy", "--apply", FLAG], world, tmp_path, Cli(world),
               stage=stages.TARGETS) == 0

    out = capsys.readouterr().out
    assert "Stage targets deployed (2 of 4)" in out and "render_agentcore_config.py" in out


def test_the_read_back_reports_a_gateway_that_is_not_ready_as_drift_and_exits_one(
        tmp_path, capsys):
    world = World("jwt", "jwt")
    runner = Cli(world)
    original = runner.__call__

    def deploy_then_break(argv, cwd):
        result = original(argv, cwd)
        if "--diff" not in argv:
            world.cloud.gateways["gw-jwt"]["policyEngineConfiguration"] = {
                "arn": rs.ENGINE_ARN, "mode": "LOG_ONLY"}
        return result

    code = run(["deploy", "--apply", FLAG], world, tmp_path, deploy_then_break,
               stage=stages.GOVERNANCE)

    assert code == 1 and "DRIFT  Gateway: the policy engine is not attached in ENFORCE" in (
        capsys.readouterr().out)


def test_before_the_governance_stage_a_missing_engine_is_not_drift(tmp_path, capsys):
    world = World("jwt", "jwt")
    world.cloud.gateways["gw-jwt"].pop("policyEngineConfiguration")

    assert run(["deploy", "--apply", FLAG], world, tmp_path, Cli(world),
               stage=stages.TARGETS) == 0


def test_a_missing_interceptor_after_the_deploy_is_not_drift_and_is_never_reattached(
        tmp_path, capsys):
    world = World("jwt", "jwt")

    assert run(["deploy", "--apply", FLAG], world, tmp_path, Cli(world)) == 0

    assert "DRIFT" not in capsys.readouterr().out


def test_a_failed_deploy_says_the_stack_rolled_back_and_what_to_run(tmp_path, capsys):
    world = World("jwt", "jwt")
    runner = Cli(world, code=1, output="boom: it failed")

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner)

    err = capsys.readouterr().err
    assert code == 2 and "deploy failed (exit 1)" in err and "rolls the stack back" in err
    assert "release_identity.py check --skip-service" in err


def test_the_authorizer_error_from_the_cli_points_at_the_render(tmp_path, capsys):
    world = World("jwt", "jwt")
    runner = Cli(world, code=1, output=deploy_order.CLI_ERROR)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2

    err = capsys.readouterr().err
    assert "render did not rename the Gateway" in err and "render_agentcore_config.py" in err


def test_a_cli_that_cannot_be_run_is_an_error_not_a_traceback(tmp_path, capsys):
    world = World("jwt", "jwt")
    runner = Cli(world, error=deploy_order.DeployOrderError("could not run x: not found"))

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2

    captured = capsys.readouterr()
    assert "not found" in captured.err and "Traceback" not in captured.err


def test_the_default_runner_reports_a_missing_program_and_a_timeout(tmp_path, monkeypatch):
    import subprocess

    def missing(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(deploy_order.DeployOrderError, match="not found"):
        deploy_order.run_command(["/nope"], tmp_path)

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired("x", 1)
    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(deploy_order.DeployOrderError, match="did not finish"):
        deploy_order.run_command(["/slow"], tmp_path)


def test_the_stage_numbers_cover_the_four_stages():
    assert [deploy_order.stage_label(s) for s in stages.STAGES] == [
        "stage gateway (1 of 4)", "stage targets (2 of 4)", "stage governance (3 of 4)",
        "stage complete (4 of 4)"]
