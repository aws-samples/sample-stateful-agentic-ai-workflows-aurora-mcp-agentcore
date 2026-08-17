"""
Production mode — managed AgentCore Runtime concierge + Aurora RLS.

Presenter walkthrough — AgentCore on one turn
---------------------------------------------
  1. AgentCore Runtime   — managed concierge decision (runtimeSessionId · microVM)
  2. AgentCore Identity  — workload / IAM envelope (security span)
  3. AgentCore Memory    — list + semantic recall + create_event mirror
  4. Aurora RLS units    — short authorized read and write transactions
  5. AgentCore Gateway   — managed MCP tools/list + tools/call for trip search
  6. persist_turn        — Aurora write + AgentCore Memory write-back

This module is **live** — imported by `chat.py` → `production_search()`.

AWS docs (AgentCore):
  - What is AgentCore?
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html
  - Runtime:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime.html
  - Gateway (managed MCP):
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html
  - Memory:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html
  - Identity:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html
  - CLI get started:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html

AWS docs (Aurora):
  - RDS Data API transactions (RLS ``scoped_session``):
    https://docs.aws.amazon.com/rdsdataservice/latest/APIReference/API_BeginTransaction.html
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.agentcore.cli_config import require_agentcore_platform
from backend.agentcore.gateway import get_agentcore_gateway
from backend.agentcore.identity import get_agentcore_identity
from backend.agentcore.memory import get_agentcore_memory
from backend.agentcore.runtime import get_agentcore_runtime
from backend.agents.production_04.memory_agent import (
    MemoryAgent as TravelerMemorySpecialist,
    ActivityEntry as MemoryActivity,
)
from backend.db.rds_data_client import get_rds_data_client
from backend.memory.store import get_memory_store

logger = logging.getLogger(__name__)


class ProductionAgent:
    """
    Phase 4 concierge orchestrator.

    Loads memory under RLS, searches through AgentCore Gateway, sends the live
    context and candidates to the managed AgentCore Runtime concierge, and
    persists the returned decision under RLS.
    """

    AGENT_FILE = "agents/production_04/concierge.py"

    def __init__(self, activity_callback: Optional[Callable[[MemoryActivity], Any]] = None):
        self.activity_callback = activity_callback or (lambda _: None)
        self.traveler_memory = TravelerMemorySpecialist(activity_callback=self.activity_callback)
        self.store = get_memory_store()
        self.db = get_rds_data_client()
        self.identity = get_agentcore_identity()
        self.agentcore_memory = get_agentcore_memory()
        self.agentcore_runtime = get_agentcore_runtime()
        self.agentcore_gateway = get_agentcore_gateway()

    def _log(self, activity_type: str, title: str, details: Optional[str] = None, **kwargs) -> None:
        self.activity_callback(
            MemoryActivity(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                activity_type=activity_type,
                title=title,
                details=details,
                # Pass the SQL through so the showcase's SQL tab can pick
                # it up. Defaults to None when the span isn't a DB call.
                sql_query=kwargs.get("sql_query"),
                agent_name=kwargs.get("agent_name", "ProductionAgent"),
                agent_file=self.AGENT_FILE,
                telemetry=kwargs.get("telemetry"),
            )
        )

    async def _search_packages(
        self,
        message: str,
        limit: int,
    ) -> Tuple[List[Any], List[Any]]:
        """Trip discovery via AgentCore Gateway managed MCP (tools/list + tools/call)."""
        gateway = self.agentcore_gateway
        packages_raw, _meta = await asyncio.to_thread(
            gateway.semantic_trip_search, message, limit
        )
        tool_list, _list_raw = await asyncio.to_thread(gateway.list_tools)
        self._log(
            "tool_call",
            "AgentCore Gateway · tools/list",
            details=f"{len(tool_list)} MCP tools at gateway endpoint",
            telemetry={
                "category": "gateway",
                "component": "Bedrock AgentCore Gateway",
                "status": "ok",
                "fields": [
                    {"label": "endpoint", "value": gateway.gateway_url, "mono": True},
                    {"label": "tools", "value": str(len(tool_list))},
                    {
                        "label": "discovered",
                        "value": ", ".join(t["name"] for t in tool_list[:4])
                        + (" …" if len(tool_list) > 4 else ""),
                    },
                ],
            },
        )
        self._log(
            "search",
            f"AgentCore Gateway · tools/call → {gateway.search_tool}",
            details=f"Found {len(packages_raw)} packages via managed MCP",
            telemetry={
                "category": "gateway",
                "component": "Bedrock AgentCore Gateway",
                "status": "ok",
                "fields": [
                    {"label": "tool", "value": gateway.search_tool, "mono": True},
                    {"label": "packages", "value": str(len(packages_raw))},
                    {"label": "auth", "value": "Bearer" if gateway.access_token else "SigV4"},
                ],
            },
        )

        # Gateway owns discovery and ranking, while the local catalog read
        # hydrates the richer card facts that the managed tool may omit
        # (inventory, highlights, region). Preserve the Gateway score/order.
        package_ids = [
            str(package.get("package_id") or "")
            for package in packages_raw
            if package.get("package_id")
        ]
        if package_ids:
            placeholders = ", ".join(["%s"] * len(package_ids))
            detail_rows = await self.db.execute(
                f"""
                    SELECT package_id, name, operator, price_per_person,
                           description, image_url, trip_type, destination,
                           region, durations, availability, highlights
                    FROM trip_packages
                    WHERE package_id IN ({placeholders})
                """,
                tuple(package_ids),
            )
            detail_by_id = {
                str(row.get("package_id") or ""): dict(row)
                for row in detail_rows
            }
            packages_raw = [
                {
                    **package,
                    **detail_by_id.get(str(package.get("package_id") or ""), {}),
                    **(
                        {"similarity": package.get("similarity")}
                        if "similarity" in package
                        else {}
                    ),
                }
                for package in packages_raw
            ]
            self._log(
                "database",
                "Hydrated managed search results",
                details=f"{len(detail_rows)} live catalog rows joined to Gateway ranking",
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
                        {"label": "preserved_order", "value": "Gateway ranking"},
                    ],
                },
            )

        from types import SimpleNamespace

        packages = [
            SimpleNamespace(
                package_id=p.get("package_id", ""),
                name=p.get("name", ""),
                operator=p.get("operator", ""),
                price_per_person=float(p.get("price_per_person", 0.0)),
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
        return packages, []

    async def process_turn(
        self,
        message: str,
        traveler_id: str,
        conversation_id: Optional[str],
        limit: int,
    ) -> Tuple[List[Any], List[Any], str, str, List[Dict[str, Any]]]:
        """Run one production concierge turn through the full AgentCore envelope.

        The turn is the Phase 4 story end to end — each step emits a trace span:
          1. AgentCore Identity   — resolve the IAM/workload envelope (security span)
          2. Traveler grant       — authorize the workload for traveler_id
          3. Aurora RLS read      — short transaction for profile and memory reads
          4. AgentCore Runtime    — managed concierge decision from live context
          5. AgentCore Memory     — recall recent session events + semantic recall
          6. AgentCore Gateway    — managed MCP tools/list + tools/call for trip search
          7. Aurora RLS write     — separate short transaction for turn + audit
          8. AgentCore Memory     — mirror the committed turn with create_event

        Args:
            message: The traveler's utterance for this turn.
            traveler_id: Traveler identifier (RLS scope, e.g. trv_meridian_demo).
            conversation_id: Existing conversation id, or None to start one.
            limit: Max trip packages to surface.

        Returns:
            Tuple of (packages, activities, response_text, conversation_id, memory_facts).
        """
        require_agentcore_platform()
        activities: List[Any] = []

        # Route BOTH the Strands MemoryAgent spans (Aurora recall/persist tools)
        # AND this concierge's own self._log spans (AgentCore Identity / Runtime
        # / Gateway / Memory) into the same `activities` list that process_turn
        # returns. Without repointing self.activity_callback, the AgentCore
        # spans went to the constructor's no-op default and never reached the
        # trace panel — so the UI showed only the Aurora tools even though the
        # AgentCore data-plane calls really ran.
        outer_callback = self.activity_callback

        def collect(entry: MemoryActivity) -> None:
            activities.append(entry)
            outer_callback(entry)

        self.activity_callback = collect
        self.traveler_memory.activity_callback = collect

        # Resolve the identity envelope first so it can land in both the
        # Security trace span and the per-turn audit row.
        scope = await asyncio.to_thread(self.identity.scope_for_turn)

        self._log(
            "reasoning",
            "AgentCore Identity resolved",
            details=f"workload={scope.workload_identity or '—'} · token={scope.token_status}",
            telemetry={
                "category": "security",
                "component": "Bedrock AgentCore Identity",
                "status": "ok" if scope.token_status == "live" else "delegated",
                "fields": [
                    {"label": "iam_identity", "value": scope.iam_identity, "mono": True},
                    {
                        "label": "workload_identity",
                        "value": scope.workload_identity or "(unconfigured — using IAM principal)",
                        "mono": bool(scope.workload_identity),
                    },
                    {
                        "label": "resource_provider",
                        "value": scope.resource_provider or "—",
                    },
                    {"label": "token_status", "value": scope.token_status},
                ],
            },
        )

        # Embedding is an external Bedrock call. Prepare it before opening the
        # RLS read unit so the Data API transaction never waits on a model.
        query_vector = await asyncio.to_thread(
            self.store.prepare_embedding_vector,
            message,
            input_type="search_query",
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

                self._log(
                    "security",
                    "Workload traveler grant allowed",
                    details=(
                        f"{scope.authorization.provider}:{scope.authorization.subject_id} "
                        f"-> {traveler_id}"
                    ),
                    telemetry={
                        "category": "security",
                        "component": "Aurora identity binding",
                        "status": "ok",
                        "fields": [
                            {
                                "label": "authorization.provider",
                                "value": scope.authorization.provider,
                            },
                            {
                                "label": "authorization.subject",
                                "value": scope.authorization.subject_id,
                                "mono": True,
                            },
                            {
                                "label": "authorization.decision",
                                "value": "allow",
                            },
                            {
                                "label": "traveler_id",
                                "value": traveler_id,
                                "mono": True,
                            },
                            {
                                "label": "binding_table",
                                "value": "traveler_identity_bindings",
                                "mono": True,
                            },
                        ],
                    },
                )

                self._log(
                    "security",
                    "Aurora RLS · short read unit",
                    details=(
                        f"app.current_traveler_id={traveler_id} · "
                        "role=meridian_app · commits before external calls"
                    ),
                    sql_query=(
                        "-- Short RLS read transaction (SET LOCAL reverts on commit)\n"
                        f"SET LOCAL app.current_traveler_id = '{traveler_id}';\n"
                        "SET LOCAL app.agent_type = 'concierge_agent';\n"
                        "SET LOCAL ROLE meridian_app;"
                    ),
                    telemetry={
                        "category": "security",
                        "component": "Aurora RLS",
                        "status": "ok",
                        "fields": [
                            {
                                "label": "iam_identity",
                                "value": scope.iam_identity,
                                "mono": True,
                            },
                            {
                                "label": "authorization.subject",
                                "value": scope.authorization.subject_id,
                                "mono": True,
                            },
                            {
                                "label": "rls.traveler_id",
                                "value": traveler_id,
                                "mono": True,
                            },
                            {"label": "rls.role", "value": "meridian_app", "mono": True},
                            {"label": "transaction.unit", "value": "read"},
                        ],
                    },
                )

                conv_id = await self.store.get_or_create_conversation(
                    traveler_id,
                    conversation_id,
                    transaction_id=read_tx,
                )
                profile = await self.store.recall_profile(
                    traveler_id,
                    transaction_id=read_tx,
                )
                session = await self.traveler_memory.recall_session_context(conv_id)
                prefs = await self.traveler_memory.recall_traveler_preferences(
                    traveler_id
                )
                similar = await self.traveler_memory.recall_similar_interactions(
                    traveler_id,
                    message,
                )
        finally:
            self.traveler_memory._transaction_id = None
            self.traveler_memory._prepared_query_vector = None
            self.traveler_memory._query_vector_prepared = False

        memory_facts: List[Dict[str, Any]] = prefs.get("facts", [])
        memory_context = self.store.format_memory_context(
            profile,
            session.get("turns", []),
            memory_facts,
            similar.get("interactions", []),
        )

        memory_namespace = self.agentcore_memory._namespace(traveler_id, conv_id)
        agentcore_turns = await asyncio.to_thread(
            self.agentcore_memory.list_recent_turns,
            traveler_id,
            conv_id,
            limit=6,
        )
        self._log(
            "reasoning",
            "AgentCore Memory · recent session events",
            details=f"{len(agentcore_turns)} events",
            telemetry={
                "category": "memory_short",
                "component": "Bedrock AgentCore Memory",
                "status": "ok",
                "memory": {
                    "shortTerm": {
                        "label": "AgentCore session events",
                        "items": [
                            (turn.get("text") or "")[:120]
                            for turn in agentcore_turns
                        ],
                    }
                },
                "fields": [
                    {
                        "label": "memory_id",
                        "value": self.agentcore_memory.memory_id,
                        "mono": True,
                    },
                    {"label": "namespace", "value": memory_namespace},
                ],
            },
        )

        agentcore_semantic = await asyncio.to_thread(
            self.agentcore_memory.semantic_recall,
            traveler_id,
            conv_id,
            message,
            top_k=3,
        )
        self._log(
            "reasoning",
            "AgentCore Memory · semantic retrieve",
            details=f"{len(agentcore_semantic)} records",
            telemetry={
                "category": "memory_long",
                "component": "Bedrock AgentCore Memory",
                "status": "ok",
                "memory": {
                    "longTerm": {
                        "label": "AgentCore semantic recall",
                        "items": [
                            (record.get("text") or "")[:120]
                            for record in agentcore_semantic
                        ],
                    }
                },
                "fields": [
                    {"label": "operation", "value": "retrieve_memory_records"},
                    {"label": "top_k", "value": "3"},
                ],
            },
        )

        self._log(
            "reasoning",
            "Apply traveler context to search",
            details=f"orchestration=deterministic · {memory_context[:240]}",
        )

        packages, search_activities = await self._search_packages(message, limit)
        activities.extend(search_activities)

        runtime_context = "\n".join(
            part
            for part in (
                memory_context,
                "AgentCore recent turns: "
                + "; ".join(
                    (turn.get("text") or "")[:240] for turn in agentcore_turns
                ),
                "AgentCore semantic recall: "
                + "; ".join(
                    (record.get("text") or "")[:240]
                    for record in agentcore_semantic
                ),
            )
            if part
        )
        runtime_candidates = [
            {
                "package_id": getattr(package, "package_id", ""),
                "name": getattr(package, "name", ""),
                "destination": getattr(package, "destination", ""),
                "operator": getattr(package, "operator", ""),
                "price_per_person": getattr(package, "price_per_person", None),
                "durations": getattr(package, "durations", None),
                "availability": getattr(package, "availability", None),
                "highlights": getattr(package, "highlights", None),
            }
            for package in packages
        ]
        runtime_decision = await asyncio.to_thread(
            self.agentcore_runtime.invoke_turn,
            conv_id,
            traveler_id,
            message,
            runtime_context,
            runtime_candidates,
        )
        self._log(
            "reasoning",
            "AgentCore Runtime · concierge decision",
            details=(
                f"session={runtime_decision.runtime_session_id} · "
                f"invoke={runtime_decision.invoke_status}"
            ),
            telemetry={
                "category": "runtime",
                "component": "Bedrock AgentCore Runtime · MeridianConcierge",
                "status": "ok",
                "fields": [
                    {
                        "label": "runtime_arn",
                        "value": runtime_decision.runtime_arn,
                        "mono": True,
                    },
                    {
                        "label": "runtimeSessionId",
                        "value": runtime_decision.runtime_session_id,
                        "mono": True,
                    },
                    {"label": "qualifier", "value": runtime_decision.qualifier},
                    {"label": "isolation", "value": runtime_decision.isolation},
                    {"label": "invoke_status", "value": runtime_decision.invoke_status},
                    {"label": "decision_source", "value": "managed Runtime"},
                ],
            },
        )

        shown = [
            {"package_id": getattr(package, "package_id", None), "name": package.name}
            for package in packages
        ]

        response_message = runtime_decision.message

        # Prepare all write-side vectors before the short RLS write unit. These
        # independent Bedrock calls run concurrently to reduce turn latency.
        interaction_text = f"User: {message}\nAssistant: {response_message}"
        user_vector, assistant_vector, interaction_vector = await asyncio.gather(
            asyncio.to_thread(
                self.store.prepare_embedding_vector,
                message,
                input_type="search_document",
            ),
            asyncio.to_thread(
                self.store.prepare_embedding_vector,
                response_message,
                input_type="search_document",
            ),
            asyncio.to_thread(
                self.store.prepare_embedding_vector,
                interaction_text,
                input_type="search_document",
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
                authorization=scope.authorization,
            ) as write_tx:
                self.traveler_memory._transaction_id = write_tx
                await self.traveler_memory.persist_turn(
                    traveler_id,
                    conv_id,
                    message,
                    response_message,
                    shown,
                )
                self._log(
                    "tool_call",
                    "Strands @tool persist_turn",
                    details=(
                        "Reauthorized traveler scope; wrote 2 messages + "
                        f"1 trip_interaction in a short RLS write unit · "
                        f"{len(shown)} packages shown"
                    ),
                    sql_query=(
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
                    ),
                    telemetry={
                        "category": "memory_short",
                        "component": "Aurora write path · scoped_session",
                        "status": "ok",
                        "fields": [
                            {
                                "label": "table",
                                "value": (
                                    "conversation_messages + trip_interactions"
                                ),
                            },
                            {
                                "label": "rls.traveler_id",
                                "value": traveler_id,
                                "mono": True,
                            },
                            {"label": "transaction.unit", "value": "write"},
                        ],
                    },
                )

                await self.store.write_audit(
                    agent_name="ProductionAgent",
                    operation="production_turn",
                    traveler_id=traveler_id,
                    rls_traveler=traveler_id,
                    rls_agent_type="concierge_agent",
                    iam_identity=scope.iam_identity,
                    authorization_provider=scope.authorization.provider,
                    authorization_subject=scope.authorization.subject_id,
                    authorization_decision="allow",
                    rows_returned=len(packages),
                    transaction_id=write_tx,
                )
        finally:
            self.traveler_memory._transaction_id = None
            self.traveler_memory._prepared_turn_vectors = None

        # Aurora is committed before the managed-memory mirror. Cross-store
        # delivery is a separate consistency domain; production deployments
        # should drive retries from an outbox rather than hold the DB tx open.
        agentcore_write = await asyncio.to_thread(
            self.agentcore_memory.record_turn,
            traveler_id,
            conv_id,
            message,
            response_message,
        )
        self._log(
            "tool_call",
            "AgentCore Memory · create_event",
            details=f"event_id={agentcore_write.get('event_id')} · after Aurora commit",
            telemetry={
                "category": "memory_short",
                "component": "Bedrock AgentCore Memory",
                "status": "ok",
                "fields": [
                    {"label": "operation", "value": "create_event"},
                    {"label": "actor_id", "value": traveler_id, "mono": True},
                    {"label": "session_id", "value": conv_id, "mono": True},
                    {"label": "consistency", "value": "post-commit mirror"},
                ],
            },
        )

        self._log(
            "result",
            "Memory-grounded reply ready",
            details=f"{len(packages)} packages · Aurora memory updated",
            telemetry={
                "category": "synthesis",
                "component": "ProductionAgent",
                "status": "ok",
            },
        )

        return packages, activities, response_message, conv_id, memory_facts


def create_production_agent(activity_callback=None) -> ProductionAgent:
    return ProductionAgent(activity_callback=activity_callback)


# Back-compat aliases for older imports and docs.
create_concierge_agent = create_production_agent
ConciergeAgent = ProductionAgent
