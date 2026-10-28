"""A tool that could not be set up ends the turn with that error; ordinary failures do not.

Every test runs a real Strands ``Agent`` from the installed strands-agents with a scripted model,
through Phase 3's ``run_retrieval_turn`` or the Concierge Runtime's own ``main.py``. A test that
fired the hook through a ``HookRegistry`` alone could not see what the tool executor and the
event loop do with it, so these tests pin the behavior of the installed version.
"""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    CredentialRetrievalError,
    EndpointConnectionError,
    EndpointResolutionError,
    LoginRefreshRequired,
    NoCredentialsError,
    NoRegionError,
    PartialCredentialsError,
    ReadTimeoutError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)
from strands import Agent, tool
from strands.models import Model
from strands.session.session_manager import SessionManager
from strands.tools.executors import SequentialToolExecutor
from strands.types.exceptions import MCPClientInitializationError, ToolProviderException

from backend.agents import strands_hooks
from backend.agents.phase_03_retrieval import agent as phase3
from backend.agents.phase_04_production import concierge as concierge_mod
from backend.agents.strands_hooks import StopOnToolSetupError, ToolSetupError
from backend.audit_vocabulary import ToolCall
from backend.tools import catalog_tools
from tests.runtime_support import load_runtime_main
from tests.test_audit_outcome import RUNTIME_PATHS, committed, decision_with, runtime_streams
from tests.test_production_transaction_boundaries import build_agent
from tests.test_runtime_span_timings import FakeClock, FakeSession

MERIDIAN = Path(__file__).resolve().parents[1]
RUNTIME = MERIDIAN / "meridian_agentcore" / "app" / "MeridianConcierge"
ACCOUNT = "123456789012"
spec = importlib.util.spec_from_file_location("concierge_tool_errors", RUNTIME / "tool_errors.py")
runtime_copy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime_copy)

CEDAR_DENIAL = (
    "Tool execution failed: AuthorizeActionException - Tool Execution Denied: "
    "Tool call not allowed due to policy enforcement"
)
# What the model would say if it were called again after a failed call.
DECLINE = "I am sorry, I could not look that up right now."
# The reply a stopped turn leaves in the conversation, which AgentCore Memory keeps.
STOPPED_REPLY = ("I could not complete that request because a service it needs is unavailable. "
                 "Please try again later.")
STOPPED = ("assistant", [{"text": STOPPED_REPLY}])
DETAILS = "MeridianHolds___get_package_details"


def client_error(code, message="no", operation="ExecuteStatement"):
    """A botocore ClientError as an AWS service returns it, with its error code."""
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


SETUP_FAILURES = {
    "MCPClientInitializationError": lambda: MCPClientInitializationError("gateway unreachable"),
    "ToolProviderException": lambda: ToolProviderException("tool listing failed"),
    "AccessDeniedException": lambda: client_error("AccessDeniedException"),
    "NoCredentialsError": NoCredentialsError,
}
ORDINARY_FAILURES = {
    "validation error": lambda: ValueError("package id is not a trip"),
    "Cedar denial raised": lambda: RuntimeError(CEDAR_DENIAL),
    "Cedar denial result": lambda: {"status": "error", "content": [{"text": CEDAR_DENIAL}]},
    "ThrottlingException": lambda: client_error("ThrottlingException", "Too many requests"),
    "StatementTimeoutException": lambda: client_error(
        "StatementTimeoutException", "The execution of the SQL statement timed out."),
}


# ------------------------------------------------------------------- the scripted model

def calls(name, **arguments):
    """A model reply that calls one tool."""
    return calls_each((name, arguments))


def calls_each(*requests):
    """A model reply that calls several tools, in this order: ``(name, arguments)`` pairs."""
    return ("tools", requests)


def says(text):
    """A model reply that answers with text and ends its turn."""
    return ("text", text)


