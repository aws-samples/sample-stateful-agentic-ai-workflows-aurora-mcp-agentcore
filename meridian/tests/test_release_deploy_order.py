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
from datetime import timedelta

import pytest

from scripts import release_identity
from scripts.identity_release import deploy_order, snapshot, stages, settings
from tests import release_plan_fixtures as fx
from tests import release_support as rs
from tests.aws_recorders import Recorder, client_error
from tests.gateway_release_support import bare, current
from tests.test_release_identity_cli import NOW, env

FLAG = settings.CONFIRM_FLAG
DEPLOY_ARGV = ["/opt/homebrew/bin/agentcore", "deploy", "-y"]
DIFF_ARGV = ["/opt/homebrew/bin/agentcore", "deploy", "--diff", "--json"]
GATEWAY_TYPE = "AWS::BedrockAgentCore::Gateway"
JWT_REPLACES_IAM = fx.first_stage()
IAM_REPLACES_JWT = fx.first_stage(new=fx.IAM_GATEWAY, old=fx.JWT_GATEWAY)
FIRST_BUILD = fx.first_stage(new=fx.IAM_GATEWAY, old=None)
QUIET_PLAN = fx.complete_stage()
STATUS = fx.STATUS
CONSTRUCTS = {"jwt": fx.JWT_GATEWAY, "iam": fx.IAM_GATEWAY}
OTHER = {"jwt": "iam", "iam": "jwt"}


def gateway_of(mode, **extra):
    """The mode's Gateway as the control plane describes it, with its own id and role."""
    return bare(mode, gatewayId=f"gw-{mode}",
                roleArn=f"arn:aws:iam::{rs.ACCOUNT}:role/role-{mode}", **extra)


class Cloud:
    """The control plane's Gateways, by id, changed by the stand-in deploy."""

    def __init__(self, *modes, **extras):
        self.gateways = {f"gw-{mode}": gateway_of(mode, **extras.get(mode, {})) for mode in modes}
        self.targets = {gid: ["SemanticTripSearchLambda"] for gid in self.gateways}
        self.popped = {}
        self.calls = []

    def at_stage(self, mode, stage):
        """Make the mode's Gateway look like the stack just before ``stage`` is deployed."""
        gateway_id = f"gw-{mode}"
        if gateway_id not in self.gateways:
            return
        holds = stage in (stages.GOVERNANCE, stages.COMPLETE)
        self.targets[gateway_id] = ["SemanticTripSearchLambda"] + (
            [settings.HOLDS_TARGET] if holds else [])
        if stage in (stages.TARGETS, stages.GOVERNANCE):
            engine = self.gateways[gateway_id].pop("policyEngineConfiguration", None)
            if engine is not None:
                self.popped[gateway_id] = engine

    def restore_engines(self):
        """A deploy of the governance stage attaches the engine."""
        for gateway_id, engine in self.popped.items():
            if gateway_id in self.gateways:
                self.gateways[gateway_id]["policyEngineConfiguration"] = engine
        self.popped = {}

    def list_gateway_targets(self, gatewayIdentifier, **kwargs):
        self.calls.append("list_gateway_targets")
        return {"items": [{"name": name, "targetId": f"t-{name}"}
                          for name in self.targets[gatewayIdentifier]]}

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

    def __init__(self, world, plan=None, diff_code=0, code=0, output="deployed",
                 creates=None, removes=None, error=None, keeps_removed=False):
        super().__init__()
        self.keeps_removed = keeps_removed
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
            self.world.cloud.restore_engines()
            if self.removes and not self.keeps_removed:
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


