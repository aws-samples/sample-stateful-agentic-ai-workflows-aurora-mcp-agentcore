"""The tool-choice eval: its case set, scorer and agent build, all offline.

Nothing here calls Amazon Bedrock. The live runner needs ``--apply`` and the confirmation flag,
and refuses when ``CI`` is set; the last tests pin that.
"""

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from backend.agents.concierge_definition import (
    PHASE4_TOOLS,
    TOOL_ARGUMENTS,
    WORKFLOW_ONLY_TOOLS,
    WRITE_TOOLS,
)
from scripts import run_tool_choice_eval as cli
from scripts.tool_choice_eval import runner, stubs, surface
from scripts.tool_choice_eval.cases import CaseError, load_cases, parse_case
from scripts.tool_choice_eval.scoring import (
    ToolCall,
    errored,
    failing_cases,
    matches,
    required_passes,
    score_case,
    summarize,
)
from scripts.tool_choice_eval.stubs import CallRecorder, canned_result
from tests.scripted_model import ScriptedModel

TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "meridian_agentcore" / "agentcore" / "agentcore.template.json"
)
MODEL_VISIBLE = set(PHASE4_TOOLS) - set(WORKFLOW_ONLY_TOOLS)
READ_TOOLS = MODEL_VISIBLE - set(WRITE_TOOLS)


def case_with(**fields):
    raw = {"id": "sample_case", "prompt": "Convert 10 USD to EUR."}
    return parse_case({**raw, **fields})


@pytest.fixture(scope="module")
def runtime():
    return runner.load_runtime()


# -- the case set ---------------------------------------------------------------------------


def test_the_committed_cases_load_with_unique_ids():
    cases = load_cases()
    assert len(cases) >= 20
    assert len({case.id for case in cases}) == len(cases)


@pytest.mark.parametrize("raw, message", [
    ({"id": "x"}, "id must match"),
    ({"id": "sample_case", "prompt": "q", "turn": "maybe"}, "turn must be one of"),
    ({"id": "sample_case", "prompt": "q", "colour": 1}, "unknown keys"),
    ({"id": "sample_case"}, "needs a prompt"),
    ({"id": "sample_case", "prompt": "q", "expected_tools": "price_range"}, "list of tool names"),
    ({"id": "sample_case", "prompt": "q", "expected_tools": ["a", "a"]}, "repeats a tool"),
    ({"id": "sample_case", "prompt": "q", "expected_tools": ["a"], "forbidden_tools": ["a"]},
     "both wanted and forbidden"),
    ({"id": "sample_case", "prompt": "q", "expected_tools": ["a"], "allowed_tools": ["a"]},
     "both expected and allowed"),
    ({"id": "sample_case", "prompt": "q", "expected_arguments": {"price_range": {"d": {
        "equals": "x"}}}}, "the case does not call"),
    ({"id": "sample_case", "prompt": "q", "expected_tools": ["a"], "expected_arguments": {
        "a": {"d": {"regex": "x"}}}}, "matcher must be"),
    ({"id": "sample_case", "prompt": "q", "expected_tools": ["a"], "expected_arguments": {
        "a": {"d": {"equals": 1, "equals_ci": "1"}}}}, "exactly one matcher"),
    ({"id": "sample_case", "turn": "confirmed_hold", "prompt": "q", "outcome": "ok",
      "target": {"a": 1}}, "no prompt"),
    ({"id": "sample_case", "turn": "confirmed_hold", "outcome": "ok", "target": {"a": 1},
      "expected_tools": ["price_range"]}, "no tools to expect"),
    ({"id": "sample_case", "prompt": "q", "history": [{"role": "system", "text": "t"}]},
     "history entry"),
    ({"id": "sample_case", "prompt": "q", "budget_ceiling_cents": -1}, "non-negative"),
    (["not", "an", "object"], "JSON object"),
])
def test_a_malformed_case_is_refused_with_the_reason(raw, message):
    with pytest.raises(CaseError, match=message):
        parse_case(raw)


