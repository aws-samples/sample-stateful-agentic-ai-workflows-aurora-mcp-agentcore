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
import hashlib
import uuid
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypedDict
from urllib.parse import quote

from backend.agents.orchestration_05.hold_intent import prepare_hold_node
from backend.agents.orchestration_05.governed_hold import (
    HOLD_TOOL,
    hold_arguments,
    place_governed_hold,
)
# Shared with Phase 4 but deliberately outside its package: this module is
# imported at startup, and importing the Phase 4 concierge stack there breaks
# the App Runner deployment (see docs/AGENTCORE_LEARNINGS.md).
from backend.agents.budget import budget_ceiling_from_facts
from backend.db.journey_store import ExecutionLeaseLostError, ScopedDb, ensure_journey
from backend.agents.orchestration_05.packages import (
    first_available_duration,
    package_to_dict,
    top_ranked_package,
)

logger = logging.getLogger(__name__)

# LangGraph imports are kept module-local so the rest of the backend doesn't
# fail to import when langgraph isn't installed (e.g. in Phase 1-4 unit
# tests).  The Phase 5 router only imports this module when a request hits
# /api/chat with phase=5.
from langchain_core.runnables import RunnableConfig
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
WORKER_INSTANCE_ID = f"worker-{uuid.uuid4().hex[:8]}"


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


def _first_env(*names: str, default: str = "") -> str:
    """Return the first environment variable that holds a non-blank value.

    Always returns a string. Callers build the checkpoint DSN from several
    fallback chains; an unset chain must degrade to the MemorySaver path
    rather than raise, so this never returns ``None``.
    """
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return default


def _auto_checkpoint_dsn_enabled() -> bool:
    """Allow derived DSNs only when explicitly enabled outside development."""
    configured = os.getenv("LANGGRAPH_AUTO_CHECKPOINT_DSN")
    if configured is not None:
        return _truthy_env("LANGGRAPH_AUTO_CHECKPOINT_DSN")
    return os.getenv("ENVIRONMENT", "development").strip().lower() == "development"


def _resolve_checkpoint_dsn() -> Optional[str]:
    """Resolve the DSN used by LangGraph PostgresSaver.

    Priority:
      1. Explicit LANGGRAPH_CHECKPOINT_DSN.
      2. Auto-built DSN from environment-injected checkpoint credentials.

    The application never reads a Secrets Manager secret directly. Inject
    LANGGRAPH_CHECKPOINT_DSN, or inject the discrete credentials outside the
    process before startup. Auto-building is development-only unless
    LANGGRAPH_AUTO_CHECKPOINT_DSN is explicitly enabled.
    """
    explicit = os.getenv("LANGGRAPH_CHECKPOINT_DSN")
    if explicit:
        return explicit
    if not _auto_checkpoint_dsn_enabled():
        return None

    username = _first_env("LANGGRAPH_CHECKPOINT_USERNAME", "AURORA_USERNAME")
    password = _first_env("LANGGRAPH_CHECKPOINT_PASSWORD", "AURORA_PASSWORD")
    host = _first_env(
        "LANGGRAPH_CHECKPOINT_HOST",
        "AURORA_HOST",
        "AURORA_CLUSTER_ENDPOINT",
    )
    port = _first_env("LANGGRAPH_CHECKPOINT_PORT", "AURORA_PORT", default="5432")
    database = _first_env("LANGGRAPH_CHECKPOINT_DATABASE", "AURORA_DATABASE")

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


def _data_api_checkpoints_enabled() -> bool:
    """Whether to checkpoint through the RDS Data API.

    Opt-in rather than inferred. ``RDSDataClient()`` validates neither its
    ARNs nor its credentials at construction, so probing for a client would
    adopt the Data API saver anywhere a dotenv is loaded, unit tests included.
    """
    return _truthy_env("LANGGRAPH_CHECKPOINT_DATA_API", "false")


