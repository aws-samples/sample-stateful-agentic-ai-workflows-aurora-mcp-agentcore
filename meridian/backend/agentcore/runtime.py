"""
Bedrock AgentCore Runtime adapter for Phase 4.

Requires a live Runtime deployed via @aws/agentcore CLI. Calls
``invoke_agent_runtime`` on every turn — no in-process simulation.

AWS docs:
  - AgentCore Runtime overview:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime.html
  - invoke_agent_runtime (boto3):
    https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/invoke_agent_runtime.html
  - CLI get started:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html
"""

from __future__ import annotations

import json
import logging
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from backend.agentcore.cli_config import resolve_agentcore_config
from backend.agentcore.errors import AgentCoreNotConfiguredError

logger = logging.getLogger(__name__)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class RuntimeSession:
    """AgentCore Runtime session envelope for one concierge turn."""

    runtime_arn: str
    runtime_session_id: str
    qualifier: str
    isolation: str
    invoke_status: str


class AgentCoreRuntimeAdapter:
    """AgentCore Runtime data-plane client — real API calls only."""

    def __init__(
        self,
        runtime_arn: Optional[str] = None,
        qualifier: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        cli = resolve_agentcore_config()
        self.runtime_arn = runtime_arn or cli.runtime_arn
        self.qualifier = qualifier or cli.runtime_qualifier
        self.region = region or cli.region
        self.cli_sources = cli.sources
        self._client = None

    @property
    def configured(self) -> bool:
        return bool(self.runtime_arn)

    def _require_arn(self) -> str:
        if not self.runtime_arn:
            raise AgentCoreNotConfiguredError(
                missing=("runtime_arn",),
                project_dir=resolve_agentcore_config().cli_project_dir or "",
                sources=resolve_agentcore_config().sources,
            )
        return self.runtime_arn

    def _get_client(self):
        if self._client is None:
            self._client = boto3.client("bedrock-agentcore", region_name=self.region)
        return self._client

    @staticmethod
    def _build_runtime_session_id(conversation_id: str, traveler_id: str) -> str:
        """
        Build an AgentCore-compliant runtime session id.

        AgentCore validates a minimum runtimeSessionId length. Meridian conversation
        ids can be shorter, so derive a stable id with a hash suffix.
        """
        source = (conversation_id or traveler_id or "session").strip()
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", source).strip("-_")
        if not slug:
            slug = "session"
        slug = slug[:24]
        digest = hashlib.sha256(f"{traveler_id}:{conversation_id}".encode("utf-8")).hexdigest()[:32]
        return f"rt-{slug}-{digest}"

    def session_for_turn(self, conversation_id: str, traveler_id: str) -> RuntimeSession:
        """Open a Runtime session and ping ``invoke_agent_runtime``."""
        arn = self._require_arn()
        session_id = self._build_runtime_session_id(conversation_id, traveler_id)
        invoke_status = self._invoke_session_start(arn, session_id, traveler_id)
        return RuntimeSession(
            runtime_arn=arn,
            runtime_session_id=session_id,
            qualifier=self.qualifier,
            isolation="microVM · session-scoped CPU/memory/filesystem",
            invoke_status=invoke_status,
        )

    def _invoke_session_start(self, arn: str, session_id: str, traveler_id: str) -> str:
        payload = json.dumps(
            {
                "event": "concierge_session_start",
                "traveler_id": traveler_id,
                "timestamp": _utc_timestamp(),
            }
        ).encode()
        client = self._get_client()
        try:
            response = client.invoke_agent_runtime(
                agentRuntimeArn=arn,
                runtimeSessionId=session_id,
                payload=payload,
                qualifier=self.qualifier,
            )
            chunks = response.get("response") or []
            if chunks:
                _ = b"".join(
                    chunk if isinstance(chunk, (bytes, bytearray)) else chunk.encode()
                    for chunk in chunks
                )
            return "live"
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            logger.error("invoke_agent_runtime failed: %s", code)
            raise RuntimeError(f"AgentCore Runtime invoke failed: {code}") from exc


_adapter: Optional[AgentCoreRuntimeAdapter] = None


def get_agentcore_runtime() -> AgentCoreRuntimeAdapter:
    global _adapter
    if _adapter is None:
        _adapter = AgentCoreRuntimeAdapter()
    return _adapter
