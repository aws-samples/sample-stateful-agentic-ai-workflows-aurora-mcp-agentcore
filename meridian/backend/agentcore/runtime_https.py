"""Invoke an AgentCore Runtime over HTTPS with the signed-in caller's bearer token.

A Runtime accepts either IAM SigV4 or a JWT, never both, and boto3 cannot invoke with a bearer
token, so once a Runtime has a JWT authorizer the backend posts to the invocation URL itself:

    POST https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<URL-encoded ARN>/invocations
         ?qualifier=<endpoint>
    Authorization: Bearer <access token>
    X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: <session id>

https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html

The reply is returned in the shape ``stream_chunks`` already reads (``{"response": body}`` with
``read(size)`` and ``close()``), so the SSE handling is shared with the IAM path. Errors carry the
code boto3 would have raised (``RetryableConflictException`` for 409 and so on), so the existing
retry rules apply unchanged. No message produced here contains the token.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx

from backend.agentcore.caller_claims import ensure_unexpired

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
STATUS_CODES = {
    400: "ValidationException",
    401: "UnauthorizedException",
    402: "ServiceQuotaExceededException",
    403: "AccessDeniedException",
    404: "ResourceNotFoundException",
    409: "RetryableConflictException",
    424: "RuntimeClientError",
    429: "ThrottlingException",
    500: "InternalServerException",
}
TOKEN_REJECTED = (401, 403)
TIMEOUT = httpx.Timeout(connect=5.0, read=45.0, write=10.0, pool=5.0)


class RuntimeHttpError(RuntimeError):
    """The Runtime answered with an error status.

    Attributes:
        code: The exception name boto3 would have raised for this status.
        status: The HTTP status code (0 when no response arrived).
    """

    def __init__(self, code: str, status: int) -> None:
        super().__init__(f"AgentCore Runtime invoke failed: {code}")
        self.code = code
        self.status = status


class ConnectionDropped(RuntimeError):
    """The connection closed before any response header arrived (safe to retry reads)."""


def invocation_url(region: str, runtime_arn: str, qualifier: str) -> str:
    """The data-plane URL that invokes ``runtime_arn``."""
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
        f"{quote(runtime_arn, safe='')}/invocations?qualifier={quote(qualifier, safe='')}"
    )


class StreamBody:
    """The response body with the ``read``/``close`` surface of a boto3 streaming body."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self._chunks = response.iter_raw()
        self._pending = b""

    def read(self, size: int) -> bytes:
        """Up to ``size`` bytes; whatever has arrived, without waiting to fill ``size``."""
        if not self._pending:
            self._pending = next(self._chunks, b"")
        taken, self._pending = self._pending[:size], self._pending[size:]
        return taken

    def close(self) -> None:
        self._response.close()


class RuntimeHttpClient:
    """Posts invocations with a bearer token over one pooled HTTP client.

    Args:
        transport: An ``httpx`` transport, for tests. Production uses the default.
    """

    def __init__(self, *, transport: Optional[httpx.BaseTransport] = None) -> None:
        self._client = httpx.Client(
            timeout=TIMEOUT, transport=transport, follow_redirects=False
        )

    def invoke(self, *, url: str, token: str, session_id: str, payload: bytes) -> Dict[str, Any]:
        """Start one invocation and return ``{"response": StreamBody}``.

        Raises:
            CallerTokenExpired: The token is expired, or the Runtime refused it after it expired.
            ConnectionDropped: The connection closed before headers arrived.
            RuntimeHttpError: The Runtime answered with an error status, or no answer arrived.
        """
        ensure_unexpired(token)
        request = self._client.build_request("POST", url, content=payload, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            SESSION_HEADER: session_id,
        })
        try:
            response = self._client.send(request, stream=True)
        except (httpx.RemoteProtocolError, httpx.ReadError) as exc:
            raise ConnectionDropped("The Runtime connection closed before a response.") from exc
        except httpx.HTTPError as exc:
            raise RuntimeHttpError("ConnectionError", 0) from exc
        if response.status_code == 200:
            return {"response": StreamBody(response)}
        response.close()
        if response.status_code in TOKEN_REJECTED:
            ensure_unexpired(token)
        raise RuntimeHttpError(
            STATUS_CODES.get(response.status_code, f"HTTP{response.status_code}"),
            response.status_code,
        )
