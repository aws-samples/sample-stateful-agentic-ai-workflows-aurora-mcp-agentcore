"""
Bedrock AgentCore Runtime adapter for Phase 4.

Requires a live Runtime deployed via @aws/agentcore CLI. Calls
``invoke_agent_runtime`` on every turn with ``accept: text/event-stream`` (or, with
``MERIDIAN_AGENTCORE_AUTH=jwt``, posts to the invocation URL with the caller's bearer token) and
collects the JSON events the runtime yields: the spans for every gateway tool
call it made, the packages it found, the hold or booking it placed or was
refused, and the traveler-facing message.

AWS docs:
  - AgentCore Runtime overview:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime.html
  - invoke_agent_runtime (boto3):
    https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/invoke_agent_runtime.html
  - CLI get started:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html
"""

from __future__ import annotations

import codecs
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectionClosedError

from backend.chat_stream import emit_chat_event
from backend.concierge_voice import DirectReplyStream, direct_reply
from backend.agentcore.auth_mode import jwt_mode
from backend.agentcore.caller_credential import require_caller_token
from backend.agentcore.cli_config import resolve_agentcore_config
from backend.agentcore.errors import AgentCoreNotConfiguredError, CallerTokenExpired
from backend.agentcore.runtime_https import (
    ConnectionDropped,
    RuntimeHttpClient,
    RuntimeHttpError,
    invocation_url,
)

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
    booking: Optional[dict[str, Any]] = None
    booking_refused: Optional[str] = None
    policy_decision: Optional[str] = None
    trace_id: Optional[str] = None
    usage: dict[str, Any] = field(default_factory=dict)
    # The Runtime's own measurement of the turn. None when it reports none.
    elapsed_ms: Optional[int] = None
    isolation: str = "microVM, session-scoped CPU/memory/filesystem"


