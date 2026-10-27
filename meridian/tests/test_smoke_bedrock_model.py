"""The Bedrock smoke asks only questions Sonnet 5.5 can answer, and says what to set."""

import json

import pytest
from botocore.exceptions import ClientError

from scripts import smoke_bedrock_model as smoke
from scripts.identity_release.settings import CONFIRM_FLAG

ACCOUNT = "123456789012"
MODEL_ID = "global.anthropic.claude-sonnet-5-5"
ADAPTIVE = {"thinking": {"type": "adaptive"}}
EXPECTED_CANDIDATES = [
    ("provider default (no thinking field)", {}),
    ("adaptive thinking", ADAPTIVE),
    ("thinking between_tools", {"thinking": {"type": "between_tools"}}),
    ("adaptive thinking, effort low", {**ADAPTIVE, "output_config": {"effort": "low"}}),
    ("adaptive thinking, effort medium", {**ADAPTIVE, "output_config": {"effort": "medium"}}),
    ("adaptive thinking, effort high", {**ADAPTIVE, "output_config": {"effort": "high"}}),
]


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def converse(self, **kwargs):
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def answered(text="ready", tokens=3, input_tokens=11):
    return {
        "stopReason": "end_turn",
        "usage": {"inputTokens": input_tokens, "outputTokens": tokens},
        "output": {"message": {"content": [{"text": text}]}},
    }


def cut_off(tokens=1024):
    return {
        "stopReason": "max_tokens",
        "usage": {"inputTokens": 11, "outputTokens": tokens},
        "output": {"message": {"content": [{"reasoningContent": {}}]}},
    }


def failed(code, status=500, message="service trouble"):
    error = {"Error": {"Code": code, "Message": message}}
    return ClientError({**error, "ResponseMetadata": {"HTTPStatusCode": status}}, "Converse")


def rejected(message="thinking is not supported"):
    error = {"Error": {"Code": "ValidationException", "Message": message}}
    return ClientError(error, "Converse")


def outcomes_for_every_candidate(*leading):
    """The leading outcomes, then an answer for each remaining candidate."""
    return [*leading, *[answered()] * (len(smoke.CANDIDATES) - len(leading))]


@pytest.fixture
def client(monkeypatch):
    def install(*outcomes):
        fake = FakeClient(*outcomes)
        monkeypatch.setattr(smoke.boto3, "client", lambda *args, **kwargs: fake)
        return fake

    return install


def test_the_output_budget_leaves_room_for_adaptive_thinking():
    assert smoke.MAX_TOKENS == 1024
    request = smoke.converse_request(MODEL_ID, ADAPTIVE)
    assert request["inferenceConfig"] == {"maxTokens": 1024}


def test_the_candidate_list_is_pinned():
    assert list(smoke.CANDIDATES) == EXPECTED_CANDIDATES


def test_no_candidate_request_carries_a_parameter_the_model_rejects():
    for _label, fields in smoke.CANDIDATES:
        request = smoke.converse_request(MODEL_ID, fields)
        text = json.dumps(request)
        assert "temperature" not in text and "toolChoice" not in text
        assert "disabled" not in text and "budget_tokens" not in text
        assert request["inferenceConfig"] == {"maxTokens": smoke.MAX_TOKENS}


def test_the_model_request_fields_are_sent_only_for_a_candidate_that_has_some():
    plain = smoke.converse_request("m", {})
    thinking = smoke.converse_request("m", ADAPTIVE)
    assert "additionalModelRequestFields" not in plain
    assert thinking["additionalModelRequestFields"] == ADAPTIVE


def test_an_effort_candidate_sends_adaptive_thinking_and_the_effort_together(client):
    fake = client(*outcomes_for_every_candidate())
    smoke.main(["--apply", CONFIRM_FLAG])
    sent = [request.get("additionalModelRequestFields") for request in fake.requests]
    assert sent[3:] == [
        {"thinking": {"type": "adaptive"}, "output_config": {"effort": level}}
        for level in ("low", "medium", "high")
    ]
    assert sent[2] == {"thinking": {"type": "between_tools"}}


def test_a_dry_run_calls_nothing(monkeypatch, capsys):
    monkeypatch.setattr(smoke.boto3, "client", lambda *a, **k: pytest.fail("no AWS in a dry run"))
    assert smoke.main([]) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and "effort low" in out


def test_apply_needs_the_confirmation_flag(monkeypatch, capsys):
    monkeypatch.setattr(smoke.boto3, "client", lambda *a, **k: pytest.fail("refused first"))
    assert smoke.main(["--apply"]) == 3
    assert CONFIRM_FLAG in capsys.readouterr().err


