"""The verdict table answers the four open questions from what the probes saw."""

import json

import pytest

from scripts.gateway_harness import verdicts as v

JORDAN, DECOY = v.JORDAN, v.DECOY


def ok(traveler, **extra):
    event = {"travelerId": traveler, "note": "x", **extra}
    custom = {"bedrockAgentCoreToolName": "EchoTarget___echo"}
    text = json.dumps({"event": event, "custom": custom})
    body = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}}
    return v.classify(200, body)


def echoed(event):
    text = json.dumps({"event": event, "custom": {}})
    return v.classify(200, {"result": {"content": [{"type": "text", "text": text}]}})


BAD_TYPE_ACCEPTED = echoed({"travelerId": 12345, "note": "x"})
DROP_ACCEPTED = echoed({"note": "x"})


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
        "bad_type": BAD_TYPE_ACCEPTED,
        "drop_required": DROP_ACCEPTED,
        "forced_refusal": REFUSED,
        "cedar_alone_control": ok(JORDAN),
        "cedar_alone": DENIED,
    }
    return v.Observations({**base, **outcomes}, binding_policy_accepted=True,
                          listed_tools=["EchoTarget___echo"], interceptors_after_detach=0)


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


def test_revalidation_is_reported_for_each_probe():
    open_gate = table(observations())["Q2"].finding
    assert "before the interceptor: No" in open_gate
    assert "wrong type is rejected after it: No" in open_gate
    assert "removed by the interceptor is rejected after it: No" in open_gate
    strict = table(observations(
        omitted_required=v.Outcome("error", 200, "missing travelerId"),
        bad_type=v.Outcome("error", 200, "travelerId must be a string"),
        drop_required=v.Outcome("error", 200, "required property travelerId"),
    ))["Q2"]
    assert "before the interceptor: Yes" in strict.finding
    assert "wrong type is rejected after it: Yes" in strict.finding
    assert "removed by the interceptor is rejected after it: Yes" in strict.finding
    assert strict.status == v.INFO


def test_q2_says_an_accepted_probe_does_not_prove_additional_properties_is_enforced():
    finding = table(observations())["Q2"].finding
    assert "does not show that additionalProperties:false is enforced" in finding
    assert "whether re-validation happens at all" in finding


def test_one_rejected_rewrite_is_enough_to_show_the_gateway_revalidates():
    row = table(observations(bad_type=v.Outcome("error", 200, "schema validation failed")))["Q2"]
    assert "wrong type is rejected after it: Yes" in row.finding and row.status == v.INFO


def test_an_accepted_rewrite_that_did_not_arrive_as_rewritten_is_inconclusive():
    row = table(observations(bad_type=ok(DECOY), drop_required=ok(DECOY)))["Q2"]
    assert row.finding.count("inconclusive") == 2 and row.status == v.UNKNOWN


def test_cedar_alone_is_informative_and_never_fails_the_run():
    denied = table(observations())["Q5"]
    assert denied.status == v.INFO and denied.finding.startswith("Yes. Cedar denied")
    leaked = table(observations(cedar_alone=ok(JORDAN)))["Q5"]
    assert leaked.status == v.INFO and leaked.finding.startswith("No. Cedar did not stop")
    assert v.passed(v.derive_verdicts(observations(cedar_alone=ok(JORDAN))))


def test_cedar_alone_reports_an_unexpected_outcome_without_deciding():
    odd = table(observations(cedar_alone=v.Outcome("http_error", 502, "bad gateway")))["Q5"]
    assert odd.status == v.UNKNOWN and odd.finding.startswith("Inconclusive: http_error 502")
    missing = observations()
    del missing.outcomes["cedar_alone"]
    assert table(missing)["Q5"].finding == "not probed"


@pytest.mark.parametrize("control", [
    v.Outcome("error", 200, "boom"), v.Outcome("http_error", 0, "transport failure"),
    v.Outcome("refused", 200, "Identity Check Failed: x"), ok(DECOY), echoed({"note": "x"}),
    v.Outcome("ok", 200, echo=None), None])
def test_a_denial_without_a_working_off_mode_control_is_inconclusive(control):
    obs = observations()
    if control is None:
        del obs.outcomes["cedar_alone_control"]
    else:
        obs.outcomes["cedar_alone_control"] = control
    row = table(obs)["Q5"]
    assert row.status == v.UNKNOWN
    assert row.finding == "Inconclusive: the off-mode control did not reach the target"


def test_a_denial_with_other_32002_text_is_not_cedars():
    other = v.Outcome("denied", 200, "some other -32002 text")
    row = table(observations(cedar_alone=other))["Q5"]
    assert row.status == v.UNKNOWN and row.finding.startswith("Inconclusive")
    exact = v.classify(200, {"error": {"code": -32002, "message": "Tool Execution Denied: x"}})
    assert table(observations(cedar_alone=exact))["Q5"].finding.startswith("Yes.")


