"""
Phase 4 — Strands concierge orchestration with traveler memory + hybrid search.
"""

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from strands import Agent
from strands.models import BedrockModel

from backend.agentcore.identity import get_agentcore_identity
from backend.agentcore.memory import get_agentcore_memory
from backend.agents.phase4.memory_agent import MemoryAgent, ActivityEntry as MemoryActivity
from backend.db.rds_data_client import get_rds_data_client
from backend.memory.store import get_memory_store

logger = logging.getLogger(__name__)


def _orchestration_mode() -> str:
    return os.getenv("STRANDS_ORCHESTRATION", "full").lower().strip()


class ConciergeOrchestrator:
    """Phase 4: recall traveler context → search → persist turn (Strands @tool)."""

    AGENT_FILE = "agents/phase4/concierge.py"

    def __init__(self, activity_callback: Optional[Callable[[MemoryActivity], Any]] = None):
        self.activity_callback = activity_callback or (lambda _: None)
        self.memory_agent = MemoryAgent(activity_callback=self.activity_callback)
        self.store = get_memory_store()
        self.db = get_rds_data_client()
        self.identity = get_agentcore_identity()
        self.agentcore_memory = get_agentcore_memory()
        self.model = BedrockModel(
            model_id="global.anthropic.claude-opus-4-7-v1",
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        )
        self.agent = Agent(
            model=self.model,
            tools=[
                self.memory_agent.recall_session_context,
                self.memory_agent.recall_traveler_preferences,
                self.memory_agent.recall_similar_interactions,
                self.memory_agent.persist_turn,
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
                agent_name=kwargs.get("agent_name", "ConciergeOrchestrator"),
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
            logger.warning("Strands memory recall failed: %s — falling back to direct calls", exc)
            self._log(
                "error",
                "Bedrock memory orchestration unavailable — direct recall path",
                details=str(exc)[:200],
            )
            return False

    async def process_turn(
        self,
        message: str,
        traveler_id: str,
        conversation_id: Optional[str],
        limit: int,
        search_fn: Callable[..., Awaitable[Tuple[List[Any], List[Any]]]],
    ) -> Tuple[List[Any], List[Any], str, str, List[Dict[str, Any]]]:
        activities: List[Any] = []

        def collect(entry: MemoryActivity) -> None:
            activities.append(entry)
            self.activity_callback(entry)

        self.memory_agent.activity_callback = collect
        mode = _orchestration_mode()

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
                "status": "ok" if scope.token_status in ("live", "unconfigured") else "delegated",
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
            self.memory_agent._transaction_id = tx

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

            self._log(
                "reasoning",
                "Concierge session start (Strands)",
                details=f"traveler={traveler_id}, conversation={conv_id}, orchestration={mode}",
                telemetry={
                    "category": "runtime",
                    "component": "Strands Agents + Aurora",
                    "status": "ok",
                    "fields": [
                        {"label": "traveler_id", "value": traveler_id, "mono": True},
                        {"label": "conversation_id", "value": conv_id, "mono": True},
                        {"label": "orchestration", "value": mode},
                    ],
                },
            )

            # AgentCore Memory: managed session store.  In configured envs it
            # returns the last few turns; in workshop mode it logs an honest
            # "unconfigured" status and we fall through to Aurora.
            agentcore_turns = self.agentcore_memory.list_recent_turns(
                traveler_id, conv_id, limit=6
            )
            self._log(
                "reasoning",
                "AgentCore Memory · recent session events",
                details=(
                    f"{len(agentcore_turns)} events"
                    if self.agentcore_memory.configured
                    else f"skipped — {self.agentcore_memory.status}"
                ),
                telemetry={
                    "category": "memory_short",
                    "component": "Bedrock AgentCore Memory",
                    "status": "ok" if self.agentcore_memory.configured else "cache_hit",
                    "memory": {
                        "shortTerm": {
                            "label": "AgentCore session events",
                            "items": [
                                (t.get("text") or "")[:120]
                                for t in agentcore_turns
                            ]
                            or [
                                f"(no events — {self.agentcore_memory.status})"
                            ],
                        }
                    },
                    "fields": [
                        {
                            "label": "memory_id",
                            "value": self.agentcore_memory.memory_id or "(unset)",
                            "mono": True,
                        },
                        {"label": "namespace", "value": f"{traveler_id}/{conv_id}"},
                    ],
                },
            )

            # Try LLM-driven recall first.  The @tool callbacks in MemoryAgent
            # populate `activities` as Bedrock invokes them; they also reuse
            # `self._transaction_id` so the RLS GUC stays in scope.
            llm_ok = False
            if mode == "full":
                llm_ok = await self._llm_driven_memory_recall(traveler_id, conv_id, message)

            # Materialize structured memory state for the search step.  If the
            # LLM already invoked the tools, these hit the same Aurora tables
            # a second time and return identical data; cost is negligible.
            session = await self.memory_agent.recall_session_context(conv_id)
            prefs = await self.memory_agent.recall_traveler_preferences(traveler_id)
            similar = await self.memory_agent.recall_similar_interactions(traveler_id, message)
            memory_facts: List[Dict[str, Any]] = prefs.get("facts", [])
            memory_context = self.store.format_memory_context(
                profile, session.get("turns", []), memory_facts, similar.get("interactions", [])
            )

            self._log(
                "reasoning",
                "Apply traveler context to search",
                details=f"orchestration={'llm' if llm_ok else 'direct'} · {memory_context[:240]}",
            )

            packages, search_activities = await search_fn(message, limit=limit)
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

            await self.memory_agent.persist_turn(
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
                details=(
                    f"event_id={agentcore_write.get('event_id')}"
                    if agentcore_write.get("status") == "ok"
                    else f"skipped — {agentcore_write.get('reason') or agentcore_write.get('code') or self.agentcore_memory.status}"
                ),
                telemetry={
                    "category": "memory_short",
                    "component": "Bedrock AgentCore Memory",
                    "status": "ok" if agentcore_write.get("status") == "ok" else "cache_hit",
                    "fields": [
                        {"label": "operation", "value": "create_event"},
                        {"label": "actor_id", "value": traveler_id, "mono": True},
                        {"label": "session_id", "value": conv_id, "mono": True},
                    ],
                },
            )

            await self.store.write_audit(
                agent_name="ConciergeOrchestrator",
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
                telemetry={"category": "synthesis", "component": "ConciergeOrchestrator", "status": "ok"},
            )

        self.memory_agent._transaction_id = None
        return packages, activities, response_message, conv_id, memory_facts


def create_concierge(activity_callback=None) -> ConciergeOrchestrator:
    return ConciergeOrchestrator(activity_callback=activity_callback)

