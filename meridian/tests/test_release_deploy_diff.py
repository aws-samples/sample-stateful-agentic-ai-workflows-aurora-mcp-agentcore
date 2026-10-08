"""The plan gate reads the CDK text diff of `agentcore deploy --diff --json` for each stage.

The format is read from the installed CLI's source, which prints the formatted diff (lines like
``[+] AWS::BedrockAgentCore::Gateway <path> <logical id>`` with ``├─ [~] Property`` children),
then a JSON status object. The gate fails closed: a stage that must add a Gateway and finds no
change lines at all is a finding that shows the tail of the output.
"""

from __future__ import annotations

import pytest

from scripts.identity_release import deploy_diff, stages
from tests import release_plan_fixtures as fx
from tests import release_support as rs

ARGV = ("/opt/homebrew/bin/agentcore", "deploy", "--diff", "--json")
ESC = "\x1b"
STATUS = '{"success":true,"targetName":"default","stackName":"AgentCore-meridianv2-default"}'
GATEWAY = "AWS::BedrockAgentCore::Gateway"
TARGET = "AWS::BedrockAgentCore::GatewayTarget"
OLD = "[-] {} Mcp/GatewayMeridianAurora/Resource McpGatewayMeridianAuroraAB12CD34"
NEW = "[+] {} Mcp/GatewayMeridianAuroraJwt/Resource McpGatewayMeridianAuroraJwt1A2B3C4D"
RUNTIME = ("[~] AWS::BedrockAgentCore::Runtime Application/MeridianConcierge/Resource Mer12\n"
           " ├─ [~] AuthorizerConfiguration\n"
           " │   └─ [+] Added: .CustomJWTAuthorizer\n"
           " └─ [~] RequestHeaderConfiguration\n")


def plan(*blocks: str) -> str:
    return "Stack AgentCore-meridianv2-default\nResources\n" + "\n".join(blocks) + "\n" + STATUS


def findings(output, stage=stages.GATEWAY, mode="jwt", replacing=True, code=0):
    return deploy_diff.plan_findings(code, output, ARGV, stage=stage, mode=mode,
                                     replacing=replacing)


def ansi(text: str) -> str:
    return "\n".join(f"{ESC}[32m{line}{ESC}[39m" if line.startswith("[+]") else line
                     for line in text.splitlines())


REPLACEMENT = fx.first_stage()


# ---------------------------------------------------------------- the parser


def test_the_parser_reads_kinds_types_the_rest_of_the_line_and_children():
    changes = deploy_diff.parse(plan(RUNTIME, NEW.format(GATEWAY)))

    assert [c.kind for c in changes] == ["~", "+"]
    assert changes[0].type == "AWS::BedrockAgentCore::Runtime"
    assert "Application/MeridianConcierge/Resource" in changes[0].rest
    assert changes[0].children == ("AuthorizerConfiguration", "Added: .CustomJWTAuthorizer",
                                   "RequestHeaderConfiguration")
    assert changes[1].children == ()


def test_the_parser_ignores_ansi_colour_and_the_status_object():
    assert deploy_diff.parse(ansi(REPLACEMENT)) == deploy_diff.parse(REPLACEMENT)


def test_output_with_no_change_lines_parses_to_nothing():
    assert deploy_diff.parse("No stack differences detected.\n" + STATUS) == []


def test_a_child_line_is_never_taken_for_a_resource():
    assert deploy_diff.parse(" ├─ [-] AWS::Lambda::Function x y\n") == []


# ------------------------------------------------------- the replacement stage


def test_the_replacement_plan_passes_for_jwt():
    assert findings(REPLACEMENT) == []
    assert findings(ansi(REPLACEMENT)) == []


def test_the_iam_replacement_plan_passes_for_iam():
    swapped = fx.first_stage(new=fx.IAM_GATEWAY, old=fx.JWT_GATEWAY)

    assert findings(swapped, mode="iam") == []


def test_the_construct_prefix_trap_is_not_a_match():
    only_old_added = fx.first_stage(new=fx.IAM_GATEWAY, old=fx.JWT_GATEWAY)

    found = findings(only_old_added, mode="jwt")

    assert any("add exactly one Gateway" in line and "GatewayMeridianAuroraJwt" in line
               for line in found)
    assert findings(only_old_added, mode="iam") == []


def test_the_iam_gateway_is_matched_by_its_logical_id_alone_but_not_the_jwt_one():
    bare_ids = plan("[+] AWS::BedrockAgentCore::Gateway McpGatewayMeridianAuroraAB12CD34",
                    "[-] AWS::BedrockAgentCore::Gateway McpGatewayMeridianAuroraJwt1A2B3C4D")

    assert findings(bare_ids, mode="iam") == []
    assert findings(bare_ids, mode="jwt") != []


def test_the_jwt_gateway_added_in_the_iam_release_is_not_the_iam_gateway():
    wrong_way_round = fx.first_stage()

    found = findings(wrong_way_round, mode="iam")

    assert any("add exactly one Gateway" in line and "GatewayMeridianAurora," in line
               for line in found)


