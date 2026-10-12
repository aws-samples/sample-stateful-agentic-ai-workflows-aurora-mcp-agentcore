"""Tests for backend.agent_catalog."""

from backend.agent_catalog import PHASE_CATALOG, get_phase_spec, format_skills_summary


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
    assert spec.primary_agent == "ProductionAgent"
    assert "MemoryAgent" in spec.specialists
    assert any(s.name == "recall_session_context" for s in spec.skills)
