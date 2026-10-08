"""Each classifier turns a raw answer into a verdict that names the layer that answered."""

import json

import pytest

from scripts.identity_probes.classify import (
    classify_backend,
    classify_database_decoy,
    classify_database_jordan,
    classify_gateway,
    classify_runtime,
    gateway_shape,
    tool_payload,
)

IDENTITY = {"detail": "The authenticated caller is not authorized for that traveler."}
GRANT = {"detail": "aws_iam subject is not authorized for traveler trv_demo_decoy"}
WORKLOAD = {"error": "This traveler is not authorized for the current workload."}


@pytest.mark.parametrize(("status", "body", "result", "refuser"), [
    (200, {"ok": True}, "allowed", None),
    (403, IDENTITY, "refused", "backend_identity_check"),
    (403, GRANT, "refused", "workload_grant"),
    (403, WORKLOAD, "refused", "workload_grant"),
    (403, {"detail": "something else"}, "error", None),
    (403, {"error": "something else"}, "error", None),
    (403, "plain text", "error", None),
    (401, {"detail": "sign in"}, "error", None),
    (500, "boom", "error", None),
])
def test_backend_answers(status, body, result, refuser):
    verdict = classify_backend(status, body)

    assert (verdict.result, verdict.refused_by) == (result, refuser)


def test_a_backend_detail_that_is_not_text_is_an_unrecognised_check():
    assert classify_backend(403, {"detail": [{"msg": "x"}]}).result == "error"


def test_runtime_refuses_a_different_traveler():
    events = [{"type": "error", "code": "authorization",
               "message": "The request names a different traveler than the signed-in caller."}]

    verdict = classify_runtime(events)

    assert (verdict.result, verdict.refused_by) == ("refused", "runtime_traveler_check")


@pytest.mark.parametrize("message", [
    "token_use",
    "The forwarded access token was refused: traveler.",
    "The forwarded access token was refused: malformed.",
    "No signed-in caller: no bearer token was forwarded.",
])
def test_a_token_problem_is_an_error_and_never_a_traveler_refusal(message):
    events = [{"type": "error", "code": "authorization", "message": message}]

    verdict = classify_runtime(events)

    assert (verdict.result, verdict.refused_by) == ("error", None)


@pytest.mark.parametrize("events", [
    [{"type": "heartbeat"}, {"type": "result", "state": {}}],
    [{"type": "activity", "name": "start"}, {"type": "result", "message": "hello"}],
])
def test_runtime_allows_only_a_result(events):
    assert classify_runtime(events).result == "allowed"


@pytest.mark.parametrize("events", [
    [], [{"type": "heartbeat"}],
    [{"type": "activity", "name": "AgentCore Runtime: turn started"}],
    [{"type": "error", "code": "token_expired", "message": "Sign in again."}],
    [{"type": "error", "code": "internal", "message": "Reference ab12"}],
    [{"type": "error", "message": "no code"}],
])
def test_runtime_anything_else_is_an_error(events):
    assert classify_runtime(events).result == "error"


def text_result(payload, is_error=False):
    result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    if is_error:
        result["isError"] = True
    return {"result": result}


def test_tool_payload_reads_the_text_content_as_json():
    assert tool_payload(text_result({"package": {"package_id": "CTY-002"}})) == {
        "package": {"package_id": "CTY-002"}}
    assert tool_payload({"result": {"content": [{"text": "not json"}]}}) == {}
    assert tool_payload("nonsense") == {}


@pytest.mark.parametrize(("raw", "shape"), [
    ({"error": {"code": 1}}, "error"),
    (text_result({"detail": "x"}, is_error=True), "result.isError"),
    (text_result({"error": "hold_price_mismatch"}), "payload.error"),
    (text_result({"bookingId": "HLD-1"}), "success"),
    ("nope", "not_a_dict"),
])
def test_gateway_shape_names_the_form_of_the_answer(raw, shape):
    assert gateway_shape(raw) == shape


def test_gateway_allows_a_plain_result():
    verdict = classify_gateway(text_result({"bookingId": "HLD-1"}), deny_rows=0, design="both")

    assert verdict.result == "allowed"


INTERCEPTOR_TEXT = "Identity Check Failed: the access token has expired or carries no expiry."
CEDAR_TEXT = "Tool Execution Denied: Tool call not allowed due to policy enforcement"