class ScriptedModel(Model):
    """A model provider that answers each call with its next scripted reply and counts calls."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def update_config(self, **_config):
        pass

    def get_config(self):
        return {}

    def structured_output(self, *_args, **_kwargs):
        raise NotImplementedError("These tests never ask for structured output.")

    async def stream(self, _messages, _tool_specs=None, _system_prompt=None, **_kwargs):
        self.calls += 1
        kind, reply = self.replies.pop(0)
        yield {"messageStart": {"role": "assistant"}}
        if kind == "tools":
            for index, (name, arguments) in enumerate(reply):
                start = {"toolUse": {"toolUseId": f"call-{self.calls}-{index}", "name": name}}
                yield {"contentBlockStart": {"start": start}}
                delta = {"toolUse": {"input": json.dumps(arguments)}}
                yield {"contentBlockDelta": {"delta": delta}}
                yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
            return
        yield {"contentBlockDelta": {"delta": {"text": reply}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}


def spoken(message):
    """Who said a message and what it says, without the tracking id Strands adds."""
    return message["role"], message["content"]


def outcome_of(outcome):
    """Raise ``outcome`` when it is an exception, otherwise return it as the tool's result."""
    if isinstance(outcome, Exception):
        raise outcome
    return outcome


# ------------------------------------------------------------- the Phase 3 agent

HYBRID = "hybrid_trip_search"
NO_TRIPS = {"packages": [], "candidates": {"semantic": 0, "lexical": 0, "merged": 0},
            "reranked": False}


class NoMcpTools:
    """Phase 3's MCP pool with no MCP tools: the turn runs only its two in-process tools."""

    def __init__(self):
        self.releases, self.resets = [], []

    def acquire(self):
        return 1, []

    def release(self, generation):
        self.releases.append(generation)

    def reset(self, generation):
        self.resets.append(generation)


def phase3_with(monkeypatch, outcomes, *replies):
    """A scripted model and a pool for Phase 3 turns whose hybrid search raises or returns
    each of ``outcomes`` in turn."""
    remaining = list(outcomes)

    async def search(_query, limit=5):
        return outcome_of(remaining.pop(0)), []

    monkeypatch.setattr(catalog_tools, "hybrid_trip_search_with_spans", search)
    return ScriptedModel(*replies), NoMcpTools()


def phase3_turn(model, pool):
    return asyncio.run(phase3.run_retrieval_turn(
        "Tokyo culture trips", traveler_id="trv_hook_test", model=model, pool=pool))


@pytest.mark.parametrize("failure", SETUP_FAILURES.values(), ids=SETUP_FAILURES)
def test_a_tool_that_could_not_be_set_up_ends_the_phase_3_turn(monkeypatch, failure):
    error = failure()
    model, pool = phase3_with(
        monkeypatch, [error], calls(HYBRID, query="Tokyo culture trips"), says(DECLINE))

    with pytest.raises(phase3.RetrievalTurnError) as stopped:
        phase3_turn(model, pool)

    assert model.calls == 1
    cause = stopped.value.__cause__
    assert isinstance(cause, ToolSetupError) and cause.__cause__ is error
    assert str(cause) == f"Tool {HYBRID} could not run ({type(error).__name__}): {error}"
    assert pool.releases == [1] and pool.resets == [], "a local tool's error says nothing of MCP"


@pytest.mark.parametrize("failure", ORDINARY_FAILURES.values(), ids=ORDINARY_FAILURES)
def test_an_ordinary_tool_failure_still_reaches_the_phase_3_model(monkeypatch, failure):
    model, pool = phase3_with(
        monkeypatch, [failure()], calls(HYBRID, query="Tokyo"), says("No trip fits."))

    turn = phase3_turn(model, pool)

    assert model.calls == 2
    assert turn.message == "No trip fits." and pool.resets == []


