"""One reading of a Gateway answer: the audit words for how the call ended."""

import json

import pytest

from backend.agents.phase_05_workflow.run_calls import call_of_response
from backend.audit_vocabulary import (
    BUSINESS_RULE, CODE_CLASSES, DATABASE_GUARD, ToolCall,
)

TOOL = "MeridianHolds___create_courtesy_hold"
CEDAR_DENIAL = "AuthorizeActionException - Tool Execution Denied: denied by default"


def reply(payload=None, *, text=None, is_error=False):
    body = text if text is not None else json.dumps(payload)
    return {"result": {"content": [{"type": "text", "text": body}], "isError": is_error}}


def test_an_answer_with_no_error_is_an_allowed_call_under_its_bare_name():
    assert call_of_response(TOOL, reply({"hold": {"bookingId": "b"}})) == ToolCall(
        "create_courtesy_hold", "allowed")


@pytest.mark.parametrize(("code", "kind"), sorted(CODE_CLASSES.items()))
def test_every_named_code_reads_as_its_audit_class(code, kind):
    call = call_of_response(TOOL, reply({"error": code}))
    if kind in (DATABASE_GUARD, BUSINESS_RULE):
        assert call == ToolCall("create_courtesy_hold", "refused", kind, code)
    else:
        assert call == ToolCall("create_courtesy_hold", "error", None, code)


def test_a_grant_refusal_is_a_workload_grant_refusal_only_beside_its_deny_row():
    kept = reply({"error": "traveler_not_authorized", "governance": {"access_audit_id": "authz_1"}})
    bare = reply({"error": "traveler_not_authorized", "governance": {}})
    assert call_of_response(TOOL, kept) == ToolCall(
        "create_courtesy_hold", "refused", "workload_grant", "traveler_not_authorized", "authz_1")
    assert call_of_response(TOOL, bare) == ToolCall(
        "create_courtesy_hold", "error", None, "traveler_not_authorized")


def test_a_cedar_denial_is_a_policy_refusal():
    assert call_of_response(TOOL, reply(text=CEDAR_DENIAL, is_error=True)) == ToolCall(
        "create_courtesy_hold", "refused", "policy")


def test_an_identity_refusal_keeps_the_interceptors_reason_key():
    text = "Identity Check Failed: the request carries no bearer token."
    assert call_of_response(TOOL, reply(text=text, is_error=True)) == ToolCall(
        "create_courtesy_hold", "refused", "identity_check", "no_token")


@pytest.mark.parametrize("response", [
    reply(text="AccessDeniedException: not authorized to invoke this gateway", is_error=True),
    reply(text="not json"),
    reply(text="[1, 2]"),
    {"error": {"code": -32603, "message": "internal error"}},
    reply({"error": "a code nothing classifies"}),
])
def test_anything_no_layer_claims_is_an_error_with_no_free_text_code(response):
    call = call_of_response(TOOL, response)
    assert call.result == "error" and call.refused_by is None and call.refusal_code is None