async def _probe_data_api_checkpoints(saver: Any) -> None:
    """Confirm the checkpoint tables answer before the saver is adopted.

    Catches expired credentials and an unapplied migration 007 here, where
    the backend can still fall back, rather than mid-turn on the first write.

    Args:
        saver: The candidate ``AuroraDataApiSaver``.

    Raises:
        Exception: Whatever the Data API raises when the probe fails.
    """
    await saver.client.execute("SELECT 1 FROM checkpoints LIMIT 1", ())


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
            error: Optional[str] = None
            if _data_api_checkpoints_enabled():
                try:
                    from backend.db.aurora_dataapi_saver import AuroraDataApiSaver
                    from backend.db.rds_data_client import get_rds_data_client

                    saver = AuroraDataApiSaver(get_rds_data_client())
                    await _probe_data_api_checkpoints(saver)
                except Exception as exc:  # noqa: BLE001 - fall through to the guard
                    error = f"Data API checkpointing unavailable: {exc}"
                    logger.warning("%s Falling back to MemorySaver.", error)
                else:
                    _checkpoint_backend = CheckpointBackend(
                        saver=saver,
                        kind="AuroraDataApiSaver",
                        durable=True,
                    )
                    return _checkpoint_backend

            if _checkpoint_required():
                raise RuntimeError(
                    "Durable workflow checkpoints are required, but no "
                    "LANGGRAPH_CHECKPOINT_DSN or checkpoint credentials resolved."
                    + (f" {error}" if error else "")
                )
            _checkpoint_backend = CheckpointBackend(
                saver=MemorySaver(),
                kind="MemorySaver (in-process)",
                durable=False,
                error=error,
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
    auto_configured = _auto_checkpoint_dsn_enabled() and bool(
        (
            os.getenv("LANGGRAPH_CHECKPOINT_HOST")
            or os.getenv("AURORA_HOST")
            or os.getenv("AURORA_CLUSTER_ENDPOINT")
        )
        and (
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
    worker_instance_id: str
    intent: str  # 'search' | 'availability' | 'memory_recall'
    packages: List[Any]
    response: str
    activities: List[Dict[str, Any]]
    availability_checks: int
    travelers_count: int
    resumed_from_checkpoint: str
    workflow_status: str
    resumed_after_restart: bool
    hold_id: str
    hold_expires_at: str
    hold_created_at: str
    hold_observed_at: str
    hold_status: str
    hold_package: str
    hold_duration: str
    hold_seats_remaining: int
    hold_intent: Dict[str, Any]
    execution_id: str
    journey_id: str


# How long a courtesy hold survives. Short enough that the room can watch the
# clock move across a worker restart, long enough not to expire mid-demo.
HOLD_MINUTES = int(os.getenv("MERIDIAN_HOLD_MINUTES", "15"))


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


def _is_recovery_request(query: str) -> bool:
    """Does this query justify committing inventory?

    Only a disruption does. `_classify_intent` labels anything that names both
    a trip and a date as a "plan", which correctly includes a documented
    availability question like "Which trip lengths are still available for
    Amalfi Coast Villa Week?". Routing on intent alone would let a read reserve
    seats, so the hold node needs a stronger signal: the traveler's trip broke
    and we are rebuilding it.
    """
    q = (query or "").lower()
    disrupted = any(
        marker in q
        for marker in ("cancelled", "canceled", "disrupt", "stranded", "rebook", "missed")
    )
    reworking = any(
        marker in q for marker in ("rework", "rebuild", "replan", "re-plan", "recover")
    )
    return disrupted and reworking


def _classify_intent(query: str) -> str:
    q = query.lower()
    # Explicit recall language wins over incidental planning words. For
    # example, "Recall my October Tokyo plan..." is asking for memory even
    # though "plan" also appears as a noun.
    memory_signals = (
        "recall ",
        "remember",
        "last time",
        "previous",
        "we discussed",
        "you said",
    )
    if any(s in q for s in memory_signals):
        return "memory_recall"

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
    return "search"


def _hold_key(thread_id: str, package_id: str, duration: str) -> str:
    """A stable id for one intended hold.

    The hold used to take a fresh uuid4 on every invocation, so replaying the
    node after a crash asked the database for a *second* hold rather than the
    same one again. LangGraph guarantees the node runs at least once, not
    exactly once - the external effect has to carry its own identity for that.

    Deriving the key from the thread and what is being held means a retry
    presents the same booking_id, and the primary key on ``bookings`` rejects
    the duplicate instead of reserving inventory twice. A genuinely different
    hold - another package, another duration - still gets its own key.
    """
    digest = hashlib.sha256(f"{thread_id}|{package_id}|{duration}".encode()).hexdigest()
    return f"hold_{digest[:24]}"


class WorkflowAuthorizationError(PermissionError):
    """A caller tried to reach a workflow thread that is not theirs.

    Distinct from a generic failure so the API can answer 403 rather than 500:
    the request was understood and refused, not broken.
    """


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
        self.checkpointer_durable = False
        self.interrupt_after = ""
        self.graph = self._build_graph()

    @property
    def _uses_durable_saver(self) -> bool:
        """Whether the configured checkpointer persists outside this process.

        Read from the backend's capability flag rather than its name. A name
        test silently mislabels any backend added later.
        """
        return self.checkpointer_durable

    def _checkpoint_activity(self, node: str, elapsed_ms: int) -> Dict[str, Any]:
        """Trace the actual configured checkpointer, not just the ideal one."""
        if self._uses_durable_saver:
            return _activity(
                "tool_call",
                f"Checkpoint · {self.checkpointer_kind}.put",
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
                        {"label": "checkpoint_durable", "value": "true"},
                        {"label": "checkpoint_store", "value": "checkpoints"},
                        {
                            "label": "checkpoint_tables",
                            "value": ", ".join(POSTGRES_CHECKPOINT_TABLES),
                        },
                        {"label": "durability", "value": self.checkpointer_kind},
                    ],
                },
            )

        return _activity(
            "tool_call",
            "Checkpoint · MemorySaver.put",
            details=(
                f"Workflow state kept in-process after {node} node ({elapsed_ms}ms). "
                "Set LANGGRAPH_CHECKPOINT_DSN, or LANGGRAPH_CHECKPOINT_DATA_API "
                "to checkpoint over the Data API, for Aurora durability."
            ),
            telemetry={
                "category": "memory_short",
                "component": "LangGraph MemorySaver (in-process)",
                "status": "ok",
                "fields": [
                    {"label": "checkpointer", "value": self.checkpointer_kind},
                    {"label": "checkpoint_durable", "value": "false"},
                    {"label": "checkpoint_store", "value": "process memory"},
                    {"label": "durability", "value": "ephemeral"},
                ],
            },
        )

    # ------------------------------------------------------------- checkpointer

    async def _ensure_checkpoint_backend(self) -> CheckpointBackend:
        backend = await initialize_checkpoint_backend()
        if self.checkpointer is not backend.saver:
            self.checkpointer = backend.saver
            self.checkpointer_kind = backend.kind
            self.checkpointer_durable = backend.durable
            self.graph = self._build_graph()
        return backend

    async def _preflight_checkpoint_connection(
        self,
        backend: CheckpointBackend,
    ) -> None:
        """Fail quickly when the local Aurora tunnel or checkpoint pool is down."""
        if backend.pool is None:
            return

        timeout = float(
            max(
                1,
                _int_env("LANGGRAPH_CHECKPOINT_PREFLIGHT_TIMEOUT", 3),
            )
        )
        try:
            async with backend.pool.connection(timeout=timeout) as connection:
                await connection.execute("SELECT 1")
        except Exception as exc:
            raise RuntimeError(
                "Aurora checkpoint connection unavailable. Start "
                "scripts/start_checkpoint_tunnel.sh and restart the backend."
            ) from exc

    # ------------------------------------------------------------------ graph

    def _build_graph(self):
        """Compile the LangGraph StateGraph with conditional routing + checkpointer."""
        builder = StateGraph(WorkflowState)
        builder.add_node("classify", self._node_classify)
        builder.add_node("search", self._node_search)
        builder.add_node("availability", self._node_availability)
        builder.add_node("memory_recall", self._node_memory_recall)
        builder.add_node("prepare_hold", prepare_hold_node)
        builder.add_node("hold", self._node_hold)
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
        # Only the 'plan' path commits inventory. A bare availability lookup
        # is a read, and must not reserve seats as a side effect.
        builder.add_conditional_edges(
            "availability",
            lambda state: (
                "prepare_hold"
                if _is_recovery_request(state.get("query", ""))
                else "synthesize"
            ),
            {"prepare_hold": "prepare_hold", "synthesize": "synthesize"},
        )
        # The intent is allocated and checkpointed in its own node, so a
        # worker that dies inside the hold resumes with the same identity
        # rather than allocating a second one.
        builder.add_edge("prepare_hold", "hold")
        builder.add_edge("hold", "synthesize")
        builder.add_edge("memory_recall", "synthesize")
        builder.add_edge("synthesize", END)
        interrupt_after = self.interrupt_after
        allowed_interrupts = {
            "classify",
            "search",
            "availability",
            "prepare_hold",
            "hold",
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

    @staticmethod
    def _interrupt_after_for_query(query: str) -> str:
        configured = os.getenv("LANGGRAPH_DEMO_INTERRUPT_AFTER", "").strip()
        if configured:
            return configured

        # The canonical Stateful Recovery finale deliberately pauses after search so the
        # audience sees a committed checkpoint before availability fan-out.
        # Other Phase 5 branches continue in one request.
        normalized = query.lower()
        is_recovery_finale = (
            ("canceled" in normalized or "cancelled" in normalized)
            and "flight" in normalized
            and "then check" in normalized
            and "best three" in normalized
        )
        return "search" if is_recovery_finale else ""

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
        raw_packages, search_activities = await self.search_fn(
            state["query"],
            limit=5,
        )
        packages = [package_to_dict(package) for package in raw_packages]
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
        """Worker node: check duration inventory (delegates to Package fn).

        On the 'plan' path this runs AFTER search (step 2 of 2) and layers
        availability onto the prior trip results; for a standalone 'availability'
        intent it surfaces the availability rows directly. Checkpoints after return.
        """
        start = _utc_now()
        is_plan = state.get("intent") == "plan"
        prior = [
            package_to_dict(package)
            for package in (state.get("packages", []) or [])
        ]

        if is_plan and prior:
            targets = [
                package
                for package in prior[:3]
                if package.get("product_id") or package.get("package_id")
            ]

            async def check_target(
                package: Dict[str, Any],
            ) -> tuple[List[Any], List[Any], str]:
                package_id = str(
                    package.get("product_id")
                    or package.get("package_id")
                    or ""
                )
                return await self.availability_fn(
                    state["query"],
                    package_id=package_id,
                )

            target_results = (
                await asyncio.gather(
                    *(check_target(package) for package in targets)
                )
                if targets
                else []
            )
            availability_by_id: Dict[str, Dict[str, Any]] = {}
            sub_activities: List[Any] = []
            for avail_packages, target_activities, _msg in target_results:
                sub_activities.extend(target_activities)
                for package in avail_packages:
                    normalized = package_to_dict(package)
                    package_id = str(
                        normalized.get("product_id")
                        or normalized.get("package_id")
                        or ""
                    )
                    if package_id:
                        availability_by_id[package_id] = normalized

            packages = []
            for package in prior:
                package_id = str(
                    package.get("product_id")
                    or package.get("package_id")
                    or ""
                )
                availability = availability_by_id.get(package_id)
                packages.append(
                    {
                        **package,
                        **(
                            {
                                "available_sizes": availability.get(
                                    "available_sizes"
                                ),
                                "availability": availability.get(
                                    "availability"
                                ),
                            }
                            if availability
                            else {}
                        ),
                    }
                )
            availability_checks = len(target_results)
            availability_rows = len(availability_by_id)
        else:
            raw_available, sub_activities, _msg = await self.availability_fn(
                state["query"]
            )
            packages = [
                package_to_dict(package) for package in raw_available
            ]
            availability_checks = 1 if packages else 0
            availability_rows = len(packages)

        elapsed = int((_utc_now() - start).total_seconds() * 1000)
        activities = list(state.get("activities", []))
        activities.append(
            _activity(
                "delegation",
                (
                    "Workflow node: availability fan-out"
                    if is_plan and prior
                    else "Workflow node: availability"
                ),
                details=(
                    f"Checked duration inventory for {availability_checks} "
                    "top-ranked trips in parallel"
                    if is_plan and prior
                    else f"{availability_rows} availability rows"
                ),
                execution_time_ms=elapsed,
                telemetry={
                    "category": "orchestration",
                    "component": (
                        "LangGraph → PackageAgent fan-out"
                        if is_plan and prior
                        else "LangGraph → PackageAgent"
                    ),
                    "status": "ok",
                    "fields": [
                        {"label": "node", "value": "availability"},
                        {
                            "label": "checks",
                            "value": str(availability_checks),
                        },
                        {"label": "rows", "value": str(availability_rows)},
                        {"label": "step", "value": "2 of 2" if is_plan else "1 of 1"},
                    ],
                },
            )
        )
        for sa in sub_activities:
            activities.append(_coerce_activity(sa))
        activities.append(self._checkpoint_activity("availability", elapsed))
        return {
            "packages": packages,
            "activities": activities,
            "availability_checks": availability_checks,
        }

    async def _node_hold(self, state: WorkflowState, config: RunnableConfig = None) -> WorkflowState:
        """Worker node: place a courtesy hold on the top-ranked option through the gateway.

        This is what makes the durability claim concrete. The previous nodes
        checkpoint *workflow position*; this one commits a row with real
        consequences: inventory is decremented and the hold carries a TTL. A
        worker can die between here and ``synthesize`` and the hold is still
        there on resume, with time remaining, because it lives in Aurora rather
        than in the process.

        The write goes through AgentCore Gateway, so the Cedar policy that
        governs the Phase 4 agent decides this hold too, and the ``MeridianHolds``
        Lambda runs ``create_courtesy_hold`` under its own traveler grant. The
        worker verifies its lease here and passes its execution id so the Lambda
        verifies it again inside the write transaction; the checkpointed request
        and booking ids make a replacement worker replay the same booking with
        the original expiry.
        """
        start = _utc_now()
        activities = list(state.get("activities", []))
        packages = state.get("packages", []) or []
        traveler_id = state.get("traveler_id") or ""

        target = top_ranked_package(packages)
        if not target or not traveler_id:
            activities.append(
                _activity(
                    "reasoning",
                    "Workflow node: hold skipped",
                    details="No ranked option to hold for this traveler.",
                )
            )
            elapsed = int((_utc_now() - start).total_seconds() * 1000)
            activities.append(self._checkpoint_activity("hold", elapsed))
            return {"activities": activities}

        # Terms come from the checkpointed intent, so the hold that runs is the
        # hold that was fingerprinted. Falling back to deriving them again
        # keeps a direct call to this node working.
        intent = state.get("hold_intent") or prepare_hold_node(state).get(
            "hold_intent", {}
        )
        package_id = str(
            intent.get("package_id")
            or target.get("product_id")
            or target.get("package_id")
        )
        duration = str(intent.get("duration") or first_available_duration(target))
        quantity = int(intent.get("quantity") or state.get("travelers_count") or 1)
        unit_price = float(intent.get("unit_price") or target.get("price") or 0)
        # New business intents own a booking ID. Older checkpoints retain the
        # original thread/package key so their replay still finds the same row.
        hold_id = str(intent.get("booking_id") or _hold_key(
            state.get("conversation_id") or "", package_id, duration
        ))
        thread_id = str(state.get("conversation_id") or "")
        hold_request_id = str(intent.get("hold_request_id") or hold_id)
        execution_id = (config or {}).get("configurable", {}).get("execution_id")
        terms = {
            "package_id": package_id,
            "duration": duration,
            "quantity": quantity,
            "unit_price": unit_price,
            "hold_request_id": hold_request_id,
            "booking_id": hold_id,
        }

        try:
            journey_id, ceiling = await self._prepare_governed_hold(
                traveler_id, thread_id, execution_id, quantity, state
            )
            arguments = hold_arguments(
                terms,
                traveler_id=traveler_id,
                journey_ref=thread_id,
                budget_ceiling_cents=ceiling,
                hold_minutes=HOLD_MINUTES,
                execution_id=execution_id,
            )
            outcome = await asyncio.to_thread(place_governed_hold, self._gateway_call, arguments)
        except ExecutionLeaseLostError:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed gateway call leaves the plan unheld
            logger.warning("courtesy hold not placed: %s", exc)
            activities.append(self._hold_not_placed(str(exc)[:120], denied=False, raw=str(exc)))
            elapsed = int((_utc_now() - start).total_seconds() * 1000)
            activities.append(self._checkpoint_activity("hold", elapsed))
            return {"activities": activities}

        if not outcome.placed:
            denied = outcome.policy_decision == "deny"
            if denied:
                reason = "Cedar policy refused the hold"
            elif outcome.error == "insufficient_inventory":
                reason = "inventory changed"
            else:
                reason = outcome.error or "the gateway returned no hold"
            logger.warning("courtesy hold not placed: %s", outcome.raw_error or reason)
            activities.append(self._hold_not_placed(reason, denied=denied, raw=outcome.raw_error))
            elapsed = int((_utc_now() - start).total_seconds() * 1000)
            activities.append(self._checkpoint_activity("hold", elapsed))
            return {"activities": activities}

        hold = outcome.hold or {}
        hold_id = str(hold.get("bookingId") or hold_id)
        journey_id = str(hold.get("journeyId") or journey_id)
        replayed = bool(hold.get("replayed"))
        # A replay returns the existing booking without re-counting inventory,
        # so its seat columns are null by design.
        remaining = hold.get("seatsRemaining")
        expires_at = str(hold.get("expiresAt"))
        created_at = str(hold.get("createdAt"))
        observed_at = str(hold.get("observedAt"))
        hold_status = str(hold.get("status"))
        governance = outcome.governance or {}
        elapsed = int((_utc_now() - start).total_seconds() * 1000)
        activities.append(
            _activity(
                "database",
                "Workflow node: hold",
                details=(
                    (
                        f"Replayed the hold already placed for this request on "
                        f"{package_id} ({duration})"
                    )
                    if replayed
                    else (
                        f"Held {quantity} x {duration} on {package_id} until "
                        f"{expires_at} · {remaining} package spots left"
                    )
                ),
                sql_query=(
                    "-- AgentCore Gateway tools/call " + HOLD_TOOL + "\n"
                    "-- Cedar: meridian_hold_governance (ENFORCE) decides on the arguments\n"
                    "-- MeridianHolds Lambda: traveler grant, RLS scope, SET LOCAL ROLE meridian_app,\n"
                    "--   lease check for this execution, then\n"
                    "SELECT booking_id, status, replayed,\n"
                    "       seats_available, seats_reserved, seats_remaining\n"
                    "FROM create_courtesy_hold($1 .. $11);\n"
                    "-- request identity claimed first, then advisory lock and\n"
                    "-- capacity check, inside the RLS scope"
                ),
                execution_time_ms=elapsed,
                telemetry={
                    "category": "gateway",
                    "component": "AgentCore Gateway · MeridianHolds Lambda · Aurora",
                    "status": "ok",
                    "fields": [
                        {"label": "gateway_tool", "value": HOLD_TOOL, "mono": True},
                        {"label": "cedar_decision", "value": "allow"},
                        {"label": "workload", "value": str(governance.get("subject") or ""), "mono": True},
                        {"label": "traveler_grant", "value": str(governance.get("decision") or "")},
                        {"label": "hold_id", "value": hold_id, "mono": True},
                        {
                            "label": "hold_request_id",
                            "value": hold_request_id,
                            "mono": True,
                        },
                        {"label": "journey_id", "value": journey_id, "mono": True},
                        {"label": "replayed", "value": "yes" if replayed else "no"},
                        {"label": "package", "value": package_id},
                        {"label": "duration", "value": duration},
                        {"label": "seats_held", "value": str(quantity)},
                        {"label": "seats_remaining", "value": str(remaining) if remaining is not None else ""},
                        {"label": "expires_at", "value": expires_at},
                        {"label": "hold_created_at", "value": created_at},
                        {"label": "hold_observed_at", "value": observed_at},
                        {"label": "hold_status", "value": hold_status},
                    ],
                },
            )
        )
        activities.append(self._checkpoint_activity("hold", elapsed))
        return {
            "activities": activities,
            "journey_id": journey_id,
            "hold_id": hold_id,
            "hold_expires_at": expires_at,
            "hold_created_at": created_at,
            "hold_observed_at": observed_at,
            "hold_status": hold_status,
            "hold_package": package_id,
            # Part of the idempotency key, so compensation can recompute it.
            "hold_duration": duration,
            "hold_seats_remaining": remaining,
        }

    async def _prepare_governed_hold(
        self,
        traveler_id: str,
        thread_id: str,
        execution_id: Optional[str],
        quantity: int,
        state: WorkflowState,
    ) -> tuple[str, int]:
        """Verify the worker lease, bind the journey, and size the budget ceiling.

        Two short RLS units, both committed before the gateway is called: the
        booking-agent unit checks the lease and binds the thread to its journey;
        the concierge unit reads every saved preference so the ceiling the Cedar
        policy compares against comes from Alex's own facts.
        """
        from backend.agentcore.identity import get_agentcore_identity
        from backend.db.rds_data_client import get_rds_data_client
        from backend.memory.store import get_memory_store

        db = get_rds_data_client()
        authorization = get_agentcore_identity().authorization_context()
        async with db.scoped_session(
            traveler_id=traveler_id, agent_type="booking_agent", authorization=authorization
        ) as transaction_id:
            scoped = ScopedDb(db, transaction_id)
            if execution_id:
                live = await scoped.execute(
                    "SELECT execution_id FROM journey_executions WHERE execution_id = %s "
                    "AND status = 'running' AND lease_expires_at > CURRENT_TIMESTAMP FOR UPDATE",
                    (execution_id,),
                )
                if not live:
                    raise ExecutionLeaseLostError("Execution lease expired before the hold")
            journey_id = state.get("journey_id") or await ensure_journey(
                scoped, traveler_id, thread_id, self.checkpointer_kind
            )
        async with db.scoped_session(
            traveler_id=traveler_id, agent_type="concierge_agent", authorization=authorization
        ) as transaction_id:
            facts = await get_memory_store().recall_preferences(
                traveler_id, limit=50, transaction_id=transaction_id
            )
        return str(journey_id), budget_ceiling_from_facts(facts, quantity)

    @staticmethod
    def _gateway_call(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        from backend.agentcore.gateway import get_agentcore_gateway

        return get_agentcore_gateway().call_tool(name, arguments)

    @staticmethod
    def _hold_not_placed(reason: str, *, denied: bool, raw: Optional[str]) -> Dict[str, Any]:
        return _activity(
            "security" if denied else "error",
            "Workflow node: hold not placed",
            details=f"No package inventory was held ({reason}). The plan continues unheld.",
            telemetry={
                "category": "security" if denied else "tool",
                "component": "Bedrock AgentCore Policy" if denied else "Bedrock AgentCore Gateway",
                "status": "denied" if denied else "error",
                "fields": [
                    {"label": "gateway_tool", "value": HOLD_TOOL, "mono": True},
                    {"label": "gateway_error", "value": raw or "", "mono": True},
                ],
            },
        )

    async def _release_hold(
        self, state: WorkflowState, *, expected_hold_id: Optional[str] = None
    ) -> None:
        """Compensating action: give the seats back.

        A durable workflow that commits inventory needs an answer for "step 3
        failed after step 2 committed". Releasing marks the booking so the
        capacity count in ``create_courtesy_hold`` stops counting it.

        ``expected_hold_id`` scopes the compensation to the run that is
        failing. The persisted state is the *latest* state for the thread, so
        without this a failed run could read a hold from an earlier successful
        run and release it - undoing work that never failed.
        """
        hold_id = state.get("hold_id")
        traveler_id = state.get("traveler_id")
        if not hold_id or not traveler_id:
            return
        if expected_hold_id is not None and hold_id != expected_hold_id:
            logger.warning(
                "not releasing hold %s: it belongs to a different run than the one that failed",
                hold_id,
            )
            return
        try:
            from backend.agentcore.identity import get_agentcore_identity
            from backend.db.rds_data_client import get_rds_data_client

            db = get_rds_data_client()
            async with db.scoped_session(
                traveler_id=traveler_id,
                agent_type="booking_agent",
                authorization=get_agentcore_identity().authorization_context(),
            ) as transaction_id:
                await db.execute(
                    "UPDATE bookings SET status = 'released' "
                    "WHERE booking_id = %s AND traveler_id = %s AND status = 'held'",
                    (hold_id, traveler_id),
                    transaction_id=transaction_id,
                )
            logger.info("released courtesy hold %s", hold_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not release hold %s: %s", hold_id, exc)

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
        availability_checks = state.get("availability_checks", 0)
        # Keep the durability clause true to the checkpointer that is actually
        # wired: PostgresSaver persists to Aurora, MemorySaver keeps state in
        # process. The trace's checkpoint spans already carry the exact store;
        # this string must not over-claim Aurora when running in-process.
        checkpoint_clause = (
            "each step checkpointed to Aurora so the plan can pause and resume"
            if self._uses_durable_saver
            else "each step checkpointed between nodes so the plan can pause "
            "and resume (in-process here; PostgresSaver persists to Aurora "
            "when the workflow runs inside the cluster VPC)"
        )
        if intent == "plan":
            response = (
                f"Planned the extension: searched the catalog, then checked "
                f"live duration options for the top {availability_checks} "
                f"choices — {checkpoint_clause}."
                if packages
                else "Ran the full plan graph (search → availability), but no "
                "trips matched — try broadening the destination."
            )
        elif intent == "availability" and packages:
            response = (
                f"Found duration inventory for {len(packages)} matching trips."
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
                        {
                            "label": "availability_checks",
                            "value": str(availability_checks),
                        },
                    ],
                },
            )
        )
        return {"response": response, "activities": activities}

    # ------------------------------------------------------------------- run

    @staticmethod
    def _authorize_thread(prior, thread_id: str, traveler_id: str) -> None:
        """Refuse to continue a workflow thread belonging to another traveler.

        Re-checked on every resume rather than once at creation, so a traveler
        whose access was revoked between the interrupt and the resume is denied
        by the same path.
        """
        persisted = str((prior.values or {}).get("traveler_id") or "")
        if not persisted:
            # An unknown thread is not an implicitly public one.
            raise WorkflowAuthorizationError(
                f"Thread {thread_id} has no recorded owner and cannot be resumed."
            )
        if persisted != traveler_id:
            raise WorkflowAuthorizationError(
                f"Thread {thread_id} belongs to another traveler."
            )

    async def run(
        self,
        query: str,
        traveler_id: str,
        conversation_id: str,
        *,
        resume: bool = False,
        travelers_count: int = 1,
        journey_id: Optional[str] = None,
        execution_id: Optional[str] = None,
    ) -> WorkflowState:
        if not isinstance(travelers_count, int) or isinstance(travelers_count, bool) or not 1 <= travelers_count <= 20:
            raise ValueError("travelers_count must be an integer between 1 and 20")
        thread_id = conversation_id or f"phase5-{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id, "execution_id": execution_id}}
        self.interrupt_after = self._interrupt_after_for_query(query)
        initial: WorkflowState = {
            "query": query,
            "journey_id": journey_id,
            "travelers_count": travelers_count,
            "traveler_id": traveler_id,
            "conversation_id": thread_id,
            "worker_instance_id": WORKER_INSTANCE_ID,
            "activities": [],
            # Explicitly per-run. Without these, a fresh run over an existing
            # conversation inherited the last run's hold_id from the persisted
            # state, and a later failure released a hold that had nothing to do
            # with it.
            "resumed_from_checkpoint": None,
            "resumed_after_restart": False,
            "workflow_status": None,
            "response": "",
            "hold_intent": None,
            "hold_duration": None,
            "packages": [],
            "availability_checks": 0,
            "hold_id": None,
            "hold_expires_at": None,
            "hold_created_at": None,
            "hold_observed_at": None,
            "hold_status": None,
            "hold_package": None,
            "hold_seats_remaining": None,
        }
        checkpoint_backend = await self._ensure_checkpoint_backend()
        self.graph = self._build_graph()
        await self._preflight_checkpoint_connection(checkpoint_backend)

        resumed_nodes: List[str] = []
        resumed_after_restart = False
        # A new turn can target an existing thread too. Check its owner before
        # either invocation mode can change the persisted state.
        prior = await self.graph.aget_state(config)
        if resume or prior.values:
            self._authorize_thread(prior, thread_id, traveler_id)

        if resume:
            resumed_nodes = list(prior.next)
            if not resumed_nodes:
                raise RuntimeError(
                    f"No interrupted workflow can be resumed for thread_id={thread_id}."
                )
            prior_worker_instance = str(
                (prior.values or {}).get("worker_instance_id") or ""
            )
            resumed_after_restart = bool(
                prior_worker_instance
                and prior_worker_instance != WORKER_INSTANCE_ID
            )
        # If the graph fails after the hold node committed inventory, give the
        # seats back. This is the compensating action a durable workflow owes
        # for anything it commits mid-flight.
        try:
            if resume:
                result = await self.graph.ainvoke(
                    None, config=config, durability="sync"
                )
            else:
                result = await self.graph.ainvoke(
                    initial, config=config, durability="sync"
                )
        except ExecutionLeaseLostError:
            # The replacement owns recovery now; this worker cannot compensate.
            raise
        except Exception:
            failed_state = await self.graph.aget_state(config)
            values = dict(failed_state.values or {})
            # Release only this intent's booking. Older saved intents use the
            # legacy thread/package/duration key for replay compatibility.
            package = values.get("hold_package")
            duration = values.get("hold_duration")
            intent = values.get("hold_intent") or {}
            expected = intent.get("booking_id") or (
                _hold_key(thread_id, str(package), str(duration))
                if package and duration else None
            )
            await self._release_hold(values, expected_hold_id=expected)
            raise

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
                            {"label": "checkpoint_durable", "value": str(self._uses_durable_saver).lower()},
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
                                    if self._uses_durable_saver
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
            result["resumed_after_restart"] = resumed_after_restart
            if resume:
                activities.append(
                    _activity(
                        "result",
                        "Workflow resumed from checkpoint",
                        details=(
                            f"thread_id={thread_id} · resumed={', '.join(resumed_nodes)} "
                            f"· store={self.checkpointer_kind} · "
                            f"worker_restart={'observed' if resumed_after_restart else 'not observed'}"
                        ),
                        telemetry={
                            "category": "orchestration",
                            "component": "LangGraph durable execution",
                            "status": "ok",
                            "fields": [
                                {"label": "checkpoint_durable", "value": str(self._uses_durable_saver).lower()},
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
                                        if self._uses_durable_saver
                                        else "in-process only"
                                    ),
                                },
                                {
                                    "label": "worker_restart",
                                    "value": (
                                        "observed"
                                        if resumed_after_restart
                                        else "not observed"
                                    ),
                                },
                            ],
                        },
                    )
                )

        result["activities"] = activities
        result["conversation_id"] = thread_id
        if not current.next:
            result["resumed_from_checkpoint"] = (
                prior.config["configurable"].get("checkpoint_id") if resume else None
            )
            result["worker_instance_id"] = WORKER_INSTANCE_ID
            result["execution_id"] = execution_id
            await self.graph.aupdate_state(config, result, as_node="synthesize")
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
