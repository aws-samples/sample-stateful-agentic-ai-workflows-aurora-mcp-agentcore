"""
Production mode: the managed AgentCore Runtime owns the tool loop; Aurora RLS bounds the turn.

Presenter walkthrough, AgentCore on one turn
--------------------------------------------
  1. AgentCore Identity  : workload / IAM envelope (security span)
  2. Traveler grant      : traveler_identity_bindings authorizes the workload for Alex
  3. Aurora RLS read     : one short transaction for profile, preferences and recall
  4. AgentCore Runtime   : the agent discovers its tools from AgentCore Gateway over MCP,
                           keeps its conversation in AgentCore Memory, searches Aurora,
                           and places a courtesy hold only when the traveler confirmed it.
                           Every tool call is checked by the gateway's Cedar policies.
  5. Aurora RLS write    : a separate short transaction persists the turn and audits it

This module is **live**: imported by `chat.py` for `production_search()` and
`production_hold()`.

AWS docs (AgentCore):
  - What is AgentCore?
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html
  - Runtime:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime.html
  - Gateway (managed MCP):
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html
  - Policy (Cedar):
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.html
  - Memory:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html
  - Identity:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html

AWS docs (Aurora):
  - RDS Data API transactions (RLS ``scoped_session``):
    https://docs.aws.amazon.com/rdsdataservice/latest/APIReference/API_BeginTransaction.html
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.agentcore.cli_config import require_agentcore_platform
from backend.agentcore.identity import get_agentcore_identity
from backend.agentcore.runtime import RuntimeDecision, get_agentcore_runtime
from backend.agents.budget import BUDGET_KEYS, budget_ceiling_from_facts
from backend.agents.production_04.memory_agent import (
    ActivityEntry as MemoryActivity,
    MemoryAgent as TravelerMemorySpecialist,
)
from backend.db.rds_data_client import get_rds_data_client
from backend.memory.store import get_memory_store

logger = logging.getLogger(__name__)

PACKAGE_DETAIL_SQL = """
    SELECT package_id, name, operator, price_per_person,
           description, image_url, trip_type, destination,
           region, durations, availability, highlights
    FROM trip_packages
    WHERE package_id IN ({placeholders})
"""
PERSIST_SQL = (
    "-- Separate short RLS write transaction:\n"
    "INSERT INTO conversation_messages "
    "(message_id, conversation_id, role, content, embedding)\n"
    "  VALUES (..., ..., 'user', $1, $2::vector);\n"
    "INSERT INTO conversation_messages "
    "(message_id, conversation_id, role, content, embedding)\n"
    "  VALUES (..., ..., 'assistant', $3, $4::vector);\n"
    "INSERT INTO trip_interactions "
    "(interaction_id, traveler_id, conversation_id,\n"
    " query_text, response_summary, packages_shown, embedding)\n"
    "  VALUES (..., $5, ..., $1, $3, $6::jsonb, $2::vector);"
)
READ_UNIT_SQL = (
    "-- Short RLS read transaction (SET LOCAL reverts on commit)\n"
    "SET LOCAL app.current_traveler_id = '{traveler_id}';\n"
    "SET LOCAL app.agent_type = 'concierge_agent';\n"
    "SET LOCAL ROLE meridian_app;"
)
CONSOLE_LOGS = (
    "https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1"
    "#logsV2:log-groups/log-group/$252Faws$252Fbedrock-agentcore$252Fruntimes$252F{runtime_id}-DEFAULT"
)


@dataclass(frozen=True)
class HoldTarget:
    """The exact terms the traveler confirmed with the Hold button."""

    package_id: str
    duration: str
    travelers: int
    unit_price_cents: int


@dataclass
class HoldOutcome:
    """What the governed hold produced, allow or deny."""

    hold: Optional[Dict[str, Any]]
    refused: Optional[str]
    policy_decision: Optional[str]
    activities: List[Any]
    message: str
    conv_id: str
    trace_id: Optional[str]


def _with_budget_fact(
    facts: List[Dict[str, Any]], budget_facts: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Keep the fact the ceiling comes from in the context the reply is built on.

    The specialist ranks preferences by confidence and truncates, which drops
    Alex's saved budget. The gateway still enforces a ceiling derived from it, so
    a reply written without it tells the traveler that no budget is on file while
    the policy is holding them to one.

    Args:
        facts: The ranked facts the specialist returned for this turn.
        budget_facts: The full preference set the ceiling is derived from.

    Returns:
        ``facts`` unchanged when it already carries a budget fact, otherwise with
        the first budget fact from the full set appended.
    """
    if {str(fact.get("key", "")).lower() for fact in facts} & set(BUDGET_KEYS):
        return facts
    for fact in budget_facts:
        if str(fact.get("key", "")).lower() in BUDGET_KEYS:
            return [*facts, fact]
    return facts


