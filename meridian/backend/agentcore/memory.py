"""
Bedrock AgentCore Memory adapter for Phase 4.

Part of the Phase 4 AgentCore platform story alongside Runtime, Gateway,
and Identity.  Memory is the managed *session* layer; Aurora owns durable
traveler preferences and interaction embeddings (RLS-scoped).

Configuration (preferred — @aws/agentcore CLI):

    cd meridian/meridian_agentcore/agentcore
    agentcore add memory --name meridian_session --strategies SEMANTIC --expiry 30
    agentcore deploy -y
    cd ../.. && python scripts/sync_agentcore_env.py --write

Memory ID is loaded from ``meridian_agentcore/agentcore/.cli/deployed-state.json``
or env override:

    AGENTCORE_MEMORY_ID=mem-abc123
    AGENTCORE_REGION=us-east-1

AWS docs:
  - AgentCore Memory:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html

API references (boto3):
- create_event:           https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/create_event.html
- list_memory_records:    https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/list_memory_records.html
- retrieve_memory_records: https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/retrieve_memory_records.html
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from backend.agentcore.cli_config import resolve_agentcore_config
from backend.agentcore.errors import AgentCoreNotConfiguredError

logger = logging.getLogger(__name__)


class AgentCoreMemoryAdapter:
    """AgentCore Memory data-plane client — the managed session layer.

    Mirrors each turn to AgentCore Memory (``create_event``) and reads it back
    (``list_memory_records`` / ``retrieve_memory_records``), scoped by the
    namespace template deployed in ``agentcore.json``:
    ``/users/{actorId}/sessions/{sessionId}``. This is the short-term session
    store; Aurora remains the durable system of record for preferences and
    embeddings. Real Bedrock AgentCore APIs only — no stubs.
    """

    def __init__(
        self,
        memory_id: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        cli = resolve_agentcore_config()
        self.memory_id = memory_id or cli.memory_id or None
        self.region = region or cli.region
        self.cli_sources = cli.sources
        self._client = None

    @property
    def configured(self) -> bool:
        return self.memory_id is not None

    def _require_memory_id(self) -> str:
        if not self.memory_id:
            cfg = resolve_agentcore_config()
            raise AgentCoreNotConfiguredError(
                missing=("memory_id",),
                project_dir=cfg.cli_project_dir or "",
                sources=cfg.sources,
            )
        return self.memory_id

    def _get_client(self):
        if self._client is None:
            self._client = boto3.client("bedrock-agentcore", region_name=self.region)
        return self._client

    @staticmethod
    def _namespace(traveler_id: str, conversation_id: str) -> str:
        # Must match the deployed AgentCore Memory namespace template in
        # meridian_agentcore/agentcore/agentcore.json:
        #   /users/{actorId}/sessions/{sessionId}
        # The Runtime role's IAM policy also limits read actions to
        # /users/*/sessions/*, so a shorter "traveler/session" namespace would
        # not be authorized for list/retrieve.
        return f"/users/{traveler_id}/sessions/{conversation_id}"

    # -------------------------------------------------------------- write path

    def record_turn(
        self,
        traveler_id: str,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
    ) -> Dict[str, Any]:
        """Write one user/assistant turn via ``create_event``."""
        memory_id = self._require_memory_id()
        client = self._get_client()

        try:
            response = client.create_event(
                memoryId=memory_id,
                actorId=traveler_id,
                sessionId=conversation_id,
                eventTimestamp=datetime.now(timezone.utc),
                payload=[
                    {"conversational": {"role": "USER", "content": {"text": user_message}}},
                    {
                        "conversational": {
                            "role": "ASSISTANT",
                            "content": {"text": assistant_message},
                        }
                    },
                ],
                metadata={"namespace": {"stringValue": self._namespace(traveler_id, conversation_id)}},
            )
            event_id = response.get("event", {}).get("eventId")
            return {"status": "ok", "event_id": event_id}
        except ClientError as exc:
            raise self._client_error("create_event", exc) from exc

    # --------------------------------------------------------------- read path

    def list_recent_turns(
        self,
        traveler_id: str,
        conversation_id: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Pull the most recent records AgentCore has for this session."""
        memory_id = self._require_memory_id()
        client = self._get_client()

        try:
            response = client.list_memory_records(
                memoryId=memory_id,
                namespace=self._namespace(traveler_id, conversation_id),
                maxResults=limit,
            )
        except ClientError as exc:
            raise self._client_error("list_memory_records", exc) from exc

        rows: List[Dict[str, Any]] = []
        for rec in response.get("memoryRecordSummaries", []):
            content = rec.get("content", {})
            text = content.get("text") if isinstance(content, dict) else None
            rows.append(
                {
                    "text": text or "",
                    "score": rec.get("score"),
                    "created_at": rec.get("createdAt"),
                }
            )
        return rows

    def semantic_recall(
        self,
        traveler_id: str,
        conversation_id: str,
        query: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """AgentCore semantic recall scoped to this traveler's namespace."""
        memory_id = self._require_memory_id()
        client = self._get_client()

        try:
            response = client.retrieve_memory_records(
                memoryId=memory_id,
                namespace=self._namespace(traveler_id, conversation_id),
                searchCriteria={"searchQuery": query, "topK": top_k},
                maxResults=top_k,
            )
        except ClientError as exc:
            raise self._client_error("retrieve_memory_records", exc) from exc

        return [
            {
                "text": (rec.get("content") or {}).get("text", ""),
                "score": rec.get("score"),
            }
            for rec in response.get("memoryRecordSummaries", [])
        ]

    # ----------------------------------------------------------------- errors

    @staticmethod
    def _client_error(op: str, exc: ClientError) -> RuntimeError:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        logger.error("AgentCore Memory %s failed (%s): %s", op, code, exc)
        return RuntimeError(f"AgentCore Memory {op} failed: {code}")


_adapter: Optional[AgentCoreMemoryAdapter] = None


def get_agentcore_memory() -> AgentCoreMemoryAdapter:
    global _adapter
    if _adapter is None:
        _adapter = AgentCoreMemoryAdapter()
    return _adapter