def test_a_file_with_a_repeated_id_or_bad_json_or_no_case_is_refused(tmp_path):
    line = json.dumps({"id": "sample_case", "prompt": "q"})
    for text, message in [(f"{line}\n{line}\n", "repeated ids"), ("{oops\n", "not valid JSON"),
                          ("\n", "no cases")]:
        path = tmp_path / "cases.jsonl"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(CaseError, match=message):
            load_cases(path)


def test_writes_are_forbidden_unless_a_case_expects_or_allows_them():
    assert case_with().forbidden() == set(WRITE_TOOLS)
    allowed = case_with(allowed_tools=["create_courtesy_hold"])
    assert allowed.forbidden() == set(WRITE_TOOLS) - {"create_courtesy_hold"}
    explicit = case_with(forbidden_tools=["price_range"])
    assert explicit.forbidden() == set(WRITE_TOOLS) | {"price_range"}


# -- the cases against the Gateway ------------------------------------------------------------


def named_tools(case):
    return (set(case.expected_tools) | set(case.allowed_tools) | set(case.forbidden_tools)
            | set(case.expected_arguments))


def test_every_tool_a_case_names_exists_in_the_gateway_schemas():
    served = {name.split("___")[-1] for name in surface.gateway_tools()}
    for case in load_cases():
        assert named_tools(case) <= served, case.id


def test_every_expected_argument_is_a_property_of_that_tools_schema():
    by_bare = {name.split("___")[-1]: tool for name, tool in surface.gateway_tools().items()}
    for case in load_cases():
        for tool, arguments in case.expected_arguments.items():
            properties = set(by_bare[tool]["inputSchema"]["properties"])
            assert set(arguments) <= properties, (case.id, tool)
            assert set(arguments) <= set(TOOL_ARGUMENTS[tool]), (case.id, tool)


def test_every_read_tool_the_model_sees_is_expected_by_at_least_one_case():
    expected = {tool for case in load_cases() for tool in case.expected_tools}
    assert READ_TOOLS <= expected, sorted(READ_TOOLS - expected)


def test_every_write_tool_the_model_sees_has_an_allowing_case_and_a_forbidding_case():
    cases = load_cases()
    for tool in set(WRITE_TOOLS) & MODEL_VISIBLE:
        allowing = [c for c in cases if tool in set(c.allowed_tools) | set(c.expected_tools)]
        forbidding = [c for c in cases if tool in c.forbidden()]
        assert allowing and forbidding, tool


def test_the_set_covers_multi_tool_unconfirmed_and_confirmed_turns():
    cases = load_cases()
    assert any(len(case.expected_tools) >= 2 for case in cases)
    assert any(case.turn == "unconfirmed" and set(case.allowed_tools) & set(WRITE_TOOLS)
               for case in cases)
    assert {"confirmed_hold", "confirmed_booking"} <= {case.turn for case in cases}


def test_the_workflow_only_tool_is_a_forbidden_case_and_never_a_wanted_one():
    cases = load_cases()
    assert any("release_hold" in case.forbidden_tools for case in cases)
    for case in cases:
        assert not set(WORKFLOW_ONLY_TOOLS) & (
            set(case.expected_tools) | set(case.allowed_tools)), case.id


def test_no_prompt_names_a_tool_it_could_be_choosing():
    for case in load_cases():
        for tool in MODEL_VISIBLE:
            assert not re.search(rf"\b{tool}\b", case.prompt), case.id


# -- the tool surface -------------------------------------------------------------------------


def test_the_surface_is_read_from_the_gateway_files_under_the_template_target_names():
    spec = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    targets = {t["name"] for t in spec["agentCoreGateways"][0]["targets"]}
    served = surface.gateway_tools()
    assert {name.split("___")[0] for name in served} == targets
    assert {name.split("___")[-1] for name in served} >= set(PHASE4_TOOLS)


def test_the_model_sees_exactly_the_phase_four_tools_less_the_workflow_only_one(runtime):
    seen = {tool.tool_name.split("___")[-1] for tool in runner.stub_tools(runtime)}
    assert seen == MODEL_VISIBLE
    spec = {t.tool_name: t.tool_spec for t in runner.stub_tools(runtime)}["MeridianCatalog___"
                                                                           "price_range"]
    gateway = surface.gateway_tools()["MeridianCatalog___price_range"]
    assert spec["description"] == gateway["description"]
    assert spec["inputSchema"] == {"json": gateway["inputSchema"]}


