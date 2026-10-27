"""The one-click hold goes through the runtime and the governed gateway, then persists under RLS."""

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from fastapi import HTTPException

import backend.routers.chat as chat_router
from backend.agentcore.runtime import AgentCoreRuntimeAdapter
from backend.agentcore.runtime_https import STATUS_CODES, RuntimeHttpError
from backend.agents.phase_04_production.write_outcome import (
    STEP_WORDS,
    PlacedWriteUnfinished,
    WriteOutcomeUnknown,
)
from backend.agents.phase_04_production import concierge as concierge_mod
from backend.agents.phase_04_production.concierge import HoldTarget
from backend.catalog_compat import row_to_api_product
from tests.test_production_transaction_boundaries import build_agent, remembered, runtime_decision

TARGET = HoldTarget(package_id="CTY-002", duration="7 nights", travelers=2, unit_price_cents=250000)


def test_confirmed_hold_is_sent_to_the_runtime_and_recorded(monkeypatch):
    events, calls = [], []
    held = {"bookingId": "HLD-1", "status": "held", "expiresAt": "2026-09-11T00:00:00+00:00",
            "seatsRemaining": 2}
    decision = runtime_decision(
        message="Held CTY-002 for two travelers.",
        packages=[],
        hold=held,
        policy_decision="allow",
        activities=[{"id": "rt-h", "timestamp": "t", "activity_type": "order",
                     "title": "AgentCore Gateway: tools/call → create_courtesy_hold"}],
    )
    agent = build_agent(events, decision, calls)
    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda **_kw: None)

    outcome = asyncio.run(agent.process_hold("trv_meridian_demo", "conv-9", TARGET))

    assert outcome.hold == held
    assert outcome.policy_decision == "allow"
    assert outcome.message == "Held CTY-002 for two travelers."
    assert outcome.conv_id == "conv-test"
    assert outcome.trace_id == "abc123"
    _args, kwargs = calls[0]
    assert kwargs["hold_confirmed"] is True
    assert kwargs["hold_target"] == {
        "package_id": "CTY-002", "duration": "7 nights", "travelers": 2, "unit_price_cents": 250000,
    }
    assert kwargs["budget_ceiling_cents"] == 640000
    assert events.index("tx-1:commit") < events.index("external:runtime") < events.index("tx-2:open")
    assert "aurora:audit:production_hold" in events
    titles = [entry.title for entry in outcome.activities]
    assert "AgentCore Gateway: tools/call → create_courtesy_hold" in titles
    assert titles[-1] == "Courtesy hold persisted"
    assert outcome.activities[-1].telemetry["status"] == "held"


def test_denied_hold_returns_the_refusal_without_a_hold(monkeypatch):
    events, calls = [], []
    decision = runtime_decision(
        message="I could not place the hold: it is over your saved budget.",
        packages=[],
        hold=None,
        hold_refused="No Cedar policy permits this hold, so the gateway denied it by default.",
        policy_decision="deny",
    )
    agent = build_agent(events, decision, calls)
    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda **_kw: None)

    outcome = asyncio.run(agent.process_hold("trv_meridian_demo", None, TARGET))

    assert outcome.hold is None
    assert outcome.policy_decision == "deny"
    assert "denied it by default" in outcome.refused
    assert outcome.activities[-1].title == "Courtesy hold not placed"
    assert outcome.activities[-1].telemetry["status"] == "denied"
    assert "aurora:audit:production_hold" in events


def test_confirmed_booking_is_sent_to_the_runtime_and_recorded(monkeypatch):
    from backend.agents.phase_04_production.concierge import BookingTarget

    events, calls = [], []
    booked = {"bookingId": "HLD-1", "status": "confirmed", "confirmedAt": "2026-09-10 13:00:00+00",
              "totalAmount": "5000.00"}
    decision = runtime_decision(
        message="Booked CTY-002 for two travelers.",
        packages=[],
        booking=booked,
        policy_decision="allow",
        activities=[{"id": "rt-b", "timestamp": "t", "activity_type": "order",
                     "title": "AgentCore Gateway: tools/call → confirm_booking"}],
    )
    agent = build_agent(events, decision, calls)
    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda **_kw: None)
    target = BookingTarget(booking_id="HLD-1", total_cents=500000, package_id="CTY-002",
                           duration="7 nights", travelers=2)

    outcome = asyncio.run(agent.process_booking("trv_meridian_demo", "conv-9", target))

    assert outcome.booking == booked
    assert outcome.policy_decision == "allow"
    assert outcome.message == "Booked CTY-002 for two travelers."
    _args, kwargs = calls[0]
    assert kwargs["booking_confirmed"] is True
    assert "hold_confirmed" not in kwargs
    assert kwargs["booking_target"] == {
        "booking_id": "HLD-1", "total_cents": 500000, "package_id": "CTY-002",
        "duration": "7 nights", "travelers": 2,
    }
    assert kwargs["budget_ceiling_cents"] == 640000
    assert events.index("tx-1:commit") < events.index("external:runtime") < events.index("tx-2:open")
    assert "aurora:audit:production_booking" in events
    titles = [entry.title for entry in outcome.activities]
    assert "AgentCore Gateway: tools/call → confirm_booking" in titles
    assert titles[-1] == "Booking confirmed in Aurora"
    assert outcome.activities[-1].telemetry["status"] == "confirmed"


