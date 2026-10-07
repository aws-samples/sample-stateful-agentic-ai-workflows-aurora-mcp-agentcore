"""Invoke and stop the MeridianWorkflow AgentCore Runtime.

The backend authenticates the caller and binds the traveler. Then it sends the
Runtime a typed ``workflow_turn`` built from those fields alone, so a request can
never smuggle a pause point or another traveler into the payload. The Runtime
streams heartbeats and one coded result, and this client turns the code back into
the workflow's own exception, so chat.py maps HTTP status exactly as before.

With ``MERIDIAN_AGENTCORE_AUTH=jwt`` the Runtime has a JWT authorizer, so a run or ping is posted
over HTTPS with the signed-in caller's bearer token (``runtime_https``) instead of being signed with
IAM. Stopping a session stays IAM-signed: ``StopRuntimeSession`` takes no bearer token.
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from backend.agentcore.auth_mode import jwt_mode
from backend.agentcore.caller_credential import require_caller_token
from backend.agentcore.cli_config import resolve_agentcore_config
from backend.agentcore.errors import AgentCoreNotConfiguredError, CallerTokenExpired
from backend.agentcore.runtime import iter_sse, stream_chunks
from backend.agentcore.runtime_https import RuntimeHttpClient, RuntimeHttpError, invocation_url
from backend.agents.phase_05_workflow.governed_hold import HoldOutcomeUnknown
from backend.agents.phase_05_workflow.runner import (
    WorkflowCommand,
    WorkflowConflictError,
    WorkflowRequestError,
)
from backend.agents.phase_05_workflow.state import WorkflowAuthorizationError
from backend.db.journey_store import ExecutionLeaseLostError

logger = logging.getLogger(__name__)

WORKFLOW_EVENT = "workflow_turn"
CONFLICT_RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0)
ERRORS = {
    "request": WorkflowRequestError,
    "authorization": WorkflowAuthorizationError,
    "conflict": WorkflowConflictError,
    "lease_lost": ExecutionLeaseLostError,
    "hold_unknown": HoldOutcomeUnknown,
    "token_expired": CallerTokenExpired,
}


def workflow_session_id(traveler_id: str, thread_id: str) -> str:
    """The Runtime session for one traveler's workflow thread.

    Stable per thread, so a stop followed by a resume reuses it and AgentCore
    starts a new microVM. Salted with ``phase5`` so it never names a concierge
    session.
    """
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", thread_id).strip("-_")[:24] or "thread"
    digest = hashlib.sha256(f"phase5|{traveler_id}|{thread_id}".encode("utf-8")).hexdigest()
    return f"rt-wf-{slug}-{digest[:32]}"


@dataclass(frozen=True)
class SessionStop:
    """What a stop request found.

    Attributes:
        runtime_session_id: The session the stop targeted.
        outcome: ``stopped``, or ``not_running`` when AgentCore had no live session.
    """

    runtime_session_id: str
    outcome: str


class WorkflowRuntimeClient:
    """The backend's view of MeridianWorkflow.

    Args:
        runtime_arn: The Runtime ARN; defaults to the resolved AgentCore config.
        qualifier: The endpoint; defaults to ``DEFAULT``.
        region: The AgentCore region; defaults to the resolved config.
        client: A boto3 ``bedrock-agentcore`` client, for tests.
        http: The bearer-token HTTPS client used in ``jwt`` mode, for tests.
        sleep: Waits between retries of a session that is being provisioned.
    """

    def __init__(self, runtime_arn: Optional[str] = None, *, qualifier: Optional[str] = None,
                 region: Optional[str] = None, client: Any = None,
                 http: Optional[RuntimeHttpClient] = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._http = http
        self._runtime_arn = runtime_arn
        self._qualifier = qualifier
        self._region = region
        self._client = client
        self._sleep = sleep

    def _arn(self) -> str:
        if self._runtime_arn:
            return self._runtime_arn
        cfg = resolve_agentcore_config()
        if not cfg.workflow_runtime_arn:
            raise AgentCoreNotConfiguredError(
                missing=("workflow_runtime_arn",), project_dir=str(cfg.cli_project_dir),
                sources=tuple(cfg.sources),
            )
        return cfg.workflow_runtime_arn

    def _client_for(self) -> Any:
        if self._client is None:
            cfg = resolve_agentcore_config()
            self._client = boto3.client(
                "bedrock-agentcore",
                region_name=self._region or cfg.region,
                config=Config(retries={"total_max_attempts": 1, "mode": "standard"},
                              connect_timeout=5, read_timeout=45),
            )
        return self._client

    @staticmethod
    def payload(command: WorkflowCommand) -> bytes:
        """The Runtime payload: the command's typed fields and nothing else."""
        return json.dumps({
            "event": WORKFLOW_EVENT,
            "mode": "resume" if command.resume else "start",
            "thread_id": command.thread_id,
            "traveler_id": command.traveler_id,
            "query": command.query,
            "travelers_count": command.travelers_count,
            "review_only": command.review_only,
        }).encode("utf-8")

    def _send(self, session_id: str, payload: bytes) -> Any:
        """One invocation: IAM-signed by default, with the caller's bearer token in jwt mode."""
        if not jwt_mode():
            return self._client_for().invoke_agent_runtime(
                agentRuntimeArn=self._arn(), runtimeSessionId=session_id, payload=payload,
                qualifier=self._qualifier or "DEFAULT", contentType="application/json",
                accept="text/event-stream",
            )
        self._http = self._http or RuntimeHttpClient()
        region = self._region or resolve_agentcore_config().region
        return self._http.invoke(
            url=invocation_url(region, self._arn(), self._qualifier or "DEFAULT"),
            token=require_caller_token(), session_id=session_id, payload=payload,
        )

    def _invoke(self, session_id: str, payload: bytes) -> Any:
        # Retry only RetryableConflictException: AgentCore is provisioning or
        # tearing the session down, so the request never reached the workflow.
        for delay in (*CONFLICT_RETRY_DELAYS, None):
            try:
                return self._send(session_id, payload)
            except (ClientError, RuntimeHttpError) as exc:
                code = exc.code if isinstance(exc, RuntimeHttpError) else (
                    exc.response["Error"]["Code"])
                if code != "RetryableConflictException" or delay is None:
                    logger.warning("Workflow Runtime invoke failed: code=%s session=%s",
                                   code, session_id)
                    raise RuntimeError(f"Workflow Runtime invoke failed: {code}") from exc
                self._sleep(delay)
        raise AssertionError("unreachable")

    @staticmethod
    def _result(response: Any) -> Dict[str, Any]:
        chunks = stream_chunks(response)
        try:
            for event in iter_sse(chunks):
                kind = event.get("type")
                if kind == "result":
                    return event.get("state") or {}
                if kind == "error":
                    error = ERRORS.get(event.get("code"), RuntimeError)
                    raise error(event.get("message") or "The workflow Runtime reported an error.")
        finally:
            chunks.close()
        raise RuntimeError(
            "The workflow Runtime ended without a result. Re-read the saved journey."
        )

    def _run_sync(self, session_id: str, payload: bytes) -> Dict[str, Any]:
        return self._result(self._invoke(session_id, payload))

    async def run(self, command: WorkflowCommand) -> Dict[str, Any]:
        """Run one workflow turn on the Runtime and return its public state.

        Raises:
            WorkflowRequestError, WorkflowAuthorizationError, WorkflowConflictError,
            ExecutionLeaseLostError, HoldOutcomeUnknown: As the runner raised them.
            AgentCoreNotConfiguredError: No workflow Runtime ARN is configured.
            RuntimeError: The invoke failed, or the stream ended without a result.
        """
        session_id = workflow_session_id(command.traveler_id, command.thread_id)
        return await asyncio.to_thread(self._run_sync, session_id, self.payload(command))

    async def ping(self, session_id: str) -> Dict[str, Any]:
        """Start or reach a session without touching any journey."""
        payload = json.dumps({"event": WORKFLOW_EVENT, "mode": "ping"}).encode("utf-8")
        return await asyncio.to_thread(self._run_sync, session_id, payload)

    def _stop_sync(self, session_id: str) -> SessionStop:
        try:
            self._client_for().stop_runtime_session(
                agentRuntimeArn=self._arn(), runtimeSessionId=session_id,
                qualifier=self._qualifier or "DEFAULT",
            )
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "ResourceNotFoundException":
                return SessionStop(session_id, "not_running")
            logger.warning("Workflow Runtime stop failed: code=%s session=%s", code, session_id)
            raise RuntimeError(f"Stopping the workflow Runtime session failed: {code}") from exc
        return SessionStop(session_id, "stopped")

    async def stop_session(self, traveler_id: str, thread_id: str) -> SessionStop:
        """Stop the Runtime session that runs this traveler's thread."""
        return await asyncio.to_thread(
            self._stop_sync, workflow_session_id(traveler_id, thread_id)
        )


_client: Optional[WorkflowRuntimeClient] = None


def get_workflow_runtime() -> WorkflowRuntimeClient:
    """The process-wide workflow Runtime client."""
    global _client
    if _client is None:
        _client = WorkflowRuntimeClient()
    return _client