def test_a_stopped_phase_3_turn_raises_the_masked_error_and_shows_no_reply(monkeypatch):
    failure = client_error(
        "AccessDeniedException", f"not authorized on arn:aws:iam::{ACCOUNT}:role/x", "Converse")
    model, pool = phase3_with(monkeypatch, [failure], calls(HYBRID, query="x"))

    with pytest.raises(phase3.RetrievalTurnError) as stopped:
        phase3_turn(model, pool)

    cause = str(stopped.value.__cause__)
    assert "<acct>" in cause and ACCOUNT not in cause
    assert ACCOUNT not in str(stopped.value) and STOPPED_REPLY not in str(stopped.value)


def test_the_next_phase_3_turn_after_a_stopped_one_is_answered(monkeypatch):
    model, pool = phase3_with(
        monkeypatch, [NoCredentialsError(), NO_TRIPS],
        calls(HYBRID, query="Tokyo"), calls(HYBRID, query="Tokyo"), says("No trip fits."))

    with pytest.raises(phase3.RetrievalTurnError):
        phase3_turn(model, pool)
    turn = phase3_turn(model, pool)

    assert model.calls == 3
    assert turn.message == "No trip fits." and pool.releases == [1, 1] and pool.resets == []


# ------------------------------------------------------------- the Concierge Runtime

TURN = {"event": "concierge_turn", "traveler_id": "trv_hook_test",
        "conversation_id": "conv-hook", "prompt": "Find Tokyo culture trips"}


class Gateway:
    """The Gateway as the Runtime's MCP client sees it: the tools it lists and direct calls."""

    def __init__(self, tools=(), call=None):
        self.tools = list(tools)
        self.call = call

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def list_tools_sync(self):
        return self.tools

    def call_tool_sync(self, tool_use_id, name, arguments, read_timeout_seconds=None):
        return self.call(tool_use_id, name, arguments)


def package_details(outcome, name=DETAILS):
    """A Gateway tool named as the Gateway lists it, which raises or returns ``outcome``."""

    @tool(name=name)
    def get_package_details(package_id: str) -> dict:
        """Live durations and availability for one package.

        Args:
            package_id: The package to read.
        """
        return outcome_of(outcome)

    return get_package_details


async def collect(events):
    return [event async for event in events]


class Remembered(SessionManager):
    """AgentCore Memory as the Runtime's Agent sees it: it keeps every message added."""

    session_id = "conv-hook"

    def __init__(self):
        self.messages = []

    def append_message(self, message, agent, **kwargs) -> None:
        self.messages.append(message)

    def redact_latest_message(self, redact_message, agent, **kwargs) -> None:
        raise AssertionError("These tests never redact a message.")

    def sync_agent(self, agent, **kwargs) -> None:
        pass

    def initialize(self, agent, **kwargs) -> None:
        pass


def run_concierge(monkeypatch, model, gateway, payload=TURN, *, memory=None):
    """Run one turn through the Runtime's real entrypoint; return what it yielded.

    The Runtime builds its own Agent and hooks exactly as deployed. Only the Gateway, the model,
    Memory and the trace hook's class are replaced; the trace hook class still emits every span,
    and also records the exception class of each tool call it sees. Memory is off unless
    ``memory`` is given.

    Returns:
        The JSON strings the entrypoint yielded, and the exception classes the trace hook saw.
    """
    runtime = load_runtime_main(monkeypatch, "meridian_runtime_tool_setup")
    runtime.SESSION = FakeSession(FakeClock())
    runtime.memory_session = lambda: runtime.SESSION
    runtime.memory_manager = lambda *_args: memory
    runtime.concierge_model = lambda: model
    runtime.MCPClient = lambda **_kwargs: gateway
    seen = []

    class RecordingTraceHooks(runtime.TraceHooks):
        def after(self, event) -> None:
            exception = getattr(event, "exception", None)
            seen.append(type(exception).__name__ if exception else None)
            super().after(event)

    runtime.TraceHooks = RecordingTraceHooks
    return asyncio.run(collect(runtime.invoke(payload))), seen


