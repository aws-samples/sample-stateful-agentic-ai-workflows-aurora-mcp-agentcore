"""
Phase → agent → tool/skill catalog for verbose backend logging.

Mirrors the capability ladder so logs name the same
agents, specialists, and @tool signatures the frontend displays.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from backend.trace_names import (
    AVAILABILITY_CODE, MCP_CODE, MEMORY_CODE, PRODUCTION_CODE, RETRIEVAL_CODE, SQL_CODE,
)


@dataclass(frozen=True)
class SkillSpec:
    name: str
    agent: str
    signature: str
    file: str


@dataclass(frozen=True)
class PhaseSpec:
    phase: int
    label: str
    primary_agent: str
    agent_file: str
    method: str
    specialists: tuple[str, ...]
    skills: tuple[SkillSpec, ...]


PHASE_CATALOG: Dict[int, PhaseSpec] = {
    1: PhaseSpec(
        phase=1,
        label="SQL search",
        primary_agent=SQL_CODE,
        agent_file="tools/catalog_tools.py",
        method="Direct RDS Data API: one parameterized search_trips statement",
        specialists=(),
        skills=(
            SkillSpec(
                "search_trips", SQL_CODE,
                "search_trips(trip_type, max_price_per_person, limit, text)",
                "tools/catalog_tools.py",
            ),
        ),
    ),
    2: PhaseSpec(
        phase=2,
        label="MCP client",
        primary_agent=MCP_CODE,
        agent_file="routers/chat.py",
        method=(
            "Typed tools on the meridian-concierge MCP server: search_trips, "
            "compare_packages, price_range, region_inventory, currency_convert, loyalty_balance"
        ),
        specialists=(),
        skills=tuple(
            SkillSpec(name, "meridian-concierge", signature, "mcp/concierge_server.py")
            for name, signature in (
                ("search_trips", "search_trips(trip_type, max_price_per_person, limit, text)"),
                ("compare_packages", "compare_packages(package_ids)"),
                ("price_range", "price_range(destination)"),
                ("region_inventory", "region_inventory(region, trip_type)"),
                ("currency_convert", "currency_convert(amount, from_ccy, to_ccy)"),
                ("loyalty_balance", "loyalty_balance(travelerId, program)"),
            )
        ),
    ),
    3: PhaseSpec(
        phase=3,
        label="Retrieval Agent",
        primary_agent="RetrievalAgent",
        agent_file="agents/phase_03_retrieval/agent.py",
        method="One Strands agent loop over the cumulative tools; the reply is the agent's own",
        specialists=(),
        skills=(
            SkillSpec("hybrid_trip_search", "RetrievalAgent",
                      "hybrid_trip_search(query, limit=5) in process",
                      "tools/catalog_tools.py"),
            SkillSpec("get_package_details", "RetrievalAgent",
                      "get_package_details(packageId) in process",
                      "tools/package_tools.py"),
            SkillSpec("search_trips", "meridian-concierge",
                      "search_trips(trip_type, max_price_per_person, limit, text) over MCP stdio",
                      "mcp/concierge_server.py"),
            SkillSpec("compare_packages", "meridian-concierge",
                      "compare_packages(package_ids) over MCP stdio", "mcp/concierge_server.py"),
            SkillSpec("price_range", "meridian-concierge",
                      "price_range(destination) over MCP stdio", "mcp/concierge_server.py"),
            SkillSpec("region_inventory", "meridian-concierge",
                      "region_inventory(region, trip_type) over MCP stdio",
                      "mcp/concierge_server.py"),
            SkillSpec("currency_convert", "meridian-concierge",
                      "currency_convert(amount, from_ccy, to_ccy) over MCP stdio",
                      "mcp/concierge_server.py"),
            SkillSpec("loyalty_balance", "meridian-concierge",
                      "loyalty_balance(travelerId, program) over MCP stdio",
                      "mcp/concierge_server.py"),
        ),
    ),
    4: PhaseSpec(
        phase=4,
        label="Production Agent",
        primary_agent=PRODUCTION_CODE,
        agent_file="agents/phase_04_production/concierge.py",
        method="Identity + RLS around AgentCore Runtime; the runtime agent calls Gateway tools under Cedar policy",
        specialists=(MEMORY_CODE, "MeridianConcierge runtime"),
        skills=(
            SkillSpec("runtime_turn", "AgentCore Runtime", "invoke_agent_runtime(session_id, authorized context, hold contract) streamed over SSE", "agentcore/runtime.py"),
            SkillSpec(
                "hybrid_trip_search", "AgentCore Gateway",
                "MeridianCatalog___hybrid_trip_search(query, limit)",
                "meridian_agentcore/agentcore/gateway_targets/"
                "meridian_catalog/lambda_function.py",
            ),
            SkillSpec("get_package_details", "AgentCore Gateway", "MeridianHolds___get_package_details(packageId)", "meridian_agentcore/agentcore/gateway_targets/meridian_holds/lambda_function.py"),
            SkillSpec(
                "loyalty_balance", "AgentCore Gateway",
                "MeridianHolds___loyalty_balance(travelerId, program)",
                "meridian_agentcore/agentcore/gateway_targets/"
                "meridian_holds/lambda_function.py",
            ),
            SkillSpec("create_courtesy_hold", "AgentCore Gateway + Policy", "MeridianHolds___create_courtesy_hold(... travelerConfirmed, budgetCeilingCents) under Cedar ENFORCE", "meridian_agentcore/agentcore/gateway_targets/meridian_holds/lambda_function.py"),
            SkillSpec(
                "recall_session_context", MEMORY_CODE,
                "recall_session_context(conversation_id, limit=6)",
                "agents/phase_04_production/memory_agent.py",
            ),
            SkillSpec(
                "recall_traveler_preferences", MEMORY_CODE,
                "recall_traveler_preferences(traveler_id, limit=8)",
                "agents/phase_04_production/memory_agent.py",
            ),
            SkillSpec(
                "recall_similar_interactions", MEMORY_CODE,
                "recall_similar_interactions(traveler_id, query, limit=3)",
                "agents/phase_04_production/memory_agent.py",
            ),
            SkillSpec(
                "persist_turn", MEMORY_CODE,
                "persist_turn(...)",
                "agents/phase_04_production/memory_agent.py",
            ),
        ),
    ),
    5: PhaseSpec(
        phase=5,
        label="Orchestration Agent",
        primary_agent="WorkflowRunner",
        agent_file="agents/phase_05_workflow/graph.py",
        method="Strands Graph (classify → branch → synthesize)",
        specialists=(RETRIEVAL_CODE, AVAILABILITY_CODE),
        skills=(
            SkillSpec("classify", "WorkflowNodes", "classify(state)", "agents/phase_05_workflow/nodes.py"),
            SkillSpec("snapshot", "SnapshotSessionManager", "AuroraSnapshotStorage.write(key, data)", "agents/phase_05_workflow/snapshot_storage.py"),
            SkillSpec("hold", "WorkflowNodes", "hold(state, config)", "agents/phase_05_workflow/nodes.py"),
            SkillSpec("synthesize", "WorkflowNodes", "synthesize(state)", "agents/phase_05_workflow/nodes.py"),
        ),
    ),
}


def get_phase_spec(phase: int) -> Optional[PhaseSpec]:
    return PHASE_CATALOG.get(phase)


def format_skills_summary(phase: int) -> str:
    spec = get_phase_spec(phase)
    if not spec:
        return ""
    return ", ".join(f"{s.name} ({s.agent})" for s in spec.skills)


def format_specialists_summary(phase: int) -> str:
    spec = get_phase_spec(phase)
    if not spec or not spec.specialists:
        return "—"
    return ", ".join(spec.specialists)
