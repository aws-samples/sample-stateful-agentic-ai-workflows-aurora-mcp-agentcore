"""
Bedrock AgentCore Runtime adapter for Phase 4.

Requires a live Runtime deployed via @aws/agentcore CLI. Calls
``invoke_agent_runtime`` on every turn with ``accept: text/event-stream`` and
collects the JSON events the runtime yields: the spans for every gateway tool
call it made, the packages it found, the hold it placed or was refused, and the
traveler-facing message.

AWS docs:
  - AgentCore Runtime overview:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime.html
  - invoke_agent_runtime (boto3):
    https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/invoke_agent_runtime.html
  - CLI get started:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
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
    """Everything the managed runtime did on one turn."""

    runtime_arn: str
    runtime_session_id: str
    qualifier: str
    message: str
    recommended_package_ids: list[str]
    follow_ups: list[str]
    activities: list[dict[str, Any]] = field(default_factory=list)
    packages: list[dict[str, Any]] = field(default_factory=list)
    hold: Optional[dict[str, Any]] = None
    hold_refused: Optional[str] = None
    policy_decision: Optional[str] = None
    trace_id: Optional[str] = None
    usage: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0
    isolation: str = "microVM · session-scoped CPU/memory/filesystem"


def parse_sse(raw: bytes) -> list[dict[str, Any]]:
    """Decode the runtime's SSE body into the JSON objects it yielded.

    BedrockAgentCoreApp encodes each yielded string as the SSE data value, so a
    JSON object yielded by the app arrives as one additional JSON-encoded
    string layer. Both shapes are accepted.
    """
    events: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        text = line[5:].strip()
        if not text:
            continue
        value = json.loads(text)
        if isinstance(value, str):
            value = json.loads(value)
        if isinstance(value, dict):
            events.append(value)
    return events


def _read_stream(response: dict[str, Any]) -> bytes:
    body = response.get("response")
    chunks = bytearray()
    if hasattr(body, "read"):
        while True:
            chunk = body.read(4096)
            if not chunk:
                break
            chunks.extend(chunk)
        return bytes(chunks)
    for chunk in body or []:
        if isinstance(chunk, (bytes, bytearray)):
            chunks.extend(chunk)
        elif isinstance(chunk, dict):
            payload = chunk.get("chunk", {}).get("bytes") or chunk.get("bytes") or b""
            chunks.extend(payload if isinstance(payload, (bytes, bytearray)) else str(payload).encode())
        else:
            chunks.extend(str(chunk).encode())
    return bytes(chunks)


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

    @property
    def runtime_id(self) -> str:
        return (self.runtime_arn or "").rsplit("/", 1)[-1]

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
                    retries={"total_max_attempts": 3, "mode": "adaptive"},
                    connect_timeout=5,
                    read_timeout=180,
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

    def invoke_turn(
        self,
        conversation_id: str,
        traveler_id: str,
        prompt: str,
        memory_context: str,
        *,
        budget_ceiling_cents: int,
        travelers_count: int,
        hold_confirmed: bool = False,
        hold_target: Optional[dict[str, Any]] = None,
    ) -> RuntimeDecision:
        """Run one concierge turn inside AgentCore Runtime and collect its events.

        Args:
            conversation_id: Meridian conversation id; also the AgentCore Memory session.
            traveler_id: The authorized traveler; also the AgentCore Memory actor.
            prompt: The traveler's message for this turn.
            memory_context: Aurora-recalled context the backend authorized under RLS.
            budget_ceiling_cents: The ceiling the gateway policy compares a hold against.
            travelers_count: Party size for this turn.
            hold_confirmed: True only when the traveler clicked Hold on a trip.
            hold_target: The exact hold terms when ``hold_confirmed`` is True.

        Returns:
            The decision with every span, package, hold outcome and usage the runtime reported.

        Raises:
            RuntimeError: When the invoke fails or the runtime reports an error event.
        """
        arn = self._require_arn()
        session_id = self._build_runtime_session_id(conversation_id, traveler_id)
        payload = json.dumps({
            "event": "concierge_turn",
            "traveler_id": traveler_id,
            "conversation_id": conversation_id,
            "prompt": prompt,
            "memory_context": memory_context[:6000],
            "budget_ceiling_cents": int(budget_ceiling_cents),
            "travelers_count": int(travelers_count),
            "hold_confirmed": bool(hold_confirmed),
            "hold_target": hold_target,
            "timestamp": _utc_timestamp(),
        }).encode()
        try:
            response = self._get_client().invoke_agent_runtime(
                agentRuntimeArn=arn,
                runtimeSessionId=session_id,
                payload=payload,
                qualifier=self.qualifier,
                contentType="application/json",
                accept="text/event-stream",
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            logger.error("invoke_agent_runtime failed: %s", code)
            raise RuntimeError(f"AgentCore Runtime invoke failed: {code}") from exc
        return self._decision(arn, session_id, parse_sse(_read_stream(response)))

    def _decision(self, arn: str, session_id: str, events: list[dict[str, Any]]) -> RuntimeDecision:
        decision = RuntimeDecision(
            runtime_arn=arn,
            runtime_session_id=session_id,
            qualifier=self.qualifier,
            message="",
            recommended_package_ids=[],
            follow_ups=[],
        )
        for event in events:
            kind = event.get("type")
            if kind == "activity":
                decision.activities.append({k: v for k, v in event.items() if k != "type"})
            elif kind == "packages":
                decision.packages = list(event.get("packages") or [])
            elif kind == "hold":
                decision.hold = event.get("hold")
                decision.hold_refused = event.get("refused")
                decision.policy_decision = event.get("policyDecision")
            elif kind == "error":
                raise RuntimeError(f"AgentCore Runtime error: {event.get('message')}")
            elif kind == "result":
                _apply_result(decision, event)
        if not decision.message:
            raise RuntimeError("AgentCore Runtime returned no concierge message.")
        return decision


def _apply_result(decision: RuntimeDecision, event: dict[str, Any]) -> None:
    decision.message = str(event.get("message") or "").strip()
    decision.recommended_package_ids = [
        str(value) for value in event.get("recommended_package_ids") or [] if value
    ]
    decision.follow_ups = [str(value) for value in event.get("follow_ups") or [] if value]
    decision.trace_id = event.get("trace_id")
    decision.usage = dict(event.get("usage") or {})
    decision.elapsed_ms = int(event.get("elapsed_ms") or 0)
    if event.get("hold") and not decision.hold:
        decision.hold = event.get("hold")


_adapter: Optional[AgentCoreRuntimeAdapter] = None


def get_agentcore_runtime() -> AgentCoreRuntimeAdapter:
    global _adapter
    if _adapter is None:
        _adapter = AgentCoreRuntimeAdapter()
    return _adapter
