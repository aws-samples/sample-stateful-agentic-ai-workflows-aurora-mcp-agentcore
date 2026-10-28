"""How one Gateway call ended, in the words an audit row stores.

This reads the MCP response itself with the rules ``audit_outcome`` applies to a Phase 4 span
(``_failure`` and ``_verdict``), so one answer reads the same in both phases: a Cedar denial and
the interceptor's text name their layers from their own evidence; the holds Lambda's
``traveler_not_authorized`` is a workload-grant refusal only beside the ``access_audit_id`` of
the deny row it kept; a code in ``CODE_CLASSES`` is a refusal by its class or an error; anything
else is an error that stores no free text as a code. It imports the audit vocabulary and
``governed_hold`` and nothing else of Phase 5, so the Workflow Runtime bundle stays small.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from backend.agents.phase_05_workflow.governed_hold import envelope_failure, response_text
from backend.audit_vocabulary import (
    ALLOWED,
    BUSINESS_RULE,
    CODE_CLASSES,
    DATABASE_GUARD,
    ERROR,
    IDENTITY_CHECK,
    POLICY,
    REFUSED,
    WORKLOAD_GRANT,
    WORKLOAD_GRANT_REFUSAL,
    ToolCall,
    identity_refusal_code,
)
from backend.tools.tool_names import bare_tool_name


def call_of_response(tool: str, response: Dict[str, Any]) -> ToolCall:
    """The audit element for one ``tools/call`` response.

    Args:
        tool: The Gateway tool name, target prefix included.
        response: The JSON-RPC response the Gateway returned.

    Returns:
        A ``ToolCall`` under the bare tool name: ``allowed`` when the answer carries no error,
        ``refused`` with the layer whose own evidence refused it, else ``error``.
    """
    bare = bare_tool_name(tool)
    text = response_text(response)
    failure = envelope_failure(response, text)
    if failure is not None:
        message, layer = failure
        return _envelope_call(bare, message, layer)
    try:
        payload = json.loads(text) if text else {}
    except ValueError:
        return ToolCall(bare, ERROR)
    if not isinstance(payload, dict):
        return ToolCall(bare, ERROR)
    return _payload_call(bare, payload)


def _envelope_call(bare: str, message: str, layer: Optional[str]) -> ToolCall:
    if layer == POLICY:
        return ToolCall(bare, REFUSED, POLICY)
    if layer == IDENTITY_CHECK:
        return ToolCall(bare, REFUSED, IDENTITY_CHECK, identity_refusal_code(message))
    return ToolCall(bare, ERROR)


def _payload_call(bare: str, payload: Dict[str, Any]) -> ToolCall:
    code = payload.get("error")
    if code is None:
        return ToolCall(bare, ALLOWED)
    if code == WORKLOAD_GRANT_REFUSAL:
        governance = payload.get("governance")
        audit_id = governance.get("access_audit_id") if isinstance(governance, dict) else None
        if isinstance(audit_id, str) and audit_id:
            return ToolCall(bare, REFUSED, WORKLOAD_GRANT, code, audit_id)
        return ToolCall(bare, ERROR, None, code)
    kind = CODE_CLASSES.get(code) if isinstance(code, str) else None
    if kind in (DATABASE_GUARD, BUSINESS_RULE):
        return ToolCall(bare, REFUSED, kind, code)
    return ToolCall(bare, ERROR, None, code if kind else None)
