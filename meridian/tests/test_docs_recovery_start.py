"""Both Phase 5 guides start the recovery with one click on Start recovery."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUIDES = ["README.md", "docs/CODE_WALKTHROUGH.md"]
START_GUIDES = [*GUIDES, "docs/OPERATIONS.md"]


@pytest.mark.parametrize("guide", GUIDES)
def test_the_guide_starts_the_recovery_with_one_click_not_a_pasted_prompt(guide):
    text = " ".join((ROOT / guide).read_text().split())
    assert "Recovery desk" in text
    assert "click **Start recovery** once" in text
    assert "explicit recovery intent" in text
    assert "nothing to paste" in text or "do not paste or resend" in text


@pytest.mark.parametrize("guide", GUIDES)
def test_the_guide_never_asks_for_a_pasted_or_resent_prompt(guide):
    text = " ".join((ROOT / guide).read_text().lower().split())
    allowed = text.replace("do not paste or resend the prompt", "")
    for instruction in ("paste the prompt", "paste this prompt", "resend the prompt"):
        assert instruction not in allowed


def test_operations_stop_session_step_starts_from_the_recovery_desk() -> None:
    text = " ".join((ROOT / "docs/OPERATIONS.md").read_text().split())
    step = text[text.index("**Stop the session from the browser.**"):]
    step = step[:step.index("**Scripts against real")]
    assert "Recovery desk" in step
    assert "click **Start recovery** once" in step
    assert "pauses after `search`" in step
    assert "canceled-flight prompt" not in step


def test_operations_never_tells_the_reader_to_type_the_prompt_for_the_pause() -> None:
    text = " ".join((ROOT / "docs/OPERATIONS.md").read_text().split())
    assert "Run the canceled-flight prompt in the Workflow phase" not in text
