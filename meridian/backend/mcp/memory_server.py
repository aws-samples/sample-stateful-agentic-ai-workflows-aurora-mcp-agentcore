"""
Custom MCP server: Meridian traveler memory.

Why this exists
===============

Phase 2 already shows how an agent can call Aurora through the
*public* `awslabs.postgres-mcp-server`.  That covers raw SQL.  The
abstract claims "MCP servers for contextual memory" — so this module
exposes Phase 4's traveler memory (durable preferences, conversation
history, semantic recall) over the same Model Context Protocol.

Any MCP-capable client (Strands, Claude Desktop, the LangGraph adapter,
etc.) can attach to it via stdio and call:

    - recall_traveler_profile(traveler_id)       → name + home airport + budget
    - recall_preferences(traveler_id, limit)     → durable preference rows
    - recall_recent_turns(conversation_id, limit) → short-term session history
    - semantic_recall_interactions(query, ...)   → pgvector similarity over past trips
    - persist_preference(traveler_id, key, value, confidence)
    - persist_turn(conversation_id, role, content)

Run it stand-alone (e.g. for Claude Desktop):

    AURORA_CLUSTER_ARN=... AURORA_SECRET_ARN=... \
        python -m backend.mcp.memory_server

In the live app, Phase 4 (Production) reaches memory directly through
`backend.memory.store` inside the Aurora RLS transaction — this stand-alone
server exists for external MCP hosts (e.g. Claude Desktop) that want the
same tools over the wire.

Security notes
==============

Every tool wraps its DB work in `db.scoped_session(...)`. The session first
checks that the authenticated AWS workload has an active grant for the
requested traveler. Only then does it set the Phase 4 RLS GUCs
(`app.current_traveler_id` / `app.agent_type`). The MCP server workload cannot
expand itself to an unbound traveler; that request is rejected before SQL
runs. RLS then filters every query to the workload's granted traveler.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from backend.agentcore.identity import get_agentcore_identity
from backend.memory.store import get_memory_store

logger = logging.getLogger(__name__)

mcp = FastMCP("meridian-memory")

DEFAULT_AGENT_TYPE = os.getenv("MCP_MEMORY_AGENT_TYPE", "memory_agent")


def _store():
    return get_memory_store()


def _authorization():
    return get_agentcore_identity().authorization_context()


@mcp.tool()
async def recall_traveler_profile(traveler_id: str) -> Dict[str, Any]:
    """Return the traveler's profile row (name, home airport, party size, budget, dietary notes).

    All reads are scoped via Aurora RLS to the supplied `traveler_id`.
    Returns an empty dict if the traveler does not exist or RLS rejects the row.
    """
    store = _store()
    async with store.db.scoped_session(
        traveler_id=traveler_id,
        agent_type=DEFAULT_AGENT_TYPE,
        authorization=_authorization(),
    ) as tx:
        profile = await store.recall_profile(traveler_id, transaction_id=tx)
    return profile or {}


@mcp.tool()
async def recall_preferences(traveler_id: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Return durable preference facts for the traveler, ordered by confidence."""
    store = _store()
    async with store.db.scoped_session(
        traveler_id=traveler_id,
        agent_type=DEFAULT_AGENT_TYPE,
        authorization=_authorization(),
    ) as tx:
        return await store.recall_preferences(traveler_id, limit=limit, transaction_id=tx)


@mcp.tool()
async def recall_recent_turns(
    conversation_id: str,
    traveler_id: str,
    limit: int = 6,
) -> List[Dict[str, Any]]:
    """Return the last N turns from conversation_messages.

    `traveler_id` is required because the conversation row is RLS-scoped
    to the owning traveler — passing the wrong id would return zero rows.
    """
    store = _store()
    async with store.db.scoped_session(
        traveler_id=traveler_id,
        agent_type=DEFAULT_AGENT_TYPE,
        authorization=_authorization(),
    ) as tx:
        rows = await store.recall_short_term(conversation_id, limit=limit, transaction_id=tx)
    return [
        {"role": r.get("role"), "content": r.get("content"), "created_at": str(r.get("created_at"))}
        for r in rows
    ]