def refusal_text(text):
    return {"result": {"isError": True, "content": [{"type": "text", "text": text}]}}


@pytest.mark.parametrize(("raw", "refuser"), [
    (refusal_text(INTERCEPTOR_TEXT), "gateway_interceptor"),
    (refusal_text(CEDAR_TEXT), "gateway_cedar"),
    (refusal_text("AuthorizeActionException - " + CEDAR_TEXT), "gateway_cedar"),
    ({"error": {"code": -32002, "message": "denied"}}, "gateway_cedar"),
])
def test_a_refusal_with_live_evidence_names_its_layer(raw, refuser):
    verdict = classify_gateway(raw, deny_rows=0, design="both")

    assert (verdict.result, verdict.refused_by) == ("refused", refuser)


@pytest.mark.parametrize("raw", [
    {"error": {"http_status": 401, "message": "Invalid Bearer token"}},
    {"error": {"http_status": 403, "message": "AccessDenied"}},
    {"error": {"http_status": 500, "message": "boom"}},
    {"error": {"code": -32603, "message": "internal"}},
    text_result({"error": "validation: bad arg"}),
    text_result({"detail": "something odd"}, is_error=True),
    refusal_text("The server is overloaded"),
    "nope",
])
def test_a_failure_that_is_not_a_recognised_refusal_is_an_error(raw):
    verdict = classify_gateway(raw, deny_rows=0, design="both")

    assert (verdict.result, verdict.refused_by) == ("error", None)


def test_a_new_deny_audit_row_means_the_holds_lambda_grant_refused():
    raw = text_result({"error": "traveler_not_authorized"})

    verdict = classify_gateway(raw, deny_rows=1, design="both")

    assert (verdict.result, verdict.refused_by) == ("refused", "gateway_workload_grant")


@pytest.mark.parametrize("raw", [
    text_result({"error": "internal error"}, is_error=True),
    text_result({"detail": "something odd"}, is_error=True),
    refusal_text("The server is overloaded"),
])
def test_a_deny_row_without_the_lambdas_own_refusal_payload_is_an_error(raw):
    verdict = classify_gateway(raw, deny_rows=1, design="both")

    assert (verdict.result, verdict.refused_by) == ("error", None)


def test_a_deny_row_does_not_turn_an_http_failure_into_a_refusal():
    raw = {"error": {"http_status": 401, "message": "Invalid Bearer token"}}

    assert classify_gateway(raw, deny_rows=1, design="both").result == "error"


def test_a_deny_row_beside_a_refusal_from_an_earlier_layer_is_contradictory():
    verdict = classify_gateway(refusal_text(CEDAR_TEXT), deny_rows=1, design="both")

    assert verdict.result == "error" and "contradict" in verdict.detail


@pytest.mark.parametrize(("design", "raw", "result"), [
    ("cedar", refusal_text(INTERCEPTOR_TEXT), "error"),
    ("cedar", refusal_text(CEDAR_TEXT), "refused"),
    ("interceptor", refusal_text(CEDAR_TEXT), "error"),
    ("interceptor", refusal_text(INTERCEPTOR_TEXT), "refused"),
    ("both", refusal_text(CEDAR_TEXT), "refused"),
    ("surprise", refusal_text(CEDAR_TEXT), "error"),
])
def test_a_refusal_must_come_from_a_layer_the_shipped_design_has(design, raw, result):
    assert classify_gateway(raw, deny_rows=0, design=design).result == result


def test_database_decoy_sees_nothing_of_jordans_rows():
    verdict = classify_database_decoy(0, 9)

    assert (verdict.result, verdict.refused_by) == ("refused", "database_rls")
    assert verdict.detail == "0 of 9 rows visible"


def test_database_decoy_seeing_rows_is_a_failure_to_refuse():
    assert classify_database_decoy(3, 9).result == "allowed"


def test_a_check_with_no_rows_to_hide_proves_nothing():
    assert classify_database_decoy(0, 0).result == "error"
    assert classify_database_jordan(0, 0).result == "error"


def test_database_jordan_sees_his_rows_and_no_more_than_exist():
    assert classify_database_jordan(9, 9).result == "allowed"
    assert classify_database_jordan(0, 9).result == "refused"
    assert classify_database_jordan(12, 9).result == "error"
