"""
Phase → agent → tool/skill catalog for verbose backend logging.

Mirrors the Meridian Pro workshop UI skill matrix so logs name the same
agents, specialists, and @tool signatures the frontend displays.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


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
        label="SQL Agent",
        primary_agent="SQLAgent",
        agent_file="agents/phase1/agent.py",
        method="Direct RDS Data API (SQL WHERE filters)",
        specialists=(),
        skills=(
            SkillSpec("sql_filter", "SQLAgent", "run_sql(category, max_price)", "agents/phase1/agent.py"),
        ),
    ),
    2: PhaseSpec(
        phase=2,
        label="MCP Agent",
        primary_agent="MCPAgent",
        agent_file="agents/phase2/agent.py",
        method="MCP run_query → Aurora via postgres-mcp-server",
        specialists=(),
        skills=(
            SkillSpec("run_query", "postgres-mcp-server", "run_query(sql, params)", "mcp/postgres/server.py"),
        ),
    ),
    3: PhaseSpec(
        phase=3,
        label="Retrieval Agent",
        primary_agent="RetrievalAgent",
        agent_file="agents/phase3/supervisor.py",
        method="Strands supervisor + Bedrock tool delegation",
        specialists=("SearchAgent", "PackageAgent", "BookingAgent"),
        skills=(
            SkillSpec("_semantic_search_tool", "SearchAgent", "_semantic_search_tool(query, limit=5)", "agents/phase3/search_agent.py"),
            SkillSpec("_check_availability_tool", "PackageAgent", "_check_availability_tool(package_id, duration?)", "agents/phase3/package_agent.py"),
            SkillSpec("_process_booking_tool", "BookingAgent", "_process_booking_tool(customer_id, items)", "agents/phase3/booking_agent.py"),
        ),
    ),
    4: PhaseSpec(
        phase=4,
        label="Production Agent",
        primary_agent="ProductionAgent",
        agent_file="agents/phase4/concierge.py",
        method="Strands concierge + AgentCore (Runtime/Gateway/Memory/Identity) + Aurora",
        specialists=("MemoryAgent", "RetrievalAgent"),
        skills=(
            SkillSpec("runtime_session", "AgentCore Runtime", "invoke_agent_runtime(session_id, payload)", "agentcore/runtime.py"),
            SkillSpec("gateway_search", "AgentCore Gateway", "tools/call(semantic_trip_search)", "agentcore/gateway.py"),
            SkillSpec("recall_session_context", "MemoryAgent", "recall_session_context(conversation_id, limit=6)", "agents/phase4/memory_agent.py"),
            SkillSpec("recall_traveler_preferences", "MemoryAgent", "recall_traveler_preferences(traveler_id, limit=8)", "agents/phase4/memory_agent.py"),
            SkillSpec("recall_similar_interactions", "MemoryAgent", "recall_similar_interactions(traveler_id, query, limit=3)", "agents/phase4/memory_agent.py"),
            SkillSpec("persist_turn", "MemoryAgent", "persist_turn(...)", "agents/phase4/memory_agent.py"),
        ),
    ),
    5: PhaseSpec(
        phase=5,
        label="Orchestration Agent",
        primary_agent="OrchestrationAgent",
        agent_file="agents/phase5/workflow.py",
        method="LangGraph StateGraph (classify → branch → synthesize)",
        specialists=("SearchAgent", "PackageAgent"),
        skills=(
            SkillSpec("classify", "OrchestrationAgent", "classify_intent(state)", "agents/phase5/workflow.py"),
            SkillSpec("checkpoint", "PostgresSaver", "save_checkpoint(thread_id, state)", "agents/phase5/workflow.py"),
            SkillSpec("synthesize", "OrchestrationAgent", "synthesize_reply(state)", "agents/phase5/workflow.py"),
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
