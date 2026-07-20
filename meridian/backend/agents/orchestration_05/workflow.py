"""
Phase 5 — LangGraph workflow that orchestrates classify → branch → synthesize.

Why this exists: Phase 3 / 4 use Strands for tool routing.  Phase 5 shows the
*workflow* pattern — an explicit StateGraph with conditional edges and a
durable checkpoint.  The state survives interruption because LangGraph
serializes it after every node.

State machine
=============

    classify ─┬─→ search ───────────────┐
              │      └─(plan)─→ availability ─┐
              ├─→ availability ───────────────┤
              └─→ memory_recall ──────────────┤
                                              ▼
                                          synthesize → END

The "plan" intent is the multi-step path: search THEN availability run as
two sequential worker nodes, each checkpointed to Aurora. That's the case
an explicit StateGraph handles that a single tool call can't make visible.

Checkpointer
============

If `LANGGRAPH_CHECKPOINT_DSN` is set we create one bounded psycopg pool and
share an `AsyncPostgresSaver` across requests (durable, multi-process).
Otherwise we use an in-process `MemorySaver` so the workshop demo still runs
without direct DB connectivity. Set `LANGGRAPH_CHECKPOINT_REQUIRED=true` in a
production deployment so an unavailable durable store fails closed.

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
import json
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypedDict
from urllib.parse import quote

logger = logging.getLogger(__name__)

# LangGraph imports are kept module-local so the rest of the backend doesn't
# fail to import when langgraph isn't installed (e.g. in Phase 1-4 unit
# tests).  The Phase 5 router only imports this module when a request hits
# /api/chat with phase=5.
from langgraph.graph import StateGraph, END  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # type: ignore
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
except ImportError:  # pragma: no cover - optional extra
    AsyncPostgresSaver = None  # type: ignore
    AsyncConnectionPool = None  # type: ignore
    dict_row = None  # type: ignore


AGENT_FILE = "agents/orchestration_05/workflow.py"
POSTGRES_CHECKPOINT_TABLES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_timestamp() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _truthy_env(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _secret_json(secret_arn: str) -> Dict[str, Any]:
    """Read the existing Aurora secret so Phase 5 can build a Postgres DSN.

    The repo's primary data path uses RDS Data API, so .env intentionally
    stores a Secrets Manager ARN instead of a plaintext database password.
    PostgresSaver needs a direct PostgreSQL DSN; this adapter derives it from
    the same Aurora secret rather than requiring a second presenter-only env var.
    """
    try:
        import boto3

        region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=secret_arn)
        raw = response.get("SecretString") or "{}"
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception as exc:
        logger.warning("Unable to read Aurora secret for PostgresSaver DSN: %s", exc)
        return {}


def _resolve_checkpoint_dsn() -> Optional[str]:
    """Resolve the DSN used by LangGraph PostgresSaver.

    Priority:
      1. Explicit LANGGRAPH_CHECKPOINT_DSN.
      2. Auto-built DSN from Aurora env + Secrets Manager secret.

    Set LANGGRAPH_AUTO_CHECKPOINT_DSN=false to disable the auto-build fallback.
    """
    explicit = os.getenv("LANGGRAPH_CHECKPOINT_DSN")
    if explicit:
        return explicit
    if not _truthy_env("LANGGRAPH_AUTO_CHECKPOINT_DSN"):
        return None

    secret: Dict[str, Any] = {}
    secret_arn = os.getenv("LANGGRAPH_CHECKPOINT_SECRET_ARN") or os.getenv(
        "AURORA_SECRET_ARN"
    )
    if secret_arn:
        secret = _secret_json(secret_arn)

    username = (
        os.getenv("LANGGRAPH_CHECKPOINT_USERNAME")
        or os.getenv("AURORA_USERNAME")
        or str(secret.get("username") or "")
    ).strip()
    password = (
        os.getenv("LANGGRAPH_CHECKPOINT_PASSWORD")
        or os.getenv("AURORA_PASSWORD")
        or str(secret.get("password") or "")
    ).strip()
    host = (
        os.getenv("LANGGRAPH_CHECKPOINT_HOST")
        or os.getenv("AURORA_HOST")
        or os.getenv("AURORA_CLUSTER_ENDPOINT")
        or str(secret.get("host") or "")
    ).strip()
    port = str(
        os.getenv("LANGGRAPH_CHECKPOINT_PORT")
        or os.getenv("AURORA_PORT")
        or secret.get("port")
        or "5432"
    ).strip()
    database = (
        os.getenv("LANGGRAPH_CHECKPOINT_DATABASE")
        or os.getenv("AURORA_DATABASE")
        or str(secret.get("dbname") or secret.get("database") or "")
    ).strip()

    if not all((username, password, host, port, database)):
        return None

    user = quote(username, safe="")
    pwd = quote(password, safe="")
    db = quote(database, safe="")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}?sslmode=require"


@dataclass
class CheckpointBackend:
    saver: Any
    kind: str
    durable: bool
    pool: Any = None
    error: Optional[str] = None


_checkpoint_backend: Optional[CheckpointBackend] = None
_checkpoint_init_lock: Optional[asyncio.Lock] = None


def _checkpoint_required() -> bool:
    return _truthy_env("LANGGRAPH_CHECKPOINT_REQUIRED", "false")


async def initialize_checkpoint_backend() -> CheckpointBackend:
    """Initialize the process-wide LangGraph checkpointer once."""
    global _checkpoint_backend, _checkpoint_init_lock

    if _checkpoint_backend is not None:
        return _checkpoint_backend

    if _checkpoint_init_lock is None:
        _checkpoint_init_lock = asyncio.Lock()

    async with _checkpoint_init_lock:
        if _checkpoint_backend is not None:
            return _checkpoint_backend

        dsn = _resolve_checkpoint_dsn()
        if not dsn:
            if _checkpoint_required():
                raise RuntimeError(
                    "Durable workflow checkpoints are required, but no "
                    "LANGGRAPH_CHECKPOINT_DSN or checkpoint credentials resolved."
                )
            _checkpoint_backend = CheckpointBackend(
                saver=MemorySaver(),
                kind="MemorySaver (in-process)",
                durable=False,
            )
            return _checkpoint_backend

        if (
            AsyncPostgresSaver is None
            or AsyncConnectionPool is None
            or dict_row is None
        ):
            message = (
                "Durable workflow checkpoints require "
                "langgraph-checkpoint-postgres and psycopg-pool."
            )
            if _checkpoint_required():
                raise RuntimeError(message)
            logger.warning("%s Falling back to MemorySaver.", message)
            _checkpoint_backend = CheckpointBackend(
                saver=MemorySaver(),
                kind="MemorySaver (in-process)",
                durable=False,
                error=message,
            )
            return _checkpoint_backend

        min_size = max(
            1,
            _int_env(
                "LANGGRAPH_CHECKPOINT_POOL_MIN_SIZE",
                _int_env("AURORA_MIN_POOL_SIZE", 1),
            ),
        )
        max_size = max(
            min_size,
            _int_env(
                "LANGGRAPH_CHECKPOINT_POOL_MAX_SIZE",
                _int_env("AURORA_MAX_POOL_SIZE", 10),
            ),
        )
        pool_timeout = max(
            1,
            _int_env(
                "LANGGRAPH_CHECKPOINT_POOL_TIMEOUT",
                _int_env("AURORA_POOL_TIMEOUT", 10),
            ),
        )
        connect_timeout = max(
            1,
            _int_env("LANGGRAPH_CHECKPOINT_CONNECT_TIMEOUT", 5),
        )
        pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            timeout=float(pool_timeout),
            open=False,
            name="meridian-langgraph-checkpoints",
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
                "connect_timeout": connect_timeout,
            },
        )

        try:
            await pool.open(wait=True, timeout=float(pool_timeout))
            saver = AsyncPostgresSaver(pool)
            await saver.setup()
            _checkpoint_backend = CheckpointBackend(
                saver=saver,
                kind="PostgresSaver (Aurora · pooled)",
                durable=True,
                pool=pool,
            )
        except Exception as exc:
            await pool.close()
            message = f"PostgresSaver unavailable: {str(exc)[:240]}"
            if _checkpoint_required():
                raise RuntimeError(message) from exc
            logger.warning("%s Falling back to MemorySaver.", message)
            _checkpoint_backend = CheckpointBackend(
                saver=MemorySaver(),
                kind="MemorySaver (in-process)",
                durable=False,
                error=message,
            )

        return _checkpoint_backend


async def close_checkpoint_backend() -> None:
    """Close the shared checkpoint pool during application shutdown."""
    global _checkpoint_backend, _checkpoint_init_lock

    backend = _checkpoint_backend
    _checkpoint_backend = None
    _checkpoint_init_lock = None
    if backend is not None and backend.pool is not None:
        await backend.pool.close()


def checkpoint_backend_status() -> Dict[str, Any]:
    """Return non-secret checkpoint configuration for health and diagnostics."""
    backend = _checkpoint_backend
    explicitly_configured = bool(os.getenv("LANGGRAPH_CHECKPOINT_DSN"))
    auto_configured = _truthy_env("LANGGRAPH_AUTO_CHECKPOINT_DSN") and bool(
        (
            os.getenv("LANGGRAPH_CHECKPOINT_HOST")
            or os.getenv("AURORA_HOST")
            or os.getenv("AURORA_CLUSTER_ENDPOINT")
        )
        and (
            os.getenv("LANGGRAPH_CHECKPOINT_SECRET_ARN")
            or os.getenv("AURORA_SECRET_ARN")
            or (
                (
                    os.getenv("LANGGRAPH_CHECKPOINT_USERNAME")
                    or os.getenv("AURORA_USERNAME")
                )
                and (
                    os.getenv("LANGGRAPH_CHECKPOINT_PASSWORD")
                    or os.getenv("AURORA_PASSWORD")
                )
            )
        )
    )
    return {
        "kind": backend.kind if backend else "not initialized",
        "durable": backend.durable if backend else False,
        "required": _checkpoint_required(),
        "configured": explicitly_configured or auto_configured,
        "error": backend.error if backend else None,
    }


class WorkflowState(TypedDict, total=False):
    query: str
    traveler_id: str
    conversation_id: str
    intent: str  # 'search' | 'availability' | 'memory_recall'
    packages: List[Any]
    response: str
    activities: List[Dict[str, Any]]
    workflow_status: str


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
        "timestamp": _utc_timestamp(),
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
    # "plan" is the multi-step intent: prompts that ask for a trip AND its
    # open dates in one breath. It routes through TWO sequential worker
    # nodes (search → availability) before synthesis — the case where an
    # explicit LangGraph StateGraph genuinely beats a single tool call,
    # because the graph composes steps and checkpoints between each.
    plan_signals = (
        "plan ",
        "plan our",
        "plan a",
        "plan me",
        "find a trip and",
        "and check availability",
        "and the open dates",
        "with open dates",
        "shortlist and",
        "then check",
        "end to end",
        "end-to-end",
    )
    # A prompt that names BOTH a destination/search intent AND a date/slot
    # intent is also a plan (e.g. "Kyoto trip and when it's available").
    has_search_intent = any(
        s in q for s in ("trip", "getaway", "escape", "vacation", "holiday", "find", "show me")
    )
    has_date_intent = any(
        s in q for s in ("date", "dates", "available", "availability", "departure", "slots", "when")
    )
    if any(s in q for s in plan_signals) or (has_search_intent and has_date_intent):
        return "plan"

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
        self.checkpointer = MemorySaver()
        self.checkpointer_kind = "MemorySaver (initializing)"
        self.graph = self._build_graph()

    @property
    def _uses_postgres_saver(self) -> bool:
        return self.checkpointer_kind.startswith("PostgresSaver")

    def _checkpoint_activity(self, node: str, elapsed_ms: int) -> Dict[str, Any]:
        """Trace the actual configured checkpointer, not just the ideal one."""
        if self._uses_postgres_saver:
            return _activity(
                "tool_call",
                "Checkpoint · PostgresSaver.put",
                details=f"Workflow state serialized after {node} node ({elapsed_ms}ms)",
                sql_query=(
                    "INSERT INTO checkpoints\n"
                    "  (thread_id, checkpoint_ns, checkpoint_id,\n"
                    "   parent_checkpoint_id, type, checkpoint, metadata)\n"
                    "VALUES ($1, $2, $3, $4, 'msgpack', $5, $6)\n"
                    "ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id)\n"
                    "DO UPDATE SET checkpoint = EXCLUDED.checkpoint;\n"
                    "-- Channel values and pending writes are stored in\n"
                    "-- checkpoint_blobs and checkpoint_writes."
                ),
                telemetry={
                    "category": "memory_short",
                    "component": "Aurora · LangGraph checkpoint tables",
                    "status": "ok",
                    "fields": [
                        {"label": "checkpointer", "value": self.checkpointer_kind},
                        {"label": "checkpoint_store", "value": "checkpoints"},
                        {
                            "label": "checkpoint_tables",
                            "value": ", ".join(POSTGRES_CHECKPOINT_TABLES),
                        },
                        {"label": "durability", "value": "Aurora"},
                    ],
                },
            )

        return _activity(
            "tool_call",
            "Checkpoint · MemorySaver.put",
            details=(
                f"Workflow state kept in-process after {node} node ({elapsed_ms}ms). "
                "Set LANGGRAPH_CHECKPOINT_DSN for Aurora durability."
            ),
            telemetry={
                "category": "memory_short",
                "component": "LangGraph MemorySaver (in-process)",
                "status": "ok",
                "fields": [
                    {"label": "checkpointer", "value": self.checkpointer_kind},
                    {"label": "checkpoint_store", "value": "process memory"},
                    {"label": "durability", "value": "ephemeral"},
                ],
            },
        )

    # ------------------------------------------------------------- checkpointer

    async def _ensure_checkpoint_backend(self) -> None:
        backend = await initialize_checkpoint_backend()
        if self.checkpointer is backend.saver:
            return
        self.checkpointer = backend.saver
        self.checkpointer_kind = backend.kind
        self.graph = self._build_graph()

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
        # classify fans out to the right worker. "plan" enters at search,
        # then chains into availability (see the conditional edge below).
        builder.add_conditional_edges(
            "classify",
            lambda state: state.get("intent", "search"),
            {
                "search": "search",
                "plan": "search",
                "availability": "availability",
                "memory_recall": "memory_recall",
            },
        )
        # The edge OUT of search is itself conditional: a plain "search"
        # intent finishes at synthesize, but a "plan" intent continues to
        # the availability node — two sequential worker steps, each with
        # its own PostgresSaver checkpoint. This is the multi-step graph
        # composition that a single Strands tool call can't make explicit.
        builder.add_conditional_edges(
            "search",
            lambda state: "availability" if state.get("intent") == "plan" else "synthesize",
            {
                "availability": "availability",
                "synthesize": "synthesize",
            },
        )
        builder.add_edge("availability", "synthesize")
        builder.add_edge("memory_recall", "synthesize")
        builder.add_edge("synthesize", END)
        interrupt_after = os.getenv("LANGGRAPH_DEMO_INTERRUPT_AFTER", "").strip()
        allowed_interrupts = {
            "classify",
            "search",
            "availability",
            "memory_recall",
            "synthesize",
        }
        if interrupt_after and interrupt_after not in allowed_interrupts:
            logger.warning(
                "Ignoring invalid LANGGRAPH_DEMO_INTERRUPT_AFTER=%s",
                interrupt_after,
            )
            interrupt_after = ""

        return builder.compile(
            checkpointer=self.checkpointer,
            interrupt_after=[interrupt_after] if interrupt_after else None,
        )

    # ---------------------------------------------------------------- nodes

    async def _node_classify(self, state: WorkflowState) -> WorkflowState:
        """Entry node: classify the query into an intent that drives routing.

        Writes `intent` into state; the conditional edge out of this node reads
        it to fan out to search / availability / memory_recall (or the 'plan'
        path, which enters at search and chains into availability).
        """
        start = _utc_now()
        intent = _classify_intent(state["query"])
        elapsed = int((_utc_now() - start).total_seconds() * 1000)
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
        """Worker node: run trip discovery (delegates to the Phase 3 search fn).

        Checkpoints state to Aurora after returning. On the 'plan' path this is
        step 1 of 2 — the conditional edge then routes to the availability node.
        """
        start = _utc_now()
        packages, search_activities = await self.search_fn(state["query"], limit=5)
        elapsed = int((_utc_now() - start).total_seconds() * 1000)
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
        activities.append(self._checkpoint_activity("search", elapsed))
        return {"packages": packages, "activities": activities}

    async def _node_availability(self, state: WorkflowState) -> WorkflowState:
        """Worker node: check departure availability (delegates to Package fn).

        On the 'plan' path this runs AFTER search (step 2 of 2) and layers
        availability onto the prior trip results; for a standalone 'availability'
        intent it surfaces the availability rows directly. Checkpoints after return.
        """
        start = _utc_now()
        avail_packages, sub_activities, _msg = await self.availability_fn(state["query"])
        elapsed = int((_utc_now() - start).total_seconds() * 1000)
        # In the multi-step "plan" path this node runs AFTER search, so we
        # keep the richer search results (trip cards + similarity scores)
        # as the user-facing set and treat availability as a layered-on
        # step. For a standalone "availability" intent there are no prior
        # search packages, so we surface the availability rows directly.
        is_plan = state.get("intent") == "plan"
        prior = state.get("packages", []) or []
        packages = prior if (is_plan and prior) else avail_packages
        activities = list(state.get("activities", []))
        activities.append(
            _activity(
                "delegation",
                "Workflow node: availability",
                details=(
                    f"Checked departures across {len(prior)} planned trips"
                    if is_plan and prior
                    else f"{len(avail_packages)} availability rows"
                ),
                execution_time_ms=elapsed,
                telemetry={
                    "category": "orchestration",
                    "component": "LangGraph → PackageAgent",
                    "status": "ok",
                    "fields": [
                        {"label": "node", "value": "availability"},
                        {"label": "rows", "value": str(len(avail_packages))},
                        {"label": "step", "value": "2 of 2" if is_plan else "1 of 1"},
                    ],
                },
            )
        )
        for sa in sub_activities:
            activities.append(_coerce_activity(sa))
        activities.append(self._checkpoint_activity("availability", elapsed))
        return {"packages": packages, "activities": activities}

    async def _node_memory_recall(self, state: WorkflowState) -> WorkflowState:
        """Worker node: recall prior context (delegates to the Phase 4 memory fn).

        Skips gracefully if no memory function is wired. Checkpoints after return,
        matching the search and availability nodes.
        """
        start = _utc_now()
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
        elapsed = int((_utc_now() - start).total_seconds() * 1000)
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
        activities.append(self._checkpoint_activity("memory_recall", elapsed))
        return {"packages": packages, "activities": activities}

    async def _node_synthesize(self, state: WorkflowState) -> WorkflowState:
        """Terminal node: compose the user-facing reply from accumulated state.

        All branches converge here before END. The response wording reflects the
        intent — notably the 'plan' path narrates the two-step, checkpointed run.
        """
        packages = state.get("packages", []) or []
        intent = state.get("intent", "search")
        # Keep the durability clause true to the checkpointer that is actually
        # wired: PostgresSaver persists to Aurora, MemorySaver keeps state in
        # process. The trace's checkpoint spans already carry the exact store;
        # this string must not over-claim Aurora when running in-process.
        checkpoint_clause = (
            "each step checkpointed to Aurora so the plan can pause and resume"
            if self._uses_postgres_saver
            else "each step checkpointed between nodes so the plan can pause "
            "and resume (in-process here; PostgresSaver persists to Aurora "
            "when the workflow runs inside the cluster VPC)"
        )
        if intent == "plan":
            response = (
                f"Planned the extension: searched the catalog, then checked "
                f"available duration options across {len(packages)} matching "
                f"trips — {checkpoint_clause}."
                if packages
                else "Ran the full plan graph (search → availability), but no "
                "trips matched — try broadening the destination."
            )
        elif intent == "availability" and packages:
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
        *,
        resume: bool = False,
    ) -> WorkflowState:
        thread_id = conversation_id or f"phase5-{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id}}
        initial: WorkflowState = {
            "query": query,
            "traveler_id": traveler_id,
            "conversation_id": thread_id,
            "activities": [],
        }
        await self._ensure_checkpoint_backend()

        resumed_nodes: List[str] = []
        if resume:
            prior = await self.graph.aget_state(config)
            resumed_nodes = list(prior.next)
            if not resumed_nodes:
                raise RuntimeError(
                    f"No interrupted workflow can be resumed for thread_id={thread_id}."
                )
            result = await self.graph.ainvoke(None, config=config)
        else:
            result = await self.graph.ainvoke(initial, config=config)

        result = dict(result or {})
        current = await self.graph.aget_state(config)
        activities = list(result.get("activities", []) or [])

        if current.next:
            next_nodes = ", ".join(current.next)
            activities.append(
                _activity(
                    "result",
                    "Workflow paused at checkpoint",
                    details=(
                        f"thread_id={thread_id} · next={next_nodes} · "
                        f"store={self.checkpointer_kind}"
                    ),
                    telemetry={
                        "category": "orchestration",
                        "component": "LangGraph durable execution",
                        "status": "held",
                        "fields": [
                            {"label": "thread_id", "value": thread_id},
                            {"label": "next_node", "value": next_nodes},
                            {
                                "label": "checkpointer",
                                "value": self.checkpointer_kind,
                            },
                            {
                                "label": "durability",
                                "value": (
                                    "Aurora"
                                    if self._uses_postgres_saver
                                    else "in-process only"
                                ),
                            },
                        ],
                    },
                )
            )
            result["response"] = (
                f"Workflow paused after a committed checkpoint. Resume thread "
                f"{thread_id} to continue with {next_nodes}."
            )
            result["workflow_status"] = "paused"
        else:
            result["workflow_status"] = "resumed" if resume else "complete"
            if resume:
                activities.append(
                    _activity(
                        "result",
                        "Workflow resumed from checkpoint",
                        details=(
                            f"thread_id={thread_id} · resumed={', '.join(resumed_nodes)} "
                            f"· store={self.checkpointer_kind}"
                        ),
                        telemetry={
                            "category": "orchestration",
                            "component": "LangGraph durable execution",
                            "status": "ok",
                            "fields": [
                                {"label": "thread_id", "value": thread_id},
                                {
                                    "label": "resumed_nodes",
                                    "value": ", ".join(resumed_nodes),
                                },
                                {
                                    "label": "checkpointer",
                                    "value": self.checkpointer_kind,
                                },
                                {
                                    "label": "durability",
                                    "value": (
                                        "Aurora"
                                        if self._uses_postgres_saver
                                        else "in-process only"
                                    ),
                                },
                            ],
                        },
                    )
                )

        result["activities"] = activities
        result["conversation_id"] = thread_id
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