@pytest.mark.parametrize("outcome", [
    v.Outcome("refused", 200, "Identity Check Failed: x"), ok(DECOY),
    v.Outcome("ok", 200, echo=None)])
def test_other_cedar_alone_outcomes_are_inconclusive(outcome):
    row = table(observations(cedar_alone=outcome))["Q5"]
    assert row.status == v.UNKNOWN and row.finding.startswith("Inconclusive")


def test_inconclusive_q5_is_distinguishable_from_yes_and_no_yet_does_not_fail_a_pass():
    yes, no = table(observations())["Q5"], table(observations(cedar_alone=ok(JORDAN)))["Q5"]
    odd = observations(cedar_alone=v.Outcome("error", 200, "x"))
    inconclusive = table(odd)["Q5"]
    assert inconclusive.status not in (yes.status, no.status)
    assert v.passed(v.derive_verdicts(odd)), "Q5 only picks between fallbacks after a failure"
    failing = observations(cedar_alone=v.Outcome("error", 200, "x"),
                           decoy_names_jordan=ok(JORDAN))
    assert not v.passed(v.derive_verdicts(failing))


def test_detaching_must_leave_no_interceptor_or_the_run_fails():
    gone = table(observations())["C5"]
    assert gone.status == v.PASS and gone.finding.startswith("Yes. An update without")
    assert v.passed(v.derive_verdicts(observations()))
    stuck = observations()
    stuck.interceptors_after_detach = 1
    row = table(stuck)["C5"]
    assert row.status == v.FAIL and "still reports 1 interceptor" in row.finding
    assert not v.passed(v.derive_verdicts(stuck))


def test_an_unprobed_detach_is_unknown_and_fails_the_run():
    unprobed = observations()
    unprobed.interceptors_after_detach = None
    row = table(unprobed)["C5"]
    assert row.finding == "not probed" and row.status == v.UNKNOWN
    assert not v.passed(v.derive_verdicts(unprobed))


def test_a_detach_error_is_a_failing_row_with_the_reason():
    broken = observations()
    broken.interceptors_after_detach = None
    broken.detach_error = "gateway detach still UPDATING after 300 s"
    row = table(broken)["C5"]
    assert row.status == v.FAIL and "UPDATING after 300 s" in row.finding
    assert not v.passed(v.derive_verdicts(broken))


def test_passed_requires_the_q5_and_c5_rows():
    rows = v.derive_verdicts(observations())
    assert v.passed(rows)
    for key in ("Q5", "C5"):
        assert not v.passed([row for row in rows if row.key != key])


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
    assert rows["Q1"].status == v.UNKNOWN and rows["Q2"].status == v.UNKNOWN


def test_a_server_error_is_inconclusive_for_revalidation_not_a_yes():
    row = table(observations(omitted_required=v.Outcome("http_error", 503, "x")))["Q2"]
    assert "inconclusive" in row.finding and "Yes" not in row.finding
    assert row.status == v.UNKNOWN
    denied = table(observations(bad_type=DENIED))["Q2"]
    assert "inconclusive" in denied.finding and denied.status == v.UNKNOWN


def test_an_error_that_names_neither_field_is_inconclusive():
    row = table(observations(omitted_required=v.Outcome("error", 200, "boom")))["Q2"]
    assert "inconclusive" in row.finding and row.status == v.UNKNOWN


def test_a_skipped_probe_cannot_pass_the_table():
    obs = observations()
    del obs.outcomes["drop_required"]
    assert v.passed(v.derive_verdicts(obs)) is False
    skipped_q1 = observations()
    del skipped_q1.outcomes["decoy_names_jordan"]
    assert v.passed(v.derive_verdicts(skipped_q1)) is False


def test_the_result_needs_every_row():
    assert v.passed([]) is False
    assert v.format_table([]).endswith("RESULT: FAIL")
    without_c4 = [row for row in v.derive_verdicts(observations()) if row.key != "C4"]
    assert v.passed(without_c4) is False


def test_an_account_id_in_server_text_is_masked():
    outcome = v.classify(200, {"error": {"code": 1, "message": "arn:aws:iam::123456789012:role/x"}})
    assert "123456789012" not in outcome.message and "<acct>" in outcome.message
    raw = v.Outcome("error", 200, "arn:aws:iam::123456789012:role/x\nline two")
    text = v.format_table(v.derive_verdicts(observations(forced_refusal=raw)))
    assert "123456789012" not in text and "<acct>" in text


def test_a_token_shaped_string_is_redacted_in_the_table():
    jwt = "ey" + "Jhbc.ey" + "JzdWIiOiJ4In" + "0.sig_-9"
    raw = v.Outcome("error", 200, f"bad token {jwt} here")
    text = v.format_table(v.derive_verdicts(observations(forced_refusal=raw)))
    assert jwt not in text and "<token>" in text
    assert jwt not in v.classify(200, {"error": {"code": 1, "message": jwt}}).message