# -- scoring ----------------------------------------------------------------------------------


@pytest.mark.parametrize("value, matcher, expected", [
    (2000, {"equals": 2000}, True),
    (2000.0, {"equals": 2000}, True),
    ("2000", {"equals": 2000}, False),
    (True, {"equals": 1}, False),
    (" tokyo ", {"equals_ci": "Tokyo"}, True),
    ("Tokyo, Japan", {"contains_ci": "tokyo"}, True),
    ("Kyoto", {"contains_ci": "tokyo"}, False),
    (["B", "A"], {"set_equals": ["A", "B"]}, True),
    (["A"], {"set_equals": ["A", "B"]}, False),
    ("A", {"set_equals": ["A"]}, False),
    (None, {"equals_ci": "x"}, False),
])
def test_matchers(value, matcher, expected):
    assert matches(value, matcher) is expected


def test_a_run_that_calls_exactly_the_expected_tools_with_the_right_arguments_scores_full():
    case = case_with(
        expected_tools=["currency_convert"],
        expected_arguments={"currency_convert": {"to_currency": {"equals_ci": "EUR"}}})
    score = score_case(case, [ToolCall("currency_convert", {"to_currency": "eur"})])
    assert score.selected and not score.forbidden
    assert score.argument_checks == (("currency_convert", "to_currency", True),)


def test_a_missing_tool_an_extra_tool_and_a_wrong_argument_are_each_reported():
    case = case_with(
        expected_tools=["price_range", "currency_convert"], allowed_tools=["search_trips"],
        expected_arguments={"price_range": {"destination": {"contains_ci": "tokyo"}}})
    score = score_case(case, [ToolCall("price_range", {"destination": "Kyoto"}),
                              ToolCall("region_inventory", {})])
    assert not score.selected
    assert score.missing == ("currency_convert",) and score.unexpected == ("region_inventory",)
    assert score.forbidden == ()
    assert score.argument_checks == (("price_range", "destination", False),)


def test_an_unallowed_write_is_forbidden_and_an_allowed_one_is_not():
    strict = case_with(expected_tools=["price_range"])
    assert score_case(strict, [ToolCall("price_range", {}),
                               ToolCall("create_courtesy_hold", {})]).forbidden == (
        "create_courtesy_hold",)
    lenient = case_with(allowed_tools=["create_courtesy_hold"])
    score = score_case(lenient, [ToolCall("create_courtesy_hold", {})])
    assert score.forbidden == () and score.selected


def test_any_call_on_a_confirmed_turn_is_forbidden_because_the_model_has_no_tools():
    case = parse_case({"id": "confirmed_case", "turn": "confirmed_hold", "outcome": "held",
                       "target": {"packageId": "CTY-002"}})
    assert score_case(case, []).selected
    assert score_case(case, [ToolCall("price_range", {})]).forbidden == ("price_range",)


def test_a_repeated_tool_call_checks_the_arguments_of_any_of_its_calls():
    case = case_with(
        expected_tools=["get_package_details"],
        expected_arguments={"get_package_details": {"packageId": {"equals": "CTY-002"}}})
    calls = [ToolCall("get_package_details", {"packageId": "CTY-001"}),
             ToolCall("get_package_details", {"packageId": "CTY-002"})]
    assert score_case(case, calls).argument_checks[0][2] is True


def test_arguments_split_across_two_calls_of_one_tool_do_not_pass():
    case = case_with(
        expected_tools=["currency_convert"],
        expected_arguments={"currency_convert": {
            "amount": {"equals": 2400}, "from_currency": {"equals_ci": "USD"},
            "to_currency": {"equals_ci": "EUR"}}})
    split = [ToolCall("currency_convert", {"amount": 2400, "from_currency": "CAD",
                                           "to_currency": "JPY"}),
             ToolCall("currency_convert", {"amount": 1, "from_currency": "USD",
                                           "to_currency": "EUR"})]
    checks = score_case(case, split).argument_checks
    assert not all(passed for _, _, passed in checks)
    assert checks == (("currency_convert", "amount", False),
                      ("currency_convert", "from_currency", True),
                      ("currency_convert", "to_currency", True))


