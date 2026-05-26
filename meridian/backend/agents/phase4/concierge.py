"""
Phase 4 — Production Agent (Strands + full AgentCore stack + Aurora RLS).

Presenter walkthrough — AgentCore on one turn
---------------------------------------------
  1. AgentCore Runtime   — session envelope (runtimeSessionId · microVM isolation)
  2. AgentCore Identity  — workload / IAM envelope (security span)
  3. AgentCore Memory    — list + semantic recall + create_event mirror
  4. Aurora RLS tx       — scoped_session + MemoryAgent @tools
  5. AgentCore Gateway   — managed MCP tools/list + tools/call for trip search
  6. persist_turn        — Aurora write + AgentCore Memory write-back

This module is **live** — imported by `chat.py` → `phase4_search()`.

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
import os
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from strands import Agent
from strands.models import BedrockModel

from backend.config import config
from backend.agentcore.cli_config import require_agentcore_platform
from backend.agentcore.gateway import get_agentcore_gateway
from backend.agentcore.identity import get_agentcore_identity
from backend.agentcore.memory import get_agentcore_memory
from backend.agentcore.runtime import get_agentcore_runtime
from backend.agents.phase4.memory_agent import MemoryAgent, ActivityEntry as MemoryActivity
from backend.db.rds_data_client import get_rds_data_client
from backend.memory.store import get_memory_store

logger = logging.getLogger(__name__)


class ProductionAgent:
    """
    Phase 4 concierge orchestrator.

    Composes MemoryAgent @tools into a Strands Agent, searches trips
    via AgentCore Gateway MCP, and persists every turn under RLS.
    """

    AGENT_FILE = "agents/phase4/concierge.py"

    def __init__(self, activity_callback: Optional[Callable[[MemoryActivity], Any]] = None):
        self.activity_callback = activity_callback or (lambda _: None)
        self.traveler_memory = MemoryAgent(activity_callback=self.activity_callback)
        self.store = get_memory_store()
        self.db = get_rds_data_client()
        self.identity = get_agentcore_identity()
        self.agentcore_memory = get_agentcore_memory()
        self.agentcore_runtime = get_agentcore_runtime()
        self.agentcore_gateway = get_agentcore_gateway()
        self.model = BedrockModel(
            model_id=config.bedrock.model_id,
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        )
        # Concierge Agent — memory @tools are bound methods from MemoryAgent.
        self.agent = Agent(
            model=self.model,
            tools=[
                self.traveler_memory.recall_session_context,
                self.traveler_memory.recall_traveler_preferences,
                self.traveler_memory.recall_similar_interactions,
                self.traveler_memory.persist_turn,
            ],
            system_prompt="Meridian concierge: load traveler memory from Aurora, search trips, save the turn.",
        )

    def _log(self, activity_type: str, title: str, details: Optional[str] = None, **kwargs) -> None:
        self.activity_callback(
            MemoryActivity(
                id=str(uuid.uuid4()),
                timestamp=datetime.utcnow().isoformat() + "Z",
                activity_type=activity_type,
                title=title,
                details=details,
                agent_name=kwargs.get("agent_name", "ProductionAgent"),
                agent_file=self.AGENT_FILE,
                telemetry=kwargs.get("telemetry"),
            )
        )

    async def _llm_driven_memory_recall(
        self,
        traveler_id: str,
        conv_id: str,
        message: str,
    ) -> bool:
        """
        Ask Bedrock (via Strands) to choose which memory tools to call this turn.

        Returns True if the LLM successfully drove tool selection. The MemoryAgent
        callbacks emit trace spans naturally as tools fire; the supervisor here
        only needs to know success vs failure to decide on fallback.
        """
        prompt = (
            f"Active traveler: {traveler_id}\n"
            f"Active conversation: {conv_id}\n"
            f"User said: {message}\n\n"
            "Before searching, recall what we know:\n"
            f"- Call recall_session_context with conversation_id='{conv_id}' to load recent turns.\n"
            f"- Call recall_traveler_preferences with traveler_id='{traveler_id}' for durable preferences.\n"
            f"- Call recall_similar_interactions with traveler_id='{traveler_id}' and the user query.\n"
            "Then briefly summarize the memory you loaded."
        )
        try:
            await self.agent.invoke_async(prompt)
            return True
        except Exception as exc:
            logger.error("Strands memory recall failed: %s", exc)
            self._log(
                "error",
                "Bedrock memory orchestration failed",
                details=str(exc)[:200],
            )
            raise

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
        from types import SimpleNamespace

        products = [
            SimpleNamespace(
                product_id=p.get("package_id", ""),
                name=p.get("name", ""),
                operator=p.get("operator", ""),
                price_per_person=float(p.get("price_per_person", 0.0)),
                description=p.get("description", "") or "",
                image_url=p.get("image_url", "") or "",
                trip_type=p.get("trip_type", "") or "",
                similarity=p.get("similarity"),
            )
            for p in packages_raw
        ]
        return products, []

    async def process_turn(
        self,
        message: str,
        traveler_id: str,
        conversation_id: Optional[str],
        limit: int,
    ) -> Tuple[List[Any], List[Any], str, str, List[Dict[str, Any]]]:
        require_agentcore_platform()
        activities: List[Any] = []

        def collect(entry: MemoryActivity) -> None:
            activities.append(entry)
            self.activity_callback(entry)

        self.traveler_memory.activity_callback = collect

        # Resolve the identity envelope first so it can land in both the
        # Security trace span and the per-turn audit row.
        scope = self.identity.scope_for_turn()

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

        # Open one Aurora transaction for the whole turn and pin the RLS
        # session variables.  Every memory read/write below runs through this
        # transaction id, so traveler_preferences / conversation_messages /
        # trip_interactions enforce per-traveler isolation in Postgres itself.
        async with self.db.scoped_session(
            traveler_id=traveler_id, agent_type="concierge_agent"
        ) as tx:
            self.traveler_memory._transaction_id = tx

            self._log(
                "security",
                "RLS scope set on Aurora session",
                details=(
                    f"app.current_traveler_id={traveler_id} · "
                    f"app.agent_type=concierge_agent"
                ),
                telemetry={
                    "category": "security",
                    "component": "Aurora RLS",
                    "status": "ok",
                    "fields": [
                        {"label": "iam_identity", "value": scope.iam_identity, "mono": True},
                        {"label": "rls.traveler_id", "value": traveler_id, "mono": True},
                        {"label": "rls.agent_type", "value": "concierge_agent"},
                        {"label": "policies", "value": "traveler_preferences, conversation_messages, trip_interactions"},
                    ],
                },
            )

            conv_id = await self.store.get_or_create_conversation(
                traveler_id, conversation_id, transaction_id=tx
            )
            profile = await self.store.recall_profile(traveler_id, transaction_id=tx)

            runtime_session = self.agentcore_runtime.session_for_turn(conv_id, traveler_id)
            self._log(
                "reasoning",
                "AgentCore Runtime · session envelope",
                details=(
                    f"session={runtime_session.runtime_session_id} · "
                    f"invoke={runtime_session.invoke_status}"
                ),
                telemetry={
                    "category": "runtime",
                    "component": "Bedrock AgentCore Runtime",
                    "status": "ok",
                    "fields": [
                        {
                            "label": "runtime_arn",
                            "value": runtime_session.runtime_arn,
                            "mono": True,
                        },
                        {
                            "label": "runtimeSessionId",
                            "value": runtime_session.runtime_session_id,
                            "mono": True,
                        },
                        {"label": "qualifier", "value": runtime_session.qualifier},
                        {"label": "isolation", "value": runtime_session.isolation},
                        {"label": "invoke_status", "value": runtime_session.invoke_status},
                    ],
                },
            )

            self._log(
                "reasoning",
                "Concierge session start (Strands)",
                details=f"traveler={traveler_id}, conversation={conv_id}",
                telemetry={
                    "category": "runtime",
                    "component": "Strands Agents + Aurora",
                    "status": "ok",
                    "fields": [
                        {"label": "traveler_id", "value": traveler_id, "mono": True},
                        {"label": "conversation_id", "value": conv_id, "mono": True},
                    ],
                },
            )

            agentcore_turns = self.agentcore_memory.list_recent_turns(
                traveler_id, conv_id, limit=6
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
                                (t.get("text") or "")[:120]
                                for t in agentcore_turns
                            ],
                        }
                    },
                    "fields": [
                        {
                            "label": "memory_id",
                            "value": self.agentcore_memory.memory_id,
                            "mono": True,
                        },
                        {"label": "namespace", "value": f"{traveler_id}/{conv_id}"},
                    ],
                },
            )

            agentcore_semantic = self.agentcore_memory.semantic_recall(
                traveler_id, conv_id, message, top_k=3
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
                                (r.get("text") or "")[:120] for r in agentcore_semantic
                            ],
                        }
                    },
                    "fields": [
                        {"label": "operation", "value": "retrieve_memory_records"},
                        {"label": "top_k", "value": "3"},
                    ],
                },
            )

            llm_ok = await self._llm_driven_memory_recall(traveler_id, conv_id, message)

            # Materialize structured memory state for the search step.  If the
            # LLM already invoked the tools, these hit the same Aurora tables
            # a second time and return identical data; cost is negligible.
            session = await self.traveler_memory.recall_session_context(conv_id)
            prefs = await self.traveler_memory.recall_traveler_preferences(traveler_id)
            similar = await self.traveler_memory.recall_similar_interactions(traveler_id, message)
            memory_facts: List[Dict[str, Any]] = prefs.get("facts", [])
            memory_context = self.store.format_memory_context(
                profile, session.get("turns", []), memory_facts, similar.get("interactions", [])
            )

            self._log(
                "reasoning",
                "Apply traveler context to search",
                details=f"orchestration={'llm' if llm_ok else 'direct'} · {memory_context[:240]}",
            )

            packages, search_activities = await self._search_packages(message, limit)
            activities.extend(search_activities)

            shown = [
                {"package_id": getattr(p, "product_id", None) or getattr(p, "package_id", ""), "name": p.name}
                for p in packages
            ]

            if packages:
                hint = f" for {memory_facts[0]['value']}" if memory_facts else ""
                response_message = f"Welcome back — here are {len(packages)} trips that fit your profile{hint}:"
            else:
                response_message = "No exact matches yet; I've saved this search to your history."

            await self.traveler_memory.persist_turn(
                traveler_id, conv_id, message, response_message, shown
            )

            # Mirror the turn into AgentCore Memory so the managed service has
            # an authoritative session record.  Skipped silently when the
            # adapter is not configured.
            agentcore_write = self.agentcore_memory.record_turn(
                traveler_id, conv_id, message, response_message
            )
            self._log(
                "tool_call",
                "AgentCore Memory · create_event",
                details=f"event_id={agentcore_write.get('event_id')}",
                telemetry={
                    "category": "memory_short",
                    "component": "Bedrock AgentCore Memory",
                    "status": "ok",
                    "fields": [
                        {"label": "operation", "value": "create_event"},
                        {"label": "actor_id", "value": traveler_id, "mono": True},
                        {"label": "session_id", "value": conv_id, "mono": True},
                    ],
                },
            )

            await self.store.write_audit(
                agent_name="ProductionAgent",
                operation="phase4_turn",
                traveler_id=traveler_id,
                rls_traveler=traveler_id,
                rls_agent_type="concierge_agent",
                iam_identity=scope.iam_identity,
                rows_returned=len(packages),
                transaction_id=tx,
            )

            self._log(
                "result",
                "Memory-grounded reply ready",
                details=f"{len(packages)} packages · Aurora memory updated",
                telemetry={"category": "synthesis", "component": "ProductionAgent", "status": "ok"},
            )

        self.traveler_memory._transaction_id = None
        return packages, activities, response_message, conv_id, memory_facts


def create_production_agent(activity_callback=None) -> ProductionAgent:
    return ProductionAgent(activity_callback=activity_callback)


# Back-compat aliases for older imports and docs
create_memory_agent = create_production_agent
MemoryAgent = ProductionAgent