def test_an_abbreviated_flag_is_refused():
    with pytest.raises(SystemExit) as stopped:
        smoke.main(["--app"])
    assert stopped.value.code == 2


def run(capsys):
    code = smoke.main(["--apply", CONFIRM_FLAG])
    return code, capsys.readouterr().out


def test_the_first_usable_candidate_is_reported_and_a_refusal_is_masked(client, capsys):
    rejection = rejected(f"bad request for arn:aws:bedrock:us-east-1:{ACCOUNT}:x")
    fake = client(*outcomes_for_every_candidate(answered(), rejection))

    code, out = run(capsys)

    assert code == 0 and len(fake.requests) == len(smoke.CANDIDATES)
    assert "USABLE        provider default" in out
    assert "REJECTED      adaptive thinking: ValidationException" in out
    assert "<acct>" in out and ACCOUNT not in out
    assert "First usable: provider default" in out


def test_every_reply_prints_its_measurements_on_its_own_line(client, capsys, monkeypatch):
    monkeypatch.setattr(smoke, "elapsed_ms", lambda started: 417)
    client(*outcomes_for_every_candidate(answered(tokens=5, input_tokens=19), rejected()))
    _code, out = run(capsys)
    lines = out.splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith("USABLE        provider"))
    measured = lines[at + 1]
    assert "stop end_turn" in measured and "input 19 tokens" in measured
    assert "output 5 tokens" in measured and "417 ms" in measured
    assert "text returned" in measured
    refused = next(line for line in lines if line.startswith("REJECTED      adaptive thinking:"))
    assert "417 ms" in refused
    assert sum("stop end_turn" in line for line in lines) == len(smoke.CANDIDATES) - 1


def test_a_reply_without_usage_prints_n_a(client, capsys):
    client(*outcomes_for_every_candidate({"stopReason": "end_turn", "output": {
        "message": {"content": [{"text": "ready"}]}}}))
    assert "input n/a tokens, output n/a tokens" in run(capsys)[1]


def test_a_max_tokens_stop_with_no_text_is_accepted_but_not_usable(client, capsys):
    client(*outcomes_for_every_candidate(cut_off()))
    code, out = run(capsys)
    assert code == 0
    assert "ACCEPTED      provider default" in out
    assert "stop max_tokens" in out and "accepted; reply did not fit in maxTokens" in out
    assert "not accepted" not in out and "First usable: adaptive thinking" in out


def test_accepted_but_empty_candidates_are_never_recommended(client, capsys, monkeypatch):
    monkeypatch.setattr(smoke, "REQUEST_FIELDS", {})
    client(*[cut_off()] * len(smoke.CANDIDATES))
    code, out = run(capsys)
    assert code == 1
    assert "No candidate was usable" in out and "raise MAX_TOKENS and rerun" in out
    assert "First usable" not in out and "They already are." not in out
    assert "must be" not in out


def test_a_validation_exception_is_rejected(client, capsys):
    client(*outcomes_for_every_candidate(answered(), rejected("budget_tokens is not supported")))
    _code, out = run(capsys)
    assert "REJECTED      adaptive thinking: ValidationException" in out
    assert "budget_tokens is not supported" in out
    assert "First usable: provider default" in out


def test_a_client_error_that_names_the_setting_is_rejected(client, capsys):
    client(*outcomes_for_every_candidate(
        answered(), failed("BadRequest", 400, "effort is not allowed")))
    assert "REJECTED      adaptive thinking" in run(capsys)[1]


@pytest.mark.parametrize("outcome", [
    failed("ThrottlingException", 429, "Too many requests"),
    failed("AccessDeniedException", 403, "no access to arn:aws:bedrock:us-east-1:%s:x" % ACCOUNT),
    failed("ModelTimeoutException", 408, "timed out"),
    failed("ServiceUnavailableException", 503),
    failed("ValidationLookalike", 400, "the model id is unknown"),
    ConnectionError("connection reset"),
    TimeoutError("read timed out"),
])
def test_an_error_that_says_nothing_about_the_setting_is_inconclusive(client, capsys, outcome):
    client(*outcomes_for_every_candidate(outcome))
    code, out = run(capsys)
    assert code == 0
    assert "INCONCLUSIVE  provider default" in out and "REJECTED      provider" not in out
    assert "check credentials, model access, quota" in out
    assert ACCOUNT not in out
    assert "First usable: adaptive thinking" in out


def test_a_throttle_on_one_candidate_does_not_hide_a_usable_one(client, capsys, monkeypatch):
    monkeypatch.setattr(smoke, "REQUEST_FIELDS", {})
    client(*outcomes_for_every_candidate(failed("ThrottlingException", 429, "slow down")))
    code, out = run(capsys)
    assert code == 0
    assert "INCONCLUSIVE  provider default (no thinking field): ThrottlingException" in out
    assert "First usable: adaptive thinking" in out