def framed(yielded):
    """The Runtime's JSON strings as the AgentCore app streams them: JSON inside JSON."""
    return b"".join(f"data: {json.dumps(item)}\n\n".encode() for item in yielded)


def stored_after(monkeypatch, yielded, operation):
    """Stream the Runtime's output to the backend's real adapter and Phase 4 path.

    Returns:
        The one audit row the backend committed, and the error that reached its caller.
    """
    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda **_kw: None)
    rows = []

    async def write_audit(**row):
        rows.append(row)
        return "aud_test"

    agent = build_agent([], decision_with(), [])
    agent.store.write_audit = write_audit
    runtime_streams(agent, framed(yielded))
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(RUNTIME_PATHS[operation](agent))
    (row,) = committed(agent, rows)
    return row, raised.value


@pytest.mark.parametrize("failure", SETUP_FAILURES.values(), ids=SETUP_FAILURES)
def test_a_tool_that_could_not_be_set_up_ends_the_runtime_turn_with_an_error_event(
        monkeypatch, failure):
    error = failure()
    model = ScriptedModel(calls(DETAILS, package_id="CTY-002"), says(DECLINE))

    yielded, seen = run_concierge(monkeypatch, model, Gateway([package_details(error)]))

    events = [json.loads(item) for item in yielded]
    message = f"Tool {DETAILS} could not run ({type(error).__name__}): {error}"
    assert model.calls == 1
    assert seen == [type(error).__name__]
    assert events[-1] == {"type": "error", "error_type": "ToolSetupError", "message": message}
    assert [event for event in events if event["type"] in ("token", "result")] == []
    assert [event["title"] for event in events if event.get("activity_type") == "error"] == [
        "Tool call failed"]


def test_the_backend_records_the_failed_call_of_a_runtime_turn_that_could_not_set_up_a_tool(
        monkeypatch):
    model = ScriptedModel(calls(DETAILS, package_id="CTY-002"), says(DECLINE))
    gateway = Gateway([package_details(NoCredentialsError())])
    yielded, _seen = run_concierge(monkeypatch, model, gateway)

    row, raised = stored_after(monkeypatch, yielded, "production_turn")

    assert row["tool_calls"] == [ToolCall("get_package_details", "error")]
    assert row["authorization_decision"] == "error"
    assert row["turn_error"] == (f"ToolSetupError: Tool {DETAILS} could not run "
                                 "(NoCredentialsError): Unable to locate credentials")
    assert str(raised) == (f"AgentCore Runtime error: Tool {DETAILS} could not run "
                           "(NoCredentialsError): Unable to locate credentials")


def test_the_runtime_remembers_a_fixed_reply_for_a_turn_it_stopped(monkeypatch):
    failure = client_error("AccessDeniedException", f"not authorized on {ACCOUNT}", "Invoke")
    model = ScriptedModel(calls(DETAILS, package_id="CTY-002"), says(DECLINE))
    memory = Remembered()

    yielded, _seen = run_concierge(
        monkeypatch, model, Gateway([package_details(failure)]), memory=memory)

    assert json.loads(yielded[-1])["type"] == "error"
    assert [message["role"] for message in memory.messages] == [
        "user", "assistant", "user", "assistant"]
    assert spoken(memory.messages[-1]) == STOPPED


IAM_REFUSAL = (f"User: arn:aws:sts::{ACCOUNT}:assumed-role/MeridianConcierge/session is not "
               "authorized to perform: bedrock:InvokeModelWithResponseStream")


class RefusedModel(ScriptedModel):
    """A model whose call IAM refuses, as Amazon Bedrock answers a role without access."""

    async def stream(self, *_args, **_kwargs):
        self.calls += 1
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": IAM_REFUSAL}},
                          "ConverseStream")
        yield  # Unreachable; it makes stream an async generator, as the Model contract requires.