def write_snapshot(release_dir, mode, hours_ago=1, gateway_id="live", account=rs.ACCOUNT,
                   region=rs.REGION, baseline=(), commit=rs.SHA):
    """A complete snapshot of the mode's Gateway, taken ``hours_ago`` hours before NOW."""
    taken = NOW - timedelta(hours=hours_ago)
    gateway = {"gatewayId": f"gw-{mode}" if gateway_id == "live" else gateway_id,
               "name": settings.gateway_physical_name(mode)}
    document = {
        "schema": snapshot.SCHEMA, "takenAt": taken.isoformat(), "commit": commit,
        "account": account, "region": region, "mode": mode, "gateway": gateway,
        "runtimes": {}, "service": {"ServiceArn": rs.SERVICE_ARN}, "site": {}, "roles": {},
        "lambdas": {}, "policies": {}, "baselineFindings": list(baseline), "redacted": [],
        "complete": True}
    snapshot.write(document, release_dir, taken)


def operator_snapshots(mode):
    """The snapshots an operator really has when the first stage of ``mode`` runs.

    A jwt release replaces the iam Gateway, saved before the window. A rollback to iam replaces
    the jwt Gateway, so a jwt snapshot was taken for the gate, and it is the newest file.
    """
    return ["iam"] if mode == "jwt" else ["iam", "jwt"]


PLANS = {stages.TARGETS: fx.targets_stage, stages.GOVERNANCE: fx.governance_stage,
         stages.COMPLETE: lambda gateway: fx.complete_stage()}


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


def run(argv, world, tmp_path, runner, rendered=None, deployed=None, snaps="auto",
        proof=True, environment=None, mode=None, stage=stages.COMPLETE, changes=None,
        configure_live=True):
    mode = mode or world.mode
    if configure_live:
        world.cloud.at_stage(mode, stage)
    if getattr(runner, "plan", False) is None:
        runner.plan = (PLANS[stage](CONSTRUCTS[mode]) if stage in PLANS
                       else (fx.first_stage() if mode == "jwt" else IAM_REPLACES_JWT))
    root = project(tmp_path, spec(mode, stage) if rendered is None else rendered,
                   state(mode, stage) if deployed is None else deployed)
    release = tmp_path / "release"
    modes = operator_snapshots(mode) if snaps == "auto" else snaps
    for index, kept in enumerate(modes):
        kwargs = {"mode": kept} if isinstance(kept, str) else dict(kept)
        write_snapshot(release, **{"hours_ago": len(modes) - index, **kwargs})
    receipt = tmp_path / "proof.json"
    if proof:
        receipt.write_text(json.dumps(rs.receipt(NOW)))
    deps = release_identity.Dependencies(
        env=environment or env(MERIDIAN_AGENTCORE_AUTH=mode), session=world.session,
        now=lambda: NOW, head_sha=lambda: rs.SHA, proof_path=receipt, release_dir=release,
        sleep=lambda seconds: None, agentcore_dir=root, run_command=runner,
        tree_changes=lambda: [] if changes is None else changes)
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
    ({"snaps": []}, "no complete snapshot"),
    ({"snaps": ["jwt"]}, "newest complete snapshot is of the jwt release"),
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
    runner = Cli(world, FIRST_BUILD, creates="iam")

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GATEWAY,
               snaps=[], environment=env(MERIDIAN_AGENTCORE_AUTH="iam")) == 0


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

    assert run(["deploy"], world, tmp_path, Cli(world), rendered="not json", snaps=[]) == 2
    assert "render_agentcore_config.py" in capsys.readouterr().err
    assert run(["deploy"], world, tmp_path, Cli(world), deployed="{broken", snaps=[]) == 2
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
    runner = Cli(world, fx.governance_stage())
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


def test_a_failed_deploy_sends_the_operator_to_the_stack_status_not_to_a_guess(tmp_path, capsys):
    world = World("jwt", "jwt")
    runner = Cli(world, code=1, output="boom: it failed")

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner)

    err = capsys.readouterr().err
    assert code == 2 and "deploy failed (exit 1)" in err
    assert "read the stack status in CloudFormation first" in err
    assert "as it was before this stage" not in err
    assert "release_identity.py check --skip-service" in err
    stuck = [line for line in err.splitlines() if "UPDATE_ROLLBACK_FAILED" in line]
    assert len(stuck) == 1 and "continue-update-rollback" in stuck[0]


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


