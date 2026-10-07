"""AgentCore Gateway request interceptor: pin ``travelerId`` to the signed-in traveler.

What the Gateway is assumed to have verified before it invokes this function: the Cognito access
token's signature, issuer, app client and expiry. The token is therefore decoded here without a
signature check. The traveler comes from the token's ``traveler_id`` claim, which a
pre-token-generation trigger copied from Aurora's ``traveler_identity_bindings``. For every
``tools/call`` of a tool that takes a ``travelerId``, whatever the model or the Runtime sent is
replaced by that claim. A call that carries no usable claim is refused with a tool error the MCP
client understands; nothing reaches the target.

Cheap checks run anyway, so a request that bypassed validation still cannot get a forged traveler
through when the optional environment is configured: the token must be well-formed base64url JWT
text, carry exactly one ``Authorization: Bearer`` header (any case of the name; two are refused as
``ambiguous``), have ``token_use`` equal to ``access`` and a numeric ``exp`` in the future
(``expired``), and, when ``EXPECTED_CLIENT_ID`` or ``EXPECTED_ISSUER`` is set to a non-empty value,
a matching ``client_id`` or ``iss``. None of these replaces signature verification.

The function fails closed: any unexpected input or error becomes a refusal. It logs reason codes
and exception class names only, never a token and never a claim value.

Configuration: ``PINNED_TOOLS`` (comma-separated tool names) replaces the default Meridian set; the
throwaway-Gateway harness uses it for its echo tool. The interceptor must be configured with
``passRequestHeaders: true`` so the ``Authorization`` header is in the event.

Event and response shapes (interceptor version 1.0, MCP target):
https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors-types.html
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import time
from typing import Any, Dict, Optional

logger = logging.getLogger()
logger.setLevel(logging.INFO)

INTERCEPTOR_VERSION = "1.0"
TOOL_CALL_METHOD = "tools/call"
TRAVELER_ARGUMENT = "travelerId"
TRAVELER_CLAIM = "traveler_id"
TRAVELER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
MAX_TOKEN_CHARS = 8192
TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
BEARER_VALUE = re.compile(r"bearer +(\S+)", re.IGNORECASE)
DEFAULT_PINNED_TOOLS = (
    "MeridianHolds___create_courtesy_hold",
    "MeridianHolds___confirm_booking",
)
REFUSAL_PREFIX = "Identity Check Failed: "
REFUSALS = {
    "malformed": "the request could not be read.",
    "no_headers": "the Gateway did not pass the caller's headers to the interceptor.",
    "no_token": "the request carries no bearer token.",
    "token": "the bearer token is not an access token this system issues.",
    "ambiguous": "the request carries more than one Authorization header.",
    "expired": "the access token has expired or carries no expiry.",
    "claim": "the access token carries no single traveler for this system.",
    "internal": "the identity check could not be completed.",
}


class Refusal(Exception):
    """The call must not reach the target.

    Attributes:
        reason: A key of ``REFUSALS``. Safe to log.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def pinned_tools() -> frozenset:
    """The tool names whose ``travelerId`` is pinned."""
    configured = os.getenv("PINNED_TOOLS", "")
    names = [name.strip() for name in configured.split(",") if name.strip()]
    return frozenset(names or DEFAULT_PINNED_TOOLS)


def _bearer(headers: Any) -> str:
    if not isinstance(headers, dict):
        raise Refusal("no_headers")
    values = [
        value
        for name, value in headers.items()
        if isinstance(name, str) and name.lower() == "authorization"
    ]
    if len(values) > 1:
        raise Refusal("ambiguous")
    value = values[0] if values else None
    match = BEARER_VALUE.fullmatch(value.strip()) if isinstance(value, str) else None
    if match is None:
        raise Refusal("no_token")
    return match.group(1)


def _decode_segment(segment: str) -> Any:
    padded = segment + "=" * (-len(segment) % 4)
    raw = base64.b64decode(padded.replace("-", "+").replace("_", "/"), validate=True)
    return json.loads(raw)


