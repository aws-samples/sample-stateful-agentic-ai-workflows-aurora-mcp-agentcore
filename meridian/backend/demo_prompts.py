"""The five-phase prompt ladder, in one place.

Each phase has two prompts that work and one "tee-up" prompt that needs the
next phase. meridian/README.md documents them.

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
            "Compare three trip types and convert each price to euros."
        ),
        tee_up_label="Compare 3 trip types in EUR",
    ),
    2: PhasePrompts(
        works=[
            "Compare three trip types and convert each price to euros.",
            "What is the off-season price range for Tokyo trips in November?",
        ],
        tee_up=(
            "Find a quiet, romantic wine-country retreat with a private villa."
        ),
        tee_up_label="Describe a mood, not a filter",
    ),
    3: PhasePrompts(
        works=[
            "Find a quiet, romantic wine-country retreat with a private villa.",
            "Which trip lengths are still available for Tuscany Wine & "
            "Wellness?",
        ],
        tee_up=(
            "Recall my Tokyo plan and saved preferences: home airport, food needs, and budget."
        ),
        tee_up_label="Ask it to remember me",
    ),
    4: PhasePrompts(
        works=[
            "Find Tokyo trips that fit my saved preferences.",
            "Recall my Tokyo plan and saved preferences: home airport, food needs, and budget.",
        ],
        tee_up=(
            "My JFK-to-Tokyo flight was canceled. Rework the trip, then "
            "check duration availability for the best three options."
        ),
        tee_up_label="Break it with a multi-step plan",
    ),
    5: PhasePrompts(
        works=[
            "My JFK-to-Tokyo flight was canceled. Rework the trip, then "
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
