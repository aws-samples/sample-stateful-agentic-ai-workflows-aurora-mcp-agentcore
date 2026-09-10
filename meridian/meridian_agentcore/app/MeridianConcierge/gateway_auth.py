"""SigV4 signing for the AgentCore Gateway MCP endpoint.

The gateway uses AWS_IAM inbound authorization, so the runtime signs every MCP
request with its own execution-role credentials. No bearer tokens, no identity
provider, no secrets in the code.
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