def test_denied_booking_returns_the_refusal_without_a_booking(monkeypatch):
    from backend.agents.phase_04_production.concierge import BookingTarget

    events, calls = [], []
    decision = runtime_decision(
        message="I could not confirm the booking: it is over your saved budget.",
        packages=[],
        booking=None,
        booking_refused="No Cedar policy permits this booking, so the gateway denied it by default.",
        policy_decision="deny",
    )
    agent = build_agent(events, decision, calls)
    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda **_kw: None)
    target = BookingTarget(booking_id="HLD-1", total_cents=500000, package_id="CTY-002",
                           duration="7 nights", travelers=2)

    outcome = asyncio.run(agent.process_booking("trv_meridian_demo", None, target))

    assert outcome.booking is None
    assert outcome.policy_decision == "deny"
    assert "denied it by default" in outcome.refused
    assert outcome.activities[-1].title == "Booking not confirmed"
    assert outcome.activities[-1].telemetry["status"] == "denied"


# --------------- a failed hold or booking request answers with what is known about the write

PACKAGE = {"package_id": "CTY-002", "name": "Tokyo Culture & Cuisine", "price_per_person": 2500.0,
           "durations": ["7 nights"], "operator": "JAL Premium", "trip_type": "City Breaks"}
HELD_LINE = {"booking_id": "HLD-1", "status": "held", "total_amount": "5000.00",
             "package_id": "CTY-002", "duration": "7 nights", "travelers_count": 2,
             "unit_price": "2500.00"}
DATA_PLANE = "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/rt-1/invocations"
RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/rt-1"
# What failed after the write, as the reply words it.
FAILED_STEP = {
    "reply": "the concierge's reply",
    "trace": "showing this request's trace",
    "audit": "recording this request in the audit log",
    "memory": "saving this conversation",
}
PLACED_REPLY = {
    "hold": "The hold was placed, but {} failed. Retry the hold to read its receipt.",
    "booking": "The booking was confirmed, but {} failed. "
               "Retry the confirmation to read its receipt.",
}
UNKNOWN_REPLY = {
    "hold": "We could not confirm whether the hold was placed. Check your trips before trying "
            "again.",
    "booking": "We could not confirm whether the booking was confirmed. Check your trips before "
               "trying again.",
}
NOT_COMPLETED_REPLY = {
    "hold": "The governed hold could not be completed. Check the AgentCore platform.",
    "booking": "The governed booking could not be completed. Check the AgentCore platform.",
}
WRITES = {
    "hold": {"hold": {"bookingId": "HLD-1", "status": "held"}},
    "booking": {"booking": {"bookingId": "HLD-1", "status": "confirmed"}},
}
REFUSALS = {
    "hold": {"hold": None, "hold_refused": "No Cedar policy permits this hold."},
    "booking": {"booking": None, "booking_refused": "No Cedar policy permits this booking."},
}
STARTED = {"type": "activity", "id": "rt-0", "timestamp": "t", "activity_type": "reasoning",
           "title": "AgentCore Runtime: turn started"}
THROTTLED = {"type": "error", "error_type": "ModelThrottledException",
             "message": "An error occurred (ThrottlingException): Too many requests"}


class StepFailed(Exception):
    """One step of the request failing after the Runtime returned."""


def reported(route, refused=False):
    """The Runtime's event for the write: placed, or refused by Cedar."""
    if refused:
        return {"type": route, route: None, "refused": REFUSALS[route][f"{route}_refused"],
                "policyDecision": "deny"}
    return {"type": route, **WRITES[route], "policyDecision": "allow"}


def sse(*events):
    """Runtime events as the data plane streams them."""
    return b"".join(f"data: {json.dumps(event)}\n\n".encode() for event in events)


def real_runtime(agent):
    """The real Runtime adapter over a fake data plane; returns the data plane."""
    adapter = AgentCoreRuntimeAdapter(runtime_arn=RUNTIME_ARN, qualifier="DEFAULT",
                                      region="us-east-1")
    adapter._client = MagicMock()
    agent.agentcore_runtime = adapter
    return adapter._client


