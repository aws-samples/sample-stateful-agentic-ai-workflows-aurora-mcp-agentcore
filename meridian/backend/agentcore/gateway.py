"""
Bedrock AgentCore Gateway adapter for Phase 4 (managed MCP).

AgentCore Gateway converts APIs, Lambda functions, and existing MCP servers
into a **single MCP endpoint** agents can call.  Phase 4 uses it as the
managed tool plane for trip search — the same semantic search capability as
Phase 3, but routed through Gateway instead of inline Python.

Configuration (preferred — @aws/agentcore CLI):

    cd meridian/meridian_agentcore/agentcore
    agentcore add gateway --name meridian-aurora --authorizer-type AWS_IAM
    agentcore add gateway-target --name AuroraSearch --type mcp-server ...
    agentcore deploy -y
    agentcore fetch access --name meridian-aurora --type gateway --json
    cd ../.. && python scripts/sync_agentcore_env.py --write --fetch-gateway-token

Gateway URL is loaded from ``meridian_agentcore/agentcore/.cli/deployed-state.json``
or env override:

    AGENTCORE_GATEWAY_URL=https://{id}.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
    AGENTCORE_GATEWAY_SEARCH_TOOL=meridian-aurora___semantic_trip_search
    AGENTCORE_GATEWAY_ACCESS_TOKEN=   # optional; omit for IAM SigV4

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
from botocore.exceptions import ClientError

from backend.agentcore.cli_config import resolve_agentcore_config
from backend.agentcore.errors import AgentCoreNotConfiguredError

logger = logging.getLogger(__name__)


class AgentCoreGatewayAdapter:
    """MCP client for an AgentCore Gateway endpoint (tools/list + tools/call)."""

    DEFAULT_SEARCH_TOOL = "SemanticTripSearchLambda___semantic_trip_search"

    def __init__(
        self,
        gateway_url: Optional[str] = None,
        search_tool: Optional[str] = None,
        access_token: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        cli = resolve_agentcore_config()
        raw_url = gateway_url or cli.gateway_url or ""
        self.gateway_url = raw_url.rstrip("/")
        if self.gateway_url and not self.gateway_url.endswith("/mcp"):
            self.gateway_url = f"{self.gateway_url}/mcp"

        self.search_tool = search_tool or cli.gateway_search_tool or self.DEFAULT_SEARCH_TOOL
        self.access_token = access_token or os.getenv("AGENTCORE_GATEWAY_ACCESS_TOKEN")
        self.region = region or cli.region
        self.cli_sources = cli.sources
        self._control = None
        if not self.gateway_url:
            self._gateway_url_missing = True
        else:
            self._gateway_url_missing = False

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

    def _control_client(self):
        if self._control is None:
            self._control = boto3.client("bedrock-agentcore-control", region_name=self.region)
        return self._control

    def resolve_gateway_url_from_id(self, gateway_id: str) -> Optional[str]:
        """Look up gateway MCP URL via control plane (for provisioning scripts)."""
        try:
            response = self._control_client().get_gateway(gatewayIdentifier=gateway_id)
            gateway = response.get("gateway") or response
            url = gateway.get("gatewayUrl") or gateway.get("gatewayEndpoint")
            if url:
                return url if url.endswith("/mcp") else f"{url.rstrip('/')}/mcp"
        except ClientError as exc:
            logger.warning("get_gateway failed: %s", exc)
        return None

    # ----------------------------------------------------------- MCP transport

    def _build_headers(self, body: bytes) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
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
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
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
        """MCP ``tools/call`` — invoke one Gateway-hosted tool."""
        return self._mcp_request(
            "tools/call",
            params={"name": tool_name, "arguments": arguments},
        )

    def semantic_trip_search(
        self,
        query: str,
        limit: int = 5,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Call the Gateway search tool — real MCP tools/call to Aurora-backed search."""
        raw = self.call_tool(
            self.search_tool,
            {"query": query, "limit": limit},
        )
        packages = _extract_packages_from_mcp_result(raw)
        return packages, {"status": "ok", "tool": self.search_tool, "raw": raw}


def _extract_packages_from_mcp_result(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse MCP tools/call result into trip package dicts."""
    result = raw.get("result") or {}
    content = result.get("content") or []

    # MCP content blocks: [{type: "text", text: "..."}]
    for block in content:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if not text:
            continue
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "packages" in parsed:
                return parsed["packages"]
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            continue

    if isinstance(result, dict) and "packages" in result:
        return result["packages"]
    return []


_adapter: Optional[AgentCoreGatewayAdapter] = None


def get_agentcore_gateway() -> AgentCoreGatewayAdapter:
    global _adapter
    if _adapter is None:
        _adapter = AgentCoreGatewayAdapter()
    return _adapter
