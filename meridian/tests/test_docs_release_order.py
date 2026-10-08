"""The release documents keep the order the tools enforce: stages, holds, interceptor, publish."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OPERATIONS = (DOCS / "OPERATIONS.md").read_text(encoding="utf-8")
RUNBOOK = (DOCS / "AGENTCORE_DEPLOY_RUNBOOK.md").read_text(encoding="utf-8")
README = (ROOT / "scripts" / "README.md").read_text(encoding="utf-8")
LEARNINGS = (DOCS / "AGENTCORE_LEARNINGS.md").read_text(encoding="utf-8")
AGENTCORE_README = (ROOT / "meridian_agentcore" / "README.md").read_text(encoding="utf-8")
CONFIRM = "--apply --i-understand-this-changes-aws"


def flat(text: str) -> str:
    return " ".join(text.split())


def window() -> str:
    start = OPERATIONS.index("### The window order")
    return OPERATIONS[start:OPERATIONS.index("### Read the release back")]


def replacement() -> str:
    start = OPERATIONS.index("### How the Gateway is replaced")
    return OPERATIONS[start:OPERATIONS.index("### The window order")]


def test_the_window_deploys_four_stages_then_attaches_the_interceptor_then_publishes():
    text = window()

    first = text.index("release_identity.py deploy --apply")
    bind = text.index("bind_gateway_workload.py")
    holds = text.index(f"publish_gateway_parameters.py --gateway-login {CONFIRM}")
    attach = text.index("release_identity.py gateway --to jwt")
    publish = text.index("scripts/publish.py")
    assert first < bind < holds < attach < publish
    assert text.count("release_identity.py deploy") >= 4
    for stage in ("Stage 1", "Stage 2", "Stage 3", "Stage 4"):
        assert stage in text


def test_no_command_block_in_the_window_runs_a_bare_agentcore_deploy():
    commands = [line.strip() for line in window().splitlines()
                if line.strip().startswith(("agentcore", "/opt/homebrew/bin/agentcore"))]

    assert not [line for line in commands if " deploy" in line]


def test_the_design_note_says_why_the_gateway_is_replaced_and_why_not_alongside():
    text = flat(replacement())

    assert "Authorizer type cannot be updated for an existing gateway" in text
    assert "cannot sit side by side" in text
    assert "meridian-aurora-jwt" in text and "AGENTCORE_GATEWAY_MERIDIAN_AURORA_JWT_URL" in text
    assert "no `retire-old-gateway` command" in text
    assert "Verified" not in text  # the table of what is verified sits after the window order


def test_the_window_states_what_is_verified_and_what_is_assumed():
    text = window()

    assert "Verified in the installed construct code" in text
    assert "Assumed" in text and "Not measured" in text


def test_the_rollback_section_rebuilds_the_gateway_in_stages_and_never_writes_a_replaced_one():
    start = OPERATIONS.index("Restore order, which is the true reverse of the release:")
    text = flat(OPERATIONS[start:OPERATIONS.index("## Prove the decoy is refused")])

    for phrase in ("new id and URL", "--only revoke", "MERIDIAN_AGENTCORE_AUTH=iam",
                   "check --expect iam", "bind_gateway_workload.py", "never written"):
        assert phrase in text


def test_every_release_document_says_the_jwt_gateway_is_a_new_resource():
    assert "meridian-aurora-jwt" in flat(RUNBOOK)
    assert "never run `agentcore deploy` bare in `jwt` mode" in flat(RUNBOOK).lower()
    assert "Never run `agentcore deploy` bare in `jwt` mode" in flat(README)
    assert "release_identity.py deploy" in RUNBOOK and "#the-window-order" in RUNBOOK
    assert "`deploy` runs `/opt/homebrew/bin/agentcore deploy -y`" in README
    assert "cannot change an authorizer type" in flat(README)
    assert "four staged deploys" in flat(AGENTCORE_README)
    assert "construct id" in flat(LEARNINGS) and "four deploys" in flat(LEARNINGS)


# --------------------------------------------------- accuracy checks against the code


COMMAND = re.compile(r"release_identity\.py\s+((?:check|interceptor-delete|interceptor|lambdas|"
                     r"semantic-lambda|gateway|deploy|holds-logs|snapshot|rollback)\b[^`\n]*)")


def documented_commands() -> list[list[str]]:
    """Every `release_identity.py ...` command in the release documents that has no placeholder
    prose in it, split like a shell would."""
    found = []
    for text in (OPERATIONS, RUNBOOK, README):
        for match in COMMAND.finditer(text):
            line = match[1].split("   ")[0].rstrip(" .,:;)")
            if any(mark in line for mark in "[(<…") or "..." in line or "|" in line:
                continue
            found.append(shlex.split(line))
    return found


def test_every_documented_release_command_parses_with_the_real_parser():
    from scripts import release_identity

    commands = documented_commands()

    assert len(commands) >= 15
    for tokens in commands:
        try:
            release_identity.build_parser().parse_args(tokens)
        except SystemExit as stopped:
            raise AssertionError(f"documented command does not parse: {tokens}") from stopped


def plan_gate_section() -> str:
    start = OPERATIONS.index("**What the plan gate accepts.**")
    return flat(OPERATIONS[start:OPERATIONS.index("After stage 4 the interceptor is attached")])


def test_the_plan_gate_table_names_what_the_code_expects_of_each_stage():
    from scripts.identity_release import deploy_diff, stages

    text = plan_gate_section()

    for stage, (_kind, wanted) in deploy_diff.EXPECTED.items():
        row = text[text.index(f"{stages.STAGES.index(stage) + 1} `{stage}`"):]
        row = row[:row.index("|", row.index("|", row.index("|") + 1) + 1)]
        for resource in wanted:
            assert resource.rsplit("::", 1)[-1] in row, (stage, resource)
    for mode in ("iam", "jwt"):
        assert deploy_diff.construct_id(mode) in text
    assert stages.ENGINE_NAME in text and "no parsed resource line" in text


def test_the_docs_say_the_first_live_plan_is_read_by_eye_and_no_real_plan_is_committed():
    text = flat(OPERATIONS)

    assert "No live `agentcore deploy --diff --json` has been captured" in text
    assert "Read the first live plan by eye" in text or "read the first live plan by eye" in text


def test_the_rollback_documentation_names_the_snapshot_choice_and_the_file_flag():
    start = OPERATIONS.index("### Save the configuration, then roll back")
    text = flat(OPERATIONS[start:OPERATIONS.index("## Prove the decoy is refused")])

    for phrase in ("of the release that is not live", "never chosen on its own",
                   "Every rollback command it prints passes `--snapshot FILE`",
                   "does not choose it"):
        assert phrase in text, phrase
    assert "MERIDIAN_AGENTCORE_AUTH`, which the `snapshot` command does not read" in text


def test_the_failed_deploy_advice_names_the_stack_status_and_the_stuck_rollback():
    text = flat(window())

    assert "stack status in CloudFormation first" in text
    assert "UPDATE_ROLLBACK_FAILED" in text and "continue-update-rollback" in text


# ------------------------------------------- the retained holds log group (stage 1 to 2)

LOG_GROUP = "/aws/lambda/meridianv2-MeridianHolds"
HOLDS_LOGS = f"release_identity.py holds-logs {CONFIRM}"


def test_the_window_deletes_the_holds_log_group_between_stage_one_and_stage_two():
    text = window()

    stage_one = text.index("release_identity.py deploy --apply")
    logs = text.index(HOLDS_LOGS)
    stage_two = text.index("7. Stage 2")
    bind = text.index("bind_gateway_workload.py")
    assert stage_one < logs < stage_two < bind


def test_the_design_note_explains_the_log_group_collision_and_the_permissions():
    text = flat(replacement())

    assert LOG_GROUP in text and "CloudFormation" in text and "retained" in text
    assert "no log export" in text or "not exported" in text
    for word in ("logs:DescribeLogGroups", "logs:DeleteLogGroup",
                 "lambda:GetFunctionConfiguration"):
        assert word in text, word
    assert "exact name" in text and "refuses" in text


def test_the_stage_table_says_stage_two_refuses_while_the_log_group_exists():
    start = OPERATIONS.index("| 2 `targets`")
    row = flat(OPERATIONS[start:OPERATIONS.index("\n", start)])

    assert "holds-logs" in row


def test_the_rollback_steps_delete_the_log_group_between_the_first_two_deploys():
    start = OPERATIONS.index("Rolling back to `iam` after the release")
    text = OPERATIONS[start:OPERATIONS.index("The `iam` Gateway that comes back")]

    deploy = text.index("deploy --to iam --apply")
    logs = text.index("release_identity.py holds-logs")
    bind = text.index("bind_gateway_workload.py")
    assert deploy < logs < bind


def test_the_rollback_docs_say_deploy_finds_its_snapshot_and_takes_no_flag():
    start = OPERATIONS.index("### Save the configuration, then roll back")
    text = flat(OPERATIONS[start:OPERATIONS.index("## Prove the decoy is refused")])

    assert "`deploy` takes no `--snapshot`" in text and "finds the snapshot" in text


def test_the_runbook_and_the_scripts_readme_name_the_log_group_step():
    for text in (RUNBOOK, README):
        assert "holds-logs" in text
    assert LOG_GROUP in flat(RUNBOOK)


def test_the_documented_log_group_name_is_the_one_the_code_derives():
    from scripts.identity_release import holds_logs

    assert holds_logs.log_group_name() == LOG_GROUP