def test_the_target_view_flags_credential_like_keys():
    echo = {"event": {"travelerId": JORDAN}, "custom": {"authorization": "x"}}
    body = {"result": {"content": [{"type": "text", "text": json.dumps(echo)}]}}
    finding = table(observations(jordan_names_jordan=v.classify(200, body)))["Q3"].finding
    assert "No claim" not in finding and "authorization" in finding


def test_malformed_shapes_become_errors_not_crashes():
    assert v.classify(200, {"result": []}).kind == "error"
    assert v.classify(200, {"result": {"content": "x"}}).kind == "error"
    null_text = {"result": {"content": [{"type": "text", "text": None}]}}
    assert v.classify(200, null_text).kind == "error"
    listed = {"result": {"content": [{"type": "text", "text": json.dumps({"event": []})}]}}
    outcome = v.classify(200, listed)
    assert outcome.kind == "ok"
    assert table(observations(jordan_names_jordan=outcome))["C1"].status == v.FAIL


def test_the_table_has_a_row_per_verdict_and_a_result_line():
    text = v.format_table(v.derive_verdicts(observations()))
    for key in ("Q1", "Q2", "Q3", "Q4", "Q5", "C1", "C2", "C3", "C4", "C5"):
        assert f"\n{key} " in text
    assert text.endswith("RESULT: PASS")
    assert v.format_table(v.derive_verdicts(v.Observations())).endswith("RESULT: FAIL")


def masked_denial(text):
    return v.Outcome("denied", 200, text)


def test_q2_unknown_never_fails_an_otherwise_passing_run():
    masked = observations(bad_type=DENIED, drop_required=DENIED)
    rows = v.derive_verdicts(masked)
    assert {row.key: row.status for row in rows}["Q2"] == v.UNKNOWN
    assert v.passed(rows)


def test_a_q2_fail_row_still_fails_the_run():
    rows = [
        v.Verdict(row.key, row.question, row.finding, v.FAIL if row.key == "Q2" else row.status)
        for row in v.derive_verdicts(observations())
    ]
    assert not v.passed(rows)


def test_other_unknown_rows_still_fail_the_run():
    for key in ("Q1", "C5"):
        rows = [
            v.Verdict(r.key, r.question, r.finding, v.UNKNOWN if r.key == key else r.status)
            for r in v.derive_verdicts(observations())
        ]
        assert not v.passed(rows), key


def test_q5_unknown_still_does_not_fail_the_run():
    rows = v.derive_verdicts(observations(cedar_alone=v.Outcome("error", 200, "boom")))
    assert {row.key: row.status for row in rows}["Q5"] == v.UNKNOWN and v.passed(rows)


def test_q2_says_what_it_could_not_measure_when_cedar_masks_the_probes():
    finding = table(observations(bad_type=DENIED, drop_required=DENIED))["Q2"].finding
    assert "Not measured" in finding
    assert "Cedar denied" in finding and "re-validates" in finding
    assert "permit_all" in finding


def test_a_denied_probe_keeps_a_masked_truncated_message_note_on_its_row():
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln"
    text = f"Tool Execution Denied: acct 123456789012 token {jwt} " + "x" * 400
    row = table(observations(cedar_alone=masked_denial(text)))["Q5"]
    assert jwt not in row.notes and "123456789012" not in row.notes
    assert "<token>" in row.notes and "<acct>" in row.notes
    assert row.notes.startswith("cedar_alone: denied 200: Tool Execution Denied")
    assert len(row.notes.split(": ", 2)[2]) <= 300


def test_a_bearer_text_or_account_id_in_a_refusal_is_masked_in_notes_and_table():
    refused = v.Outcome("refused", 200, "Identity Check Failed: Bearer abc.def 210987654321")
    rows = v.derive_verdicts(observations(forced_refusal=refused))
    text = v.format_table(rows)
    assert "210987654321" not in text and "abc.def" not in text
    assert "forced_refusal: refused 200" in text


def test_ok_probes_add_no_notes_and_notes_name_each_non_ok_probe_of_q2():
    assert table(observations())["Q1"].notes == ""
    notes = table(observations(bad_type=DENIED, drop_required=DENIED))["Q2"].notes
    assert "bad_type: denied 200" in notes and "drop_required: denied 200" in notes
    assert "omitted_required" not in notes


def test_a_q2_probe_that_never_ran_still_fails_the_run():
    obs = observations()
    del obs.outcomes["bad_type"]
    rows = v.derive_verdicts(obs)
    assert {row.key: row.finding for row in rows}["Q2"] == "not probed"
    assert not v.passed(rows)
