"""The local Runtime harness sends the caller the way the Runtime receives one."""

import pytest

from backend.agents.phase_05_workflow.runtime_entry import parse_turn, workflow_turn
from backend.agents.phase_05_workflow.state import WorkflowAuthorizationError
from tests.jwt_support import access_token
from tests.workflow_runtime_caller import runtime_headers

PAYLOAD = {"event": "workflow_turn", "mode": "start", "thread_id": "t", "query": "q",
           "traveler_id": "trv_meridian_demo"}


def mint_for_jordan(user_key):
    assert user_key == "jordan"
    return access_token("trv_meridian_demo")


def test_in_jwt_mode_the_minted_token_goes_in_the_authorization_header(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    headers = runtime_headers("trv_meridian_demo", mint=mint_for_jordan)
    assert headers["Authorization"].startswith("Bearer ")
    command = parse_turn(PAYLOAD, "s", headers)
    assert (command.traveler_id, command.caller_subject) == ("trv_meridian_demo", "sub-test")


def test_in_jwt_mode_the_entry_still_refuses_a_turn_with_no_headers(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    with pytest.raises(WorkflowAuthorizationError, match="No signed-in caller"):
        parse_turn(PAYLOAD, "s", None)


async def test_in_jwt_mode_the_entry_yields_an_error_event_without_the_headers(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")

    def forbidden(worker_id):
        pytest.fail("a refused turn must not build a runner")

    events = [e async for e in workflow_turn(PAYLOAD, session_id="s", runner_factory=forbidden)]
    assert events[0]["type"] == "error"


def test_in_iam_mode_no_token_is_minted_and_the_payload_names_the_traveler(monkeypatch):
    monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH", raising=False)

    def mint(user_key):
        raise AssertionError("iam mode must not sign in")

    headers = runtime_headers("trv_meridian_demo", mint=mint)
    assert headers is None
    assert parse_turn(PAYLOAD, "s", headers).traveler_id == "trv_meridian_demo"
