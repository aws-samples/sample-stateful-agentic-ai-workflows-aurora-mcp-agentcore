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


@pytest.mark.parametrize(("status", "body", "result", "refuser"), [
    (200, {"ok": True}, "allowed", None),
    (403, IDENTITY, "refused", "backend_identity_check"),
    (403, GRANT, "refused", "workload_grant"),
    (403, {"detail": "something else"}, "refused", "not_attributed"),
    (401, {"detail": "sign in"}, "error", None),
    (500, "boom", "error", None),
])
def test_backend_answers(status, body, result, refuser):
    verdict = classify_backend(status, body)

    assert (verdict.result, verdict.refused_by) == (result, refuser)


def test_a_backend_detail_that_is_not_text_still_classifies():
    assert classify_backend(403, {"detail": [{"msg": "x"}]}).result == "refused"


def test_runtime_refuses_a_different_traveler():
    events = [{"type": "error", "code": "authorization",
               "message": "The request names a different traveler than the signed-in caller."}]

    verdict = classify_runtime(events)

    assert (verdict.result, verdict.refused_by) == ("refused", "runtime_traveler_check")


def test_runtime_refuses_a_bad_token_by_another_name():
    events = [{"type": "error", "code": "authorization", "message": "token_use"}]

    assert classify_runtime(events).refused_by == "runtime_token_check"


@pytest.mark.parametrize("events", [
    [{"type": "heartbeat"}, {"type": "result", "state": {}}],
    [{"type": "activity", "name": "AgentCore Runtime: turn started"}],
])
def test_runtime_allows_a_result_or_a_first_activity(events):
    assert classify_runtime(events).result == "allowed"


@pytest.mark.parametrize("events", [
    [], [{"type": "heartbeat"}],
    [{"type": "error", "code": "token_expired", "message": "Sign in again."}],
    [{"type": "error", "code": "internal", "message": "Reference ab12"}],
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


@pytest.mark.parametrize("raw", [
    {"error": {"code": -32002, "message": "denied"}},
    text_result({"detail": "x"}, is_error=True),
    text_result({"error": "hold_price_mismatch"}),
])
def test_gateway_recognizes_three_shapes_of_refusal(raw):
    assert classify_gateway(raw, deny_rows=0, design="both").result == "refused"


def test_a_new_deny_audit_row_means_the_holds_lambda_grant_refused():
    raw = text_result({"detail": "not authorized"}, is_error=True)

    verdict = classify_gateway(raw, deny_rows=1, design="both")

    assert (verdict.result, verdict.refused_by) == ("refused", "gateway_workload_grant")


@pytest.mark.parametrize(("design", "text", "refuser"), [
    ("cedar", "anything", "gateway_cedar"),
    ("interceptor", "anything", "gateway_interceptor"),
    ("both", "Policy decision: DENY by Cedar", "gateway_cedar"),
    ("both", "request failed", "not_attributed"),
])
def test_without_an_audit_row_the_design_and_text_decide(design, text, refuser):
    raw = {"error": {"code": 403, "message": text}}

    assert classify_gateway(raw, deny_rows=0, design=design).refused_by == refuser


def test_a_gateway_answer_that_is_not_a_dict_is_a_refusal_without_a_reason():
    assert classify_gateway("nope", deny_rows=0, design="cedar").result == "refused"


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