@mcp.tool()
async def semantic_recall_interactions(
    traveler_id: str,
    query: str,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Return past trip interactions whose embedding is closest to the query.

    Uses cohere.embed-v4:0 (1024 dims) → pgvector cosine via Aurora.
    """
    store = _store()
    async with store.db.scoped_session(
        traveler_id=traveler_id,
        agent_type=DEFAULT_AGENT_TYPE,
        authorization=_authorization(),
    ) as tx:
        rows = await store.recall_similar_interactions(
            traveler_id, query, limit=limit, transaction_id=tx
        )
    return [
        {
            "query_text": r.get("query_text"),
            "response_summary": r.get("response_summary"),
            "similarity": float(r.get("similarity") or 0.0),
        }
        for r in rows
    ]


@mcp.tool()
async def persist_turn(
    conversation_id: str,
    traveler_id: str,
    role: str,
    content: str,
) -> Dict[str, Any]:
    """Append a single turn to conversation_messages, with embedding when possible."""
    if role not in ("user", "assistant", "system"):
        raise ValueError("role must be one of: user, assistant, system")
    store = _store()
    async with store.db.scoped_session(
        traveler_id=traveler_id,
        agent_type=DEFAULT_AGENT_TYPE,
        authorization=_authorization(),
    ) as tx:
        # ensure conversation row exists under RLS
        await store.get_or_create_conversation(
            traveler_id=traveler_id,
            conversation_id=conversation_id,
            transaction_id=tx,
        )
        message_id = await store.append_message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            with_embedding=True,
            transaction_id=tx,
        )
    return {"message_id": message_id, "conversation_id": conversation_id}


@mcp.tool()
async def persist_preference(
    traveler_id: str,
    preference_type: str,
    preference_key: str,
    preference_value: str,
    confidence: float = 0.7,
    source: str = "mcp_client",
) -> Dict[str, Any]:
    """Upsert a single durable preference fact for the traveler."""
    store = _store()
    async with store.db.scoped_session(
        traveler_id=traveler_id,
        agent_type=DEFAULT_AGENT_TYPE,
        authorization=_authorization(),
    ) as tx:
        await store.db.execute(
            """
            INSERT INTO traveler_preferences (
                traveler_id, preference_type, preference_key, preference_value,
                confidence, source, last_seen_at
            ) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (traveler_id, preference_type, preference_key)
            DO UPDATE SET
                preference_value = EXCLUDED.preference_value,
                confidence = EXCLUDED.confidence,
                source = EXCLUDED.source,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (
                traveler_id,
                preference_type,
                preference_key,
                preference_value,
                float(confidence),
                source,
            ),
            transaction_id=tx,
        )
    return {
        "ok": True,
        "traveler_id": traveler_id,
        "preference_key": preference_key,
        "stored_value": preference_value,
    }


@mcp.tool()
async def list_memory_tools() -> List[Dict[str, str]]:
    """Self-describe — what this server exposes.  Useful when an agent first connects."""
    return [
        {"name": "recall_traveler_profile", "summary": "name + home airport + budget"},
        {"name": "recall_preferences", "summary": "durable preference rows"},
        {"name": "recall_recent_turns", "summary": "last N conversation turns"},
        {"name": "semantic_recall_interactions", "summary": "pgvector similarity over past trips"},
        {"name": "persist_turn", "summary": "append a turn to conversation_messages"},
        {"name": "persist_preference", "summary": "upsert a durable preference fact"},
    ]


def main() -> None:
    logging.basicConfig(level=os.getenv("MCP_MEMORY_LOG_LEVEL", "INFO"))
    logger.info("starting meridian-memory MCP server (stdio)")
    mcp.run()


if __name__ == "__main__":
    main()
