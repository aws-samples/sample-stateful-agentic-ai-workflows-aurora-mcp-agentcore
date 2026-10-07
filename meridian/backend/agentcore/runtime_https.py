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

import re
from typing import Any, Dict, Optional
from urllib.parse import quote, urlsplit

import httpx

from backend.agentcore.caller_claims import ensure_unexpired
from backend.agentcore.errors import AgentCoreNotConfiguredError

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
REGION_PATTERN = re.compile(r"[a-z]{2}(-[a-z]+)+-[0-9]")
RUNTIME_ARN_PATTERN = re.compile(
    r"arn:aws:bedrock-agentcore:(?P<region>[a-z0-9-]+):[0-9]{12}:runtime/[A-Za-z0-9_-]+"
)
STREAM_FAILURES = (httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError)
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


def _refuse(missing: str) -> AgentCoreNotConfiguredError:
    return AgentCoreNotConfiguredError(missing=(missing,), project_dir="", sources=())


def invocation_url(region: str, runtime_arn: str, qualifier: str) -> str:
    """The data-plane URL that invokes ``runtime_arn``.

    The bearer token goes to whatever host this returns, so the region and ARN are checked before
    they are placed in the URL: the region must be a plain AWS region name, the ARN a commercial
    partition Runtime ARN in that same region, and the result a ``*.amazonaws.com`` host.

    Raises:
        AgentCoreNotConfiguredError: The region or ARN is malformed or they disagree.
    """
    if not REGION_PATTERN.fullmatch(region or ""):
        raise _refuse("a valid AWS region name")
    match = RUNTIME_ARN_PATTERN.fullmatch(runtime_arn or "")
    if match is None or match["region"] != region:
        raise _refuse(f"a Runtime ARN in region {region}")
    host = f"bedrock-agentcore.{region}.amazonaws.com"
    url = (
        f"https://{host}/runtimes/{quote(runtime_arn, safe='')}/invocations"
        f"?qualifier={quote(qualifier, safe='')}"
    )
    if urlsplit(url).hostname != host:
        raise _refuse("an AgentCore invocation host under amazonaws.com")
    return url


class StreamBody:
    """The response body with the ``read``/``close`` surface of a boto3 streaming body."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self._chunks = response.iter_raw()
        self._pending = b""

    def read(self, size: int) -> bytes:
        """Up to ``size`` bytes; whatever has arrived, without waiting to fill ``size``."""
        if not self._pending:
            try:
                self._pending = next(self._chunks, b"")
            except STREAM_FAILURES:
                self._response.close()
                raise RuntimeHttpError("StreamError", 0) from None
        taken, self._pending = self._pending[:size], self._pending[size:]
        return taken

    def close(self) -> None:
        self._response.close()


def _require_identity_encoding(response: httpx.Response) -> None:
    """Refuse a compressed body: ``StreamBody`` reads raw bytes and the SSE parser needs text."""
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in ("", "identity"):
        response.close()
        raise RuntimeHttpError("UnsupportedContentEncoding", 200)


class RuntimeHttpClient:
    """Posts invocations with a bearer token over one pooled HTTP client.

    Args:
        transport: An ``httpx`` transport, for tests. Production uses the default.
    """

    def __init__(self, *, transport: Optional[httpx.BaseTransport] = None) -> None:
        self._client = httpx.Client(
            timeout=TIMEOUT, transport=transport, follow_redirects=False, trust_env=False
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
            "Accept-Encoding": "identity",
            SESSION_HEADER: session_id,
        })
        try:
            response = self._client.send(request, stream=True)
        except (httpx.RemoteProtocolError, httpx.ReadError) as exc:
            raise ConnectionDropped("The Runtime connection closed before a response.") from exc
        except httpx.HTTPError as exc:
            raise RuntimeHttpError("ConnectionError", 0) from exc
        if response.status_code == 200:
            _require_identity_encoding(response)
            return {"response": StreamBody(response)}
        response.close()
        if response.status_code in TOKEN_REJECTED:
            ensure_unexpired(token)
        raise RuntimeHttpError(
            STATUS_CODES.get(response.status_code, f"HTTP{response.status_code}"),
            response.status_code,
        )