# ------------------------------------------------------------ the tree must be clean


def test_the_first_stage_refuses_a_dirty_tree_and_names_the_files(tmp_path, capsys):
    world = replacement()
    runner = Cli(world)

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GATEWAY,
               changes=[" M scripts/render_agentcore_config.py", "?? notes.txt"])

    err = capsys.readouterr().err
    assert code == 2 and "uncommitted changes" in err
    assert "scripts/render_agentcore_config.py" in err and "commit" in err
    assert runner.diffs == [] and runner.deploys == []


def test_the_dry_run_of_the_first_stage_blocks_on_a_dirty_tree(tmp_path, capsys):
    world = replacement()

    assert run(["deploy"], world, tmp_path, Cli(world), stage=stages.GATEWAY,
               changes=[" M a.py"]) == 2

    assert "BLOCKED  the working tree has uncommitted changes" in capsys.readouterr().out


@pytest.mark.parametrize("stage", [stages.TARGETS, stages.GOVERNANCE, stages.COMPLETE])
def test_a_later_stage_does_not_ask_for_a_clean_tree(tmp_path, stage):
    world = World("jwt", "jwt")

    assert run(["deploy", "--apply", FLAG], world, tmp_path, Cli(world), stage=stage,
               changes=[" M a.py"]) == 0


# ------------------------------------------------- the snapshot must be of this Gateway


def first_stage_run(tmp_path, capsys, snaps, mode="jwt", apply=True, **kwargs):
    world = replacement(mode)
    plan = JWT_REPLACES_IAM if mode == "jwt" else IAM_REPLACES_JWT
    runner = Cli(world, plan, creates=mode, removes=OTHER[mode])
    argv = ["deploy", "--apply", FLAG] if apply else ["deploy"]
    code = run(argv, world, tmp_path, runner, stage=stages.GATEWAY, snaps=snaps, **kwargs)
    return code, capsys.readouterr(), runner


def test_a_snapshot_of_another_gateway_does_not_clear_the_deletion(tmp_path, capsys):
    code, captured, runner = first_stage_run(
        tmp_path, capsys, [{"mode": "iam", "gateway_id": "gw-an-older-one"}])

    assert code == 2 and "not of the Gateway that is about to be deleted" in captured.err
    assert "snapshot --service-arn" in captured.err and runner.deploys == []


def test_the_snapshot_that_matches_the_live_gateway_is_found_behind_a_newer_one_that_does_not(
        tmp_path, capsys):
    code, captured, _ = first_stage_run(
        tmp_path, capsys, ["iam", {"mode": "iam", "gateway_id": "gw-an-older-one"}])

    assert code == 0, captured.err
    assert "snapshot-20261008T100000Z.json" in captured.out and "commit" in captured.out


@pytest.mark.parametrize(("kwargs", "fragment"), [
    ({"account": "999999999999"}, "another account"),
    ({"region": "eu-west-1"}, "another Region"),
])
def test_a_snapshot_of_another_account_or_region_does_not_clear_the_deletion(
        tmp_path, capsys, kwargs, fragment):
    code, captured, runner = first_stage_run(tmp_path, capsys, [{"mode": "iam", **kwargs}])

    assert code == 2 and fragment in captured.err and runner.deploys == []
    assert "999999999999" not in captured.err


def test_the_dry_run_prints_the_snapshot_commit_and_age(tmp_path, capsys):
    code, captured, _ = first_stage_run(
        tmp_path, capsys, [{"mode": "iam", "hours_ago": 30}], apply=False)

    assert code == 0
    assert f"commit {rs.SHA[:12]}" in captured.out and "taken 1 day 6 h ago" in captured.out
    assert "snapshot-" in captured.out


