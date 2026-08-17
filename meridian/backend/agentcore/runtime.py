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
from typing import Any, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from backend.agentcore.cli_config import resolve_agentcore_config
from backend.agentcore.errors import AgentCoreNotConfiguredError

logger = logging.getLogger(__name__)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class RuntimeDecision:
    """Managed Runtime result for one Meridian concierge turn."""

    runtime_arn: str
    runtime_session_id: str
    qualifier: str
    isolation: str
    invoke_status: str
    message: str
    recommended_package_ids: list[str]
    follow_ups: list[str]


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
            self._client = boto3.client(
                "bedrock-agentcore",
                region_name=self.region,
                config=Config(
                    retries={"total_max_attempts": 5, "mode": "adaptive"},
                    connect_timeout=5,
                    read_timeout=90,
                ),
            )
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

    @staticmethod
    def _response_bytes(response: dict[str, Any]) -> bytes:
        chunks = response.get("response") or []
        output = bytearray()
        for chunk in chunks:
            if isinstance(chunk, (bytes, bytearray)):
                output.extend(chunk)
            elif isinstance(chunk, str):
                output.extend(chunk.encode())
            elif isinstance(chunk, dict):
                payload = chunk.get("chunk", {}).get("bytes") or chunk.get("bytes")
                if isinstance(payload, str):
                    output.extend(payload.encode())
                elif isinstance(payload, (bytes, bytearray)):
                    output.extend(payload)
        return bytes(output)

    @staticmethod
    def _parse_decision(raw: bytes) -> dict[str, Any]:
        text = raw.decode("utf-8").strip()
        if not text:
            raise RuntimeError("AgentCore Runtime returned an empty concierge decision.")
        if text.startswith("data:"):
            text = "\n".join(
                line.removeprefix("data:").strip()
                for line in text.splitlines()
                if line.startswith("data:")
            )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "AgentCore Runtime returned an invalid concierge payload."
            ) from exc
        # BedrockAgentCoreApp streams JSON over SSE. The service encodes each
        # yielded string as the SSE data value, so a JSON object yielded by the
        # app arrives as one additional JSON-encoded string layer.
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "AgentCore Runtime returned an invalid concierge payload."
                ) from exc
        if not isinstance(parsed, dict) or not str(parsed.get("message", "")).strip():
            raise RuntimeError(
                "AgentCore Runtime response is missing the concierge message."
            )
        return parsed

    def invoke_turn(
        self,
        conversation_id: str,
        traveler_id: str,
        prompt: str,
        memory_context: str,
        candidates: list[dict[str, Any]],
    ) -> RuntimeDecision:
        """Execute the Meridian concierge decision inside AgentCore Runtime."""
        arn = self._require_arn()
        session_id = self._build_runtime_session_id(conversation_id, traveler_id)
        payload = json.dumps(
            {
                "event": "concierge_turn",
                "traveler_id": traveler_id,
                "conversation_id": conversation_id,
                "prompt": prompt,
                "memory_context": memory_context[:6000],
                "candidates": candidates[:8],
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
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            logger.error("invoke_agent_runtime failed: %s", code)
            raise RuntimeError(f"AgentCore Runtime invoke failed: {code}") from exc

        parsed = self._parse_decision(self._response_bytes(response))
        return RuntimeDecision(
            runtime_arn=arn,
            runtime_session_id=session_id,
            qualifier=self.qualifier,
            isolation="microVM · session-scoped CPU/memory/filesystem",
            invoke_status="live",
            message=str(parsed["message"]).strip(),
            recommended_package_ids=[
                str(value)
                for value in parsed.get("recommended_package_ids", [])
                if value
            ],
            follow_ups=[
                str(value) for value in parsed.get("follow_ups", []) if value
            ],
        )


_adapter: Optional[AgentCoreRuntimeAdapter] = None


def get_agentcore_runtime() -> AgentCoreRuntimeAdapter:
    global _adapter
    if _adapter is None:
        _adapter = AgentCoreRuntimeAdapter()
    return _adapter
