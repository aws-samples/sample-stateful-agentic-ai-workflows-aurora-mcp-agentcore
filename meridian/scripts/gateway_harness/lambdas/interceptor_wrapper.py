"""Harness interceptor: the production interceptor, plus switches the probes need.

``HARNESS_MODE`` (changed between probes with update-function-configuration; any other value
raises ``ValueError`` rather than silently behaving like ``pin``):

- ``pin``: run the production interceptor unchanged.
- ``bad_type``: run it, then rewrite the pinned ``travelerId`` to an integer, which the echo
  tool's schema declares a string, to find out whether the Gateway checks rewritten arguments
  against the declared schema again.
- ``drop_required``: run it, then remove the pinned ``travelerId``, which the schema declares
  required, for the same question.
- ``refuse``: answer every ``tools/call`` with the production refusal, to prove an MCP client
  understands that shape.

With ``HARNESS_RECORD=1`` each event is logged with every bearer token replaced by a placeholder,
so the committed contract fixtures can be recorded from real Gateway events. The masking is by
shape, not by header name: the ``Authorization`` header, any ``Bearer <value>`` text in any field,
and any JWT-shaped string anywhere in the event.
"""

import copy
import json
import os
import re

import lambda_function as production

RECORD_MARKER = "RECORDED_EVENT "
TOKEN_PLACEHOLDER = "Bearer {{TOKEN}}"
MODES = ("pin", "bad_type", "drop_required", "refuse")
BEARER_TEXT = re.compile(r"Bearer\s+[^\s\"\\]+", re.IGNORECASE)
JWT_TEXT = re.compile(r"\bey[\w-]{6,}\.[\w-]+\.[\w-]*")
WRONG_TYPE_TRAVELER = 12345


def _mask_text(text):
    return JWT_TEXT.sub("{{JWT}}", BEARER_TEXT.sub(TOKEN_PLACEHOLDER, text))


def _mask(value):
    if isinstance(value, str):
        return _mask_text(value)
    if isinstance(value, list):
        return [_mask(item) for item in value]
    if isinstance(value, dict):
        return {key: _mask(item) for key, item in value.items()}
    return value


def redacted(event):
    """A copy of the event with the Authorization header and every token-shaped string masked."""
    clean = copy.deepcopy(event)
    request = (clean.get("mcp") or {}).get("gatewayRequest") or {} if isinstance(
        clean, dict) else {}
    headers = request.get("headers") if isinstance(request, dict) else None
    if isinstance(headers, dict):
        for name in list(headers):
            if isinstance(name, str) and name.lower() == "authorization":
                headers[name] = TOKEN_PLACEHOLDER
    return _mask(clean)


def _rewritten_arguments(output):
    request = output["mcp"].get("transformedGatewayRequest")
    if not request:
        return None
    arguments = request["body"].get("params", {}).get("arguments")
    return arguments if isinstance(arguments, dict) else None


def _bad_type(arguments):
    arguments["travelerId"] = WRONG_TYPE_TRAVELER


def _drop_required(arguments):
    arguments.pop("travelerId", None)


REWRITES = {"bad_type": _bad_type, "drop_required": _drop_required}


def _refusal_for(event):
    """The production refusal when the event is a tools/call, else None (malformed included)."""
    try:
        body = event["mcp"]["gatewayRequest"]["body"]
    except (KeyError, TypeError):
        return None
    if isinstance(body, dict) and body.get("method") == "tools/call":
        return production.refusal(body.get("id"), "claim")
    return None


def lambda_handler(event, context):
    mode = os.getenv("HARNESS_MODE", "pin")
    if mode not in MODES:
        raise ValueError(f"HARNESS_MODE must be one of {', '.join(MODES)}")
    if os.getenv("HARNESS_RECORD") == "1":
        print(RECORD_MARKER + json.dumps(redacted(event)), flush=True)
    if mode == "refuse":
        refused = _refusal_for(event)
        if refused is not None:
            return refused
    output = production.lambda_handler(event, context)
    rewrite = REWRITES.get(mode)
    arguments = _rewritten_arguments(output) if rewrite else None
    if arguments is not None:
        rewrite(arguments)
    return output
