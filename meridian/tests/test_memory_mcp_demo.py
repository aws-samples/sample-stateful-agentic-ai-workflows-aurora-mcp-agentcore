"""The memory MCP example passes only on the expected traveler-binding denial."""

import asyncio

import pytest

from backend.mcp import memory_mcp_client as client_module
from backend.mcp.memory_mcp_client import MemoryToolError, TravelerNotAuthorized, tool_error
from examples import memory_mcp_demo as demo
from scripts import travel_catalog

DENIAL = ("Error executing tool recall_traveler_profile: aws_iam subject is not authorized "
          "for traveler trv_demo_decoy")


class StubClient:
    """A client whose decoy call allows, denies as the server does, or fails another way."""

    tools = [{"name": "recall_traveler_profile", "description": "profile"}]
    on_decoy: object = {"ok": True}

    def __init__(self):
        self.disconnected = False
        self.calls = []

    async def connect(self):
        return None

    async def disconnect(self):
        self.disconnected = True

    async def call(self, name, arguments, timeout=20.0):
        self.calls.append((name, arguments))
        if arguments.get("traveler_id") == "trv_demo_decoy":
            if isinstance(self.on_decoy, Exception):
                raise self.on_decoy
            return self.on_decoy
        return []


def run_demo(monkeypatch, *, decoy):
    stub = type("Stub", (StubClient,), {"on_decoy": decoy})
    made = []

    def factory():
        made.append(stub())
        return made[0]

    monkeypatch.setattr(demo, "MeridianMemoryMCPClient", factory)
    try:
        asyncio.run(demo.run("trv_meridian_demo", "conv_tokyo_october"))
    finally:
        client = made[0]
    return client


def test_the_expected_denial_passes_and_the_client_is_closed(monkeypatch, capsys):
    client = run_demo(monkeypatch, decoy=tool_error(DENIAL))

    assert client.disconnected is True
    out = capsys.readouterr().out
    assert "DENY trv_demo_decoy" in out and "Done: memory MCP server exercised" in out


def test_a_database_error_fails_the_example_instead_of_printing_a_denial(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exit_info:
        run_demo(monkeypatch, decoy=tool_error("Error executing tool x: database unavailable"))

    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "FAILED, not a denial" in out and "DENY" not in out
    assert "Done:" not in out


def test_a_transport_error_also_fails(monkeypatch):
    with pytest.raises(SystemExit) as exit_info:
        run_demo(monkeypatch, decoy=TimeoutError("no reply"))

    assert exit_info.value.code == 1


def test_an_unexpected_allow_fails(monkeypatch):
    with pytest.raises(SystemExit) as exit_info:
        run_demo(monkeypatch, decoy={"ok": True})

    assert exit_info.value.code == 1


def test_a_denial_that_names_another_traveler_fails(monkeypatch):
    other = tool_error(DENIAL.replace("trv_demo_decoy", "trv_someone_else"))

    with pytest.raises(SystemExit) as exit_info:
        run_demo(monkeypatch, decoy=other)

    assert exit_info.value.code == 1


def test_the_client_separates_the_binding_denial_from_every_other_tool_error():
    denial = tool_error(DENIAL)
    other = tool_error("Error executing tool x: database unavailable")

    assert isinstance(denial, TravelerNotAuthorized) and denial.traveler_id == "trv_demo_decoy"
    assert isinstance(denial, PermissionError)
    assert isinstance(other, MemoryToolError) and not isinstance(other, PermissionError)


def test_call_raises_the_typed_error_for_an_error_result(monkeypatch):
    class Session:
        async def call_tool(self, tool, arguments):
            return type("R", (), {"isError": True, "content": [], "structuredContent": None})()

    client = client_module.MeridianMemoryMCPClient()
    client.session, client._connected = Session(), True
    monkeypatch.setattr(client_module, "_decode", lambda result: "database unavailable")

    with pytest.raises(MemoryToolError, match="database unavailable"):
        asyncio.run(client.call("recall_traveler_profile", {"traveler_id": "x"}))


def test_the_default_conversation_is_a_seeded_tokyo_conversation_with_turns():
    seeded = {c["conversation_id"] for c in travel_catalog.DEMO_CONVERSATIONS}
    turns = [m for m in travel_catalog.DEMO_CONVERSATION_MESSAGES
             if m["conversation_id"] == demo.DEFAULT_CONVERSATION]

    assert demo.DEFAULT_CONVERSATION == "conv_tokyo_october"
    assert demo.DEFAULT_CONVERSATION in seeded and len(turns) >= 2
    assert demo.DEFAULT_CONVERSATION in demo.__doc__
    assert "conv_meridian_demo" not in demo.__doc__


def test_the_text_the_real_server_sends_for_a_refused_claim_is_classified_as_a_denial(
        monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from backend import authorization
    from backend.mcp import memory_server

    decision = authorization.AuthorizationDecision(
        allowed=False, decision="deny", traveler_id="trv_demo_decoy", provider="aws_iam",
        subject_id="arn:aws:sts::123456789012:assumed-role/x/y", principal="x", binding_id=None,
        audit_id=None, reason="no active identity binding")

    @asynccontextmanager
    async def refuse(**_kwargs):
        raise authorization.TravelerAuthorizationError(decision)
        yield

    monkeypatch.setattr(memory_server, "_store", lambda: SimpleNamespace(
        db=SimpleNamespace(scoped_session=refuse)))
    monkeypatch.setattr(memory_server, "_authorization", lambda: None)

    with pytest.raises(Exception) as sent:  # noqa: PT011 - FastMCP's ToolError wraps the cause
        asyncio.run(memory_server.mcp.call_tool(
            "recall_traveler_profile", {"traveler_id": "trv_demo_decoy"}))

    classified = tool_error(str(sent.value))
    assert isinstance(classified, TravelerNotAuthorized)
    assert classified.traveler_id == "trv_demo_decoy"
