"""Polish uses the one configured model and fails closed; it never swaps in another model."""

import boto3
import pytest
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from backend import bedrock_model, llm_polish
from backend.config import config

ACCOUNT = "123456789012"


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


def reply(text, stop="end_turn"):
    return {"stopReason": stop, "output": {"message": {"content": [{"text": text}]}}}


@pytest.fixture
def use(monkeypatch):
    def install(*outcomes):
        client = FakeClient(*outcomes)
        monkeypatch.setattr(llm_polish, "_bedrock_client", client)
        return client

    return install


def test_the_configured_model_answers(use):
    client = use(reply("This reply is complete."))

    result = llm_polish._polish_sync("Plan my trip", "Deterministic facts.")

    assert result.text == "This reply is complete."
    assert result.model_id == config.bedrock.model_id and result.note is None
    request = client.requests[0]
    assert request["modelId"] == config.bedrock.model_id
    assert request["inferenceConfig"] == {"maxTokens": llm_polish.POLISH_MAX_TOKENS}


def test_the_polish_budget_is_the_factory_output_cap():
    assert llm_polish.POLISH_MAX_TOKENS == 16000


def test_no_thinking_field_is_sent_unless_the_one_constant_names_one(use, monkeypatch):
    client = use(reply("One."), reply("Two."))
    llm_polish._polish_sync("q", "facts")
    assert "additionalModelRequestFields" not in client.requests[0]

    monkeypatch.setattr(bedrock_model, "REQUEST_FIELDS", {"thinking": {"type": "adaptive"}})
    llm_polish._polish_sync("q", "facts")
    assert client.requests[1]["additionalModelRequestFields"] == {"thinking": {"type": "adaptive"}}


MODEL = config.bedrock.model_id
HEAD = "The polish was not applied."
TAIL = "No other model was tried."
CALLED = f"{HEAD} {MODEL} could not be called:"
REPLIED = f"{HEAD} {MODEL} replied, but"
BUDGET = (
    f"Check the output budget, POLISH_MAX_TOKENS ({llm_polish.POLISH_MAX_TOKENS} tokens): "
    "output tokens, including any thinking, count against it."
)
GUARDRAIL = "A content filter or guardrail stopped it."
CONTEXT = "It reached the model's context window; retrying the same input will not help."
ACCESS = (
    "Check that this account has access to the model and that the role allows "
    "bedrock:InvokeModel on the model or its inference profile."
)
THROTTLE = "Try again shortly."
ATTEMPTS = llm_polish.MAX_ATTEMPTS
READ_TIMEOUT = (
    f"The last attempt timed out after {llm_polish.READ_TIMEOUT_SECONDS} seconds; "
    f"the client makes up to {ATTEMPTS} attempts in all. Try again."
)
CONNECT_TIMEOUT = (
    f"The last attempt timed out after {llm_polish.CONNECT_TIMEOUT_SECONDS} seconds; "
    f"the client makes up to {ATTEMPTS} attempts in all. Try again."
)
MESSAGE = f"problem on arn:aws:bedrock:us-east-1:{ACCOUNT}:inference-profile/x"
MASKED = "problem on arn:aws:bedrock:us-east-1:<acct>:inference-profile/x"
ENDPOINT = "https://bedrock-runtime.us-east-1.amazonaws.com"


def client_error(code, message=MESSAGE):
    return ClientError({"Error": {"Code": code, "Message": message}}, "Converse")


def modeled_error(code, message=MESSAGE):
    """The subclass boto3 raises for a modeled code, e.g. client.exceptions.ThrottlingException."""
    client = boto3.client(
        "bedrock-runtime", region_name="us-east-1",
        aws_access_key_id="test", aws_secret_access_key="test",
    )
    error_class = client.exceptions.from_code(code)
    assert error_class is not ClientError
    return error_class({"Error": {"Code": code, "Message": message}}, "Converse")


def no_text(stop):
    return {"stopReason": stop, "output": {"message": {"content": [{"reasoningContent": {}}]}}}


def with_text(stop, text="A complete sentence."):
    return reply(text, stop=stop) if stop else {
        "output": {"message": {"content": [{"text": text}]}}}


def stop_note(stop, advice=""):
    return f"{REPLIED} it stopped with stop reason {stop}.{advice and ' ' + advice} {TAIL}"


STOP_CASES = [
    (with_text("max_tokens"), stop_note("max_tokens", BUDGET)),
    (no_text("max_tokens"), stop_note("max_tokens", BUDGET)),
    (with_text("content_filtered"), stop_note("content_filtered", GUARDRAIL)),
    (no_text("content_filtered"), stop_note("content_filtered", GUARDRAIL)),
    (with_text("guardrail_intervened"), stop_note("guardrail_intervened", GUARDRAIL)),
    (no_text("guardrail_intervened"), stop_note("guardrail_intervened", GUARDRAIL)),
    (with_text("model_context_window_exceeded"),
     stop_note("model_context_window_exceeded", CONTEXT)),
    (no_text("model_context_window_exceeded"),
     stop_note("model_context_window_exceeded", CONTEXT)),
    (with_text("tool_use"), stop_note("tool_use")),
    (with_text("stop_sequence"), stop_note("stop_sequence")),
    (with_text(None), f"{REPLIED} it returned no stop reason. {TAIL}"),
    ({"stopReason": None, "output": {"message": {"content": [{"text": "Done."}]}}},
     f"{REPLIED} it returned no stop reason. {TAIL}"),
    (no_text("end_turn"), f"{REPLIED} it returned no text. {TAIL}"),
    ({"stopReason": "end_turn", "output": {"message": {"content": []}}},
     f"{REPLIED} it returned no text. {TAIL}"),
    (reply("Still missing its ending"), f"{REPLIED} its reply looked cut off. {TAIL}"),
]


