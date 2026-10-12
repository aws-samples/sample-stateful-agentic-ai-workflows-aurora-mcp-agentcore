"""The runtime signs gateway MCP requests with its execution role, not a bearer token."""

from __future__ import annotations

import sys
from pathlib import Path

import boto3
import httpx

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
sys.path.insert(0, str(RUNTIME))

from gateway_auth import GatewaySigV4  # noqa: E402


def test_gateway_requests_are_sigv4_signed_for_bedrock_agentcore():
    session = boto3.Session(
        aws_access_key_id="AKIAEXAMPLE",
        aws_secret_access_key="secret",
        aws_session_token="token",
        region_name="us-east-1",
    )
    auth = GatewaySigV4(session, "us-east-1")
    request = httpx.Request(
        "POST",
        "https://x.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
        content=b'{"jsonrpc":"2.0"}',
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        },
    )
    signed = next(auth.auth_flow(request))
    assert "AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE/" in signed.headers["Authorization"]
    assert "/us-east-1/bedrock-agentcore/aws4_request" in signed.headers["Authorization"]
    assert signed.headers["X-Amz-Security-Token"] == "token"
    assert "X-Amz-Date" in signed.headers
