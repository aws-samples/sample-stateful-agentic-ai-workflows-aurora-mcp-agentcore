"""The setup guides give every required input before the command that needs it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts import provision_service_logins, run_local
from scripts import render_agentcore_config as render_config
from tests.test_render_agentcore_config import (  # noqa: F401  (fixture)
    GATEWAY_SECRET_ARN,
    WORKFLOW_SECRET_ARN,
    project,
)

MERIDIAN = Path(__file__).resolve().parents[1]
ROOT_README = (MERIDIAN.parent / "README.md").read_text(encoding="utf-8")
RUNBOOK = (MERIDIAN / "docs" / "AGENTCORE_DEPLOY_RUNBOOK.md").read_text(encoding="utf-8")
OPERATIONS = (MERIDIAN / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
FENCE = re.compile(r"```bash\n(.*?)```", re.DOTALL)
PROVISIONERS = {
    "provision_workflow_login.py": ("AURORA_WORKFLOW_SECRET_ARN", WORKFLOW_SECRET_ARN),
    "provision_service_logins.py --login gateway": (
        "AURORA_GATEWAY_SECRET_ARN", GATEWAY_SECRET_ARN),
}
ENV_VARS = [name for name, _ in PROVISIONERS.values()]


def commands(text: str) -> list[str]:
    """Every command of every bash block in document order, continuation lines joined."""
    return [" ".join(line.split()) for block in FENCE.findall(text)
            for line in block.replace("\\\n", " ").splitlines()]


def test_the_runbook_provisions_both_logins_before_its_first_render(
    project: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name)
    for line in commands(RUNBOOK):
        if "render_agentcore_config.py" in line:
            break
        for marker, (name, arn) in PROVISIONERS.items():
            if marker in line and "--apply" in line and "--write-env" in line:
                monkeypatch.setenv(name, arn)
    assert render_config.main([]) == 0, "a required input is only provided after the first render"


def lines_before_first_render() -> list[str]:
    lines = commands(RUNBOOK)
    return lines[:next(i for i, line in enumerate(lines) if "render_agentcore_config.py" in line)]


@pytest.mark.parametrize("missing", ENV_VARS)
def test_each_missing_input_is_named_by_the_renderer_and_provided_by_an_earlier_step(
    project: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    capsys: pytest.CaptureFixture[str], missing: str,
) -> None:
    monkeypatch.delenv(missing)
    assert render_config.main([]) == 1
    assert missing in capsys.readouterr().err
    marker = next(m for m, (name, _) in PROVISIONERS.items() if name == missing)
    assert any(marker in line for line in lines_before_first_render())


def test_quick_start_provisions_the_backend_login_before_the_local_launcher() -> None:
    lines = commands(ROOT_README)
    provision = next(i for i, line in enumerate(lines)
                     if "provision_service_logins.py --login backend --apply" in line)
    launch = next(i for i, line in enumerate(lines) if "scripts/run_local.py" in line)
    assert provision < launch
    assert "--write-env" in lines[provision]
    assert "meridian/docs/OPERATIONS.md#create-the-logins" in ROOT_README


def test_the_quick_start_command_matches_the_script_and_the_launcher() -> None:
    backend = provision_service_logins.LOGINS["backend"]
    source = (MERIDIAN / "scripts" / "provision_service_logins.py").read_text(encoding="utf-8")
    command = next(line for line in commands(ROOT_README)
                   if "provision_service_logins.py --login backend --apply" in line)
    for flag in re.findall(r"--[a-z-]+", command):
        assert f'"{flag}"' in source, flag
    with pytest.raises(ValueError, match=backend.env_key):
        run_local.backend_environment({"AURORA_CLUSTER_ARN": "arn:aws:rds:x"})
    assert "Create the logins" in OPERATIONS


def test_rotation_migration_is_opt_in_in_the_quick_start_and_operations() -> None:
    assert "--with-rotation" in ROOT_README
    rotation = OPERATIONS[OPERATIONS.index("### Turn it on for one login first"):]
    step = rotation[:rotation.index("2. Check the clones")]
    assert step.count("--with-rotation") == 3
    assert "--with-rotation" in OPERATIONS[OPERATIONS.index("## Prepare the database"):
                                             OPERATIONS.index("## Run the workflow Runtime")]


def aurora_section() -> str:
    start = OPERATIONS.index("## Provision Aurora")
    return OPERATIONS[start:OPERATIONS.index("## Prepare the database")]


def test_first_deployment_verifies_both_availability_zones_before_claiming_separation() -> None:
    text = " ".join(aurora_section().split())
    assert "### First deployment: place the reader" in aurora_section()
    assert "IsClusterWriter" in text
    assert "describe-db-instances" in text and "AvailabilityZone" in text
    assert "until the two zones differ" in text
    assert "the writer does not exist yet" in text
    assert "a reader in a second Availability Zone" not in text


def reader_stage() -> str:
    text = aurora_section()
    start = text.index("### First deployment: place the reader")
    return " ".join(text[start:].split())


def test_first_deployment_failure_covers_both_stack_states_and_the_protected_cluster() -> None:
    text = reader_stage()
    assert "describe-stacks" in text and "StackStatus" in text
    assert "ROLLBACK_COMPLETE" in text
    assert "ROLLBACK_FAILED" in text
    assert "--no-deletion-protection" in text
    assert "--apply-immediately" in text
    failed = text[text.index("`ROLLBACK_FAILED`"):]
    assert failed.index("--no-deletion-protection") < failed.index("delete-stack")
    assert "has not been run against AWS" in text


def test_the_preflight_command_documents_first_creation_and_existing_stacks() -> None:
    text = " ".join(aurora_section().split())
    assert "--cluster-identifier" in text
    assert "writer and a reader" in text


AZ_CLAIM = re.compile(
    r"\b(second|another|different|separate|other)\s+(availability zone|az|zone)\b", re.IGNORECASE)
AZ_QUALIFIERS = ("verify", "verified", "read back", "differ", "if the writer and the reader",
                 "you read", "against the writer")


def unqualified_zone_claims(text: str) -> list[str]:
    """Sentences that put the reader in a different zone without a read-back qualifier."""
    flat = " ".join(text.split())
    sentences = re.split(r"(?<=[.!?])\s+|\s\|\s", flat)
    return [s for s in sentences
            if "reader" in s.lower() and AZ_CLAIM.search(s)
            and not any(q in s.lower() for q in AZ_QUALIFIERS)]


@pytest.mark.parametrize("name", ["README.md", "OPERATIONS.md"])
def test_no_guide_claims_the_reader_is_in_another_zone_without_a_verification(name: str) -> None:
    text = {"README.md": ROOT_README, "OPERATIONS.md": OPERATIONS}[name]
    assert unqualified_zone_claims(text) == [], name


def test_the_zone_claim_check_catches_the_old_wording() -> None:
    old = "a failover reader in a second Availability Zone in the template"
    assert unqualified_zone_claims(old)
    assert not unqualified_zone_claims(
        "a failover reader whose zone you verify against the writer's")


def test_first_creation_failure_says_to_delete_the_rolled_back_stack_first() -> None:
    text = " ".join(aurora_section().split())
    assert "ROLLBACK_COMPLETE" in text
    assert "aws cloudformation delete-stack --region <region> --stack-name MeridianAurora" in text
    assert text.index("ROLLBACK_COMPLETE") < text.index("name the other subnet zone")


def test_the_guide_says_the_preflight_does_not_count_the_reader_replacement_slot() -> None:
    text = " ".join(aurora_section().split())
    assert "does not count the one extra DB instance slot" in text


def test_runbook_workflow_login_text_matches_its_command_and_bullets() -> None:
    text = " ".join(RUNBOOK.split())
    assert "Without `--apply` it only reports: ```bash" not in text
    catalog = text.index("The catalog Lambda runs as its own `meridian_catalog` login")
    assert text.index("points its settings there.") < catalog
    assert catalog < text.index("Migration 015")