def test_argument_diagnostics_come_from_the_best_matching_call():
    case = case_with(
        expected_tools=["currency_convert"],
        expected_arguments={"currency_convert": {
            "amount": {"equals": 2400}, "from_currency": {"equals_ci": "USD"},
            "to_currency": {"equals_ci": "EUR"}}})
    calls = [ToolCall("currency_convert", {"amount": 1, "from_currency": "CAD",
                                           "to_currency": "JPY"}),
             ToolCall("currency_convert", {"amount": 2400, "from_currency": "USD",
                                           "to_currency": "JPY"})]
    assert score_case(case, calls).argument_checks == (
        ("currency_convert", "amount", True), ("currency_convert", "from_currency", True),
        ("currency_convert", "to_currency", False))


def test_a_tool_that_was_never_called_fails_every_argument_check():
    case = case_with(
        expected_tools=["currency_convert"],
        expected_arguments={"currency_convert": {"amount": {"equals": 1},
                                                 "to_currency": {"equals_ci": "EUR"}}})
    checks = score_case(case, []).argument_checks
    assert checks == (("currency_convert", "amount", False),
                      ("currency_convert", "to_currency", False))


def test_the_summary_rates_exclude_errored_runs_and_report_them():
    case = case_with(expected_tools=["currency_convert"],
                     expected_arguments={"currency_convert": {"amount": {"equals": 10}}})
    good = score_case(case, [ToolCall("currency_convert", {"amount": 10})])
    bad = score_case(case, [ToolCall("price_range", {}), ToolCall("create_courtesy_hold", {})])
    summary = summarize([good, bad, errored(case, "ThrottlingException")])
    assert (summary.runs, summary.errors) == (2, 1)
    assert summary.selection_accuracy == 0.5 and summary.forbidden_call_rate == 0.5
    assert summary.argument_accuracy == 0.5 and summary.argument_checks == 2
    assert summarize([]).selection_accuracy == 0.0 and summarize([]).argument_accuracy is None


# -- the per-case floor ------------------------------------------------------------------------

CONVERT_ARGS = {"amount": 2400, "from_currency": "USD", "to_currency": "EUR"}


def convert_case():
    return case_with(
        id="convert", expected_tools=["currency_convert"],
        expected_arguments={"currency_convert": {
            "amount": {"equals": 2400}, "from_currency": {"equals_ci": "USD"},
            "to_currency": {"equals_ci": "EUR"}}})


def run_of(case, kind):
    if kind == "pass":
        return score_case(case, [ToolCall("currency_convert", CONVERT_ARGS)])
    if kind == "split":
        return score_case(case, [
            ToolCall("currency_convert", {**CONVERT_ARGS, "to_currency": "JPY"}),
            ToolCall("currency_convert", {**CONVERT_ARGS, "amount": 1})])
    if kind == "forbidden":
        return score_case(case, [ToolCall("currency_convert", CONVERT_ARGS),
                                 ToolCall("create_courtesy_hold", {})])
    if kind == "error":
        return errored(case, "ThrottlingException")
    return score_case(case, [])


@pytest.mark.parametrize("repeats, needed", [(1, 1), (2, 2), (3, 2), (4, 3), (5, 3), (6, 4)])
def test_a_case_needs_a_majority_of_its_runs_to_pass(repeats, needed):
    assert required_passes(repeats) == needed


@pytest.mark.parametrize("kinds, failing", [
    (["pass"], []),
    (["split"], ["convert"]),
    (["pass", "pass", "pass"], []),
    (["pass", "pass", "split"], []),
    (["pass", "split", "split"], ["convert"]),
    (["pass", "pass", "forbidden"], []),
    (["pass", "forbidden", "miss"], ["convert"]),
    (["pass", "pass", "error"], []),
    (["pass", "error", "error"], []),
    (["error", "error", "error"], []),
    (["split", "split", "error"], ["convert"]),
])
def test_a_case_that_passes_too_few_runs_is_reported_by_id(kinds, failing):
    case = convert_case()
    assert failing_cases([run_of(case, kind) for kind in kinds]) == failing


