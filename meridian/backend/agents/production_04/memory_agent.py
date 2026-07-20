"""
Phase 4 — Memory Agent (Strands @tool memory specialist).

Presenter walkthrough
---------------------
This is the file to open when explaining Strands tool support for memory:
  • Each `@tool` maps 1:1 to an Aurora table or pgvector recall path
  • Tools are registered on both this agent AND the concierge's Agent(...)
  • `_transaction_id` pins RLS scope when called inside `scoped_session`

Tables:
  • conversation_messages  — short-term session (recall_session_context)
  • traveler_preferences   — long-term facts (recall_traveler_preferences)
  • trip_interactions      — semantic recall (recall_similar_interactions)

AWS docs:
  - RDS Data API (all reads/writes):
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
  - Cohere Embed v4 (trip_interactions pgvector recall):
    https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html
  - Aurora pgvector:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraPostgreSQL.Extensions.html#AuroraPostgreSQL.Extensions.pgvector
  - RLS policies (see ``examples/rls_for_agents.sql``):
    https://www.postgresql.org/docs/current/ddl-rowsecurity.html
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from strands import Agent, tool
from strands.models import BedrockModel
from pydantic import BaseModel

from backend.config import config
from backend.memory.store import get_memory_store


class ActivityEntry(BaseModel):
    id: str
    timestamp: str
    activity_type: str
    title: str
    details: Optional[str] = None
    sql_query: Optional[str] = None
    execution_time_ms: Optional[int] = None
    agent_name: Optional[str] = None
    agent_file: Optional[str] = None
    telemetry: Optional[Dict[str, Any]] = None


class MemoryAgent:
    """
    Memory specialist with Strands @tool methods for Aurora-backed recall and persistence.
    """

    AGENT_FILE = "agents/production_04/memory_agent.py"

    def __init__(self, activity_callback: Optional[Callable[[ActivityEntry], Any]] = None):
        self.activity_callback = activity_callback or (lambda _: None)
        self.store = get_memory_store()
        # When set, every memory tool routes its SQL through this RDS Data API
        # transaction so the RLS session variables stay in scope.
        self._transaction_id: Optional[str] = None
        # The concierge prepares Bedrock embeddings before opening its short
        # RLS transactions. Stand-alone tool calls leave these unset and retain
        # the original prepare-on-demand behavior.
        self._prepared_query_vector: Optional[str] = None
        self._query_vector_prepared = False
        self._prepared_turn_vectors: Optional[Dict[str, Optional[str]]] = None
        self.model = BedrockModel(
            model_id=config.bedrock.model_id,
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        )
        self.agent = Agent(
            model=self.model,
            tools=[
                self.recall_session_context,
                self.recall_traveler_preferences,
                self.recall_similar_interactions,
                self.persist_turn,
            ],
            system_prompt=(
                "You are the Memory Agent for Meridian concierge. "
                "Use @tool methods to load session context and traveler preferences "
                "from Aurora before search, and persist turns after responding."
            ),
        )

    def _log_tool(
        self,
        tool_name: str,
        title: str,
        details: Optional[str] = None,
        sql_query: Optional[str] = None,
        execution_time_ms: Optional[int] = None,
        telemetry: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.activity_callback(
            ActivityEntry(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                activity_type="tool_call",
                title=f"Strands @tool {tool_name}",
                details=details or f"Invoked {tool_name} via strands-agents",
                sql_query=sql_query,
                execution_time_ms=execution_time_ms,
                agent_name="MemoryAgent",
                agent_file=self.AGENT_FILE,
                telemetry=telemetry,
            )
        )

    @tool
    async def recall_session_context(self, conversation_id: str, limit: int = 6) -> Dict[str, Any]:
        """
        Load short-term session memory from conversation_messages.

        Args:
            conversation_id: Active conversation identifier
            limit: Maximum recent turns to return
        """
        start = datetime.now(timezone.utc)
        rows = await self.store.recall_short_term(
            conversation_id, limit, transaction_id=self._transaction_id
        )
        items = [
            f"{r['role']}: {str(r['content'])[:100]}{'…' if len(str(r['content'])) > 100 else ''}"
            for r in reversed(rows)
        ]
        elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        self._log_tool(
            "recall_session_context",
            "Load short-term memory",
            sql_query=(
                "SELECT role, content, created_at FROM conversation_messages "
                f"WHERE conversation_id = '{conversation_id}' ORDER BY created_at DESC LIMIT {limit}"
            ),
            execution_time_ms=elapsed,
            telemetry={
                "category": "memory_short",
                "component": "Aurora · conversation_messages",
                "status": "ok",
                "memory": {
                    "shortTerm": {
                        "label": "Session context (Aurora)",
                        "items": items or ["(no prior turns in this session)"],
                    },
                },
                "fields": [
                    {"label": "store", "value": "conversation_messages"},
                    {"label": "turns", "value": str(len(rows))},
                ],
            },
        )
        return {"turns": rows, "items": items}

    @tool
    async def recall_traveler_preferences(self, traveler_id: str, limit: int = 8) -> Dict[str, Any]:
        """Recall long-term traveler preferences from traveler_preferences."""
        start = datetime.now(timezone.utc)
        facts = await self.store.recall_preferences(
            traveler_id, limit, transaction_id=self._transaction_id
        )
        elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        self._log_tool(
            "recall_traveler_preferences",
            "Recall traveler preferences",
            sql_query=(
                "SELECT preference_type, preference_key, preference_value, confidence "
                f"FROM traveler_preferences WHERE traveler_id = '{traveler_id}' "
                f"ORDER BY confidence DESC LIMIT {limit}"
            ),
            execution_time_ms=elapsed,
            telemetry={
                "category": "memory_long",
                "component": "Aurora · traveler_preferences",
                "status": "ok",
                "memory": {"longTerm": {"label": "Traveler preferences", "facts": facts}},
                "fields": [{"label": "table", "value": "traveler_preferences"}],
            },
        )
        return {"facts": facts}

    @tool
    async def recall_similar_interactions(
        self, traveler_id: str, query: str, limit: int = 3
    ) -> Dict[str, Any]:
        """
        Semantic recall of similar past interactions via pgvector.

        Args:
            traveler_id: Traveler identifier (e.g. trv_meridian_demo)
            query: Current user query for embedding similarity
            limit: Maximum interactions to return
        """
        start = datetime.now(timezone.utc)
        rows = await self.store.recall_similar_interactions(
            traveler_id,
            query,
            limit,
            transaction_id=self._transaction_id,
            query_vector=self._prepared_query_vector,
            embedding_prepared=self._query_vector_prepared,
        )
        elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        self._log_tool(
            "recall_similar_interactions",
            "Semantic interaction recall",
            sql_query=(
                "SELECT query_text, response_summary, "
                "1 - (embedding <=> query_vector) AS similarity "
                f"FROM trip_interactions WHERE traveler_id = '{traveler_id}' "
                f"ORDER BY embedding <=> query_vector LIMIT {limit}"
            ),
            execution_time_ms=elapsed,
            telemetry={
                "category": "memory_long",
                "component": "Aurora · interaction_embeddings",
                "status": "ok" if rows else "cache_hit",
                "fields": [
                    {"label": "index", "value": "HNSW on embedding"},
                    {"label": "hits", "value": str(len(rows))},
                ],
            },
        )
        return {"interactions": rows}

    @tool
    async def persist_turn(
        self,
        traveler_id: str,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
        packages_shown: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Persist the current turn to short-term and semantic memory in Aurora.

        Args:
            traveler_id: Traveler identifier
            conversation_id: Active conversation
            user_message: User utterance
            assistant_message: Assistant response summary
            packages_shown: Optional list of trip packages shown this turn
        """
        start = datetime.now(timezone.utc)
        tx = self._transaction_id
        vectors = self._prepared_turn_vectors
        embeddings_prepared = vectors is not None
        await self.store.append_message(
            conversation_id,
            "user",
            user_message,
            transaction_id=tx,
            embedding_vector=vectors.get("user") if vectors else None,
            embedding_prepared=embeddings_prepared,
        )
        await self.store.append_message(
            conversation_id,
            "assistant",
            assistant_message,
            transaction_id=tx,
            embedding_vector=vectors.get("assistant") if vectors else None,
            embedding_prepared=embeddings_prepared,
        )
        interaction_id = await self.store.persist_interaction(
            traveler_id,
            conversation_id,
            user_message,
            assistant_message,
            packages_shown,
            transaction_id=tx,
            embedding_vector=vectors.get("interaction") if vectors else None,
            embedding_prepared=embeddings_prepared,
        )
        elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        self._log_tool(
            "persist_turn",
            "Persist turn to Aurora memory",
            details=f"Wrote messages + interaction {interaction_id}",
            sql_query=(
                "INSERT INTO conversation_messages …; INSERT INTO trip_interactions …"
            ),
            execution_time_ms=elapsed,
            telemetry={
                "category": "memory_short",
                "component": "Aurora write path",
                "status": "ok",
                "fields": [
                    {"label": "interaction_id", "value": interaction_id, "mono": True},
                    {"label": "conversation_id", "value": conversation_id, "mono": True},
                ],
            },
        )
        return {"interaction_id": interaction_id, "status": "persisted"}


def create_memory_agent(
    activity_callback: Optional[Callable[[ActivityEntry], Any]] = None,
) -> MemoryAgent:
    return MemoryAgent(activity_callback=activity_callback)


# Backward-compatible aliases for older imports.
TravelerMemoryAgent = MemoryAgent
create_traveler_memory_agent = create_memory_agent