@dataclass
class BookingTarget:
    """The held booking the traveler confirmed with the Confirm button, as Aurora holds it."""

    booking_id: str
    total_cents: int
    package_id: str
    duration: str
    travelers: int


@dataclass
class BookingOutcome:
    """What the governed confirmation produced, allow or deny."""

    booking: Optional[Dict[str, Any]]
    refused: Optional[str]
    policy_decision: Optional[str]
    activities: List[Any]
    message: str
    conv_id: str
    trace_id: Optional[str]


@dataclass
class AuthorizedRead:
    """What the short RLS read unit produced for one turn."""

    scope: Any
    conv_id: str
    memory_context: str
    memory_facts: List[Dict[str, Any]]
    budget_facts: List[Dict[str, Any]]


class ProductionAgent:
    """
    Phase 4 concierge orchestrator.

    Authorizes the workload for the traveler, reads memory under RLS, hands the
    authorized context to the managed AgentCore Runtime (which owns the gateway
    tool loop), then persists the returned decision under RLS.
    """

    AGENT_FILE = "agents/production_04/concierge.py"
    RUNTIME_FILE = "meridian_agentcore/app/MeridianConcierge/main.py"

    def __init__(self, activity_callback: Optional[Callable[[MemoryActivity], Any]] = None):
        self.activity_callback = activity_callback or (lambda _: None)
        self.traveler_memory = TravelerMemorySpecialist(activity_callback=self.activity_callback)
        self.store = get_memory_store()
        self.db = get_rds_data_client()
        self.identity = get_agentcore_identity()
        self.agentcore_runtime = get_agentcore_runtime()

    def _log(self, activity_type: str, title: str, details: Optional[str] = None, **kwargs) -> None:
        self.activity_callback(
            MemoryActivity(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                activity_type=activity_type,
                title=title,
                details=details,
                sql_query=kwargs.get("sql_query"),
                agent_name=kwargs.get("agent_name", "ProductionAgent"),
                agent_file=kwargs.get("agent_file", self.AGENT_FILE),
                telemetry=kwargs.get("telemetry"),
            )
        )

    def _forward(self, span: Dict[str, Any]) -> None:
        """Replay a span the runtime emitted as a backend activity."""
        self.activity_callback(
            MemoryActivity(
                id=str(span.get("id") or uuid.uuid4()),
                timestamp=str(span.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                activity_type=str(span.get("activity_type") or "reasoning"),
                title=str(span.get("title") or "Runtime span"),
                details=span.get("details"),
                sql_query=span.get("sql_query"),
                execution_time_ms=span.get("execution_time_ms"),
                agent_name=str(span.get("agent_name") or "ProductionAgent"),
                agent_file=str(span.get("agent_file") or self.RUNTIME_FILE),
                telemetry=span.get("telemetry"),
            )
        )

    def _collect(self, activities: List[Any]) -> None:
        """Route both the memory specialist's spans and this concierge's into one list."""
        outer_callback = self.activity_callback

        def collect(entry: MemoryActivity) -> None:
            activities.append(entry)
            outer_callback(entry)

        self.activity_callback = collect
        self.traveler_memory.activity_callback = collect

    # ------------------------------------------------------------- identity

    def _identity_spans(self, scope: Any, traveler_id: str) -> None:
        configured = bool(scope.workload_identity)
        self._log(
            "reasoning",
            "AgentCore Identity resolved" if configured else "Workload identity · AWS STS",
            details=(
                f"workload={scope.workload_identity} · token={scope.token_status}"
                if configured
                else "AgentCore Identity is not configured; the backend acts as its IAM principal"
            ),
            telemetry={
                "category": "security",
                "component": "Bedrock AgentCore Identity" if configured else "AWS STS",
                "status": "ok" if scope.token_status == "live" else "delegated",
                "fields": [
                    {"label": "iam_identity", "value": scope.iam_identity, "mono": True},
                    {
                        "label": "workload_identity",
                        "value": scope.workload_identity or "(unconfigured, using IAM principal)",
                        "mono": bool(scope.workload_identity),
                    },
                    {"label": "resource_provider", "value": scope.resource_provider or "-"},
                    {"label": "token_status", "value": scope.token_status},
                ],
            },
        )
        self._log(
            "security",
            "Workload traveler grant allowed",
            details=(
                f"{scope.authorization.provider}:{scope.authorization.subject_id} -> {traveler_id}"
            ),
            telemetry={
                "category": "security",
                "component": "Aurora identity binding",
                "status": "ok",
                "fields": [
                    {"label": "authorization.provider", "value": scope.authorization.provider},
                    {
                        "label": "authorization.subject",
                        "value": scope.authorization.subject_id,
                        "mono": True,
                    },
                    {"label": "authorization.decision", "value": "allow"},
                    {"label": "traveler_id", "value": traveler_id, "mono": True},
                    {"label": "binding_table", "value": "traveler_identity_bindings", "mono": True},
                ],
            },
        )
        self._log(
            "security",
            "Aurora RLS · short read unit",
            details=(
                f"app.current_traveler_id={traveler_id} · role=meridian_app · "
                "commits before external calls"
            ),
            sql_query=READ_UNIT_SQL.format(traveler_id=traveler_id),
            telemetry={
                "category": "security",
                "component": "Aurora RLS",
                "status": "ok",
                "fields": [
                    {"label": "iam_identity", "value": scope.iam_identity, "mono": True},
                    {"label": "rls.traveler_id", "value": traveler_id, "mono": True},
                    {"label": "rls.role", "value": "meridian_app", "mono": True},
                    {"label": "transaction.unit", "value": "read"},
                ],
            },
        )

    # ------------------------------------------------------------ read unit

    async def _authorized_read(
        self, message: str, traveler_id: str, conversation_id: Optional[str]
    ) -> AuthorizedRead:
        """Authorize the workload and read the traveler's memory in one short RLS unit."""
        scope = await asyncio.to_thread(self.identity.scope_for_turn)
        query_vector = await asyncio.to_thread(
            self.store.prepare_embedding_vector, message, input_type="search_query"
        )
        self.traveler_memory._prepared_query_vector = query_vector
        self.traveler_memory._query_vector_prepared = True
        try:
            async with self.db.scoped_session(
                traveler_id=traveler_id,
                agent_type="concierge_agent",
                authorization=scope.authorization,
            ) as read_tx:
                self.traveler_memory._transaction_id = read_tx
                self._identity_spans(scope, traveler_id)
                conv_id = await self.store.get_or_create_conversation(
                    traveler_id, conversation_id, transaction_id=read_tx
                )
                profile = await self.store.recall_profile(traveler_id, transaction_id=read_tx)
                session = await self.traveler_memory.recall_session_context(conv_id)
                prefs = await self.traveler_memory.recall_traveler_preferences(traveler_id)
                similar = await self.traveler_memory.recall_similar_interactions(
                    traveler_id, message
                )
                # The specialist recalls the top facts by confidence for the reply; the
                # budget ceiling the policy compares against needs every fact.
                budget_facts = await self.store.recall_preferences(
                    traveler_id, limit=50, transaction_id=read_tx
                )
        finally:
            self.traveler_memory._transaction_id = None
            self.traveler_memory._prepared_query_vector = None
            self.traveler_memory._query_vector_prepared = False
        facts = _with_budget_fact(prefs.get("facts", []), budget_facts)
        context = self.store.format_memory_context(
            profile, session.get("turns", []), facts, similar.get("interactions", [])
        )
        return AuthorizedRead(
            scope=scope,
            conv_id=conv_id,
            memory_context=context,
            memory_facts=facts,
            budget_facts=budget_facts,
        )

    # -------------------------------------------------------------- runtime

    def _runtime_span(self, decision: RuntimeDecision) -> None:
        runtime_id = decision.runtime_arn.rsplit("/", 1)[-1]
        self._log(
            "reasoning",
            "AgentCore Runtime · turn complete",
            details=(
                f"session={decision.runtime_session_id} · {decision.elapsed_ms} ms · "
                f"trace={decision.trace_id or 'pending'}"
            ),
            agent_file=self.RUNTIME_FILE,
            telemetry={
                "category": "runtime",
                "component": "Bedrock AgentCore Runtime · MeridianConcierge",
                "status": "ok",
                "fields": [
                    {"label": "runtime_arn", "value": decision.runtime_arn, "mono": True},
                    {"label": "runtimeSessionId", "value": decision.runtime_session_id, "mono": True},
                    {"label": "trace_id", "value": decision.trace_id or "pending", "mono": True},
                    {"label": "trace_console", "value": CONSOLE_LOGS.format(runtime_id=runtime_id)},
                    {"label": "qualifier", "value": decision.qualifier},
                    {"label": "isolation", "value": decision.isolation},
                    {
                        "label": "tokens",
                        "value": (
                            f"in {decision.usage.get('inputTokens', '-')} · "
                            f"out {decision.usage.get('outputTokens', '-')}"
                        ),
                    },
                    {"label": "decision_source", "value": "managed Runtime"},
                ],
            },
        )

    async def _runtime_turn(
        self, read: AuthorizedRead, message: str, traveler_id: str, travelers: int, **hold
    ) -> RuntimeDecision:
        decision = await asyncio.to_thread(
            self.agentcore_runtime.invoke_turn,
            read.conv_id,
            traveler_id,
            message,
            read.memory_context,
            budget_ceiling_cents=budget_ceiling_from_facts(read.budget_facts, travelers),
            travelers_count=travelers,
            **hold,
        )
        for span in decision.activities:
            self._forward(span)
        self._runtime_span(decision)
        return decision

    async def _hydrate(self, packages_raw: List[Dict[str, Any]]) -> List[Any]:
        """Join the runtime's ranked packages to live catalog rows, preserving its order."""
        package_ids = [str(p.get("package_id") or "") for p in packages_raw if p.get("package_id")]
        if package_ids:
            placeholders = ", ".join(["%s"] * len(package_ids))
            detail_rows = await self.db.execute(
                PACKAGE_DETAIL_SQL.format(placeholders=placeholders), tuple(package_ids)
            )
            detail_by_id = {str(row.get("package_id") or ""): dict(row) for row in detail_rows}
            packages_raw = [
                {
                    **package,
                    **detail_by_id.get(str(package.get("package_id") or ""), {}),
                    **({"similarity": package["similarity"]} if "similarity" in package else {}),
                }
                for package in packages_raw
            ]
            self._log(
                "database",
                "Hydrated managed search results",
                details=f"{len(detail_rows)} live catalog rows joined to the runtime's ranking",
                sql_query=(
                    "SELECT package_id, durations, availability, highlights "
                    "FROM trip_packages WHERE package_id IN (...)"
                ),
                telemetry={
                    "category": "data",
                    "component": "Aurora trip_packages",
                    "status": "ok",
                    "fields": [
                        {"label": "rows", "value": str(len(detail_rows))},
                        {"label": "preserved_order", "value": "Runtime ranking"},
                    ],
                },
            )
        return [
            SimpleNamespace(
                package_id=p.get("package_id", ""),
                name=p.get("name", ""),
                operator=p.get("operator", ""),
                price_per_person=float(p.get("price_per_person", 0.0) or 0.0),
                description=p.get("description", "") or "",
                image_url=p.get("image_url", "") or "",
                trip_type=p.get("trip_type", "") or "",
                destination=p.get("destination"),
                region=p.get("region"),
                durations=p.get("durations") or [],
                availability=p.get("availability") or {},
                highlights=p.get("highlights") or [],
                similarity=p.get("similarity"),
            )
            for p in packages_raw
        ]

    # ------------------------------------------------------------ write unit

    async def _write_unit(
        self, read: AuthorizedRead, traveler_id: str, message: str, reply: str,
        shown: List[Dict[str, Any]], operation: str,
    ) -> None:
        """Persist the turn and audit it in a separate short RLS write transaction."""
        interaction_text = f"User: {message}\nAssistant: {reply}"
        user_vector, assistant_vector, interaction_vector = await asyncio.gather(
            asyncio.to_thread(
                self.store.prepare_embedding_vector, message, input_type="search_document"
            ),
            asyncio.to_thread(
                self.store.prepare_embedding_vector, reply, input_type="search_document"
            ),
            asyncio.to_thread(
                self.store.prepare_embedding_vector, interaction_text, input_type="search_document"
            ),
        )
        self.traveler_memory._prepared_turn_vectors = {
            "user": user_vector,
            "assistant": assistant_vector,
            "interaction": interaction_vector,
        }
        try:
            async with self.db.scoped_session(
                traveler_id=traveler_id,
                agent_type="concierge_agent",
                authorization=read.scope.authorization,
            ) as write_tx:
                self.traveler_memory._transaction_id = write_tx
                await self.traveler_memory.persist_turn(
                    traveler_id, read.conv_id, message, reply, shown
                )
                self._log(
                    "tool_call",
                    "Strands @tool persist_turn",
                    details=(
                        "Reauthorized traveler scope; wrote 2 messages + 1 trip_interaction "
                        f"in a short RLS write unit · {len(shown)} packages recorded"
                    ),
                    sql_query=PERSIST_SQL,
                    telemetry={
                        "category": "memory_short",
                        "component": "Aurora write path · scoped_session",
                        "status": "ok",
                        "fields": [
                            {"label": "table", "value": "conversation_messages + trip_interactions"},
                            {"label": "rls.traveler_id", "value": traveler_id, "mono": True},
                            {"label": "transaction.unit", "value": "write"},
                        ],
                    },
                )
                await self.store.write_audit(
                    agent_name="ProductionAgent",
                    operation=operation,
                    traveler_id=traveler_id,
                    rls_traveler=traveler_id,
                    rls_agent_type="concierge_agent",
                    iam_identity=read.scope.iam_identity,
                    authorization_provider=read.scope.authorization.provider,
                    authorization_subject=read.scope.authorization.subject_id,
                    authorization_decision="allow",
                    rows_returned=len(shown),
                    transaction_id=write_tx,
                )
        finally:
            self.traveler_memory._transaction_id = None
            self.traveler_memory._prepared_turn_vectors = None

    # ----------------------------------------------------------------- turns

    async def process_turn(
        self,
        message: str,
        traveler_id: str,
        conversation_id: Optional[str],
        limit: int,
        travelers_count: int = 1,
    ) -> Tuple[List[Any], List[Any], str, str, List[Dict[str, Any]]]:
        """Run one production concierge turn through the full AgentCore envelope.

        Args:
            message: The traveler's utterance for this turn.
            traveler_id: Traveler identifier (RLS scope, e.g. trv_meridian_demo).
            conversation_id: Existing conversation id, or None to start one.
            limit: Kept for the chat router's signature; the runtime ranks its own results.
            travelers_count: Party size, which sizes the budget ceiling the policy sees.

        Returns:
            Tuple of (packages, activities, response_text, conversation_id, memory_facts).
        """
        require_agentcore_platform(require_memory=False)
        activities: List[Any] = []
        self._collect(activities)
        read = await self._authorized_read(message, traveler_id, conversation_id)
        decision = await self._runtime_turn(read, message, traveler_id, travelers_count)
        packages = await self._hydrate(decision.packages)
        shown = [{"package_id": p.package_id, "name": p.name} for p in packages]
        await self._write_unit(
            read, traveler_id, message, decision.message, shown, "production_turn"
        )
        self._log(
            "result",
            "Memory-grounded reply ready",
            details=f"{len(packages)} packages · Aurora memory updated",
            telemetry={"category": "synthesis", "component": "ProductionAgent", "status": "ok"},
        )
        return packages, activities, decision.message, read.conv_id, read.memory_facts

    async def process_hold(
        self, traveler_id: str, conversation_id: Optional[str], target: HoldTarget
    ) -> HoldOutcome:
        """Place the hold the traveler confirmed, through the runtime and the governed gateway.

        Args:
            traveler_id: Traveler identifier (RLS scope).
            conversation_id: The conversation the hold belongs to, or None to start one.
            target: The exact hold terms the traveler confirmed with the Hold button.

        Returns:
            The hold outcome. ``hold`` is None when Cedar denied the call or the
            inventory refused it; ``refused`` then carries the reason.
        """
        require_agentcore_platform(require_memory=False)
        activities: List[Any] = []
        self._collect(activities)
        message = (
            f"Place a 12-hour courtesy hold on {target.package_id} ({target.duration}) "
            f"for {target.travelers} traveler(s)."
        )
        read = await self._authorized_read(message, traveler_id, conversation_id)
        decision = await self._runtime_turn(
            read, message, traveler_id, target.travelers,
            hold_confirmed=True, hold_target=asdict(target),
        )
        shown = [{"package_id": target.package_id, "name": target.package_id}]
        await self._write_unit(
            read, traveler_id, message, decision.message, shown, "production_hold"
        )
        held = decision.hold or {}
        if decision.hold:
            status = "held"
        elif decision.policy_decision == "deny":
            status = "denied"
        else:
            status = "error"
        self._log(
            "result",
            "Courtesy hold persisted" if decision.hold else "Courtesy hold not placed",
            details=(
                f"Hold #{held.get('bookingId')} · expires {held.get('expiresAt')}"
                if decision.hold
                else (decision.hold_refused or "The runtime did not call the hold tool.")
            ),
            telemetry={"category": "synthesis", "component": "ProductionAgent", "status": status},
        )
        return HoldOutcome(
            hold=decision.hold,
            refused=decision.hold_refused,
            policy_decision=decision.policy_decision,
            activities=activities,
            message=decision.message,
            conv_id=read.conv_id,
            trace_id=decision.trace_id,
        )

    async def process_booking(
        self, traveler_id: str, conversation_id: Optional[str], target: BookingTarget
    ) -> BookingOutcome:
        """Confirm the held booking the traveler approved, through the runtime and the gateway.

        Args:
            traveler_id: Traveler identifier (RLS scope).
            conversation_id: The conversation the booking belongs to, or None to start one.
            target: The held booking as Aurora holds it, read under RLS by the caller.

        Returns:
            The booking outcome. ``booking`` is None when Cedar denied the call or Aurora
            refused it; ``refused`` then carries the reason.
        """
        require_agentcore_platform(require_memory=False)
        activities: List[Any] = []
        self._collect(activities)
        message = (
            f"Confirm booking {target.booking_id}: {target.package_id} ({target.duration}) "
            f"for {target.travelers} traveler(s), total ${target.total_cents / 100:,.2f}."
        )
        read = await self._authorized_read(message, traveler_id, conversation_id)
        decision = await self._runtime_turn(
            read, message, traveler_id, target.travelers,
            booking_confirmed=True, booking_target=asdict(target),
        )
        shown = [{"package_id": target.package_id, "name": target.package_id}]
        await self._write_unit(
            read, traveler_id, message, decision.message, shown, "production_booking"
        )
        booked = decision.booking or {}
        if decision.booking:
            status = "confirmed"
        elif decision.policy_decision == "deny":
            status = "denied"
        else:
            status = "error"
        self._log(
            "result",
            "Booking confirmed in Aurora" if decision.booking else "Booking not confirmed",
            details=(
                f"Booking #{booked.get('bookingId')} · confirmed {booked.get('confirmedAt')}"
                if decision.booking
                else (decision.booking_refused or "The runtime did not call the confirm tool.")
            ),
            telemetry={"category": "synthesis", "component": "ProductionAgent", "status": status},
        )
        return BookingOutcome(
            booking=decision.booking,
            refused=decision.booking_refused,
            policy_decision=decision.policy_decision,
            activities=activities,
            message=decision.message,
            conv_id=read.conv_id,
            trace_id=decision.trace_id,
        )


def create_production_agent(activity_callback=None) -> ProductionAgent:
    return ProductionAgent(activity_callback=activity_callback)


# Back-compat aliases for older imports and docs.
create_concierge_agent = create_production_agent
ConciergeAgent = ProductionAgent