def test_the_floor_is_judged_per_case_and_lists_ids_in_order():
    one, two = convert_case(), case_with(id="alpha", expected_tools=["currency_convert"])
    scores = [run_of(one, "split"), run_of(two, "pass"), run_of(two, "miss"),
              run_of(one, "split"), run_of(two, "miss"), run_of(one, "pass")]
    assert failing_cases(scores) == ["alpha", "convert"]


# -- the agent the runner builds ---------------------------------------------------------------


def play(case, runtime, turns):
    model = ScriptedModel(turns)
    recorder = CallRecorder()
    agent = runner.build_eval_agent(case, model, runtime, recorder)
    agent(runner.turn_prompt_for(case, runtime))
    return model, recorder


def test_an_unconfirmed_turn_offers_the_gateway_names_and_the_runtimes_prompt(runtime):
    case = case_with(expected_tools=["currency_convert"])
    model, recorder = play(case, runtime, [
        {"kind": "tool", "id": "t1", "name": "MeridianCatalog___currency_convert",
         "input": {"amount": 10, "from_currency": "USD", "to_currency": "EUR"}},
        {"kind": "text", "text": "About 9.20 euros."},
    ])
    offered = set(model.calls[0]["tools"])
    assert {n.split("___")[-1] for n in offered} == MODEL_VISIBLE
    assert "MeridianHolds___release_hold" not in offered
    assert model.calls[0]["system"].startswith(runtime.definition.system_prompt(4))
    assert runtime.prompts.UNCONFIRMED in model.calls[0]["system"]
    assert [c.name for c in recorder.calls] == ["currency_convert"]


def test_the_recorder_sees_the_pinned_traveler_not_the_one_the_model_sent(runtime):
    case = case_with(allowed_tools=["loyalty_balance"])
    _, recorder = play(case, runtime, [
        {"kind": "tool", "id": "t1", "name": "MeridianHolds___loyalty_balance",
         "input": {"travelerId": "trv_someone_else", "program": "Marriott Bonvoy"}},
        {"kind": "text", "text": "48,250 points."},
    ])
    assert recorder.calls[0].arguments["travelerId"] == runner.EVAL_TRAVELER


def test_a_call_to_a_tool_the_model_was_not_offered_is_still_recorded(runtime):
    case = case_with()
    _, recorder = play(case, runtime, [
        {"kind": "tool", "id": "t1", "name": "MeridianHolds___release_hold",
         "input": {"holdRequestId": "h", "journeyId": "j"}},
        {"kind": "text", "text": "I cannot release holds."},
    ])
    assert [c.name for c in recorder.calls] == ["release_hold"]
    assert score_case(parse_case({"id": "sample_case", "prompt": "q",
                                  "forbidden_tools": ["release_hold"]}),
                      recorder.calls).forbidden == ("release_hold",)


@pytest.mark.parametrize("turn, marker", [
    ("confirmed_hold", "confirmed a hold"), ("confirmed_booking", "confirmed the booking"),
])
def test_a_confirmed_turn_gives_the_model_no_tools_and_the_narration_prompt(
    runtime, turn, marker
):
    case = parse_case({"id": "confirmed_case", "turn": turn, "outcome": "decided",
                       "target": {"packageId": "CTY-002"}, "budget_ceiling_cents": 500000})
    model, recorder = play(case, runtime, [{"kind": "text", "text": "Done."}])
    assert model.calls[0]["tools"] == [] and recorder.calls == []
    assert marker in model.calls[0]["system"]
    assert "decided" in runner.turn_prompt_for(case, runtime)


def test_earlier_messages_of_a_case_reach_the_agent(runtime):
    case = case_with(history=[{"role": "user", "text": "hi"},
                              {"role": "assistant", "text": "hello"}])
    agent = runner.build_eval_agent(case, ScriptedModel([]), runtime, CallRecorder())
    assert [m["role"] for m in agent.messages] == ["user", "assistant"]


