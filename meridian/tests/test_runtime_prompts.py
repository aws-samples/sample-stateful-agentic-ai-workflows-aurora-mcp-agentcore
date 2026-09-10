"""The system prompt states the tool order and the hold rule the hooks enforce."""

from __future__ import annotations

import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
sys.path.insert(0, str(RUNTIME))

from prompts import narration_prompt, system_prompt, turn_prompt  # noqa: E402


def test_confirmed_turn_has_no_tools_and_narrates_the_platform_decision():
    target = {"package_id": "CTY-002", "duration": "5 nights", "travelers": 2}
    system = system_prompt(hold_confirmed=True, hold_target=target)
    assert "already placed" in system and "no tools" in system
    prompt = narration_prompt("Hold HLD-9 is held for CTY-002", target, 640000)
    assert "HLD-9" in prompt and "CTY-002" in prompt and "gateway's decision" in prompt
    assert "$6,400.00 for this party" in prompt and "only this one applies now" in prompt


def test_system_prompt_names_the_tools_and_the_confirmation_rule():
    text = system_prompt(hold_confirmed=False, hold_target=None)
    assert "semantic_trip_search" in text
    assert "get_package_details" in text
    assert "create_courtesy_hold" in text
    assert "has not confirmed" in text


def test_confirmed_turn_prompt_carries_the_exact_hold_terms():
    target = {
        "package_id": "CTY-002",
        "duration": "7 nights",
        "travelers": 2,
        "unit_price_cents": 250000,
    }
    text = turn_prompt("Hold it", "prefers window seats", target, hold_confirmed=True)
    assert "CTY-002" in text and "7 nights" in text and "250000" in text
    assert "confirmed" in text
    assert "has confirmed" in system_prompt(hold_confirmed=True, hold_target=target)


def test_unconfirmed_turn_prompt_never_carries_hold_terms():
    target = {"package_id": "CTY-002"}
    text = turn_prompt("Hold it", "", target, hold_confirmed=False)
    assert "CTY-002" not in text
