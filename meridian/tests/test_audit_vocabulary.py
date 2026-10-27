"""One vocabulary for audit rows: results, refusing layers, the decision rule and the checks."""

import pytest

from backend.audit_vocabulary import (
    CODE_CLASSES,
    IDENTITY_REFUSAL,
    IDENTITY_REFUSAL_REASONS,
    REFUSING_LAYERS,
    TURN_ERROR_LIMIT,
    ToolCall,
    authorization_decision,
    identity_refusal_code,
    tool_call_problem,
    turn_error_problem,
)
from tests.gateway_interceptor_support import load_interceptor

HOLD = "create_courtesy_hold"


def refused(layer, code=None, audit_id=None):
    return ToolCall(HOLD, "refused", layer, code, audit_id)


def test_the_layers_are_listed_in_the_order_a_call_meets_them():
    assert REFUSING_LAYERS == ("identity_check", "policy", "workload_grant", "database_guard",
                               "business_rule", "runtime_guard")


@pytest.mark.parametrize("layer", ["identity_check", "policy", "database_guard"])
def test_a_refusal_by_a_security_layer_is_a_denial(layer):
    calls = [ToolCall("get_package_details", "allowed"), refused(layer)]
    assert authorization_decision(calls) == "deny"


def test_a_workload_grant_refusal_is_a_denial():
    calls = [refused("workload_grant", "traveler_not_authorized", "authz_1")]
    assert authorization_decision(calls) == "deny"


def test_an_error_without_a_denial_is_an_error_and_a_denial_outranks_it():
    error = ToolCall("get_package_details", "error")
    assert authorization_decision([error, ToolCall(HOLD, "allowed")]) == "error"
    assert authorization_decision([error, refused("policy")]) == "deny"


@pytest.mark.parametrize("calls", [
    [],
    [ToolCall("semantic_trip_search", "allowed")],
    [refused("business_rule", "insufficient_inventory"),
     refused("runtime_guard", "write_already_attempted")],
])
def test_allowed_calls_business_refusals_and_guarded_retries_are_allowed(calls):
    assert authorization_decision(calls) == "allow"


@pytest.mark.parametrize("calls", [
    [],
    [ToolCall("semantic_trip_search", "allowed")],
    [refused("business_rule", "insufficient_inventory")],
])
def test_a_turn_that_failed_is_an_error_even_when_no_call_was(calls):
    failed = "ClientError: An error occurred (AccessDeniedException)"
    assert authorization_decision(calls, turn_error=failed) == "error"


def test_a_denial_outranks_a_turn_that_failed():
    failed = "ModelThrottledException: Too many requests"
    assert authorization_decision([refused("policy")], turn_error=failed) == "deny"


@pytest.mark.parametrize(("turn_error", "decision"), [
    (None, "allow"),
    ("CancelledError", "error"),
    ("x" * TURN_ERROR_LIMIT, "deny"),
    ("RuntimeTurnError: the turn failed", None),
])
def test_a_turn_error_the_checks_accept_passes(turn_error, decision):
    assert TURN_ERROR_LIMIT == 400
    assert turn_error_problem(turn_error, decision) is None


@pytest.mark.parametrize(("turn_error", "decision", "problem"), [
    ("", "error", "non-empty"),
    ("x" * (TURN_ERROR_LIMIT + 1), "error", "at most 400"),
    (7, "error", "non-empty"),
    ("ClientError: An error occurred (AccessDeniedException)", "allow", "'allow'"),
])
def test_a_turn_error_the_checks_refuse_is_named(turn_error, decision, problem):
    assert problem in turn_error_problem(turn_error, decision)


@pytest.mark.parametrize("call", [
    ToolCall("get_package_details", "allowed"),
    ToolCall("get_package_details", "error", refusal_code="execution_lease_lost"),
    refused("policy"),
    refused("runtime_guard", "write_already_attempted"),
    refused("workload_grant", "traveler_not_authorized", "authz_1"),
])
def test_calls_that_fit_the_vocabulary_pass(call):
    assert tool_call_problem(call) is None


@pytest.mark.parametrize(("call", "problem"), [
    ({"tool": HOLD, "result": "allowed"}, "ToolCall"),
    (ToolCall("", "allowed"), "tool"),
    (ToolCall(HOLD, "maybe"), "result"),
    (ToolCall(HOLD, "refused"), "refused_by"),
    (refused("cedar"), "refused_by"),
    (refused(""), "refused_by"),
    (ToolCall(HOLD, "allowed", "policy"), "refused_by"),
    (ToolCall(HOLD, "allowed", ""), "refused_by"),
    (ToolCall(HOLD, "error", refusal_code=""), "refusal_code"),
    (refused("workload_grant", "traveler_not_authorized"), "access_audit_id"),
    (refused("policy", None, "authz_1"), "access_audit_id"),
])
def test_calls_outside_the_vocabulary_are_named(call, problem):
    assert problem in tool_call_problem(call)


@pytest.mark.parametrize("text", [
    "Identity Check Failed: the request carries no bearer token.",
    "Tool execution failed: Identity Check Failed: the request carries no bearer token.",
])
def test_one_identity_pattern_reads_both_phases_texts(text):
    """Phase 5 sees the Gateway's text as sent; Phase 4 may see it behind the Strands wrapper."""
    assert IDENTITY_REFUSAL.match(text)


@pytest.mark.parametrize("text", [
    "Tool Execution Denied: Tool call not allowed due to policy enforcement",
    "AccessDeniedException: Identity Check Failed: quoted later in the text",
    '{"error": "traveler_not_authorized"}',
])
def test_other_texts_are_not_identity_refusals(text):
    assert not IDENTITY_REFUSAL.match(text)


# Each code classified from the code that raises it, as the Task 13 fix report's table records.
EXPECTED_CLASSES = {
    "execution_lease_lost": "error",
    "insufficient_inventory": "business_rule",
    "invalid_package_inventory": "error",
    "journey_not_owned": "database_guard",
    "traveler_scope_mismatch": "database_guard",
    "booking_agent_not_authorized": "database_guard",
    "invalid_hold_quantity": "business_rule",
    "hold_request_parameter_mismatch": "database_guard",
    "booking_not_found": "database_guard",
    "booking_amount_mismatch": "database_guard",
    "booking_not_held": "business_rule",
    "hold_expired": "business_rule",
    "hold_price_mismatch": "database_guard",
    "hold_total_mismatch": "database_guard",
    "budget_ceiling_exceeded": "business_rule",
}


def test_every_named_code_has_the_class_its_raising_code_supports():
    assert CODE_CLASSES == EXPECTED_CLASSES


def test_the_interceptor_reasons_map_back_to_its_own_keys():
    interceptor = load_interceptor()
    assert IDENTITY_REFUSAL_REASONS == {
        sentence: key for key, sentence in interceptor.REFUSALS.items()}
    assert IDENTITY_REFUSAL.match(interceptor.REFUSAL_PREFIX + interceptor.REFUSALS["internal"])


@pytest.mark.parametrize(("text", "code"), [
    ("Identity Check Failed: the identity check could not be completed.", "internal"),
    ("Tool execution failed: Identity Check Failed: the access token has expired or carries no "
     "expiry.", "expired"),
    ("Identity Check Failed:  the request carries no bearer token. ", "no_token"),
    ("Identity Check Failed: something this interceptor never says.", None),
    ("Identity Check Failed:", None),
    ("Tool Execution Denied: the identity check could not be completed.", None),
])
def test_an_identity_refusal_stores_the_interceptors_reason_key_or_nothing(text, code):
    assert identity_refusal_code(text) == code