def test_a_target_is_not_a_gateway():
    targets = plan(NEW.format(TARGET), OLD.format(TARGET))

    found = findings(targets)

    assert any("add exactly one Gateway" in line for line in found)


def test_a_replacement_that_adds_a_second_gateway_is_refused():
    found = findings(plan(NEW.format(GATEWAY), NEW.format(GATEWAY), OLD.format(GATEWAY)))

    assert any("add exactly one Gateway" in line for line in found)


def test_a_replacement_that_removes_no_gateway_is_refused_when_one_is_being_replaced():
    found = findings(fx.first_stage(old=None))

    assert len(found) == 1 and "remove exactly one Gateway" in found[0]


def test_a_first_ever_build_removes_nothing_and_a_plan_that_removes_anything_is_refused():
    assert findings(fx.first_stage(old=None), replacing=False) == []
    found = findings(REPLACEMENT, replacing=False)
    assert any("deletes AWS::BedrockAgentCore::Gateway " in line for line in found)
    assert any("deletes AWS::Lambda::Function" in line for line in found)


def test_removing_two_gateways_is_refused():
    found = findings(plan(NEW.format(GATEWAY), OLD.format(GATEWAY), OLD.format(GATEWAY)))

    assert any("remove exactly one Gateway" in line for line in found)


def test_the_gateway_removed_must_be_the_other_modes_not_just_any():
    elsewhere = plan(NEW.format(GATEWAY),
                     "[-] AWS::BedrockAgentCore::Gateway Mcp/GatewayElsewhere McpGatewayElsewhere1")

    found = findings(elsewhere)

    assert any("remove exactly one Gateway" in line and "GatewayMeridianAurora" in line
               for line in found)


def test_the_first_stage_may_remove_only_what_belongs_to_the_replaced_gateway_or_the_engine():
    stray = [fx.line("-", "AWS::Lambda::Function", ("Mcp/Elsewhere", "McpElsewhere")),
             fx.line("-", "AWS::IAM::Role", ("Mcp/GatewayMeridianAuroraJwt", "McpGatewayJwt"),
                     "Role"),
             "[-] AWS::DynamoDB::Table Other/Table Table1",
             "[-] AWS::BedrockAgentCore::Memory Application/MemoryMeridianSession Mem1"]
    original = fx.first_stage().replace(fx.STATUS, "")

    found = findings(original + "\n".join(stray) + "\n" + fx.STATUS)

    assert len([line for line in found if "deletes" in line]) == 4
    assert not any("GatewayMeridianAurora/" in line for line in found)


def test_the_first_stage_removes_only_this_projects_engine_and_rules():
    other_engine = fx.line("-", "AWS::BedrockAgentCore::PolicyEngine",
                           ("Application/PolicyEngineSomethingElse", "ApplicationPolicyEngineX"))

    found = findings(REPLACEMENT.replace(fx.STATUS, other_engine + "\n" + fx.STATUS))

    assert len(found) == 1 and "PolicyEngineSomethingElse" in found[0]


def test_the_replaced_gateways_own_constructs_and_the_engine_are_not_findings():
    removed = [c for c in deploy_diff.parse(REPLACEMENT) if c.kind == "-"]

    assert len(removed) == 13  # gateway, role, policy, 2 targets, holds role, policy, function,
    assert findings(REPLACEMENT) == []  # log group, engine and three rules


def test_no_change_lines_fail_closed_and_show_the_tail():
    found = findings("something else entirely\nline two\n" + STATUS)

    assert len(found) == 1 and "no resource change lines" in found[0]
    assert "something else entirely" in found[0] and "line two" in found[0]


def test_a_diff_that_failed_cannot_clear_the_deploy():
    found = findings("boom\nsecond", code=1)

    assert len(found) == 1 and "exited 1" in found[0] and "boom" in found[0]


# ------------------------------------------------ an authorizer change is never ok


@pytest.mark.parametrize("stage", stages.STAGES)
def test_an_in_place_authorizer_change_is_refused_in_every_stage(stage):
    changed = plan("[~] AWS::BedrockAgentCore::Gateway Mcp/GatewayMeridianAurora/Resource G1\n"
                   " ├─ [~] AuthorizerType\n"
                   " │   ├─ [-] AWS_IAM\n"
                   " │   └─ [+] CUSTOM_JWT\n"
                   " └─ [+] AuthorizerConfiguration")

    found = findings(changed, stage=stage, replacing=False)

    assert any("changes a Gateway's authorizer type" in line and "render" in line
               for line in found)


def test_a_gateway_update_that_leaves_the_authorizer_alone_is_fine_after_the_first_stage():
    assert findings(fx.governance_stage(), stage=stages.GOVERNANCE, replacing=False) == []


# ------------------------------------------------------------------ later stages


@pytest.mark.parametrize("stage", [stages.TARGETS, stages.GOVERNANCE, stages.COMPLETE])
def test_later_stages_add_and_remove_no_gateway(stage):
    assert any("Gateway" in line for line in findings(plan(NEW.format(GATEWAY)), stage=stage))
    assert any("Gateway" in line for line in findings(plan(OLD.format(GATEWAY)), stage=stage))


