"""The five-phase presenter prompt ladder, in one place.

DEMO_SCRIPT.md and PRESENTER_GUIDE.md name specific prompts for each phase:
two that work, and one "tee-up" prompt that fails on purpose and motivates the
next rung. Those strings were previously duplicated across the docs and the
test suite, and the UI's follow-up chips derived their own suggestions, so the
hand-off between phases lived only in the presenter's memory.

Keeping them here lets the chat router guarantee the tee-up prompt is always
one click away, and lets the contract tests assert against the same strings the
docs publish.
"""

from typing import Dict, List, NamedTuple


class PhasePrompts(NamedTuple):
    """Prompts for one rung of the ladder."""

    works: List[str]
    tee_up: str
    tee_up_label: str


# Phase 5 is the last rung, so its "tee-up" is the durability proof rather than
# a hand-off to a further phase.
PROMPT_LADDER: Dict[int, PhasePrompts] = {
    1: PhasePrompts(
        works=[
            "Show me city trips under $2,000 per traveler.",
            "Show me beach trips under $2,500 per traveler.",
        ],
        tee_up=(
            "Compare three trip types side by side and convert their prices "
            "to euros."
        ),
        tee_up_label="Compare 3 trip types in EUR",
    ),
    2: PhasePrompts(
        works=[
            "Compare three trip types side by side and convert their prices "
            "to euros.",
            "What is the off-season price range for Tokyo trips in November?",
        ],
        tee_up=(
            "I want a quiet, romantic escape in wine country, ideally with a "
            "villa."
        ),
        tee_up_label="Describe a mood, not a filter",
    ),
    3: PhasePrompts(
        works=[
            "I want a quiet, romantic escape in wine country, ideally with a "
            "villa.",
            "Which trip lengths are still available for Tuscany Wine & "
            "Wellness?",
        ],
        tee_up=(
            "Recall my October Tokyo plan and use my saved preferences to "
            "recommend the next step."
        ),
        tee_up_label="Ask it to remember me",
    ),
    4: PhasePrompts(
        works=[
            "Find a Tokyo culture trip for two using my saved preferences.",
            "Recall my October Tokyo plan and use my saved preferences to "
            "recommend the next step.",
        ],
        tee_up=(
            "My JFK-to-Tokyo flight was cancelled. Rework the trip, then "
            "check duration availability for the best three options."
        ),
        tee_up_label="Break it with a multi-step plan",
    ),
    5: PhasePrompts(
        works=[
            "My JFK-to-Tokyo flight was cancelled. Rework the trip, then "
            "check duration availability for the best three options.",
            "Which trip lengths are still available for Amalfi Coast Villa "
            "Week?",
        ],
        tee_up="Resume workflow from checkpoint",
        tee_up_label="Resume workflow from checkpoint",
    ),
}


def tee_up_prompt(phase: int) -> str:
    """The prompt that motivates the next rung of the ladder."""
    rung = PROMPT_LADDER.get(phase)
    return rung.tee_up if rung else ""


def working_prompts(phase: int) -> List[str]:
    """Prompts documented as known-good for this phase."""
    rung = PROMPT_LADDER.get(phase)
    return list(rung.works) if rung else []
