"""The release documents keep the order the tools enforce: Gateway, deploy, holds, publish."""

from __future__ import annotations

from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"
OPERATIONS = (DOCS / "OPERATIONS.md").read_text(encoding="utf-8")
RUNBOOK = (DOCS / "AGENTCORE_DEPLOY_RUNBOOK.md").read_text(encoding="utf-8")
README = (Path(__file__).resolve().parents[1] / "scripts" / "README.md").read_text(encoding="utf-8")
CONFIRM = "--apply --i-understand-this-changes-aws"


def window() -> str:
    start = OPERATIONS.index("### The window order")
    return OPERATIONS[start:OPERATIONS.index("### Read the release back")]


def test_the_window_moves_the_gateway_before_the_deploy_and_the_holds_after_it():
    text = window()

    gateway = text.index(f"release_identity.py gateway {CONFIRM}")
    deploy = text.index(f"release_identity.py deploy {CONFIRM}")
    holds = text.index(f"publish_gateway_parameters.py --gateway-login {CONFIRM}")
    publish = text.index("scripts/publish.py")
    assert gateway < deploy < holds < publish


def test_no_command_block_in_the_window_runs_a_bare_agentcore_deploy():
    commands = [line.strip() for line in window().splitlines()
                if line.strip().startswith(("agentcore", "/opt/homebrew/bin/agentcore"))]

    assert not [line for line in commands if " deploy" in line]


def test_the_window_states_why_and_what_is_assumed():
    text = window()

    assert "Authorizer type cannot be updated for an existing gateway" in text
    assert "Assumed, not verified" in text and "Verified in the installed code" in text


def test_the_rollback_section_orders_the_gateway_before_the_stack_and_names_the_role_statement():
    start = OPERATIONS.index("Restore order, which is the true reverse of the release:")
    text = OPERATIONS[start:OPERATIONS.index("## Prove the decoy is refused")]

    assert text.index("the Gateway through the `UpdateGateway` API") < text.index(
        "the AgentCore stack")
    for phrase in ("InvokeGateway", "MERIDIAN_AGENTCORE_AUTH=iam", "check --expect iam",
                   "/opt/homebrew/bin/agentcore deploy -y"):
        assert phrase in text


def test_the_runbook_and_the_scripts_readme_point_at_the_tool():
    assert "release_identity.py deploy" in RUNBOOK and "#the-window-order" in RUNBOOK
    assert "Authorizer type cannot be updated for an existing gateway" in RUNBOOK
    assert "`deploy` runs `/opt/homebrew/bin/agentcore deploy -y`" in README


LEARNINGS = (DOCS / "AGENTCORE_LEARNINGS.md").read_text(encoding="utf-8")
AGENTCORE_README = (Path(__file__).resolve().parents[1] / "meridian_agentcore"
                    / "README.md").read_text(encoding="utf-8")


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_window_documents_the_deliberate_gateway_divergence():
    text = flat(window())

    assert "Deliberate divergence" in text
    assert "`AWS_IAM`, no JWT authorizer block" in text
    assert "never with the template" in text
    assert "Never run `agentcore deploy` bare in `jwt` mode" in text
    assert "release_identity.py gateway" in text
    assert "agentcore deploy --diff --json" in text


def test_every_release_document_says_the_live_gateway_and_the_template_differ_on_purpose():
    assert "keeps the Gateway resource as the deployed stack has it" in flat(RUNBOOK)
    assert "never run `agentcore deploy` bare in `jwt` mode" in flat(RUNBOOK)
    assert "never run `agentcore deploy` bare in `jwt` mode" in flat(README)
    assert "`AWS_IAM` in `jwt` mode" in flat(README)
    assert "compares the template with the deployed stack template" in flat(LEARNINGS)
    assert "deliberately stays `AWS_IAM`" in flat(AGENTCORE_README)