def test_the_backend_names_the_error_of_a_runtime_turn_whose_model_call_iam_refused(
        monkeypatch):
    yielded, seen = run_concierge(monkeypatch, RefusedModel(), Gateway([]))

    events = [json.loads(item) for item in yielded]
    assert seen == []
    assert events[-1]["type"] == "error" and events[-1]["error_type"] == "ClientError"
    row, raised = stored_after(monkeypatch, yielded, "production_turn")

    assert row["tool_calls"] == [] and row["authorization_decision"] == "error"
    assert row["turn_error"] == (
        "ClientError: An error occurred (AccessDeniedException) when calling the ConverseStream "
        "operation: " + IAM_REFUSAL.replace(ACCOUNT, "<acct>"))
    assert "AccessDeniedException" in str(raised)


@pytest.mark.parametrize("failure", ORDINARY_FAILURES.values(), ids=ORDINARY_FAILURES)
def test_an_ordinary_tool_failure_still_reaches_the_runtime_model(monkeypatch, failure):
    model = ScriptedModel(calls(DETAILS, package_id="CTY-002"), says("That trip is not open."))

    yielded, seen = run_concierge(monkeypatch, model, Gateway([package_details(failure())]))

    events = [json.loads(item) for item in yielded]
    assert model.calls == 2
    assert len(seen) == 1
    assert events[-1]["type"] == "result"
    assert events[-1]["message"] == "That trip is not open."


# ----------------------------------------------------- the two copies of the hook agree

COPIES = {"backend": strands_hooks, "runtime": runtime_copy}
DATA_API = "https://rds-data.us-east-1.amazonaws.com"
# A credential, permission or configuration problem: retrying the same call cannot succeed.
SETUP_CLASSIFIED = {
    **{code: lambda code=code: client_error(code) for code in (
        "AccessDeniedException", "AccessDenied", "UnrecognizedClientException",
        "ExpiredTokenException", "ExpiredToken", "InvalidSignatureException",
        "ResourceNotFoundException", "ForbiddenException", "HttpEndpointNotEnabledException",
        "InvalidSecretException", "DatabaseNotFoundException")},
    "unknown model": lambda: client_error(
        "ValidationException", "The provided model identifier is invalid.", "Converse"),
    "endpoint not found": lambda: client_error(
        "ValidationException", "The requested endpoint was not found.", "InvokeModel"),
    "NoCredentialsError": NoCredentialsError,
    "PartialCredentialsError": lambda: PartialCredentialsError(
        provider="env", cred_var="AWS_SECRET_ACCESS_KEY"),
    "CredentialRetrievalError": lambda: CredentialRetrievalError(
        provider="container-role", error_msg="no role"),
    "TokenRetrievalError": lambda: TokenRetrievalError(provider="sso", error_msg="expired"),
    "UnauthorizedSSOTokenError": UnauthorizedSSOTokenError,
    "LoginRefreshRequired": LoginRefreshRequired,
    "NoRegionError": NoRegionError,
    "EndpointResolutionError": lambda: EndpointResolutionError(msg="no endpoint for Region"),
    "MCPClientInitializationError": lambda: MCPClientInitializationError("not running"),
    "ToolProviderException": lambda: ToolProviderException("tool listing failed"),
}
# A transient failure or bad input: the model, or the Phase 3 search re-run, can still recover.
NOT_SETUP_CLASSIFIED = {
    **{code: lambda code=code: client_error(code) for code in (
        "ThrottlingException", "StatementTimeoutException", "ServiceUnavailable",
        "ServiceUnavailableError", "ServiceUnavailableException", "ModelTimeoutException",
        "InternalServerException", "DatabaseResumingException", "DatabaseUnavailableException",
        "NotFoundException", "SecretsErrorException")},
    "bad input": lambda: client_error(
        "ValidationException", "Malformed input request, please reformat your input.", "Converse"),
    "ValidationException without a message": lambda: ClientError(
        {"Error": {"Code": "ValidationException"}}, "Converse"),
    "ClientError without an error": lambda: ClientError({}, "Converse"),
    "ReadTimeoutError": lambda: ReadTimeoutError(endpoint_url=DATA_API),
    "ConnectionClosedError": lambda: ConnectionClosedError(endpoint_url=DATA_API),
    "EndpointConnectionError": lambda: EndpointConnectionError(endpoint_url=DATA_API),
    "BotoCoreError": BotoCoreError,
    "ValueError": lambda: ValueError("package id is not a trip"),
    "no exception": lambda: None,
}


