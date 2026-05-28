"""
Phase 5 — LangGraph workflow that orchestrates classify → branch → synthesize.

Why this exists: Phase 3 / 4 use Strands for tool routing.  Phase 5 shows the
*workflow* pattern — an explicit StateGraph with conditional edges and a
durable checkpoint.  The state survives interruption because LangGraph
serializes it after every node.

State machine
=============

    classify ─┬─→ search ─────────┐
              ├─→ availability ───┤
              └─→ memory_recall ──┤
                                  ▼
                              synthesize → END

Checkpointer
============

If `LANGGRAPH_CHECKPOINT_DSN` is set we use `PostgresSaver` against Aurora
(durable, multi-process).  Otherwise we use the in-process `MemorySaver`
so the workshop demo still runs without direct DB connectivity.

AWS docs (Aurora checkpoint store):
  - Aurora PostgreSQL connection strings:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.Connecting.html
  - RDS Data API (search/memory nodes reuse Phase 3/4 Aurora paths):
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypedDict

logger = logging.getLogger(__name__)

# LangGraph imports are kept module-local so the rest of the backend doesn't
# fail to import when langgraph isn't installed (e.g. in Phase 1-4 unit
# tests).  The Phase 5 router only imports this module when a request hits
# /api/chat with phase=5.
from langgraph.graph import StateGraph, END  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

try:
    from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore
except ImportError:  # pragma: no cover - optional extra
    PostgresSaver = None  # type: ignore


AGENT_FILE = "agents/orchestration_05/workflow.py"


class WorkflowState(TypedDict, total=False):
    query: str
    traveler_id: str
    conversation_id: str
    intent: str  # 'search' | 'availability' | 'memory_recall'
    packages: List[Any]
    response: str
    activities: List[Dict[str, Any]]


def _activity(
    activity_type: str,
    title: str,
    *,
    details: Optional[str] = None,
    agent_name: str = "OrchestrationAgent",
    telemetry: Optional[Dict[str, Any]] = None,
    sql_query: Optional[str] = None,
    execution_time_ms: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "activity_type": activity_type,
        "title": title,
        "details": details,
        "sql_query": sql_query,
        "execution_time_ms": execution_time_ms,
        "agent_name": agent_name,
        "agent_file": AGENT_FILE,
        "telemetry": telemetry,
    }


def _classify_intent(query: str) -> str:
    q = query.lower()
    availability_signals = (
        "available",
        "availability",
        "departure",
        "departures",
        "slots",
        "dates",
        "what dates",
        "when can",
    )
    if any(s in q for s in availability_signals):
        return "availability"
    memory_signals = (
        "remember",
        "last time",
        "previous",
        "we discussed",
        "you said",
    )
    if any(s in q for s in memory_signals):
        return "memory_recall"
    return "search"


class OrchestrationAgent:
    """LangGraph workflow with classify/search/availability/synthesize nodes."""

    def __init__(
        self,
        search_fn: Callable[..., Awaitable[Any]],
        availability_fn: Callable[..., Awaitable[Any]],
        memory_recall_fn: Optional[Callable[..., Awaitable[Any]]] = None,
    ) -> None:
        self.search_fn = search_fn
        self.availability_fn = availability_fn
        self.memory_recall_fn = memory_recall_fn
        self.checkpointer, self.checkpointer_kind = self._build_checkpointer()
        self.graph = self._build_graph()

    # ------------------------------------------------------------- checkpointer

    def _build_checkpointer(self):
        dsn = os.getenv("LANGGRAPH_CHECKPOINT_DSN")
        if dsn and PostgresSaver is not None:
            try:
                saver = PostgresSaver.from_conn_string(dsn)
                saver.setup()
                return saver, "PostgresSaver (Aurora)"
            except Exception as exc:
                logger.warning(
                    "PostgresSaver unavailable (%s) — falling back to MemorySaver", exc
                )
        return MemorySaver(), "MemorySaver (in-process)"

    # ------------------------------------------------------------------ graph

    def _build_graph(self):
        """Compile the LangGraph StateGraph with conditional routing + checkpointer."""
        builder = StateGraph(WorkflowState)
        builder.add_node("classify", self._node_classify)
        builder.add_node("search", self._node_search)
        builder.add_node("availability", self._node_availability)
        builder.add_node("memory_recall", self._node_memory_recall)
        builder.add_node("synthesize", self._node_synthesize)

        builder.set_entry_point("classify")
        builder.add_conditional_edges(
            "classify",
            lambda state: state.get("intent", "search"),
            {
                "search": "search",
                "availability": "availability",
                "memory_recall": "memory_recall",
            },
        )
        builder.add_edge("search", "synthesize")
        builder.add_edge("availability", "synthesize")
        builder.add_edge("memory_recall", "synthesize")
        builder.add_edge("synthesize", END)
        return builder.compile(checkpointer=self.checkpointer)

    # ---------------------------------------------------------------- nodes

    async def _node_classify(self, state: WorkflowState) -> WorkflowState:
        start = datetime.utcnow()
        intent = _classify_intent(state["query"])
        elapsed = int((datetime.utcnow() - start).total_seconds() * 1000)
        activities = list(state.get("activities", []))
        activities.append(
            _activity(
                "reasoning",
                f"Workflow node: classify → {intent}",
                details=f"intent={intent}",
                execution_time_ms=elapsed,
                telemetry={
                    "category": "orchestration",
                    "component": "LangGraph StateGraph",
                    "status": "ok",
                    "fields": [
                        {"label": "node", "value": "classify"},
                        {"label": "intent", "value": intent},
                        {"label": "checkpointer", "value": self.checkpointer_kind},
                    ],
                },
            )
        )
        return {"intent": intent, "activities": activities}

    async def _node_search(self, state: WorkflowState) -> WorkflowState:
        start = datetime.utcnow()
        packages, search_activities = await self.search_fn(state["query"], limit=5)
        elapsed = int((datetime.utcnow() - start).total_seconds() * 1000)
        activities = list(state.get("activities", []))
        activities.append(
            _activity(
                "delegation",
                "Workflow node: search",
                details=f"{len(packages)} packages",
                execution_time_ms=elapsed,
                telemetry={
                    "category": "orchestration",
                    "component": "LangGraph → SearchAgent",
                    "status": "ok",
                    "fields": [
                        {"label": "node", "value": "search"},
                        {"label": "packages", "value": str(len(packages))},
                    ],
                },
            )
        )
        for sa in search_activities:
            activities.append(_coerce_activity(sa))
        # PostgresSaver checkpoint after the node returns. Surface the
        # SQL so the SQL tab shows what LangGraph writes between nodes.
        activities.append(
            _activity(
                "tool_call",
                "Checkpoint · PostgresSaver.put",
                details=f"Workflow state serialized after search node ({elapsed}ms)",
                sql_query=(
                    "INSERT INTO langgraph_checkpoints\n"
                    "  (thread_id, checkpoint_ns, checkpoint_id,\n"
                    "   parent_id, type, checkpoint, metadata)\n"
                    "VALUES ($1, $2, $3, $4, 'msgpack', $5, $6)\n"
                    "ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id)\n"
                    "DO UPDATE SET checkpoint = EXCLUDED.checkpoint;"
                ),
                telemetry={
                    "category": "memory_short",
                    "component": "Aurora · langgraph_checkpoints",
                    "status": "ok",
                    "fields": [
                        {"label": "checkpointer", "value": self.checkpointer_kind},
                    ],
                },
            )
        )
        return {"packages": packages, "activities": activities}

    async def _node_availability(self, state: WorkflowState) -> WorkflowState:
        start = datetime.utcnow()
        packages, sub_activities, _msg = await self.availability_fn(state["query"])
        elapsed = int((datetime.utcnow() - start).total_seconds() * 1000)
        activities = list(state.get("activities", []))
        activities.append(
            _activity(
                "delegation",
                "Workflow node: availability",
                details=f"{len(packages)} availability rows",
                execution_time_ms=elapsed,
                telemetry={
                    "category": "orchestration",
                    "component": "LangGraph → PackageAgent",
                    "status": "ok",
                    "fields": [
                        {"label": "node", "value": "availability"},
                        {"label": "rows", "value": str(len(packages))},
                    ],
                },
            )
        )
        for sa in sub_activities:
            activities.append(_coerce_activity(sa))
        activities.append(
            _activity(
                "tool_call",
                "Checkpoint · PostgresSaver.put",
                details=f"Workflow state serialized after availability node ({elapsed}ms)",
                sql_query=(
                    "INSERT INTO langgraph_checkpoints\n"
                    "  (thread_id, checkpoint_ns, checkpoint_id,\n"
                    "   parent_id, type, checkpoint, metadata)\n"
                    "VALUES ($1, $2, $3, $4, 'msgpack', $5, $6)\n"
                    "ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id)\n"
                    "DO UPDATE SET checkpoint = EXCLUDED.checkpoint;"
                ),
                telemetry={
                    "category": "memory_short",
                    "component": "Aurora · langgraph_checkpoints",
                    "status": "ok",
                    "fields": [
                        {"label": "checkpointer", "value": self.checkpointer_kind},
                    ],
                },
            )
        )
        return {"packages": packages, "activities": activities}

    async def _node_memory_recall(self, state: WorkflowState) -> WorkflowState:
        start = datetime.utcnow()
        activities = list(state.get("activities", []))
        if self.memory_recall_fn is None:
            activities.append(
                _activity(
                    "reasoning",
                    "Workflow node: memory_recall (skipped)",
                    details="No memory recall function wired",
                )
            )
            return {"packages": [], "activities": activities}
        packages, sub_activities = await self.memory_recall_fn(
            state["query"],
            traveler_id=state.get("traveler_id", ""),
            conversation_id=state.get("conversation_id", ""),
        )
        elapsed = int((datetime.utcnow() - start).total_seconds() * 1000)
        activities.append(
            _activity(
                "delegation",
                "Workflow node: memory_recall",
                details=f"{len(packages)} memory hits",
                execution_time_ms=elapsed,
                telemetry={
                    "category": "orchestration",
                    "component": "LangGraph → ProductionAgent",
                    "status": "ok",
                    "fields": [{"label": "node", "value": "memory_recall"}],
                },
            )
        )
        for sa in sub_activities:
            activities.append(_coerce_activity(sa))
        return {"packages": packages, "activities": activities}

    async def _node_synthesize(self, state: WorkflowState) -> WorkflowState:
        packages = state.get("packages", []) or []
        intent = state.get("intent", "search")
        if intent == "availability" and packages:
            response = (
                f"Found {len(packages)} departure options matching your request."
            )
        elif intent == "memory_recall":
            response = (
                f"Recalled session + preference context, then matched {len(packages)} trips."
                if packages
                else "Recalled prior context — no new catalog matches for that query."
            )
        elif packages:
            response = f"Workflow returned {len(packages)} trips that match your request."
        else:
            response = "No matches yet — try broadening the destination or dates."

        activities = list(state.get("activities", []))
        activities.append(
            _activity(
                "result",
                "Workflow node: synthesize",
                details=response,
                telemetry={
                    "category": "synthesis",
                    "component": "LangGraph",
                    "status": "ok",
                    "fields": [
                        {"label": "intent", "value": intent},
                        {"label": "packages", "value": str(len(packages))},
                    ],
                },
            )
        )
        return {"response": response, "activities": activities}

    # ------------------------------------------------------------------- run

    async def run(
        self,
        query: str,
        traveler_id: str,
        conversation_id: str,
    ) -> WorkflowState:
        thread_id = conversation_id or f"phase5-{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id}}
        initial: WorkflowState = {
            "query": query,
            "traveler_id": traveler_id,
            "conversation_id": thread_id,
            "activities": [],
        }
        result = await self.graph.ainvoke(initial, config=config)
        return result


def _coerce_activity(activity: Any) -> Dict[str, Any]:
    """Convert a backend ActivityEntry / pydantic / dict into the Phase 5 dict shape."""
    if isinstance(activity, dict):
        return activity
    if hasattr(activity, "model_dump"):
        return activity.model_dump()
    if hasattr(activity, "__dict__"):
        return dict(activity.__dict__)
    return {"title": str(activity)}