@pytest.mark.parametrize("kind", ["BedrockAgentCore::Gateway", "Lambda::Function",
                                  "BedrockAgentCore::GatewayTarget",
                                  "BedrockAgentCore::PolicyEngine", "BedrockAgentCore::Policy",
                                  "BedrockAgentCore::Runtime", "BedrockAgentCore::Memory",
                                  "IAM::Role", "Logs::LogGroup"])
@pytest.mark.parametrize("stage", [stages.TARGETS, stages.GOVERNANCE, stages.COMPLETE])
def test_a_later_stage_refuses_any_removal(kind, stage):
    removed = plan(f"[-] AWS::{kind} Some/Path Logical1")

    found = findings(removed, stage=stage, replacing=False)

    assert any("deletes" in line and f"AWS::{kind}" in line for line in found)


@pytest.mark.parametrize("kind", ["BedrockAgentCore::Runtime", "BedrockAgentCore::Memory"])
def test_the_first_stage_never_removes_a_runtime_or_memory(kind):
    found = findings(REPLACEMENT.replace(
        fx.STATUS, f"[-] AWS::{kind} Application/Thing Thing1\n" + fx.STATUS))

    assert any("deletes" in line and f"AWS::{kind}" in line for line in found)


def test_the_real_shaped_plans_of_the_later_stages_pass():
    assert findings(fx.targets_stage(), stage=stages.TARGETS, replacing=False) == []
    assert findings(fx.governance_stage(), stage=stages.GOVERNANCE, replacing=False) == []
    assert findings(fx.complete_stage(), stage=stages.COMPLETE, replacing=False) == []


# ------------------------------------------- every stage needs its own positive change


@pytest.mark.parametrize("stage", [stages.TARGETS, stages.GOVERNANCE, stages.COMPLETE])
@pytest.mark.parametrize("output", [
    "No stack differences detected.\n" + STATUS,
    "something the parser does not know\n" + STATUS,
    ""])
def test_a_later_stage_whose_plan_parses_to_nothing_is_refused(stage, output):
    found = findings(output, stage=stage, replacing=False)

    assert len(found) == 1 and "no resource change lines" in found[0]


@pytest.mark.parametrize(("stage", "plan_text", "missing"), [
    (stages.TARGETS, fx.complete_stage(), "AWS::BedrockAgentCore::GatewayTarget"),
    (stages.TARGETS, fx.plan(fx.line("+", "AWS::BedrockAgentCore::GatewayTarget",
                                     fx.JWT_GATEWAY, "TargetMeridianHolds")),
     "AWS::Lambda::Function"),
    (stages.TARGETS, fx.plan(fx.line("+", "AWS::Lambda::Function", fx.JWT_GATEWAY,
                                     "LambdaMeridianHolds/Function")),
     "AWS::BedrockAgentCore::GatewayTarget"),
    (stages.GOVERNANCE, fx.complete_stage(), "AWS::BedrockAgentCore::PolicyEngine"),
    (stages.GOVERNANCE, fx.plan(fx.engine_lines("+")[0]), "AWS::BedrockAgentCore::Policy"),
    (stages.GOVERNANCE, fx.plan(*fx.engine_lines("+")[1:]), "AWS::BedrockAgentCore::PolicyEngine"),
    (stages.COMPLETE, fx.targets_stage().replace("[~]", "[+]"), "AWS::BedrockAgentCore::Runtime"),
    (stages.COMPLETE, fx.plan(*fx.engine_lines("+")), "AWS::BedrockAgentCore::Runtime"),
])
def test_a_later_stage_needs_its_expected_change_not_just_no_removal(
        stage, plan_text, missing):
    found = findings(plan_text, stage=stage, replacing=False)

    assert any("expects" in line and missing in line.split("it does not")[1]
               for line in found), found


def test_a_stage_two_plan_in_a_stage_three_deploy_is_refused():
    found = findings(fx.targets_stage(), stage=stages.GOVERNANCE, replacing=False)

    assert any("expects" in line and "AWS::BedrockAgentCore::PolicyEngine" in line
               for line in found)


# --------------------------------------------------------------- what the tail shows


def test_the_tail_masks_pool_ids_client_ids_and_the_hosted_host():
    pool, client = rs.POOL, rs.CLIENT
    noisy = (f"hosted https://d.auth.us-east-1.amazoncognito.com/oauth2\n"
             f"https://cognito-idp.us-east-1.amazonaws.com/{pool}/.well-known/openid-configuration\n"
             f'"AllowedClients": ["{client}"]\npool {pool} account 123456789012\n')

    found = findings(noisy + STATUS)

    text = " ".join(found)
    for secret in (pool, client, "auth.us-east-1.amazoncognito.com", "cognito-idp"):
        assert secret not in text, secret
    assert "<pool>" in text


def test_a_failed_diffs_tail_is_masked_too():
    found = findings(f"boom {rs.POOL} \"allowedClients\": [\"{rs.CLIENT}\"]", code=1)

    assert rs.POOL not in found[0] and rs.CLIENT not in found[0]
