"""How the runtime authenticates to the AgentCore Gateway MCP endpoint.

``iam`` (the default): the gateway uses AWS_IAM inbound authorization, so the runtime signs every
MCP request with its own execution-role credentials. No bearer tokens, no secrets in the code.

``jwt`` (``MERIDIAN_AGENTCORE_AUTH=jwt``): the gateway uses a Cognito JWT authorizer, so every MCP
request carries the signed-in caller's access token, exactly as this runtime received it.
"""

from __future__ import annotations

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

SIGNED_HEADERS = ("content-type", "accept", "mcp-session-id", "mcp-protocol-version")
COPIED_HEADERS = ("Authorization", "X-Amz-Date", "X-Amz-Security-Token", "X-Amz-Content-SHA256")


class GatewaySigV4(httpx.Auth):
    """An httpx auth hook that signs requests for the bedrock-agentcore service."""

    requires_request_body = True

    def __init__(self, session, region: str, service: str = "bedrock-agentcore") -> None:
        self._credentials = session.get_credentials()
        self._region = region
        self._service = service

    def auth_flow(self, request: httpx.Request):
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() in SIGNED_HEADERS
        }
        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content or b"",
            headers=headers,
        )
        SigV4Auth(
            self._credentials.get_frozen_credentials(), self._service, self._region
        ).add_auth(aws_request)
        for name in COPIED_HEADERS:
            if name in aws_request.headers:
                request.headers[name] = aws_request.headers[name]
        yield request


def gateway_client_arguments(mode: str, session, region: str, token: str | None) -> dict:
    """The authentication arguments for ``MCPClient`` in the given mode.

    Raises:
        ValueError: In ``jwt`` mode with no token, which would silently fall back to no auth.
    """
    if mode != "jwt":
        return {"auth_provider": GatewaySigV4(session, region)}
    if not token:
        raise ValueError("jwt mode needs the caller's access token to call the gateway.")
    return {"headers": {"Authorization": f"Bearer {token}"}}
