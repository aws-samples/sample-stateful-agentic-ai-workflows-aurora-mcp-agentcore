"""Harness interceptor: the production interceptor, plus switches the probes need.

``HARNESS_MODE`` (changed between probes with update-function-configuration):

- ``pin``: run the production interceptor unchanged.
- ``extra_argument``: run it, then add a property the target's schema forbids, to find out whether
  the Gateway checks rewritten arguments against the schema again.
- ``refuse``: answer every ``tools/call`` with the production refusal, to prove an MCP client
  understands that shape.

With ``HARNESS_RECORD=1`` each event is logged with its bearer token replaced by a placeholder, so
the committed contract fixtures can be recorded from real Gateway events.
"""

import copy
import json
import os

import lambda_function as production

RECORD_MARKER = "RECORDED_EVENT "
TOKEN_PLACEHOLDER = "Bearer {{TOKEN}}"


def redacted(event):
    """A copy of the event with any Authorization header value replaced."""
    clean = copy.deepcopy(event)
    request = (clean.get("mcp") or {}).get("gatewayRequest") or {}
    headers = request.get("headers")
    if isinstance(headers, dict):
        for name in list(headers):
            if name.lower() == "authorization":
                headers[name] = TOKEN_PLACEHOLDER
    return clean


def _add_extra_argument(output):
    request = output["mcp"].get("transformedGatewayRequest")
    if request:
        arguments = request["body"].get("params", {}).get("arguments")
        if isinstance(arguments, dict):
            arguments["unexpectedField"] = "added-by-interceptor"
    return output


def lambda_handler(event, context):
    if os.getenv("HARNESS_RECORD") == "1":
        print(RECORD_MARKER + json.dumps(redacted(event)), flush=True)
    mode = os.getenv("HARNESS_MODE", "pin")
    body = event["mcp"]["gatewayRequest"]["body"]
    if mode == "refuse" and body.get("method") == "tools/call":
        return production.refusal(body.get("id"), "claim")
    output = production.lambda_handler(event, context)
    return _add_extra_argument(output) if mode == "extra_argument" else output
