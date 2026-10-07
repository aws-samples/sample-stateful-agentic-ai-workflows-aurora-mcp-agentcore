"""The verdict table answers the four open questions from what the probes saw."""

import json

from scripts.gateway_harness import verdicts as v

JORDAN, DECOY = v.JORDAN, v.DECOY


def ok(traveler, **extra):
    event = {"travelerId": traveler, "note": "x", **extra}
    custom = {"bedrockAgentCoreToolName": "EchoTarget___echo"}
    text = json.dumps({"event": event, "custom": custom})
    body = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}}
    return v.classify(200, body)


def tool_error(text):
    body = {"jsonrpc": "2.0", "id": 1, "result": {"isError": True,
                                                  "content": [{"type": "text", "text": text}]}}
    return v.classify(200, body)


DENIED = tool_error("Tool Execution Denied: Tool call not allowed due to policy enforcement")
REFUSED = tool_error("Identity Check Failed: the access token carries no single traveler "
                     "for this system.")


def observations(**outcomes):
    base = {
        "jordan_names_jordan": ok(JORDAN),
        "decoy_names_jordan": ok(DECOY),
        "omitted_required": ok(DECOY),
        "extra_property": ok(DECOY, unexpectedField="x"),
        "forced_refusal": REFUSED,
    }
    return v.Observations({**base, **outcomes}, binding_policy_accepted=True,
                          listed_tools=["EchoTarget___echo"])


def table(obs):
    return {row.key: row for row in v.derive_verdicts(obs)}


def test_classify_reads_each_reply_shape():
    assert ok(JORDAN).kind == "ok" and ok(JORDAN).echo["event"]["travelerId"] == JORDAN
    assert DENIED.kind == "denied" and REFUSED.kind == "refused"
    assert v.classify(200, {"error": {"code": -32002, "message": "no"}}).kind == "denied"
    assert v.classify(200, {"error": {"code": -32602, "message": "bad"}}).kind == "error"
    assert v.classify(401, {"message": "Unauthorized"}).kind == "http_error"
    assert v.classify(200, {"result": {"content": [{"type": "text", "text": "not json"}]}}
                      ).kind == "error"
    assert tool_error("something else").kind == "error"


def test_interceptor_first_is_read_from_cedar_allowing_a_rewritten_call():
    row = table(observations())["Q1"]
    assert row.finding.startswith("Yes. Cedar saw the rewritten id")


def test_cedar_first_is_read_from_a_denial_of_the_models_id():
    obs = observations(decoy_names_jordan=DENIED)
    assert table(obs)["Q1"].finding.startswith("No. Cedar evaluated the model's id")
    assert table(obs)["Q4"].status == v.PASS


def test_the_decoy_is_kept_out_either_way_and_fails_only_if_jordans_id_arrives():
    assert table(observations())["Q4"].status == v.PASS
    leaked = table(observations(decoy_names_jordan=ok(JORDAN)))
    assert leaked["Q4"].status == v.FAIL and leaked["Q1"].status == v.FAIL


def test_revalidation_is_reported_for_both_directions():
    open_gate = table(observations())["Q2"].finding
    assert "before the interceptor: No" in open_gate and "rejected after it: No" in open_gate
    strict = table(observations(
        omitted_required=v.Outcome("error", 200, "missing travelerId"),
        extra_property=v.Outcome("error", 200, "unexpectedField not allowed"),
    ))["Q2"].finding
    assert "before the interceptor: Yes" in strict and "rejected after it: Yes" in strict


def test_the_target_view_lists_event_keys_and_context_keys():
    finding = table(observations())["Q3"].finding
    assert "keys: note, travelerId" in finding
    assert "bedrockAgentCoreToolName" in finding


def test_controls_gate_the_result():
    assert v.passed(v.derive_verdicts(observations()))
    failing = observations(jordan_names_jordan=DENIED)
    rows = table(failing)
    assert rows["C1"].status == v.FAIL and rows["Q3"].status == v.FAIL
    assert not v.passed(v.derive_verdicts(failing))
    assert table(observations(forced_refusal=DENIED))["C2"].status == v.FAIL
    missing_policy = v.Observations(observations().outcomes, binding_policy_accepted=False)
    assert table(missing_policy)["C3"].status == v.FAIL
    no_tools = v.Observations(observations().outcomes, binding_policy_accepted=True)
    assert table(no_tools)["C4"].status == v.FAIL


def test_unprobed_questions_do_not_crash_the_table():
    rows = table(v.Observations())
    assert rows["Q1"].finding == "not probed" and rows["Q4"].status == v.FAIL


def test_the_table_has_a_row_per_verdict_and_a_result_line():
    text = v.format_table(v.derive_verdicts(observations()))
    for key in ("Q1", "Q2", "Q3", "Q4", "C1", "C2", "C3", "C4"):
        assert f"\n{key} " in text
    assert text.endswith("RESULT: PASS")
    assert v.format_table(v.derive_verdicts(v.Observations())).endswith("RESULT: FAIL")
