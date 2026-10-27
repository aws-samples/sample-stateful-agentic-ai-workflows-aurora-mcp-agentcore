"""Tests for backend.agent_catalog."""

from backend.agent_catalog import PHASE_CATALOG, get_phase_spec, format_skills_summary
from backend.trace_names import (
    AVAILABILITY_CODE, MCP_CODE, MEMORY_CODE, PRODUCTION_CODE, RETRIEVAL_CODE, SQL_CODE,
)


def test_all_five_phases_defined() -> None:
    assert set(PHASE_CATALOG) == {1, 2, 3, 4, 5}


def test_phase3_has_retrieval_specialists_and_skills() -> None:
    spec = get_phase_spec(3)
    assert spec is not None
    assert spec.primary_agent == "RetrievalAgent"
    assert "SearchAgent" in spec.specialists
    assert "PackageAgent" in spec.specialists
    assert "BookingAgent" in spec.specialists
    summary = format_skills_summary(3)
    assert "_hybrid_search_tool" in summary
    assert "SearchAgent" in summary


def test_phase4_memory_agent_catalog() -> None:
    spec = get_phase_spec(4)
    assert spec is not None
    assert spec.primary_agent == PRODUCTION_CODE
    assert MEMORY_CODE in spec.specialists
    assert any(s.name == "recall_session_context" for s in spec.skills)


def test_phase_1_and_2_are_named_for_what_runs():
    one, two = get_phase_spec(1), get_phase_spec(2)
    assert one.primary_agent == SQL_CODE
    assert [s.name for s in one.skills] == ["search_trips"]
    assert two.primary_agent == MCP_CODE
    assert [s.name for s in two.skills] == [
        "search_trips", "compare_packages", "price_range", "region_inventory",
        "currency_convert", "loyalty_balance",
    ]
    assert "run_query" not in two.method and "postgres" not in two.method.lower()


def test_phase_4_and_5_use_the_names_the_trace_prints():
    four, five = get_phase_spec(4), get_phase_spec(5)
    assert four.primary_agent == PRODUCTION_CODE
    assert MEMORY_CODE in four.specialists
    assert five.specialists == (RETRIEVAL_CODE, AVAILABILITY_CODE)
