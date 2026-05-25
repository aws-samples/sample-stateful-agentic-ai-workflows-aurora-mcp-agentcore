"""
Bedrock AgentCore Memory adapter for Phase 4.

The Phase 4 concierge writes every turn into AgentCore Memory and pulls
recent session context back from it.  Aurora still owns the long-term
preferences and interaction embeddings (RLS-scoped) — this module is the
managed *session* layer the abstract claims.

Configuration (.env):

    AGENTCORE_MEMORY_ID=mem-abc123             # provision via control plane
    AGENTCORE_REGION=us-east-1                 # optional, defaults to AWS_DEFAULT_REGION

When `AGENTCORE_MEMORY_ID` is unset the adapter no-ops cleanly so the demo
still runs offline.  This is the same mode used in workshops where
attendees do not have AgentCore Memory provisioned.

API references:
- create_event:           https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/create_event.html
- list_memory_records:    https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/list_memory_records.html
- retrieve_memory_records: https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/retrieve_memory_records.html
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class AgentCoreMemoryAdapter:
    """Thin wrapper around the AgentCore data-plane client.

    All methods are safe to call when no memory store is configured; they
    return empty results and log a single warning per process.
    """

    def __init__(
        self,
        memory_id: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        self.memory_id = memory_id or os.getenv("AGENTCORE_MEMORY_ID") or None
        self.region = region or os.getenv(
            "AGENTCORE_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        )
        self._client = None
        self._unavailable_reason: Optional[str] = None
        if not self.memory_id:
            self._unavailable_reason = "AGENTCORE_MEMORY_ID not set"

    # ------------------------------------------------------------------ helpers

    @property
    def configured(self) -> bool:
        return self.memory_id is not None and self._unavailable_reason is None

    @property
    def status(self) -> str:
        """Short human-readable status used in trace telemetry."""
        if self.configured:
            return "live"
        return self._unavailable_reason or "disabled"

    def _get_client(self):
        if self._client is None:
            try:
                self._client = boto3.client("bedrock-agentcore", region_name=self.region)
            except Exception as exc:
                self._unavailable_reason = f"boto3 client init failed: {exc}"
                logger.warning("AgentCore Memory unavailable: %s", self._unavailable_reason)
                return None
        return self._client

    @staticmethod
    def _namespace(traveler_id: str, conversation_id: str) -> str:
        # AgentCore filters records by namespace; encode actor + session here.
        return f"{traveler_id}/{conversation_id}"

    # -------------------------------------------------------------- write path

    def record_turn(
        self,
        traveler_id: str,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
    ) -> Dict[str, Any]:
        """Write one user/assistant turn as a single AgentCore event."""
        if not self.configured:
            return {"status": "skipped", "reason": self.status}
        client = self._get_client()
        if client is None:
            return {"status": "skipped", "reason": self.status}

        try:
            response = client.create_event(
                memoryId=self.memory_id,
                actorId=traveler_id,
                sessionId=conversation_id,
                eventTimestamp=datetime.utcnow(),
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
            return self._handle_client_error("create_event", exc)

    # --------------------------------------------------------------- read path

    def list_recent_turns(
        self,
        traveler_id: str,
        conversation_id: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Pull the most recent records AgentCore has for this session."""
        if not self.configured:
            return []
        client = self._get_client()
        if client is None:
            return []

        try:
            response = client.list_memory_records(
                memoryId=self.memory_id,
                namespace=self._namespace(traveler_id, conversation_id),
                maxResults=limit,
            )
        except ClientError as exc:
            self._handle_client_error("list_memory_records", exc)
            return []

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
        if not self.configured:
            return []
        client = self._get_client()
        if client is None:
            return []

        try:
            response = client.retrieve_memory_records(
                memoryId=self.memory_id,
                namespace=self._namespace(traveler_id, conversation_id),
                searchCriteria={"searchQuery": query, "topK": top_k},
                maxResults=top_k,
            )
        except ClientError as exc:
            self._handle_client_error("retrieve_memory_records", exc)
            return []

        return [
            {
                "text": (rec.get("content") or {}).get("text", ""),
                "score": rec.get("score"),
            }
            for rec in response.get("memoryRecordSummaries", [])
        ]

    # ----------------------------------------------------------------- errors

    def _handle_client_error(self, op: str, exc: ClientError) -> Dict[str, Any]:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code == "ResourceNotFoundException":
            self._unavailable_reason = f"AgentCore memory '{self.memory_id}' not found"
            self.memory_id = None  # disable for the rest of the process
        logger.warning("AgentCore Memory %s failed (%s): %s", op, code, exc)
        return {"status": "error", "code": code}


_adapter: Optional[AgentCoreMemoryAdapter] = None


def get_agentcore_memory() -> AgentCoreMemoryAdapter:
    global _adapter
    if _adapter is None:
        _adapter = AgentCoreMemoryAdapter()
    return _adapter
