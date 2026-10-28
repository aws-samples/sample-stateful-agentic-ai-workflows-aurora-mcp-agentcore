"""The plan gate reads the CDK text diff of `agentcore deploy --diff --json` for each stage.

The format is read from the installed CLI's source, which prints the formatted diff (lines like
``[+] AWS::BedrockAgentCore::Gateway <path> <logical id>`` with ``├─ [~] Property`` children),
then a JSON status object. The gate fails closed: a stage that must add a Gateway and finds no
change lines at all is a finding that shows the tail of the output.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

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

    assert len(removed) == 14  # gateway, role, policy, 2 targets, holds role, policy, function,
    assert findings(REPLACEMENT) == []  # log group, engine and four rules


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


# ------------------------------------------- the catalog release at the complete stage

CATALOG_ADDED = fx.line("+", TARGET, fx.JWT_GATEWAY, "TargetMeridianCatalog")
HOLDS_TARGET_UPDATED = fx.line("~", TARGET, fx.JWT_GATEWAY, "TargetMeridianHolds")
HOLDS_FUNCTION_UPDATED = fx.line(
    "~", "AWS::Lambda::Function", fx.JWT_GATEWAY, "LambdaMeridianHolds/Function")
RULE_UPDATED = fx.line("~", "AWS::BedrockAgentCore::Policy", fx.ENGINE, "Policymeridian_read_tools")
SEMANTIC_REMOVED = fx.line("-", TARGET, fx.JWT_GATEWAY, "TargetSemanticTripSearchLambda")


def complete(*blocks):
    return findings(fx.plan(*blocks), stage=stages.COMPLETE, replacing=False)


def test_deploy_a_adds_the_catalog_target_and_updates_the_holds_target_and_function():
    assert complete(CATALOG_ADDED, HOLDS_TARGET_UPDATED, HOLDS_FUNCTION_UPDATED) == []


def test_deploy_b_changes_a_rule_retires_the_semantic_target_and_updates_the_runtime():
    assert complete(RULE_UPDATED, SEMANTIC_REMOVED, fx.CONCIERGE) == []


OTHER_REMOVALS = [
    fx.line("-", TARGET, fx.JWT_GATEWAY, "TargetMeridianHolds"),
    fx.line("-", TARGET, fx.JWT_GATEWAY, "TargetMeridianCatalog"),
    fx.line("-", TARGET, fx.JWT_GATEWAY, "TargetSemanticTripSearchLambdaV2"),
    fx.line("-", TARGET, fx.JWT_GATEWAY, "TargetOldSemanticTripSearchLambda"),
    fx.line("-", "AWS::Lambda::Function", fx.JWT_GATEWAY, "LambdaMeridianHolds/Function"),
    fx.line("-", "AWS::Lambda::Function", fx.JWT_GATEWAY, "TargetSemanticTripSearchLambda"),
    fx.line("-", "AWS::BedrockAgentCore::Policy", fx.ENGINE, "Policymeridian_read_tools"),
    fx.line("-", "AWS::BedrockAgentCore::PolicyEngine", fx.ENGINE),
    fx.line("-", GATEWAY, fx.JWT_GATEWAY),
    fx.line("-", "AWS::BedrockAgentCore::Runtime", (
        "Application/AgentMeridianConcierge/Runtime",
        "ApplicationAgentMeridianConciergeRuntime")),
]
STAGE_PLANS = {
    stages.GATEWAY: lambda: fx.first_stage(old=None),
    stages.TARGETS: lambda: fx.targets_stage(),
    stages.GOVERNANCE: lambda: fx.governance_stage(),
    stages.COMPLETE: lambda: fx.plan(RULE_UPDATED, SEMANTIC_REMOVED, fx.CONCIERGE),
}


@pytest.mark.parametrize("removed", OTHER_REMOVALS)
@pytest.mark.parametrize("stage", list(STAGE_PLANS))
def test_a_removal_other_than_the_retired_target_is_refused_at_every_stage(stage, removed):
    output = STAGE_PLANS[stage]() + "\n" + removed

    found = findings(output, stage=stage, replacing=False)

    assert any(removed.split()[2] in line and "the plan deletes" in line for line in found)


@pytest.mark.parametrize("stage", [stages.TARGETS, stages.GOVERNANCE])
def test_the_retired_target_may_not_go_in_an_earlier_stage(stage):
    output = STAGE_PLANS[stage]() + "\n" + SEMANTIC_REMOVED

    found = findings(output, stage=stage, replacing=False)

    assert any("the plan deletes" in line for line in found)


def test_the_first_stage_still_removes_only_the_replaced_gateways_constructs():
    removed = fx.first_stage(old=None) + "\n" + SEMANTIC_REMOVED

    found = findings(removed, replacing=False)

    assert any("the plan deletes" in line for line in found)


def test_a_complete_plan_with_none_of_the_catalog_changes_is_still_refused():
    found = complete(fx.line("~", "AWS::IAM::Role", fx.JWT_GATEWAY, "LambdaMeridianHolds/Role"))

    assert any("AWS::BedrockAgentCore::Runtime" in line and "MeridianCatalog" in line
               for line in found)


def test_a_complete_plan_adding_a_different_target_is_refused():
    found = complete(fx.line("+", TARGET, fx.JWT_GATEWAY, "TargetMeridianHolds"))

    assert any("expects" in line for line in found)


def test_a_complete_plan_adding_a_lookalike_catalog_target_is_refused():
    found = complete(fx.line("+", TARGET, fx.JWT_GATEWAY, "TargetMeridianCatalogExtra"))

    assert any("expects" in line for line in found)


def test_a_complete_plan_adding_a_second_gateway_is_still_refused():
    found = complete(CATALOG_ADDED, *fx.construct_lines("+", fx.IAM_GATEWAY, *fx.GATEWAY_PARTS))

    assert any("must not add a Gateway" in line for line in found)


def test_an_in_place_authorizer_change_is_still_refused_in_a_catalog_release():
    changed = ("[~] AWS::BedrockAgentCore::Gateway Mcp/GatewayMeridianAuroraJwt/Resource Gw1\n"
               " └─ [~] AuthorizerType\n")

    found = complete(CATALOG_ADDED, changed)

    assert any("authorizer type in place" in line for line in found)


# ------------------------------------------- retiring a target needs the new Runtime

def test_retiring_the_target_with_only_a_rule_update_is_refused():
    found = complete(RULE_UPDATED, SEMANTIC_REMOVED)

    assert any("retired" in line and "Concierge Runtime" in line for line in found)


def test_retiring_the_target_with_only_the_catalog_add_is_refused():
    found = complete(CATALOG_ADDED, SEMANTIC_REMOVED)

    assert any("retired" in line and "Concierge Runtime" in line for line in found)


def test_retiring_the_target_with_the_runtime_and_a_rule_passes():
    assert complete(RULE_UPDATED, SEMANTIC_REMOVED, fx.CONCIERGE) == []
    assert complete(SEMANTIC_REMOVED, fx.CONCIERGE) == []


WORKFLOW_RUNTIME = (
    "[~] AWS::BedrockAgentCore::Runtime Application/AgentMeridianWorkflow/Runtime "
    "ApplicationAgentMeridianWorkflowRuntime1A2B3C4D\n"
    " └─ [~] RequestHeaderConfiguration\n")
LOOKALIKE_RUNTIME = WORKFLOW_RUNTIME.replace("AgentMeridianWorkflow", "AgentMeridianConciergeV2")


def test_retiring_the_target_with_only_the_workflow_runtime_updated_is_refused():
    found = complete(RULE_UPDATED, SEMANTIC_REMOVED, WORKFLOW_RUNTIME)

    assert any("retired" in line and "Concierge Runtime" in line for line in found)


def test_retiring_the_target_with_a_lookalike_concierge_runtime_is_refused():
    found = complete(RULE_UPDATED, SEMANTIC_REMOVED, LOOKALIKE_RUNTIME)

    assert any("retired" in line and "Concierge Runtime" in line for line in found)


@pytest.mark.parametrize("name", ["AgentMeridianConcierge_x", "AgentMeridianConcierge-x"])
def test_retiring_the_target_with_a_separator_lookalike_runtime_is_refused(name):
    lookalike = WORKFLOW_RUNTIME.replace("AgentMeridianWorkflow", name)
    found = complete(RULE_UPDATED, SEMANTIC_REMOVED, lookalike)

    assert any("retired" in line and "Concierge Runtime" in line for line in found)


def test_retiring_the_target_with_a_nested_concierge_path_is_refused():
    nested = fx.CONCIERGE.replace("/AgentMeridianConcierge/Runtime ",
                                  "/AgentMeridianConcierge/Role ")
    found = complete(RULE_UPDATED, SEMANTIC_REMOVED, nested)

    assert any("retired" in line and "Concierge Runtime" in line for line in found)


def test_retiring_the_target_passes_when_both_runtimes_are_updated():
    assert complete(RULE_UPDATED, SEMANTIC_REMOVED, WORKFLOW_RUNTIME, fx.CONCIERGE) == []


@pytest.mark.parametrize("path", [
    "MeridianCatalog",
    "Mcp/GatewayMeridianAuroraJwt/Targets/MeridianCatalog",
    "Mcp/GatewayMeridianAuroraJwt/Target-MeridianCatalog",
])
def test_an_unknown_target_path_shape_is_not_taken_for_the_catalog_add(path):
    added = f"[+] {TARGET} {path} McpGatewayMeridianAuroraJwtTargetsCatalog9F3A1C2B"

    found = complete(added)

    assert any("expects" in line for line in found)


@pytest.mark.parametrize("path", [
    "SemanticTripSearchLambda",
    "Mcp/GatewayMeridianAuroraJwt/Targets/SemanticTripSearchLambda",
])
def test_an_unknown_target_path_shape_is_not_taken_for_the_retire(path):
    removed = f"[-] {TARGET} {path} McpGatewayMeridianAuroraJwtSemantic9F3A1C2B"

    found = complete(RULE_UPDATED, removed, fx.CONCIERGE)

    assert any("the plan deletes" in line for line in found)


# ------------------------------------------- the code-only Lambda hotfix at the complete stage

FUNCTION = "AWS::Lambda::Function"
RULE_NAME = deploy_diff.CODE_ONLY_RULE
REAL_DIR = Path(__file__).parent / "fixtures" / "real_plans"
# Copied verbatim from the live Release 2 Deploy A plan (hashes only, no account id).
REAL_HOLDS_CODE = """\
[~] AWS::Lambda::Function Mcp/GatewayMeridianAuroraJwt/LambdaMeridianHolds/Function \
McpGatewayMeridianAuroraJwtLambdaMeridianHoldsFunction0C6BCAE7
 └─ [~] Code
     └─ [~] .S3Key:
         ├─ [-] 8c09b37123eb0d9dcec63788149e576b1614ed481ad002512d0a47a09cbf59fc.zip
         └─ [+] c1db13ab836db24060490c83b83c089adb1ce9e6e1899d81e02d398cec22e6b1.zip"""
# The shape of the Runtime's EnvironmentVariables lines in the same plan.
ENVIRONMENT = """\
 └─ [~] EnvironmentVariables
     ├─ [~] .BEDROCK_MAX_TOKENS:
     │   ├─ [-] 2048
     │   └─ [+] 16000
     └─ [-] Removed: .BEDROCK_REASONING_EFFORT"""
# The shape of the target's Description lines in the same plan.
REAL_HOLDS_TARGET = """\
[~] AWS::BedrockAgentCore::GatewayTarget Mcp/GatewayMeridianAuroraJwt/TargetMeridianHolds \
McpGatewayMeridianAuroraJwtTargetMeridianHolds572A7857
 ├─ [~] Description
 │   ├─ [-] Read one Meridian trip package.
 │   └─ [+] Read one Meridian trip package; read the loyalty balance."""
HOLDS_CODE_UPDATED = REAL_HOLDS_CODE
INTERCEPTOR_CODE_UPDATED = REAL_HOLDS_CODE.replace("LambdaMeridianHolds", "LambdaTravelerPin")
CATALOG_TARGET_UPDATED = fx.line("~", TARGET, fx.JWT_GATEWAY, "TargetMeridianCatalog")
RUNTIME_ID = ("Application/AgentMeridianWorkflow/Runtime",
              "ApplicationAgentMeridianWorkflowRuntime")
RUNTIME_UPDATED = fx.line("~", "AWS::BedrockAgentCore::Runtime", RUNTIME_ID)


def holds_with(extra: str) -> str:
    """The real holds function block with one more top-level property block after Code."""
    return REAL_HOLDS_CODE.replace(" └─ [~] Code", " ├─ [~] Code") + "\n" + extra


def scalar(name: str) -> str:
    return f" └─ [~] {name}\n     ├─ [-] old\n     └─ [+] new"


def rule_refused(*blocks, stage=stages.COMPLETE):
    found = findings(fx.plan(*blocks), stage=stage, replacing=False)
    return [line for line in found if RULE_NAME in line]


def test_a_plan_that_only_updates_function_code_passes_the_complete_stage():
    assert complete(HOLDS_CODE_UPDATED) == []


def test_a_plan_that_updates_the_code_of_two_functions_passes():
    assert complete(HOLDS_CODE_UPDATED, INTERCEPTOR_CODE_UPDATED) == []


def test_the_live_code_change_passes_with_its_value_lines_read_as_values():
    assert complete(REAL_HOLDS_CODE) == []


def test_metadata_beside_code_passes():
    assert complete(holds_with(scalar("Metadata"))) == []


def test_the_live_deploy_a_holds_function_block_alone_passes_as_the_hotfix():
    lines = (REAL_DIR / "deploy_a_diff.txt").read_text().splitlines()
    start = next(i for i, text in enumerate(lines)
                 if text.startswith("[~] AWS::Lambda::Function"))
    block = "\n".join(lines[start:start + 5])
    status = lines[-1]

    assert block == REAL_HOLDS_CODE
    assert findings("Stack X\n" + block + "\n" + status, stage=stages.COMPLETE,
                    replacing=False) == []


@pytest.mark.parametrize("child", [
    "Role", "Handler", "Runtime", "Timeout", "MemorySize", "Layers", "VpcConfig",
    "FunctionName", "SomethingNew",
])
def test_a_function_with_any_other_property_is_refused_and_the_property_is_named(child):
    found = rule_refused(holds_with(scalar(child)))

    assert len(found) == 1
    assert f"changes {child}" in found[0]


def test_an_environment_change_in_the_live_shape_is_refused_by_name():
    found = rule_refused(holds_with(ENVIRONMENT))

    assert len(found) == 1
    assert "changes EnvironmentVariables" in found[0]


def test_an_added_field_under_code_is_refused():
    text = REAL_HOLDS_CODE + "\n     └─ [+] Added: .ZipFile"

    assert "changes Code.ZipFile" in rule_refused(text)[0]


def test_an_unknown_field_nested_under_code_is_refused():
    text = (REAL_HOLDS_CODE + "\n     └─ [~] .ImageUri:"
            "\n         ├─ [-] a\n         └─ [+] b")

    assert "changes Code.ImageUri" in rule_refused(text)[0]


def test_value_lines_are_not_taken_for_properties_so_a_hash_is_never_named():
    found = rule_refused(holds_with(scalar("Role")))

    new_hash = REAL_HOLDS_CODE.splitlines()[-1].split()[-1][:8]

    assert new_hash not in found[0]


def test_a_function_update_with_no_property_lines_is_refused():
    found = rule_refused(HOLDS_FUNCTION_UPDATED)

    assert len(found) == 1
    assert "no property lines shown" in found[0]


def test_a_function_plus_the_concierge_runtime_follows_the_existing_rule():
    assert complete(HOLDS_FUNCTION_UPDATED, fx.CONCIERGE) == []


@pytest.mark.parametrize("rider", [RULE_UPDATED, RUNTIME_UPDATED])
def test_a_runtime_or_rule_beside_a_function_passes_by_the_old_rules(rider):
    assert complete(HOLDS_FUNCTION_UPDATED, rider) == []


CODE_ONLY_RIDERS = [
    fx.line("+", FUNCTION, fx.JWT_GATEWAY, "LambdaOtherThing/Function"),
    fx.line("+", TARGET, fx.JWT_GATEWAY, "TargetMeridianHolds"),
    fx.line("+", "AWS::Logs::LogGroup", fx.JWT_GATEWAY, "LambdaMeridianHolds/Function/LogGroup"),
    fx.line("+", "AWS::IAM::Role", fx.JWT_GATEWAY, "LambdaMeridianHolds/Role"),
    fx.line("-", FUNCTION, fx.JWT_GATEWAY, "LambdaOtherThing/Function"),
    fx.line("-", TARGET, fx.JWT_GATEWAY, "TargetMeridianCatalog"),
    fx.line("-", TARGET, fx.JWT_GATEWAY, "TargetSemanticTripSearchLambda"),
    fx.line("~", "AWS::BedrockAgentCore::PolicyEngine", fx.ENGINE),
    fx.line("~", GATEWAY, fx.JWT_GATEWAY),
    fx.line("~", "AWS::IAM::Role", fx.JWT_GATEWAY, "LambdaMeridianHolds/Role"),
    fx.line("~", "AWS::IAM::Policy", fx.JWT_GATEWAY, "LambdaMeridianHolds/Policy"),
    fx.line("~", "AWS::Lambda::Permission", fx.JWT_GATEWAY, "LambdaMeridianHolds/Permission"),
    fx.line("~", "AWS::Logs::LogGroup", fx.JWT_GATEWAY, "LambdaMeridianHolds/Function/LogGroup"),
    fx.line("~", "AWS::SSM::Parameter", fx.JWT_GATEWAY, "Parameter"),
    HOLDS_TARGET_UPDATED,
    CATALOG_TARGET_UPDATED,
]


@pytest.mark.parametrize("rider", CODE_ONLY_RIDERS)
def test_anything_beyond_function_code_updates_is_refused_by_name(rider):
    found = rule_refused(HOLDS_CODE_UPDATED, rider)

    assert len(found) == 1
    assert "AWS::BedrockAgentCore::Runtime" in found[0]


def test_a_target_beside_the_functions_code_is_refused_and_the_reason_is_given():
    found = rule_refused(HOLDS_CODE_UPDATED, REAL_HOLDS_TARGET)

    assert "TargetMeridianHolds" in found[0]
    assert "tool schema or description change" in found[0]


def test_a_gateway_target_alone_is_refused_with_the_reason():
    found = rule_refused(REAL_HOLDS_TARGET)

    assert "tool schema or description change" in found[0]


def test_an_added_catalog_target_still_passes_without_the_allowance():
    assert complete(CATALOG_ADDED) == []


@pytest.mark.parametrize("stage", [stages.TARGETS, stages.GOVERNANCE, stages.GATEWAY])
def test_the_allowance_does_not_apply_at_other_stages(stage):
    found = findings(fx.plan(HOLDS_CODE_UPDATED), stage=stage, replacing=False)

    assert found
    assert not any(RULE_NAME in line for line in found)


def test_a_complete_plan_with_no_function_update_does_not_say_the_rule_did_not_apply():
    found = complete(fx.line("~", "AWS::IAM::Role", fx.JWT_GATEWAY, "LambdaMeridianHolds/Role"))

    assert found
    assert not any("did not apply" in line for line in found)


def test_an_added_function_alone_does_not_say_the_rule_did_not_apply():
    found = complete(fx.line("+", FUNCTION, fx.JWT_GATEWAY, "Lx/Function"))

    assert found
    assert not any("did not apply" in line for line in found)


def test_the_refusal_names_the_rule_and_says_what_stopped_it():
    added = fx.line("+", FUNCTION, fx.JWT_GATEWAY, "Lx/Function")

    found = rule_refused(HOLDS_CODE_UPDATED, added)

    assert "did not apply" in found[0]


# ------------------------------------------- the live Release 2 plans, unchanged by the allowance

REAL_PLANS = {name: (REAL_DIR / f"{name}_diff.txt").read_text()
              for name in ("deploy_a", "deploy_b")}


@pytest.mark.parametrize("name", list(REAL_PLANS))
def test_the_live_complete_plans_pass_by_the_normal_rules_not_the_hotfix(name):
    changes = deploy_diff.parse(REAL_PLANS[name])

    assert deploy_diff._code_only_refusal(changes) is not None
    assert findings(REAL_PLANS[name], stage=stages.COMPLETE, replacing=False) == []


@pytest.mark.parametrize("name", list(REAL_PLANS))
@pytest.mark.parametrize("stage", stages.STAGES)
@pytest.mark.parametrize("replacing", [True, False])
def test_the_live_plans_give_no_rule_mention_outside_the_complete_stage(name, stage, replacing):
    found = findings(REAL_PLANS[name], stage=stage, replacing=replacing)

    if stage != stages.COMPLETE:
        assert not any(RULE_NAME in line for line in found)


def test_the_live_function_block_is_parsed_with_its_values_one_level_under_the_field():
    change = next(c for c in deploy_diff.parse(REAL_PLANS["deploy_a"]) if c.type == FUNCTION)

    old, new = REAL_HOLDS_CODE.splitlines()[-2:]
    assert [(kind, text) for _, kind, text in change.tree] == [
        ("~", "Code"), ("~", ".S3Key:"), ("-", old.split()[-1]), ("+", new.split()[-1])]


# ------------------------------------------- the saved plans carry no live identifier

ID_SHAPE = re.compile(
    r"(?:gateway|runtime|memory|policy-engine|policy|gateway-target|target)/[A-Za-z0-9_-]+"
    r"-([A-Za-z0-9]{10})\b|meridianv2_[A-Za-z]+-([A-Za-z0-9]{10})\b")
HEX_64 = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
TWELVE_DIGITS = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")
FIXTURE_FILES = sorted(REAL_DIR.glob("*.txt"))


def fixture_texts():
    return [(path.name, path.read_text()) for path in FIXTURE_FILES] + [
        ("test_release_deploy_diff.py", Path(__file__).read_text())]


def test_the_saved_plans_exist():
    assert [path.name for path in FIXTURE_FILES] == ["deploy_a_diff.txt", "deploy_b_diff.txt"]


def test_only_the_documented_fake_account_ids_appear():
    for name, text in fixture_texts():
        found = set(TWELVE_DIGITS.findall(text)) - set(fx.FAKE_ACCOUNT_IDS)
        assert not found, f"{name} holds a 12-digit number that is not a documented fake"


def test_every_resource_id_suffix_is_a_fake_the_fixture_module_defines():
    seen = 0
    for name, text in fixture_texts():
        for match in ID_SHAPE.finditer(text):
            seen += 1
            suffix = match[1] or match[2]
            assert suffix in fx.FAKE_RESOURCE_SUFFIXES, f"{name}: {match[0][:40]}"
    assert seen


def test_every_content_hash_is_one_the_fixture_module_derives():
    allowed = {hashlib.sha256(label.encode()).hexdigest() for label in fx.FAKE_HASH_LABELS}
    for name, text in fixture_texts():
        assert set(HEX_64.findall(text)) <= allowed, name