def test_a_snapshot_with_accepted_findings_is_allowed_and_says_so(tmp_path, capsys):
    code, captured, _ = first_stage_run(
        tmp_path, capsys, [{"mode": "iam", "baseline": ["Service: drift"]}], apply=False)

    assert code == 0 and "1 baseline finding(s) accepted when it was taken" in captured.out


def test_the_rollback_gate_asks_for_a_jwt_snapshot_when_only_the_iam_one_exists(tmp_path, capsys):
    code, captured, runner = first_stage_run(tmp_path, capsys, ["iam"], mode="iam")

    assert code == 2 and "no complete snapshot of the jwt release" in captured.err
    assert "newest complete snapshot is of the iam release" in captured.err
    assert "MERIDIAN_COGNITO" in captured.err and "snapshot --service-arn" in captured.err
    assert runner.deploys == []


def test_the_rollback_gate_passes_with_the_operators_real_snapshots(tmp_path, capsys):
    code, captured, _ = first_stage_run(tmp_path, capsys, "auto", mode="iam")

    assert code == 0, captured.err


# ------------------------------------------------------ the plan must show its own stage


@pytest.mark.parametrize("stage", [stages.TARGETS, stages.GOVERNANCE, stages.COMPLETE])
def test_a_later_stage_whose_plan_parses_to_nothing_deploys_nothing(tmp_path, capsys, stage):
    world = World("jwt", "jwt")
    runner = Cli(world, "No stack differences detected.\n" + STATUS)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stage) == 2

    assert "no resource change lines" in capsys.readouterr().err and runner.deploys == []


def test_a_stage_whose_plan_is_another_stages_plan_deploys_nothing(tmp_path, capsys):
    world = World("jwt", "jwt")
    runner = Cli(world, fx.targets_stage())

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner,
               stage=stages.GOVERNANCE) == 2

    assert "expects" in capsys.readouterr().err and runner.deploys == []


# ----------------------------------------------- the live Gateway says which stage is next


def test_a_live_gateway_that_already_holds_the_holds_target_refuses_the_targets_stage(
        tmp_path, capsys):
    world = World("jwt", "jwt")
    runner = Cli(world, fx.targets_stage())
    world.cloud.at_stage("jwt", stages.GOVERNANCE)

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.TARGETS,
               configure_live=False)

    err = capsys.readouterr().err
    assert code == 2 and "live Gateway" in err and "stage governance" in err
    assert runner.deploys == []


def test_a_live_gateway_with_an_engine_refuses_the_governance_stage(tmp_path, capsys):
    world = World("jwt", "jwt")
    runner = Cli(world, fx.governance_stage())
    world.cloud.at_stage("jwt", stages.COMPLETE)

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GOVERNANCE,
               configure_live=False)

    err = capsys.readouterr().err
    assert code == 2 and "live Gateway" in err and "stage complete" in err
    assert runner.deploys == []


def test_a_live_gateway_without_the_holds_target_refuses_the_complete_stage(tmp_path, capsys):
    world = World("jwt", "jwt")
    runner = Cli(world, fx.complete_stage())
    world.cloud.at_stage("jwt", stages.TARGETS)

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.COMPLETE,
               configure_live=False)

    err = capsys.readouterr().err
    assert code == 2 and "live Gateway" in err and "stage targets" in err
    assert runner.deploys == []


# ----------------------------------------------------- reading the stack back after a deploy


def test_a_replaced_gateway_that_still_exists_after_the_deploy_is_drift_with_the_manual_step(
        tmp_path, capsys):
    world = replacement()
    runner = Cli(world, JWT_REPLACES_IAM, creates="jwt", removes="iam", keeps_removed=True)

    code = run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GATEWAY)

    out = capsys.readouterr().out
    assert code == 1
    assert "DRIFT  the replaced iam Gateway (gw-iam) still exists" in out
    assert "delete-gateway --gateway-identifier gw-iam" in out
    assert "delete-gateway-target" in out and "stack status in CloudFormation" in out