def stream(agent, *reads):
    """Answer the invoke with a stream whose reads return or raise ``reads``."""
    body = MagicMock()
    body.read.side_effect = [*reads, b""]
    real_runtime(agent).invoke_agent_runtime.return_value = {"response": body}


def fail_the_trace(agent):
    events = agent.db.events

    def callback(entry):
        if "external:runtime" in events:
            raise StepFailed(f"trace entry {entry.title!r} rejected")

    agent.activity_callback = callback


def fail_the_audit(agent):
    async def write_fails(**_kwargs):
        raise StepFailed("agent_audit_log insert failed")

    agent.store.write_audit = write_fails


def fail_the_memory(agent):
    async def insert_fails(*_args):
        raise StepFailed("conversation_messages insert failed")

    agent.traveler_memory.persist_turn = insert_fails


def failing_invoke(failure):
    def fail(agent, _route):
        real_runtime(agent).invoke_agent_runtime.side_effect = failure
    return fail


def failing_status(status):
    """The invoke answered with ``status``, as boto3 raises it."""
    code = STATUS_CODES.get(status, f"HTTP{status}")

    def fail(agent, _route):
        client_error = ClientError({"Error": {"Code": code, "Message": "x"}}, "InvokeAgentRuntime")
        real_runtime(agent).invoke_agent_runtime.side_effect = client_error
    return fail


def failing_status_over_https(status):
    def fail(agent, _route):
        error = RuntimeHttpError(STATUS_CODES.get(status, f"HTTP{status}"), status)
        real_runtime(agent)
        agent.agentcore_runtime._invoke = MagicMock(side_effect=error)
    return fail


# The statuses that reject the invoke request before the container is handed the turn, and the
# ones that prove nothing about it: the container's own error (424), a 500, a front-door 502 or
# 504, and a 200 whose stream was refused.
REFUSING_STATUSES = (400, 401, 402, 403, 404, 409, 429)
UNPROVEN_STATUSES = (424, 500, 502, 503, 504, 200)


# Each failure of a request whose Runtime reported the write placed, by the step that failed.
AFTER_A_PLACED_WRITE = {
    "a Runtime error after the report": ("reply", lambda agent, route: stream(
        agent, sse(STARTED, reported(route), THROTTLED))),
    "a stream that breaks after the report": ("reply", lambda agent, route: stream(
        agent, sse(STARTED, reported(route)), ReadTimeoutError(endpoint_url=DATA_PLANE))),
    "a stream that ends after the report without a reply": ("reply", lambda agent, route: stream(
        agent, sse(STARTED, reported(route)))),
    "the trace after the write": ("trace", lambda agent, _route: fail_the_trace(agent)),
    "the audit row after the write": ("audit", lambda agent, _route: fail_the_audit(agent)),
    "the memory write after the write": ("memory", lambda agent, _route: fail_the_memory(agent)),
}
# Failures after the invoke was sent, before the Runtime reported the write either way.
OUTCOME_UNKNOWN = {
    "a read timeout after sending": failing_invoke(ReadTimeoutError(endpoint_url=DATA_PLANE)),
    "a connection closed after sending": failing_invoke(
        ConnectionClosedError(endpoint_url=DATA_PLANE)),
    "a stream that breaks before any report": lambda agent, _route: stream(
        agent, sse(STARTED), ReadTimeoutError(endpoint_url=DATA_PLANE)),
    "a Runtime error before any report": lambda agent, _route: stream(
        agent, sse(STARTED, THROTTLED)),
}
OUTCOME_UNKNOWN.update({
    f"a {status} from the invoke over {transport}": fail(status)
    for status in UNPROVEN_STATUSES
    for transport, fail in (("IAM", failing_status), ("HTTPS", failing_status_over_https))
})
# Failures that prove the Runtime never ran the turn.
NEVER_RAN = {
    "no connection was made": failing_invoke(EndpointConnectionError(endpoint_url=DATA_PLANE)),
    "the service refused the invoke": failing_invoke(ClientError(
        {"Error": {"Code": "ValidationException", "Message": "bad request"}},
        "InvokeAgentRuntime")),
}
NEVER_RAN.update({
    f"a {status} rejecting the invoke over {transport}": fail(status)
    for status in REFUSING_STATUSES
    for transport, fail in (("IAM", failing_status), ("HTTPS", failing_status_over_https))
})
# Failures after the Runtime reported the write refused.
AFTER_A_REFUSAL = {
    "a Runtime error after the refusal": lambda agent, route: stream(
        agent, sse(STARTED, reported(route, refused=True), THROTTLED)),
    "the trace after the refusal": lambda agent, _route: fail_the_trace(agent),
    "the audit row after the refusal": lambda agent, _route: fail_the_audit(agent),
    "the memory write after the refusal": lambda agent, _route: fail_the_memory(agent),
}