def iter_sse(chunks):
    """Decode complete SSE frames without buffering the entire Runtime response."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    pending = ""
    data = []

    def decode(lines):
        value = json.loads("\n".join(lines))
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, dict):
            raise ValueError("Runtime event must be a JSON object")
        return value

    for chunk in chunks:
        pending += decoder.decode(chunk)
        if len(pending) > 2_000_000:
            raise ValueError("Runtime event exceeded the stream limit")
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            line = line.rstrip("\r")
            if line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
            elif not line and data:
                yield decode(data)
                data = []
    pending += decoder.decode(b"", final=True)
    if pending.startswith("data:"):
        data.append(pending[5:].strip())
    if data:
        yield decode(data)


def parse_sse(raw: bytes) -> list[dict[str, Any]]:
    """Compatibility helper for saved Runtime responses."""
    return list(iter_sse([raw]))


def stream_chunks(response: dict[str, Any]):
    body = response.get("response")
    try:
        if hasattr(body, "read"):
            # Small reads avoid holding several model tokens behind a 4 KiB buffer.
            while chunk := body.read(128):
                yield chunk
        else:
            for chunk in body or []:
                if isinstance(chunk, (bytes, bytearray)):
                    yield chunk
                elif isinstance(chunk, dict):
                    yield chunk.get("chunk", {}).get("bytes") or chunk.get("bytes") or b""
    finally:
        if hasattr(body, "close"):
            body.close()


def _forward_runtime_events(response):
    raw_text, display_text = [], []
    paragraph_pending = False
    narration = DirectReplyStream()
    for event in iter_sse(stream_chunks(response)):
        if event.get("type") == "token" and isinstance(event.get("text"), str):
            text = event["text"]
            raw_text.append(text)
            if text and paragraph_pending and display_text:
                # Separate model messages on either side of an observed tool
                # step. Runtime token events otherwise concatenate them.
                previous = "".join(display_text)
                trailing = len(previous) - len(previous.rstrip("\n"))
                leading = len(text) - len(text.lstrip("\n"))
                text = "\n" * max(0, 2 - trailing - leading) + text
            if text:
                paragraph_pending = False
                display_text.append(text)
            delta = narration.feed(text)
            if delta:
                emit_chat_event({"type": "delta", "text": delta})
        elif event.get("type") == "packages" and isinstance(event.get("packages"), list):
            # Preview only IDs from observed Gateway results. The UI resolves
            # them against its live catalog; final hydration/persistence still
            # determines the completed turn. Never expose raw tool payloads.
            ids = list(dict.fromkeys(
                p["package_id"] for p in event["packages"]
                if isinstance(p, dict) and isinstance(p.get("package_id"), str)
                and p["package_id"]
            ))[:20]
            emit_chat_event({"type": "candidates", "package_ids": ids})
        elif event.get("type") == "activity":
            paragraph_pending = bool(display_text)
            # Expose a short, observed stage, never raw tool payloads or reasoning.
            emit_chat_event({"type": "status", "text": "Checking your trip options…"})
        elif event.get("type") == "result":
            # Match the same formatting in the authoritative answer. An actual
            # correction from Runtime takes precedence over provisional text.
            if raw_text and str(event.get("message") or "").strip() == "".join(raw_text).strip():
                tail = narration.feed("", final=True)
                if tail:
                    emit_chat_event({"type": "delta", "text": tail})
                event = {**event, "message": narration.visible.strip()}
            else:
                event = {**event, "message": direct_reply(str(event.get("message") or "")).strip()}
        yield event


class AgentCoreRuntimeAdapter:
    """AgentCore Runtime data-plane client — real API calls only."""

    def __init__(
        self,
        runtime_arn: Optional[str] = None,
        qualifier: Optional[str] = None,
        region: Optional[str] = None,
        http: Optional[RuntimeHttpClient] = None,
    ) -> None:
        self._http = http
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
                    # An invocation may commit a governed write before its
                    # acknowledgement is lost. Keep SDK retries disabled; only
                    # the narrowly guarded chat retry in invoke_turn is allowed.
                    retries={"total_max_attempts": 1, "mode": "standard"},
                    connect_timeout=5,
                    read_timeout=45,
                ),
            )
        return self._client

    def _invoke(self, arn: str, session_id: str, payload: bytes) -> dict[str, Any]:
        """One invocation: IAM-signed by default, with the caller's bearer token in jwt mode."""
        if not jwt_mode():
            return self._get_client().invoke_agent_runtime(
                agentRuntimeArn=arn,
                runtimeSessionId=session_id,
                payload=payload,
                qualifier=self.qualifier,
                contentType="application/json",
                accept="text/event-stream",
            )
        self._http = self._http or RuntimeHttpClient()
        return self._http.invoke(
            url=invocation_url(self.region, arn, self.qualifier),
            token=require_caller_token(),
            session_id=session_id,
            payload=payload,
        )

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
        booking_confirmed: bool = False,
        booking_target: Optional[dict[str, Any]] = None,
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
            booking_confirmed: True only when the traveler clicked Confirm on a held trip.
            booking_target: The held booking's identity and total when
                ``booking_confirmed`` is True.

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
            "booking_confirmed": bool(booking_confirmed),
            "booking_target": booking_target,
            "timestamp": _utc_timestamp(),
        }).encode()
        # A connection can close before response headers arrive, including when
        # reusing an idle connection. Retry once only when this turn cannot
        # authorize inventory writes: the runtime pins both confirmation flags
        # into tool arguments and Gateway policy denies unconfirmed writes.
        # Even a target without its confirmation flag opts out defensively.
        retry_allowed = (
            not hold_confirmed
            and hold_target is None
            and not booking_confirmed
            and booking_target is None
        )
        for attempt in range(2 if retry_allowed else 1):
            try:
                response = self._invoke(arn, session_id, payload)
                break
            except (ConnectionClosedError, ConnectionDropped):
                if not retry_allowed or attempt:
                    raise
                logger.warning(
                    "AgentCore connection closed before response; retrying "
                    "unconfirmed chat turn once"
                )
                time.sleep(0.25)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "Unknown")
                logger.error("invoke_agent_runtime failed: %s", code)
                raise RuntimeError(f"AgentCore Runtime invoke failed: {code}") from exc
            except RuntimeHttpError as exc:
                logger.error("Runtime invocation failed: %s", exc.code)
                raise
        # Once a response exists, a broken stream or runtime error must surface.
        # Replaying here could repeat work already performed by the runtime.
        return self._decision(arn, session_id, _forward_runtime_events(response))

    def _decision(self, arn: str, session_id: str, events) -> RuntimeDecision:
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
            elif kind == "booking":
                decision.booking = event.get("booking")
                decision.booking_refused = event.get("refused")
                decision.policy_decision = event.get("policyDecision")
            elif kind == "error":
                _raise_runtime_error(event)
            elif kind == "result":
                _apply_result(decision, event)
        if not decision.message:
            raise RuntimeError("AgentCore Runtime returned no concierge message.")
        return decision


def _raise_runtime_error(event: dict[str, Any]) -> None:
    """Raise what a Runtime ``error`` event means: an expired token is its own error."""
    if event.get("code") == "token_expired":
        raise CallerTokenExpired(str(event.get("message") or "The access token expired."))
    raise RuntimeError(f"AgentCore Runtime error: {event.get('message')}")


def _apply_result(decision: RuntimeDecision, event: dict[str, Any]) -> None:
    decision.message = str(event.get("message") or "").strip()
    decision.recommended_package_ids = [
        str(value) for value in event.get("recommended_package_ids") or [] if value
    ]
    decision.follow_ups = [str(value) for value in event.get("follow_ups") or [] if value]
    decision.trace_id = event.get("trace_id")
    decision.usage = dict(event.get("usage") or {})
    elapsed = event.get("elapsed_ms")
    measured = isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool)
    decision.elapsed_ms = int(elapsed) if measured else None
    if event.get("hold") and not decision.hold:
        decision.hold = event.get("hold")
    if event.get("booking") and not decision.booking:
        decision.booking = event.get("booking")


_adapter: Optional[AgentCoreRuntimeAdapter] = None


def get_agentcore_runtime() -> AgentCoreRuntimeAdapter:
    global _adapter
    if _adapter is None:
        _adapter = AgentCoreRuntimeAdapter()
    return _adapter
