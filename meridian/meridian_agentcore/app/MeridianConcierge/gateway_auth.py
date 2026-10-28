"""How the runtime authenticates to the AgentCore Gateway MCP endpoint.

``iam`` (the default): the gateway uses AWS_IAM inbound authorization, so the runtime signs every
MCP request with its own execution-role credentials. No bearer tokens, no secrets in the code.

``jwt`` (``MERIDIAN_AGENTCORE_AUTH=jwt``): the gateway uses a Cognito JWT authorizer, so every MCP
request carries the signed-in caller's access token, exactly as this runtime received it.
"""

from __future__ import annotations

import json

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

JWT_URL_VARIABLE = "AGENTCORE_GATEWAY_MERIDIAN_AURORA_JWT_URL"
IAM_URL_VARIABLE = "AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL"
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


GATEWAY_REJECTIONS = (401, 403)


def is_tool_call(request: httpx.Request) -> bool:
    """Whether ``request`` is the MCP ``POST`` that calls a tool.

    The listening ``GET`` stream, the session ``DELETE`` and the other JSON-RPC posts
    (``initialize``, ``tools/list``, notifications) go through the same auth, and a status
    on them does not say that a tool call was rejected.
    """
    if request.method != "POST":
        return False
    try:
        body = json.loads(request.content)
    except (ValueError, httpx.RequestNotRead):
        return False
    return isinstance(body, dict) and body.get("method") == "tools/call"


class RecordingAuth(httpx.Auth):
    """Wrap the Gateway's auth and remember each 401 or 403 the Gateway answers a tool call with.

    The MCP client turns an HTTP rejection into "Connection to the MCP server was closed", the
    same text a dropped connection gets, so the status is the only thing that tells an expired
    token or a denied ``InvokeGateway`` from a network failure. ``httpx.Auth`` sees every
    response in both modes: ``inner`` is the SigV4 signer in ``iam`` mode and None in ``jwt``
    mode, where the bearer token travels as a header. Only a ``tools/call`` post is recorded
    (``is_tool_call``), so the body is always read. One instance serves one turn and one
    Gateway client, so every status it holds came from the Gateway.
    """

    requires_request_body = True

    def __init__(self, inner: httpx.Auth | None = None) -> None:
        self.inner = inner
        self.rejections: list[int] = []
        self.requires_response_body = bool(inner and inner.requires_response_body)

    @property
    def rejected(self) -> bool:
        """Whether the Gateway rejected any tool call of this turn with a 401 or a 403."""
        return bool(self.rejections)

    def auth_flow(self, request: httpx.Request):
        flow = self.inner.auth_flow(request) if self.inner else None
        request = next(flow) if flow else request
        while True:
            response = yield request
            if response.status_code in GATEWAY_REJECTIONS and is_tool_call(response.request):
                self.rejections.append(response.status_code)
            if flow is None:
                return
            try:
                request = flow.send(response)
            except StopIteration:
                return


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


def recording_arguments(arguments: dict) -> tuple[dict, RecordingAuth]:
    """``arguments`` for ``MCPClient`` with its auth wrapped to record Gateway rejections.

    Returns:
        The new arguments (``auth_provider`` is always set) and the recorder, which the stop
        hook reads.
    """
    recorder = RecordingAuth(arguments.get("auth_provider"))
    return {**arguments, "auth_provider": recorder}, recorder


def gateway_url(environ) -> str:
    """The Gateway URL the CDK wired into this Runtime.

    The CDK names the variable after the Gateway: the IAM Gateway's and the token Gateway's (a
    different resource, because an existing Gateway's authorizer type cannot change) differ. A
    deployed stack holds one Gateway, so exactly one is set; the token one is read first.

    Raises:
        RuntimeError: When neither variable holds a URL.
    """
    for name in (JWT_URL_VARIABLE, IAM_URL_VARIABLE):
        value = (environ.get(name) or "").strip()
        if value:
            return value
    raise RuntimeError(
        f"neither {JWT_URL_VARIABLE} nor {IAM_URL_VARIABLE} is set; the stack wires one of them "
        "into the Runtime when it deploys the Gateway")
