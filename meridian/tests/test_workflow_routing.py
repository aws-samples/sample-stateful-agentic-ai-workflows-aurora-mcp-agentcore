"""Routing and pause rules for the Phase 5 graph, independent of any framework."""

import pytest

from backend.agents.phase_05_workflow import routing
from backend.agents.phase_05_workflow.routing import classify_intent, pauses_after_search
from backend.agents.phase_05_workflow.state import activity, hold_key
from backend.prompt_ladder import PROMPT_LADDER

CANONICAL_RECOVERY = PROMPT_LADDER[5].works[0]


@pytest.mark.parametrize("query, intent", [
    ("Find me a romantic trip to Paris", "search"),
    ("Find me a Kyoto cultural trip", "search"),
    ("What dates are available for Tokyo?", "availability"),
    ("When can I depart for Lisbon?", "availability"),
    ("Which duration options are available for Amalfi Coast Villa Week?", "availability"),
    (
        "My JFK flight was cancelled, rework it: which dates are available for Tokyo?",
        "availability",
    ),
    ("Recall my October Tokyo plan and saved preferences", "memory_recall"),
    ("Do you remember our last trip?", "memory_recall"),
    (
        "Recall my October Tokyo plan and use my saved preferences to recommend the next step.",
        "memory_recall",
    ),
    (CANONICAL_RECOVERY, "plan"),
    (
        "Plan the Kyoto extension: find matching packages, then verify available duration options.",
        "plan",
    ),
    ("Which trip lengths are still available for Amalfi Coast Villa Week?", "plan"),
])
def test_intents_match_the_langgraph_routing(query, intent):
    assert classify_intent(query) == intent


def test_no_word_list_decides_whether_a_run_may_hold():
    """The hold path is the typed ``recovery`` input; routing keeps no keyword test for it."""
    assert not hasattr(routing, "is_recovery_request")


def test_a_combined_search_and_dates_question_plans_as_a_read():
    """It holds both "trip" and "available" and plans two read steps, never a hold."""
    query = "Which trip lengths are still available for Amalfi Coast Villa Week?"
    assert classify_intent(query) == "plan"


def test_only_a_recovery_pauses_after_search_so_a_pause_means_a_hold_is_coming():
    assert pauses_after_search(CANONICAL_RECOVERY, review_only=False, recovery=True)
    assert pauses_after_search("Find Tokyo trips", review_only=True, recovery=True)
    assert not pauses_after_search("Find Tokyo trips", review_only=True, recovery=False)
    assert not pauses_after_search(CANONICAL_RECOVERY, review_only=False, recovery=False)
    assert not pauses_after_search(
        "My flight was canceled. Rework my Tokyo trip.", review_only=False, recovery=True
    )


def test_hold_keys_are_stable_and_fit_the_booking_column():
    first = hold_key("thread", "pkg", "7 nights")
    assert first == hold_key("thread", "pkg", "7 nights")
    assert first != hold_key("thread", "pkg", "5 nights")
    assert first.startswith("hold_") and len(first) <= 32


def test_a_span_carries_the_workflow_file_and_the_strands_component():
    span = activity(
        "reasoning", "Workflow node: classify → plan", telemetry={"component": "Strands Graph"}
    )
    assert span["agent_file"] == "agents/phase_05_workflow/graph.py"
    assert span["telemetry"]["component"] == "Strands Graph"
