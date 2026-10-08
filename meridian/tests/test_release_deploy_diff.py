"""The plan gate reads the CDK text diff of `agentcore deploy --diff --json` for each stage.

The format is read from the installed CLI's source, which prints the formatted diff (lines like
``[+] AWS::BedrockAgentCore::Gateway <path> <logical id>`` with ``├─ [~] Property`` children),
then a JSON status object. The gate fails closed: a stage that must add a Gateway and finds no
change lines at all is a finding that shows the tail of the output.
"""

from __future__ import annotations

import pytest

from scripts.identity_release import deploy_diff, stages

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


REPLACEMENT = plan(NEW.format(GATEWAY), OLD.format(GATEWAY), RUNTIME,
                   "[-] AWS::Lambda::Function Mcp/Holds/Function Holds1")


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
    swapped = plan(OLD.format(GATEWAY).replace("[-]", "[+]"),
                   NEW.format(GATEWAY).replace("[+]", "[-]"), RUNTIME)

    assert findings(swapped, mode="iam") == []


def test_the_construct_prefix_trap_is_not_a_match():
    only_old_added = plan(OLD.format(GATEWAY).replace("[-]", "[+]"),
                          NEW.format(GATEWAY).replace("[+]", "[-]"))

    found = findings(only_old_added, mode="jwt")

    assert len(found) == 1 and "GatewayMeridianAuroraJwt" in found[0]
    assert findings(only_old_added, mode="iam") == []


def test_the_iam_gateway_is_matched_by_its_logical_id_alone_but_not_the_jwt_one():
    bare_ids = plan("[+] AWS::BedrockAgentCore::Gateway McpGatewayMeridianAuroraAB12CD34",
                    "[-] AWS::BedrockAgentCore::Gateway McpGatewayMeridianAuroraJwt1A2B3C4D")

    assert findings(bare_ids, mode="iam") == []
    assert findings(bare_ids, mode="jwt") != []


def test_the_jwt_gateway_added_in_the_iam_release_is_not_the_iam_gateway():
    wrong_way_round = plan(NEW.format(GATEWAY), OLD.format(GATEWAY))

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
    found = findings(plan(NEW.format(GATEWAY), RUNTIME))

    assert len(found) == 1 and "remove exactly one Gateway" in found[0]


def test_a_first_ever_build_removes_no_gateway_and_one_that_does_is_refused():
    assert findings(plan(NEW.format(GATEWAY), RUNTIME), replacing=False) == []
    found = findings(REPLACEMENT, replacing=False)
    assert len(found) == 1 and "removes a Gateway" in found[0]


def test_removing_two_gateways_is_refused():
    found = findings(plan(NEW.format(GATEWAY), OLD.format(GATEWAY), OLD.format(GATEWAY)))

    assert any("remove exactly one Gateway" in line for line in found)


def test_no_change_lines_fail_closed_and_show_the_tail():
    found = findings("something else entirely\nline two\n" + STATUS)

    assert any("add exactly one Gateway" in line and "something else entirely" in line
               and "line two" in line for line in found)


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
    update = plan("[~] AWS::BedrockAgentCore::Gateway Mcp/GatewayMeridianAuroraJwt/Resource G1\n"
                  " └─ [~] PolicyEngineConfiguration")

    assert findings(update, stage=stages.GOVERNANCE) == []


# ------------------------------------------------------------------ later stages


@pytest.mark.parametrize("stage", [stages.TARGETS, stages.GOVERNANCE, stages.COMPLETE])
def test_later_stages_add_and_remove_no_gateway(stage):
    assert any("Gateway" in line for line in findings(plan(NEW.format(GATEWAY)), stage=stage))
    assert any("Gateway" in line for line in findings(plan(OLD.format(GATEWAY)), stage=stage))


@pytest.mark.parametrize("kind", ["BedrockAgentCore::Gateway", "Lambda::Function",
                                  "BedrockAgentCore::GatewayTarget",
                                  "BedrockAgentCore::PolicyEngine", "BedrockAgentCore::Policy",
                                  "BedrockAgentCore::Runtime", "BedrockAgentCore::Memory"])
@pytest.mark.parametrize("stage", stages.STAGES)
def test_no_stage_may_delete_what_a_stale_state_would_delete(kind, stage):
    removed = plan(f"[-] AWS::{kind} Some/Path Logical1")

    found = findings(removed + "\n" + NEW.format(GATEWAY), stage=stage)

    if kind in ("BedrockAgentCore::Gateway", "Lambda::Function",
                "BedrockAgentCore::GatewayTarget",
                "BedrockAgentCore::PolicyEngine", "BedrockAgentCore::Policy") \
            and stage == stages.GATEWAY:
        assert not any("deletes" in line for line in found)
    else:
        assert any("deletes" in line and f"AWS::{kind}" in line for line in found)


def test_additions_and_runtime_updates_after_the_first_stage_are_fine():
    holds = plan(
        "[+] AWS::BedrockAgentCore::GatewayTarget Mcp/GatewayMeridianAuroraJwt/TargetHolds T1",
        "[+] AWS::Lambda::Function Mcp/GatewayMeridianAuroraJwt/LambdaHolds/Function F1", RUNTIME)

    assert findings(holds, stage=stages.TARGETS) == []
    assert findings("No stack differences detected.\n" + STATUS, stage=stages.COMPLETE) == []
