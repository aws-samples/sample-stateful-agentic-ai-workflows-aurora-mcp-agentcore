"""Tests for backend.agent_catalog."""

import re

from backend.agents.concierge_definition import TOOL_ARGUMENTS
from backend.agent_catalog import PHASE_CATALOG, get_phase_spec, format_skills_summary
from backend.trace_names import (
    AVAILABILITY_CODE, MCP_CODE, MEMORY_CODE, PRODUCTION_CODE, RETRIEVAL_CODE, SQL_CODE,
)


def test_all_five_phases_defined() -> None:
    assert set(PHASE_CATALOG) == {1, 2, 3, 4, 5}


def test_phase3_is_one_agent_with_the_cumulative_tools() -> None:
    spec = get_phase_spec(3)
    assert spec is not None
    assert spec.primary_agent == "RetrievalAgent"
    assert spec.specialists == ()
    assert spec.agent_file == "agents/phase_03_retrieval/agent.py"
    names = {s.name for s in spec.skills}
    assert names == {"hybrid_trip_search", "get_package_details", "search_trips",
                     "compare_packages", "price_range", "region_inventory",
                     "currency_convert", "loyalty_balance"}
    assert "hybrid_trip_search" in format_skills_summary(3)


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


def _argument_names(signature: str) -> tuple[str, ...]:
    inside = re.match(r"\w+\((.*?)\)", signature).group(1)
    return tuple(part.split("=")[0].strip() for part in inside.split(",") if part.strip())


def test_every_catalog_signature_names_the_arguments_the_tools_take():
    checked = []
    for phase, spec in PHASE_CATALOG.items():
        for skill in spec.skills:
            if skill.name not in TOOL_ARGUMENTS or "..." in skill.signature:
                continue
            bare = skill.signature.split("___")[-1]
            assert _argument_names(bare) == TOOL_ARGUMENTS[skill.name], (phase, skill.name)
            checked.append((phase, skill.name))
    assert (2, "loyalty_balance") in checked and (4, "loyalty_balance") in checked
    assert len(checked) >= 15
