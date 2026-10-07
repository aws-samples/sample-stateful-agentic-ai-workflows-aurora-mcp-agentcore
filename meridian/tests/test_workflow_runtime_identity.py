"""MeridianWorkflow takes the traveler from the forwarded token in jwt mode and only then."""

import json

import pytest

from backend.agentcore.caller_credential import current_caller_token
from backend.agentcore.errors import CallerTokenExpired
from backend.agents.phase_05_workflow import runtime_entry as entry
from tests.jwt_support import access_token

JORDAN, DECOY = "trv_meridian_demo", "trv_demo_decoy"
START = {"event": "workflow_turn", "mode": "start", "thread_id": "phase5-abc",
         "traveler_id": JORDAN, "query": "My flight was canceled.", "travelers_count": 2,
         "review_only": False}


class Runner:
    def __init__(self, result=None, error=None):
        self.commands, self.tokens, self._result, self._error = [], [], result or {}, error

    async def run(self, command):
        self.commands.append(command)
        self.tokens.append(current_caller_token())
        if self._error:
            raise self._error
        return self._result


async def events(payload, runner, headers=None):
    return [e async for e in entry.workflow_turn(
        payload, session_id="rt-wf-x-" + "0" * 32, headers=headers,
        runner_factory=lambda worker_id: runner, heartbeat_seconds=10.0)]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def jwt(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")


async def test_iam_mode_trusts_the_payload_and_ignores_any_header():
    runner = Runner()
    await events(START, runner, bearer(access_token(DECOY)))
    assert runner.commands[0].traveler_id == JORDAN
    assert runner.tokens == [None]


async def test_jwt_mode_takes_the_traveler_from_the_claim_and_binds_the_token(jwt):
    runner, token = Runner(), access_token(JORDAN)
    out = await events(START, runner, bearer(token))
    assert out[-1]["type"] == "result"
    assert runner.commands[0].traveler_id == JORDAN
    assert runner.tokens == [token]
    assert token not in json.dumps(out)


async def test_jwt_mode_needs_no_traveler_in_the_payload(jwt):
    runner = Runner()
    payload = {k: v for k, v in START.items() if k != "traveler_id"}
    await events(payload, runner, bearer(access_token(DECOY)))
    assert runner.commands[0].traveler_id == DECOY


async def test_a_payload_naming_another_traveler_is_refused_and_runs_nothing(jwt):
    runner = Runner()
    out = await events({**START, "traveler_id": JORDAN}, runner, bearer(access_token(DECOY)))
    assert [e["type"] for e in out] == ["error"] and out[0]["code"] == "authorization"
    assert "different traveler" in out[0]["message"]
    assert runner.commands == []


@pytest.mark.parametrize("headers", [None, {}, {"Authorization": "Basic abc"},
                                     {"Authorization": "Bearer not-a-jwt"}])
async def test_jwt_mode_without_a_usable_token_is_refused(jwt, headers):
    runner = Runner()
    out = await events(START, runner, headers)
    assert out[0]["code"] == "authorization" and runner.commands == []


@pytest.mark.parametrize("token", [access_token(None), access_token(JORDAN, token_use="id")])
async def test_jwt_mode_refuses_a_token_that_is_not_an_access_token_with_a_traveler(jwt, token):
    runner = Runner()
    out = await events(START, runner, bearer(token))
    assert out[0]["code"] == "authorization" and runner.commands == []


async def test_an_expired_token_is_a_coded_expiry_and_runs_nothing(jwt):
    runner = Runner()
    out = await events(START, runner, bearer(access_token(JORDAN, expires_in=-1)))
    assert out == [{"type": "error", "code": "token_expired",
                    "message": "The caller's access token has expired. Sign in again."}]
    assert runner.commands == []


async def test_a_token_that_expires_during_the_run_is_reported_with_the_same_code(jwt):
    runner = Runner(error=CallerTokenExpired("The caller's access token has expired."))
    out = await events(START, runner, bearer(access_token(JORDAN)))
    assert out[-1]["code"] == "token_expired"


async def test_a_ping_needs_no_traveler_in_either_mode(jwt):
    out = await events({"event": "workflow_turn", "mode": "ping"}, Runner())
    assert out[0]["state"]["workflow_status"] == "ready"