@pytest.mark.parametrize(("response", "note"), STOP_CASES)
def test_a_reply_that_is_not_used_is_stated_as_returned(use, response, note):
    client = use(response)

    result = llm_polish._polish_sync("Plan my trip", "Deterministic facts.")

    assert result.text == "Deterministic facts." and result.model_id is None
    assert result.note == note and len(client.requests) == 1


CALL_CASES = [
    (client_error("AccessDeniedException"),
     f"{CALLED} AccessDeniedException: {MASKED}. {ACCESS} {TAIL}"),
    (modeled_error("AccessDeniedException"),
     f"{CALLED} AccessDeniedException: {MASKED}. {ACCESS} {TAIL}"),
    (client_error("ThrottlingException"),
     f"{CALLED} ThrottlingException: {MASKED}. {THROTTLE} {TAIL}"),
    (modeled_error("ThrottlingException"),
     f"{CALLED} ThrottlingException: {MASKED}. {THROTTLE} {TAIL}"),
    (modeled_error("ValidationException", f"Input is too long for the model. {MESSAGE}"),
     f"{CALLED} ValidationException: Input is too long for the model. {MASKED}. {TAIL}"),
    (client_error("ResourceNotFoundException"),
     f"{CALLED} ResourceNotFoundException: {MASKED}. {TAIL}"),
    (client_error("ExpiredTokenException"), f"{CALLED} ExpiredTokenException: {MASKED}. {TAIL}"),
    (client_error("ServiceQuotaExceededException"),
     f"{CALLED} ServiceQuotaExceededException: {MASKED}. {TAIL}"),
    (modeled_error("ModelTimeoutException"),
     f"{CALLED} ModelTimeoutException: {MASKED}. {TAIL}"),
    (client_error("ThrottlingException", "Too many requests."),
     f"{CALLED} ThrottlingException: Too many requests. {THROTTLE} {TAIL}"),
    (client_error("ValidationException", ""), f"{CALLED} ValidationException. {TAIL}"),
    (client_error("ValidationException", "Is the model id right?"),
     f"{CALLED} ValidationException: Is the model id right. {TAIL}"),
    (client_error("ValidationException", "Invalid model:"),
     f"{CALLED} ValidationException: Invalid model. {TAIL}"),
    (ReadTimeoutError(endpoint_url=ENDPOINT),
     f'{CALLED} ReadTimeoutError: Read timeout on endpoint URL: "{ENDPOINT}". '
     f"{READ_TIMEOUT} {TAIL}"),
    (ConnectTimeoutError(endpoint_url=ENDPOINT),
     f'{CALLED} ConnectTimeoutError: Connect timeout on endpoint URL: "{ENDPOINT}". '
     f"{CONNECT_TIMEOUT} {TAIL}"),
    (EndpointConnectionError(endpoint_url=ENDPOINT),
     f'{CALLED} EndpointConnectionError: Could not connect to the endpoint URL: "{ENDPOINT}". '
     f"{TAIL}"),
    (RuntimeError(MESSAGE), f"{CALLED} RuntimeError: {MASKED}. {TAIL}"),
    (RuntimeError(), f"{CALLED} RuntimeError. {TAIL}"),
]


@pytest.mark.parametrize(("failure", "note"), CALL_CASES)
def test_a_call_error_states_its_code_once_and_the_masked_message(use, failure, note):
    client = use(failure)

    result = llm_polish._polish_sync("q", "facts")

    assert result.text == "facts" and result.model_id is None
    assert result.note == note and len(client.requests) == 1
    assert ACCOUNT not in result.note


def test_a_long_message_is_cut_at_a_word_boundary_and_marked(use):
    words = " ".join(["word"] * 100)
    use(RuntimeError(words))

    note = llm_polish._polish_sync("q", "facts").note

    kept = ("word " * 40).strip()
    assert note == f"{CALLED} RuntimeError: {kept}... {TAIL}"
    assert len(kept) <= 200


def test_a_long_message_with_no_space_is_still_cut_and_marked(use):
    use(RuntimeError("x" * 1000))

    note = llm_polish._polish_sync("q", "facts").note

    assert note == f"{CALLED} RuntimeError: {'x' * 200}... {TAIL}"


def test_an_empty_tool_output_is_not_sent(use):
    client = use()
    result = llm_polish._polish_sync("q", "")
    assert result.note == "empty tool output" and client.requests == []
    assert result.skipped


def test_the_client_retries_transient_errors_adaptively(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr(llm_polish, "_bedrock_client", None)
    client = llm_polish._client()
    monkeypatch.setattr(llm_polish, "_bedrock_client", None)

    retries = client.meta.config.retries
    assert retries["mode"] == "adaptive"
    assert retries["total_max_attempts"] == llm_polish.MAX_ATTEMPTS


def test_the_timeout_note_states_the_attempts_botocore_makes(use, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr(llm_polish, "_bedrock_client", None)
    total = llm_polish._client().meta.config.retries["total_max_attempts"]
    use(ReadTimeoutError(endpoint_url=ENDPOINT))

    note = llm_polish._polish_sync("q", "facts").note

    assert f"makes up to {total} attempts in all" in note


def test_there_is_no_fallback_chain_left():
    assert not hasattr(llm_polish, "_candidate_models")
    assert not hasattr(llm_polish, "_DEFAULT_FALLBACK_CHAIN")
