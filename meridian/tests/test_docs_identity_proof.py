"""The identity-proof documents name the real probes, flags, exit codes and refusers."""

import re
from pathlib import Path

from scripts.identity_probes.probes import PLAN
from scripts.identity_probes.receipt import REFUSER_LABELS
from scripts.identity_release import settings

MERIDIAN = Path(__file__).resolve().parents[1]
DOCS = MERIDIAN / "docs"
OPERATIONS = (DOCS / "OPERATIONS.md").read_text()
ARCHITECTURE = (DOCS / "STATEFUL_ARCHITECTURE.md").read_text()
LEARNINGS = (DOCS / "AGENTCORE_LEARNINGS.md").read_text()
RUN_OF_SHOW = (DOCS / "TALK_RUN_OF_SHOW.md").read_text()
README = (MERIDIAN / "README.md").read_text()
SCRIPTS = (MERIDIAN / "scripts" / "README.md").read_text()
HARNESS_ROWS = ("Q1", "Q2", "Q3", "Q4", "Q5", "C1", "C2", "C3", "C4", "C5")


def section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert match, f"no section named {heading!r}"
    return match.group(1)


def test_operations_names_every_probe():
    body = section(OPERATIONS, "Prove the decoy is refused")

    assert [spec.id for spec in PLAN if spec.id not in body] == []


def test_operations_names_the_flags_the_files_and_the_exit_codes():
    body = section(OPERATIONS, "Prove the decoy is refused")

    for needed in (settings.CONFIRM_FLAG, "--apply", "--jordan-only", "--render",
                   ".local/identity-proof", "latest.json", "| 0 |", "| 1 |", "| 3 |"):
        assert needed in body, needed


def test_operations_explains_every_refuser_the_receipt_can_name():
    body = section(OPERATIONS, "Prove the decoy is refused")

    assert [key for key in REFUSER_LABELS if f"`{key}`" not in body] == []


def test_the_decoy_troubleshooting_row_is_no_longer_marked_as_expected_until_b2():
    assert "Expected until B2" not in OPERATIONS


def test_the_architecture_names_the_five_hops_and_the_honest_limit():
    body = section(ARCHITECTURE, "Identity: five hops")

    for word in ("Browser", "Backend", "Runtimes", "Gateway", "AWS Aurora", "traveler_id",
                 "SIGNED_SCOPE_EVALUATION.md"):
        assert word in body, word


def test_the_learnings_record_every_harness_row_that_was_measured():
    body = section(LEARNINGS, "Gateway identity: what the harness measured")

    assert [row for row in HARNESS_ROWS if f"| {row} |" not in body] == []


def test_the_run_of_show_places_the_identity_slide_and_drops_the_old_claim():
    assert "identity slide" in RUN_OF_SHOW and "shared principal" not in RUN_OF_SHOW


def test_the_readmes_list_the_command_the_capture_tools_and_the_signed_scope_note():
    assert "identity_proof.py" in SCRIPTS and "identity_capture" in SCRIPTS
    assert "SIGNED_SCOPE_EVALUATION.md" in README
    assert "SIGNED_SCOPE_EVALUATION.md" in ARCHITECTURE


STALE = {
    "README.md": (README, ("shared principal", "AgentCore Identity authenticates",
                           "over MCP with SigV4")),
    "OPERATIONS.md": (OPERATIONS, ("shared principal", "shared sample principal")),
    "STATEFUL_ARCHITECTURE.md": (ARCHITECTURE, ("shared principal", "shared sample principal")),
}


def test_no_public_document_still_describes_the_old_identity_model():
    stale = [f"{name}: {phrase}" for name, (text, phrases) in STALE.items()
             for phrase in phrases if phrase in text]

    assert stale == []
