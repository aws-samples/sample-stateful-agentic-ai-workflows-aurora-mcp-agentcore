"""The release documents keep the order the tools enforce: stages, holds, interceptor, publish."""

from __future__ import annotations

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
