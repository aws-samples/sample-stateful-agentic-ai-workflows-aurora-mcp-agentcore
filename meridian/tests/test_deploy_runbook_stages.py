"""The manual deployment walks four stages from an empty state to completion and can resume."""

import json
import re
from pathlib import Path

import pytest

from scripts import render_agentcore_config as render_config
from scripts.identity_release import stages
from tests.test_render_agentcore_config import (  # noqa: F401  (fixture)
    state_document,
    project,
)

RUNBOOK = (Path(__file__).resolve().parents[1] / "docs" / "AGENTCORE_DEPLOY_RUNBOOK.md").read_text(
    encoding="utf-8")
STAGE_LINE = re.compile(r"Build stage: (\w+) \((\d) of 4\)")


def deploy(config_dir: Path, **ids: str) -> None:
    """What a finished `agentcore deploy` leaves in the CLI's state file."""
    path = config_dir / render_config.DEPLOYED_STATE
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(state_document("meridian-aurora", **ids)), encoding="utf-8")


def render(capsys: pytest.CaptureFixture[str]) -> str:
    assert render_config.main([]) == 0
    return capsys.readouterr().out


def test_four_renders_walk_an_empty_state_to_completion(
    project: Path, capsys: pytest.CaptureFixture[str]  # noqa: F811
) -> None:
    seen = []
    rounds = [{}, {"gateway_id": "gw-1"}, {"gateway_id": "gw-1", "holds": "tgt-1"},
              {"gateway_id": "gw-1", "holds": "tgt-1", "engine": "eng-1"}]
    for ids in rounds:
        if ids:
            deploy(project, **ids)
        out = render(capsys)
        seen.append(STAGE_LINE.search(out).groups())
        assert ("Configuration complete." in out) is (len(seen) == 4)

    assert seen == [("gateway", "1"), ("targets", "2"), ("governance", "3"), ("complete", "4")]


def test_an_interrupted_deploy_resumes_at_the_same_stage_and_completion_is_stable(
    project: Path, capsys: pytest.CaptureFixture[str]  # noqa: F811
) -> None:
    deploy(project, gateway_id="gw-1")
    first = render(capsys)
    again = render(capsys)

    assert STAGE_LINE.search(first).groups() == STAGE_LINE.search(again).groups() == (
        "targets", "2")

    deploy(project, gateway_id="gw-1", holds="tgt-1", engine="eng-1")
    assert "Configuration complete." in render(capsys)
    assert "Configuration complete." in render(capsys)


def test_the_runbook_describes_the_four_stages_the_renderer_uses():
    assert "three passes" not in RUNBOOK and "Third pass" not in RUNBOOK
    for position, stage in enumerate(stages.STAGES, start=1):
        assert f"`Build stage: {stage} ({position} of {len(stages.STAGES)})`" in RUNBOOK
    assert "`Configuration complete.`" in RUNBOOK
    assert "release_identity.py deploy" in RUNBOOK.split("## 4. Deploy")[1].split("## 5.")[0]
    assert "by running the render again" in RUNBOOK