def route_failing(monkeypatch, route, decision, fail):
    """The real routes and Phase 4 agent with ``fail`` applied; platform and Aurora are fakes."""
    agent = build_agent([], decision, [])
    fail(agent, route)

    async def package(_product_id):
        return dict(PACKAGE), row_to_api_product(PACKAGE)

    async def booking(_traveler_id, _booking_id):
        return dict(HELD_LINE)

    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda **_kw: None)
    monkeypatch.setattr(concierge_mod, "create_production_agent", lambda: agent)
    monkeypatch.setattr(chat_router, "_package_for_hold", package)
    monkeypatch.setattr(chat_router, "_traveler_booking", booking)
    return agent


async def answer(route):
    with pytest.raises(HTTPException) as answered:
        if route == "hold":
            await chat_router.production_hold(chat_router.OrderRequest(
                product_id="CTY-002", size="7 nights", quantity=2, phase=4))
        else:
            await chat_router.production_booking(chat_router.BookingRequest(booking_id="HLD-1"))
    assert answered.value.status_code == 503
    return answered.value


def placed(route):
    return runtime_decision(message="Done.", packages=[], policy_decision="allow",
                            **WRITES[route])


@pytest.mark.parametrize("case", list(AFTER_A_PLACED_WRITE))
@pytest.mark.parametrize("route", ["hold", "booking"])
async def test_a_failure_after_a_reported_write_says_it_was_placed(monkeypatch, route, case):
    step, fail = AFTER_A_PLACED_WRITE[case]
    agent = route_failing(monkeypatch, route, placed(route), fail)

    answered = await answer(route)

    assert answered.detail == PLACED_REPLY[route].format(FAILED_STEP[step])
    if step == "memory":
        assert f"aurora:audit:production_{route}" in agent.db.events
        assert remembered(agent) == []


@pytest.mark.parametrize("case", list(OUTCOME_UNKNOWN))
@pytest.mark.parametrize("route", ["hold", "booking"])
async def test_a_failure_after_sending_with_no_report_says_the_outcome_is_unknown(
        monkeypatch, route, case):
    route_failing(monkeypatch, route, placed(route), OUTCOME_UNKNOWN[case])

    answered = await answer(route)

    assert answered.detail == UNKNOWN_REPLY[route]


@pytest.mark.parametrize("case", list(NEVER_RAN))
@pytest.mark.parametrize("route", ["hold", "booking"])
async def test_a_failure_before_the_runtime_ran_says_it_could_not_be_completed(
        monkeypatch, route, case):
    route_failing(monkeypatch, route, placed(route), NEVER_RAN[case])

    answered = await answer(route)

    assert answered.detail == NOT_COMPLETED_REPLY[route]


@pytest.mark.parametrize("case", list(AFTER_A_REFUSAL))
@pytest.mark.parametrize("route", ["hold", "booking"])
async def test_a_refused_write_whose_request_then_fails_does_not_claim_it(
        monkeypatch, route, case):
    refused = runtime_decision(message="Not done.", packages=[], policy_decision="deny",
                               **REFUSALS[route])
    route_failing(monkeypatch, route, refused, AFTER_A_REFUSAL[case])

    answered = await answer(route)

    assert answered.detail == NOT_COMPLETED_REPLY[route]


@pytest.mark.parametrize("route", ["hold", "booking"])
def test_the_one_reply_helper_words_all_four_cases(route):
    reply = chat_router._governed_write_reply
    assert set(FAILED_STEP) == set(STEP_WORDS)
    for step in STEP_WORDS:
        assert reply(route, PlacedWriteUnfinished(route, step)).detail == (
            PLACED_REPLY[route].format(FAILED_STEP[step]))
    assert reply(route, WriteOutcomeUnknown(route)).detail == UNKNOWN_REPLY[route]
    assert reply(route, RuntimeError("refused")).detail == NOT_COMPLETED_REPLY[route]


@pytest.mark.parametrize("case", ["placed", "unknown", "not completed"])
@pytest.mark.parametrize("route", ["hold", "booking"])
async def test_the_reply_body_carries_the_code_that_marks_it_as_the_routes_own(
        monkeypatch, route, case):
    import json as json_module

    from backend.main import http_exception_handler

    failures = {
        "placed": (placed(route), AFTER_A_PLACED_WRITE["the audit row after the write"][1]),
        "unknown": (placed(route), OUTCOME_UNKNOWN["a read timeout after sending"]),
        "not completed": (placed(route), NEVER_RAN["no connection was made"]),
    }
    decision, fail = failures[case]
    route_failing(monkeypatch, route, decision, fail)

    answered = await answer(route)
    body = json_module.loads((await http_exception_handler(None, answered)).body)

    assert body == {"error": answered.detail, "code": "governed_write"}