def test_a_replaced_gateway_that_is_gone_is_not_drift(tmp_path, capsys):
    world = replacement()
    runner = Cli(world, JWT_REPLACES_IAM, creates="jwt", removes="iam")

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner,
               stage=stages.GATEWAY) == 0

    assert "still exists" not in capsys.readouterr().out


def test_a_drift_after_a_deployed_stage_still_prints_the_next_steps(tmp_path, capsys):
    world = replacement()
    runner = Cli(world, JWT_REPLACES_IAM, creates="jwt", removes="iam", keeps_removed=True)

    run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GATEWAY)

    out = capsys.readouterr().out
    assert "do not run this stage again" in out and "render_agentcore_config.py" in out
    assert "sync_agentcore_env.py --write" in out


def test_a_read_back_that_fails_after_a_good_deploy_still_prints_the_next_steps(
        tmp_path, capsys):
    world = World("jwt", "jwt")
    runner = Cli(world, fx.targets_stage())
    original = runner.__call__

    def deploy_then_fail(argv, cwd):
        result = original(argv, cwd)
        if "--diff" not in argv:
            world.cloud.gateways["gw-jwt"]["status"] = "FAILED"
        return result

    code = run(["deploy", "--apply", FLAG], world, tmp_path, deploy_then_fail,
               stage=stages.TARGETS)

    captured = capsys.readouterr()
    assert code == 2 and "ended in FAILED" in captured.err
    assert "do not run this stage again" in captured.out
    assert "render_agentcore_config.py" in captured.out
    assert "stage governance" in captured.out


# --------------------------------------------------------------- what is printed


def test_the_deploy_output_tail_never_prints_pool_client_or_host(tmp_path, capsys):
    world = World("jwt", "jwt")
    noisy = (f"updating authorizer for pool {rs.POOL}\n"
             f"allowedClients: [{rs.CLIENT}]\n"
             f"login at https://{rs.HOSTED_UI_DOMAIN}/login\n"
             f"discovery https://cognito-idp.us-east-1.amazonaws.com/{rs.POOL}/.well-known/"
             "openid-configuration\n")
    runner = Cli(world, output=noisy)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 0

    out = capsys.readouterr().out
    for secret in (rs.POOL, rs.CLIENT, rs.HOSTED_UI_DOMAIN, "cognito-idp"):
        assert secret not in out, secret
    assert "<masked>" in out


def test_a_plan_that_cannot_be_read_never_prints_pool_client_or_host(tmp_path, capsys):
    world = World("jwt", "jwt")
    noisy = f'{{"AllowedClients": ["{rs.CLIENT}"], "pool": "{rs.POOL}"}}'
    runner = Cli(world, noisy)

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2

    err = capsys.readouterr().err
    assert "no resource change lines" in err
    assert rs.POOL not in err and rs.CLIENT not in err


def test_a_bare_client_id_in_an_unreadable_plan_is_hidden_by_value(tmp_path, capsys):
    world = World("jwt", "jwt")
    runner = Cli(world, f"something about {rs.CLIENT} and nothing parseable")

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner) == 2

    assert rs.CLIENT not in capsys.readouterr().err


def test_the_first_stage_touches_only_the_clients_and_calls_it_needs(tmp_path):
    world = replacement()
    runner = Cli(world, JWT_REPLACES_IAM, creates="jwt", removes="iam")
    lam_calls = []
    original = world.lam.get_function

    def watched(**kwargs):
        lam_calls.append(kwargs["FunctionName"])
        return original(**kwargs)

    world.lam.get_function = watched

    assert run(["deploy", "--apply", FLAG], world, tmp_path, runner, stage=stages.GATEWAY) == 0

    assert set(world.built) <= {"sts", "bedrock-agentcore-control", "iam", "cloudformation",
                                "lambda"}
    assert lam_calls and all(rs.INTERCEPTOR_ARN == name for name in lam_calls)