def _unexpired(claims: Dict[str, Any]) -> bool:
    expiry = claims.get("exp")
    if isinstance(expiry, bool) or not isinstance(expiry, (int, float)):
        return False
    return math.isfinite(expiry) and expiry > time.time()


def _expected_values_match(claims: Dict[str, Any]) -> bool:
    for env_name, claim in (("EXPECTED_CLIENT_ID", "client_id"), ("EXPECTED_ISSUER", "iss")):
        expected = os.getenv(env_name, "")
        if expected and claims.get(claim) != expected:
            return False
    return True


def _claims(token: str) -> Dict[str, Any]:
    if len(token) > MAX_TOKEN_CHARS or not TOKEN_SHAPE.fullmatch(token):
        raise Refusal("token")
    try:
        claims = _decode_segment(token.split(".")[1])
    except ValueError as exc:
        raise Refusal("token") from exc
    if not isinstance(claims, dict) or claims.get("token_use") != "access":
        raise Refusal("token")
    if not _unexpired(claims):
        raise Refusal("expired")
    if not _expected_values_match(claims):
        raise Refusal("token")
    return claims


def traveler_from_headers(headers: Any) -> str:
    """The traveler the request's access token proves.

    Raises:
        Refusal: The header is missing, the token is not an access token, or the traveler claim is
            absent, empty, not a single string, or outside the allowed pattern.
    """
    traveler_id = _claims(_bearer(headers)).get(TRAVELER_CLAIM)
    if not isinstance(traveler_id, str) or not TRAVELER_ID_PATTERN.fullmatch(traveler_id):
        raise Refusal("claim")
    return traveler_id


def _gateway_request(event: Any) -> Dict[str, Any]:
    if not isinstance(event, dict) or event.get("interceptorInputVersion") != INTERCEPTOR_VERSION:
        raise Refusal("malformed")
    mcp = event.get("mcp")
    request = mcp.get("gatewayRequest") if isinstance(mcp, dict) else None
    if not isinstance(request, dict) or not isinstance(request.get("body"), dict):
        raise Refusal("malformed")
    return request


def _output(body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "interceptorOutputVersion": INTERCEPTOR_VERSION,
        "mcp": {"transformedGatewayRequest": {"body": body}},
    }


def refusal(request_id: Any, reason: str) -> Dict[str, Any]:
    """The interceptor output that answers the caller with a tool error and calls no target."""
    return {
        "interceptorOutputVersion": INTERCEPTOR_VERSION,
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": 200,
                "body": {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": REFUSAL_PREFIX + REFUSALS[reason]}],
                    },
                },
            }
        },
    }


def pin_traveler(event: Any) -> Dict[str, Any]:
    """Return the interceptor output for one REQUEST event.

    A request that is not a ``tools/call`` of a pinned tool passes through unchanged.

    Raises:
        Refusal: The event is malformed or the caller's traveler cannot be determined.
    """
    request = _gateway_request(event)
    body = request["body"]
    if body.get("method") != TOOL_CALL_METHOD:
        return _output(body)
    params = body.get("params")
    name = params.get("name") if isinstance(params, dict) else None
    arguments = params.get("arguments", {}) if isinstance(params, dict) else None
    if not isinstance(name, str) or not isinstance(arguments, dict):
        raise Refusal("malformed")
    if name not in pinned_tools():
        return _output(body)
    traveler_id = traveler_from_headers(request.get("headers"))
    pinned_arguments = {**arguments, TRAVELER_ARGUMENT: traveler_id}
    return _output({**body, "params": {**params, "arguments": pinned_arguments}})


def _request_id(event: Any) -> Optional[Any]:
    try:
        return event["mcp"]["gatewayRequest"]["body"].get("id")
    except (KeyError, TypeError, AttributeError):
        return None


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    """Pin the traveler or refuse. Never raises, so an error cannot let a call through."""
    try:
        return pin_traveler(event)
    except Refusal as refused:
        logger.info("refused: %s", refused.reason)
        return refusal(_request_id(event), refused.reason)
    except Exception as exc:  # noqa: BLE001 - fail closed on anything unexpected
        logger.error("interceptor failed (%s); refusing the call", type(exc).__name__)
        return refusal(_request_id(event), "internal")
