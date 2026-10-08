"""Read the ``agentcore deploy --diff --json`` plan for a Gateway authorizer change.

The Gateway resource keeps the authorizer the deployed stack has (``AWS_IAM``) in every mode,
because CloudFormation cannot change a Gateway's authorizer type and compares the template with the
deployed stack template. A plan that touches the Gateway's authorizer means the render drifted from
the stack, and the deploy would fail and roll back. ``AuthorizerType`` exists only on the Gateway
resource, so any plan entry with that name is a finding. ``AuthorizerConfiguration`` also exists on
the Runtimes (those change in ``jwt`` mode on purpose), so it counts only inside a Gateway entry.

The plan format of the installed CLI is not documented offline: a parsed JSON document is walked
for those names, and any other output is scanned line by line.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

GATEWAY_TYPE = "AWS::BedrockAgentCore::Gateway"
RESOURCE_TYPE = re.compile(r"AWS::BedrockAgentCore::(\w+)")
TYPE_KEY = "AuthorizerType"
CONFIG_KEY = "AuthorizerConfiguration"
REMEDY = (
    "the render drifted from the deployed stack; re-render with python "
    "scripts/render_agentcore_config.py (the Gateway resource keeps AWS_IAM on purpose) and "
    "never move the Gateway authorizer through CloudFormation, use release_identity.py gateway"
)


def _names(node: Any, in_gateway: bool) -> set[str]:
    """The authorizer property names found in ``node``; configuration only inside a Gateway."""
    found: set[str] = set()
    if isinstance(node, dict):
        gateway = in_gateway or GATEWAY_TYPE in node or GATEWAY_TYPE in node.values()
        for key, value in node.items():
            if key == TYPE_KEY or (key == CONFIG_KEY and gateway):
                found.add(key)
            found |= _names(value, gateway or key == GATEWAY_TYPE)
    elif isinstance(node, list):
        for item in node:
            found |= _names(item, in_gateway)
    return found


def _parsed(output: str) -> Any:
    start = output.find("{")
    if start < 0:
        return None
    try:
        return json.loads(output[start:])
    except ValueError:
        return None


def _text_names(output: str) -> set[str]:
    found: set[str] = set()
    context = ""
    for line in output.splitlines():
        match = RESOURCE_TYPE.search(line)
        if match:
            context = match[1]
        if TYPE_KEY in line:
            found.add(TYPE_KEY)
        elif CONFIG_KEY in line and context == "Gateway":
            found.add(CONFIG_KEY)
    return found


def gateway_authorizer_findings(output: str) -> list[str]:
    """One finding when the plan changes the Gateway's authorizer; empty when it does not."""
    document = _parsed(output)
    names = _names(document, False) if document is not None else _text_names(output)
    if not names:
        return []
    return [f"the deploy plan changes the Gateway authorizer ({', '.join(sorted(names))}); "
            f"{REMEDY}"]


def plan_findings(code: int, output: str, argv: Sequence[str]) -> list[str]:
    """Findings for a finished diff run; a diff that failed cannot clear the deploy."""
    if code != 0:
        tail = " | ".join(line.strip() for line in output.splitlines() if line.strip())[-300:]
        return [f"{' '.join(argv)} exited {code}, so the plan cannot be checked for a Gateway "
                f"authorizer change: {tail}"]
    return gateway_authorizer_findings(output)