def test_exit_two_when_nothing_is_usable_and_something_is_inconclusive(client, capsys):
    client(failed("AccessDeniedException", 403, "denied"),
           *[rejected()] * (len(smoke.CANDIDATES) - 1))
    code, out = run(capsys)
    assert code == 2
    assert "No candidate was usable" in out and "inconclusive" in out
    assert "First usable" not in out


def test_exit_two_when_the_only_other_outcome_is_accepted_without_text(client, capsys):
    client(failed("ThrottlingException", 429), *[cut_off()] * (len(smoke.CANDIDATES) - 1))
    assert run(capsys)[0] == 2


def test_exit_one_when_every_candidate_is_refused(client, capsys):
    client(cut_off(), *[rejected()] * (len(smoke.CANDIDATES) - 1))
    code, out = run(capsys)
    assert code == 1 and "Do not release" in out


def test_it_says_when_the_constants_already_match(client, capsys, monkeypatch):
    monkeypatch.setattr(smoke, "REQUEST_FIELDS", {})
    client(*outcomes_for_every_candidate())
    assert "They already are." in run(capsys)[1]


def test_it_says_what_to_change_when_they_differ(client, capsys, monkeypatch):
    monkeypatch.setattr(smoke, "REQUEST_FIELDS", ADAPTIVE)
    client(*outcomes_for_every_candidate())
    out = run(capsys)[1]
    assert "First usable: provider default" in out and "must be {}" in out
    assert "They are {'thinking': {'type': 'adaptive'}} now: change both files." in out


def test_an_empty_candidate_does_not_make_a_match_with_the_constants(client, capsys, monkeypatch):
    monkeypatch.setattr(smoke, "REQUEST_FIELDS", {})
    client(*outcomes_for_every_candidate(cut_off()))
    out = run(capsys)[1]
    assert "They are {} now: change both files." in out and "They already are." not in out


def test_the_effort_levels_are_sorted_by_outcome_and_not_applied(client, capsys, monkeypatch):
    monkeypatch.setattr(smoke, "REQUEST_FIELDS", {})
    client(answered(), answered(), answered(), answered(), rejected(), cut_off())
    code, out = run(capsys)
    assert code == 0
    assert "Effort: usable low; accepted high; rejected medium; inconclusive none." in out
    assert "REQUEST_FIELDS is not changed for effort" in out and "They already are." in out


def test_a_throttled_effort_level_is_inconclusive_not_rejected(client, capsys):
    client(*outcomes_for_every_candidate(*[answered()] * 3, failed("ThrottlingException", 429)))
    out = run(capsys)[1]
    assert "usable medium, high" in out and "inconclusive low" in out
    assert "rejected none" in out


def test_it_says_when_no_effort_level_was_usable(client, capsys):
    client(*outcomes_for_every_candidate(answered(), answered(), answered(),
                                         rejected(), rejected(), rejected()))
    assert "Effort: usable none; accepted none; rejected low, medium, high" in run(capsys)[1]


def test_an_unknown_model_id_fails_the_baseline_and_nothing_is_called_after_it(client, capsys):
    fake = client(rejected("The provided model identifier is invalid."))
    code, out = run(capsys)
    assert code == 2
    assert len(fake.requests) == 1
    assert "REJECTED" not in out and "First usable" not in out and "must be" not in out
    assert "INCONCLUSIVE  provider default (no thinking field): the request fails" in out
    assert "check the model id, Region and access" in out
    assert out.count("INCONCLUSIVE  ") == len(smoke.CANDIDATES)
    assert "not sent: the baseline failed" in out


def test_a_non_validation_baseline_client_error_is_not_a_verdict_either(client, capsys):
    client(failed("AccessDeniedException", 403, "denied"))
    code, out = run(capsys)
    assert code == 2 and "REJECTED" not in out


def test_a_usable_baseline_keeps_a_between_tools_rejection_specific(client, capsys):
    fake = client(*outcomes_for_every_candidate(
        answered(), answered(), rejected("thinking type between_tools is not supported")))
    code, out = run(capsys)
    assert code == 0 and len(fake.requests) == len(smoke.CANDIDATES)
    assert "REJECTED      thinking between_tools: ValidationException" in out
    assert "INCONCLUSIVE" not in out


def test_an_accepted_baseline_with_no_text_also_keeps_later_rejections(client, capsys):
    client(cut_off(), *[rejected()] * (len(smoke.CANDIDATES) - 1))
    code, out = run(capsys)
    assert code == 1 and out.count("REJECTED      ") == len(smoke.CANDIDATES) - 1