def test_a_stub_answers_from_the_catalog_and_refuses_a_write_like_the_policy():
    status, payload = canned_result("MeridianCatalog___price_range", {"destination": "Tokyo"})
    assert status == "success" and payload["low"] <= payload["high"]
    status, payload = canned_result("MeridianHolds___get_package_details", {"packageId": "CTY-002"})
    assert status == "success" and payload["package_id"] == "CTY-002"
    assert canned_result("MeridianHolds___get_package_details", {"packageId": "nope"})[1][
        "error"] == "package_not_found"
    status, payload = canned_result("MeridianHolds___create_courtesy_hold", {})
    assert status == "error" and "Tool Execution Denied" in payload


def test_a_failing_model_is_an_errored_run_with_a_masked_one_line_reason(runtime):
    class Broken(ScriptedModel):
        async def stream(self, *args, **kwargs):
            raise RuntimeError("role arn:aws:iam::123456789012:role/x\nthrottled")
            yield

    score = runner.run_case(case_with(), Broken([]), runtime)
    assert score.error and "\n" not in score.error and "123456789012" not in score.error


HOLD_CALL = ToolCall("create_courtesy_hold", {})


def test_a_forbidden_call_made_before_an_error_still_counts_against_the_run(runtime):
    # One forbidden tool call, then the script runs out and the run errors.
    model = ScriptedModel([{"kind": "tool", "id": "t1", "name": "create_courtesy_hold",
                            "input": {}}])

    score = runner.run_case(case_with(), model, runtime)

    assert score.error and score.forbidden == ("create_courtesy_hold",)
    assert summarize([score]).forbidden_call_rate == 1.0


def test_errored_runs_without_a_forbidden_call_stay_out_of_every_rate():
    case = case_with(expected_tools=["currency_convert"])
    good = score_case(case, [ToolCall("currency_convert", {})])
    summary = summarize([good, errored(case, "boom", [ToolCall("price_range", {})]),
                         errored(case, "boom")])

    assert summary.forbidden_call_rate == 0.0 and (summary.runs, summary.errors) == (1, 2)


def test_a_forbidden_errored_run_counts_in_the_rate_and_fails_its_case():
    case = case_with(expected_tools=["currency_convert"])
    good = score_case(case, [ToolCall("currency_convert", {})])
    bad = errored(case, "boom", [HOLD_CALL])

    assert summarize([good, bad]).forbidden_call_rate == 0.5
    assert failing_cases([good, bad]) == [case.id]
    assert failing_cases([good, errored(case, "boom")]) == []


def test_the_fingerprint_changes_when_a_stub_result_or_a_model_setting_changes(
    runtime, monkeypatch
):
    cases = load_cases()
    before = runner.fingerprint(runtime, cases)
    monkeypatch.setitem(stubs.LOYALTY, "tier", "Silver")
    assert runner.fingerprint(runtime, cases).tools != before.tools
    monkeypatch.undo()
    monkeypatch.setenv("BEDROCK_MAX_TOKENS", "8000")
    assert runner.fingerprint(runtime, cases).settings != before.settings
    monkeypatch.undo()
    monkeypatch.setenv("BEDROCK_MODEL_ID", "another.model")
    assert runner.fingerprint(runtime, cases).settings != before.settings


def test_the_recorded_baseline_is_what_the_code_fingerprints_and_the_readme_names(
    runtime, monkeypatch
):
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("BEDROCK_MAX_TOKENS", raising=False)
    marks = runner.fingerprint(runtime, load_cases())
    baseline = runner.BASELINE
    readme = (Path(__file__).resolve().parents[1] / "backend" / "agents" / "README.md").read_text()

    assert runner.resolved_model_id() == baseline["model_id"]
    for name in ("prompt", "tools", "cases", "settings"):
        assert getattr(marks, name) == baseline[name], name
        assert baseline[name] in readme, name
    assert "scripts/tool_choice_eval/runner.py" in readme


