"""
Bedrock AgentCore Gateway adapter for Phase 4 (managed MCP).

AgentCore Gateway converts APIs, Lambda functions, and existing MCP servers
into a **single MCP endpoint** agents can call.  In Phase 4 the agent inside
AgentCore Runtime discovers and calls the gateway tools itself (hybrid trip
search, package details, the governed courtesy hold), and the gateway's Cedar
policies decide on every call.  This adapter is the laptop-side client the
backend keeps for pre-session checks and smoke tests: ``tools/list`` and a
single ``tools/call`` signed with the caller's own credentials.

Configuration (preferred — @aws/agentcore CLI):

    cd meridian/meridian_agentcore
    agentcore validate --json
    agentcore deploy -y
    cd .. && python scripts/sync_agentcore_env.py --write

Gateway URL is loaded from ``meridian_agentcore/agentcore/.cli/deployed-state.json``
or env override:

    AGENTCORE_GATEWAY_URL=https://{id}.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
    AGENTCORE_GATEWAY_ACCESS_TOKEN=   # optional; omit for IAM SigV4

With ``MERIDIAN_AGENTCORE_AUTH=jwt`` every request carries the signed-in caller's Cognito access
token (``backend.agentcore.caller_credential``) and ``AGENTCORE_GATEWAY_ACCESS_TOKEN`` is ignored.

Docs:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from backend.agentcore.auth_mode import jwt_mode
from backend.agentcore.caller_claims import ensure_unexpired
from backend.agentcore.caller_credential import require_caller_token
from backend.agentcore.cli_config import resolve_agentcore_config
from backend.agentcore.errors import AgentCoreNotConfiguredError

logger = logging.getLogger(__name__)


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Surface a 3xx as an HTTPError so the bearer token never reaches a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


NO_REDIRECT_OPENER = urllib.request.build_opener(_RefuseRedirects)


class AgentCoreGatewayAdapter:
    """MCP client for an AgentCore Gateway endpoint (tools/list + tools/call)."""

    def __init__(
        self,
        gateway_url: Optional[str] = None,
        access_token: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        cli = resolve_agentcore_config()
        raw_url = gateway_url or cli.gateway_url or ""
        self.gateway_url = raw_url.rstrip("/")
        if self.gateway_url and not self.gateway_url.endswith("/mcp"):
            self.gateway_url = f"{self.gateway_url}/mcp"

        self.access_token = access_token or os.getenv("AGENTCORE_GATEWAY_ACCESS_TOKEN")
        self.region = region or cli.region

    @property
    def configured(self) -> bool:
        return bool(self.gateway_url)

    def _require_url(self) -> str:
        if not self.gateway_url:
            cfg = resolve_agentcore_config()
            raise AgentCoreNotConfiguredError(
                missing=("gateway_url",),
                project_dir=cfg.cli_project_dir or "",
                sources=cfg.sources,
            )
        return self.gateway_url

    # ----------------------------------------------------------- MCP transport

    def _build_headers(self, body: bytes) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if jwt_mode():
            token = require_caller_token()
            ensure_unexpired(token)
            headers["Authorization"] = f"Bearer {token}"
            return headers
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
            return headers

        credentials = boto3.Session().get_credentials()
        if credentials is None:
            raise RuntimeError("No AWS credentials for SigV4 Gateway auth")
        frozen = credentials.get_frozen_credentials()
        request = AWSRequest(
            method="POST",
            url=self.gateway_url,
            data=body,
            headers=headers,
        )
        SigV4Auth(frozen, "bedrock-agentcore", self.region).add_auth(request)
        return dict(request.headers)

    def _mcp_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._require_url()
        payload = {
            "jsonrpc": "2.0",
            "id": f"meridian-{method.replace('/', '-')}",
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        body = json.dumps(payload).encode("utf-8")
        headers = self._build_headers(body)
        req = urllib.request.Request(
            self.gateway_url,
            data=body,
            headers=headers,
            method="POST",
        )
        open_request = NO_REDIRECT_OPENER.open if jwt_mode() else urllib.request.urlopen
        try:
            with open_request(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and jwt_mode():
                # Raises CallerTokenExpired when the token ran out while the call was in flight.
                ensure_unexpired(require_caller_token())
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"Gateway HTTP {exc.code}: {detail}") from exc

    # -------------------------------------------------------------- public API

    def list_tools(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """MCP ``tools/list`` against the live Gateway endpoint."""
        raw = self._mcp_request("tools/list")
        tools = (raw.get("result") or {}).get("tools") or []
        summaries = [
            {
                "name": t.get("name", ""),
                "description": (t.get("description") or "")[:120],
            }
            for t in tools
        ]
        return summaries, raw

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """MCP ``tools/call`` from the laptop, for smoke tests and pre-session checks.

        The Phase 4 turn itself never calls tools from here: the agent inside
        AgentCore Runtime discovers and calls them, and the gateway's Cedar
        policies decide on every call.
        """
        return self._mcp_request(
            "tools/call",
            params={"name": tool_name, "arguments": arguments},
        )


_adapter: Optional[AgentCoreGatewayAdapter] = None


def get_agentcore_gateway() -> AgentCoreGatewayAdapter:
    global _adapter
    if _adapter is None:
        _adapter = AgentCoreGatewayAdapter()
    return _adapter