@pytest.mark.parametrize("copy", COPIES.values(), ids=COPIES)
@pytest.mark.parametrize("error", SETUP_CLASSIFIED.values(), ids=SETUP_CLASSIFIED)
def test_a_credential_permission_or_configuration_error_is_a_setup_error(copy, error):
    assert copy.is_setup_error(error())


@pytest.mark.parametrize("copy", COPIES.values(), ids=COPIES)
@pytest.mark.parametrize("error", NOT_SETUP_CLASSIFIED.values(), ids=NOT_SETUP_CLASSIFIED)
def test_a_transient_or_input_error_is_not_a_setup_error(copy, error):
    assert not copy.is_setup_error(error())


AVAILABILITY = "MeridianHolds___get_availability"


@pytest.mark.parametrize("copy", COPIES.values(), ids=COPIES)
def test_on_the_sequential_executor_the_first_requested_setup_error_is_reported(copy):
    first, second = ToolProviderException("tool listing failed"), NoCredentialsError()
    model = ScriptedModel(calls_each((DETAILS, {"package_id": "CTY-002"}),
                                     (AVAILABILITY, {"package_id": "CTY-002"})), says(DECLINE))
    stop = copy.StopOnToolSetupError()
    agent = Agent(model=model, hooks=[stop], callback_handler=None,
                  tools=[package_details(first), package_details(second, AVAILABILITY)],
                  tool_executor=SequentialToolExecutor())

    agent("Is CTY-002 open?")

    with pytest.raises(copy.ToolSetupError) as stopped:
        stop.raise_if_stopped()
    assert model.calls == 1
    assert str(stopped.value) == (
        f"Tool {DETAILS} could not run (ToolProviderException): tool listing failed")
    assert stopped.value.__cause__ is first


def stopped_turn(provider, error):
    """Run one Agent turn whose only tool raises ``error``.

    Returns:
        What the caller's ``raise_if_stopped`` raises, and the last message of the turn.
    """
    model = ScriptedModel(calls(DETAILS, package_id="CTY-002"), says(DECLINE))
    agent = Agent(model=model, tools=[package_details(error)], hooks=[provider],
                  callback_handler=None)
    agent("Is CTY-002 open?")
    with pytest.raises(Exception) as stopped:
        provider.raise_if_stopped()
    assert stopped.value.__class__.__name__ == "ToolSetupError"
    return str(stopped.value), spoken(agent.messages[-1])


def test_the_runtime_copy_and_the_backend_hook_agree():
    assert [e.__name__ for e in strands_hooks.SETUP_ERRORS] == [
        e.__name__ for e in runtime_copy.SETUP_ERRORS]
    assert strands_hooks.SETUP_CODES == runtime_copy.SETUP_CODES
    for name in ("pattern", "flags"):
        assert getattr(strands_hooks.MODEL_OR_ENDPOINT_NOT_FOUND, name) == getattr(
            runtime_copy.MODEL_OR_ENDPOINT_NOT_FOUND, name)
    error = client_error("ExpiredTokenException", f"token for {ACCOUNT} expired", "Invoke")
    turns = [stopped_turn(provider, error)
             for provider in (StopOnToolSetupError(), runtime_copy.StopOnToolSetupError())]
    assert turns[0] == turns[1]
    assert turns[0][1] == STOPPED


def test_the_runtime_copy_masks_exactly_what_the_backend_masks():
    from backend.security.redaction import ACCOUNT_ID_PATTERN

    assert runtime_copy.ACCOUNT_ID.pattern == ACCOUNT_ID_PATTERN.pattern