def test_the_fingerprint_changes_when_the_prompt_text_changes(runtime, monkeypatch):
    cases = load_cases()
    before = runner.fingerprint(runtime, cases)
    monkeypatch.setattr(runtime.prompts, "UNCONFIRMED", runtime.prompts.UNCONFIRMED + " x")
    assert runner.fingerprint(runtime, cases).prompt != before.prompt
    assert runner.fingerprint(runtime, cases).tools == before.tools


def raw_case(**fields):
    base = {"id": "sample_case", "prompt": "Convert 10 USD to EUR.",
            "expected_tools": ["currency_convert"],
            "expected_arguments": {"currency_convert": {"amount": {"equals": 10}}}}
    return {**base, **fields}


def confirmed_raw(**fields):
    base = {"id": "confirmed_case", "turn": "confirmed_hold", "outcome": "held",
            "target": {"packageId": "CTY-002"}, "budget_ceiling_cents": 500000}
    return {**base, **fields}


@pytest.mark.parametrize("raw_a, raw_b", [
    (raw_case(), raw_case(memory_context="Prefers aisle seats.")),
    (raw_case(), raw_case(expected_tools=["price_range", "currency_convert"])),
    (raw_case(), raw_case(allowed_tools=["loyalty_balance"])),
    (raw_case(), raw_case(forbidden_tools=["price_range"])),
    (raw_case(), raw_case(note="a different reason")),
    (raw_case(), raw_case(expected_arguments={"currency_convert": {"amount": {"equals": 11}}})),
    (raw_case(), raw_case(expected_arguments={"currency_convert": {"amount": {
        "equals_ci": "10"}}})),
    (confirmed_raw(), confirmed_raw(outcome="refused")),
    (confirmed_raw(), confirmed_raw(target={"packageId": "CTY-001"})),
    (confirmed_raw(), confirmed_raw(budget_ceiling_cents=1)),
    (confirmed_raw(), confirmed_raw(turn="confirmed_booking")),
])
def test_the_fingerprint_covers_every_field_of_a_case(runtime, raw_a, raw_b):
    first = runner.fingerprint(runtime, [parse_case(raw_a)])
    second = runner.fingerprint(runtime, [parse_case(raw_b)])
    assert (first.prompt, first.cases) != (second.prompt, second.cases)


def test_the_fingerprint_changes_when_confirmed_turn_text_changes(runtime, monkeypatch):
    cases = [parse_case(confirmed_raw()), parse_case(confirmed_raw(
        id="confirmed_booking_case", turn="confirmed_booking"))]
    before = runner.fingerprint(runtime, cases)
    for name in ("CONFIRMED", "BOOKING_CONFIRMED"):
        monkeypatch.setattr(runtime.prompts, name, getattr(runtime.prompts, name) + " x")
        after = runner.fingerprint(runtime, cases)
        assert after.prompt != before.prompt, name
        monkeypatch.undo()
    monkeypatch.setattr(runtime.prompts, "ACTION_LABELS", {"hold": "a hold!", "booking": "b!"})
    assert runner.fingerprint(runtime, cases).cases != before.cases


def test_the_fingerprint_changes_when_the_unconfirmed_turn_template_changes(
    runtime, monkeypatch
):
    cases = [parse_case(raw_case(memory_context="Prefers aisle seats."))]
    before = runner.fingerprint(runtime, cases)
    original = runtime.prompts.turn_prompt
    monkeypatch.setattr(runtime.prompts, "turn_prompt",
                        lambda *args: original(*args) + "\nextra")
    assert runner.fingerprint(runtime, cases).cases != before.cases


def test_the_fingerprint_is_stable_across_runs_and_key_order(runtime):
    first = parse_case(raw_case(expected_arguments={"currency_convert": {
        "amount": {"equals": 10}, "to_currency": {"equals_ci": "EUR"}}}))
    reordered = parse_case(raw_case(expected_arguments={"currency_convert": {
        "to_currency": {"equals_ci": "EUR"}, "amount": {"equals": 10}}}))
    one = runner.fingerprint(runtime, [first])
    assert one == runner.fingerprint(runtime, [first])
    assert one == runner.fingerprint(runtime, [reordered])


# -- the command line never reaches Amazon Bedrock by accident ---------------------------------


@pytest.fixture
def bedrock_forbidden(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the eval reached for a Bedrock model")

    monkeypatch.setattr(runner, "load_runtime_model", refuse)
    monkeypatch.setattr(runner, "run_cases", refuse)


def test_the_default_run_is_a_dry_run_that_prints_the_plan(bedrock_forbidden, capsys):
    assert cli.main([], {}) == cli.EXIT_PASS
    out = capsys.readouterr().out
    assert "dry run: nothing was sent" in out
    assert "MeridianCatalog___hybrid_trip_search" in out and "release_hold" not in out


def test_apply_without_the_confirmation_flag_is_refused(bedrock_forbidden, capsys):
    assert cli.main(["--apply"], {}) == cli.EXIT_REFUSED
    assert "--i-understand-this-changes-aws" in capsys.readouterr().err


def test_apply_with_both_flags_is_still_refused_when_ci_is_set(bedrock_forbidden, capsys):
    argv = ["--apply", "--i-understand-this-changes-aws"]
    assert cli.main(argv, {"CI": "true"}) == cli.EXIT_REFUSED
    assert "CI is set" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["--aply"], ["--repeats", "0"], ["--case", "no_such_case"],
                                  ["--report", "/tmp/eval-report.json"]])
def test_a_usage_error_or_unknown_case_exits_refused_not_inconclusive(
    bedrock_forbidden, argv, capsys
):
    assert cli.main(argv, {}) == cli.EXIT_REFUSED


def test_a_live_run_returns_the_exit_code_for_its_thresholds(monkeypatch, capsys):
    cases = [c for c in load_cases() if c.id == "currency_usd_to_eur"]
    monkeypatch.setattr(cli, "load_cases", lambda: cases)
    monkeypatch.setattr(runner, "load_runtime_model", lambda: object())
    good = score_case(cases[0], [ToolCall("currency_convert", {
        "amount": 2400, "from_currency": "USD", "to_currency": "EUR"})])
    argv = ["--apply", "--i-understand-this-changes-aws"]
    for scores, code in [([good], cli.EXIT_PASS),
                         ([score_case(cases[0], [])], cli.EXIT_MISSED),
                         ([good, errored(cases[0], "boom")], cli.EXIT_INCONCLUSIVE),
                         ([errored(cases[0], "boom")], cli.EXIT_INCONCLUSIVE),
                         ([errored(cases[0], "boom", [ToolCall("create_courtesy_hold", {})])],
                          cli.EXIT_MISSED)]:
        monkeypatch.setattr(runner, "run_cases", lambda *a, _s=scores, **k: _s)
        assert cli.main(argv, {}) == code
    capsys.readouterr()


def test_one_case_below_the_floor_is_a_miss_even_when_the_averages_pass(monkeypatch, capsys):
    cases = [c for c in load_cases() if c.id == "currency_usd_to_eur"]
    monkeypatch.setattr(cli, "load_cases", lambda: cases)
    monkeypatch.setattr(runner, "load_runtime_model", lambda: object())
    good = score_case(cases[0], [ToolCall("currency_convert", CONVERT_ARGS)])
    split = score_case(cases[0], [
        ToolCall("currency_convert", {**CONVERT_ARGS, "to_currency": "JPY"}),
        ToolCall("currency_convert", {**CONVERT_ARGS, "amount": 1})])
    others = [replace(good, case_id=f"other_{n}") for n in range(30)]
    scores = [good, split, split, *others]
    monkeypatch.setattr(runner, "run_cases", lambda *a, **k: scores)
    assert summarize(scores).argument_accuracy >= cli.DEFAULT_MIN_ARGUMENTS
    argv = ["--apply", "--i-understand-this-changes-aws", "--repeats", "3"]
    assert cli.main(argv, {}) == cli.EXIT_MISSED
    out = capsys.readouterr().out
    assert "MISSED" in out and "currency_usd_to_eur" in out and "other_0" not in out
