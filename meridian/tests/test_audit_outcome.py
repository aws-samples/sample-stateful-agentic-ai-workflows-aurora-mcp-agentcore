"""Phase 4 audit rows carry the verified person and every tool call with how it ended."""

import asyncio
import json
import logging
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from botocore.exceptions import EndpointConnectionError, ReadTimeoutError
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from hypothesis import example, given, strategies as st
from pydantic import ValidationError

import backend.routers.chat as chat_router
from backend import http_auth
from backend.agentcore.errors import CallerTokenExpired
from backend.agentcore.runtime import AgentCoreRuntimeAdapter, RuntimeTurnError
from backend.agents.phase_04_production import audit_outcome
from backend.agents.phase_04_production import concierge as concierge_mod
from backend.agents.phase_04_production.audit_outcome import (
    TurnAudit,
    audit_from_decision,
    read_turn,
    turn_error_of,
)
from backend.agents.phase_04_production.concierge import BookingTarget, HoldTarget
from backend.agents.phase_04_production.write_outcome import (
    PlacedWriteUnfinished,
    WriteOutcomeUnknown,
)
from backend.agents.concierge_definition import CATALOG_TOOLS
from backend.audit_vocabulary import CODE_CLASSES, ToolCall, turn_error_problem
from backend.cognito_auth import CognitoVerifier
from backend.http_auth import HttpPrincipal
from backend.main import app
from backend.memory.store import MemoryStore
from tests.test_http_auth_cognito import CONFIG, token_for
from tests.test_runtime_trace_hooks import _answer, _traced_turn
from tests.test_production_transaction_boundaries import (
    FakeMemoryAgent,
    build_agent,
    remembered,
    runtime_decision,
)

SUB = "11111111-2222-3333-4444-555555555555"
ACCOUNT = "123456789012"
HOLD = HoldTarget(package_id="CTY-002", duration="7 nights", travelers=2, unit_price_cents=250000)
CEDAR_DENIAL = (
    "Tool execution failed: AuthorizeActionException - Tool Execution Denied: "
    "Tool call not allowed due to policy enforcement"
)
SEARCH = "MeridianCatalog___hybrid_trip_search"
DETAILS = "MeridianHolds___get_package_details"
HOLD_TOOL = "MeridianHolds___create_courtesy_hold"
BOOK_TOOL = "MeridianHolds___confirm_booking"


def last_line_class(exc_text: str) -> str:
    """The exception class the last line of a formatted traceback names, without its module."""
    return exc_text.splitlines()[-1].split(":")[0].rsplit(".", 1)[-1]


def span(status="ok", category="gateway", title="t", **fields):
    return {
        "id": "s", "timestamp": "t", "activity_type": "tool_call", "title": title,
        "telemetry": {"category": category, "status": status,
                      "fields": [{"label": k, "value": v} for k, v in fields.items()]},
    }


def before(call_id, tool):
    """The span the Runtime emits before a call runs."""
    return span(call_id=call_id, tool=tool, arguments="{}", cedar_policy="meridian_read_tools")


def done(call_id):
    """The span after a call the target answered."""
    return span(call_id=call_id, result="found", cedar_decision="allow")


def failed(call_id, tool, text, **fields):
    """The span after a call that failed without a Cedar denial."""
    return span("error", "tool", "Tool call failed", call_id=call_id, tool=tool, arguments="{}",
                gateway_error=text, **fields)


def denied(call_id, tool):
    """The span after Cedar denied a call."""
    return span("denied", "security", "Hold refused by Cedar policy", call_id=call_id, tool=tool,
                arguments="{}", gateway_error=CEDAR_DENIAL, cedar_decision="deny")


def called(call_id, tool):
    return [before(call_id, tool), done(call_id)]


def decision_with(*spans, **overrides):
    return runtime_decision(activities=list(spans), **overrides)


def calls_of(*spans, **overrides):
    return audit_from_decision(decision_with(*spans, **overrides)).tool_calls


# ----------------------------------------------------------------- the pure reading

def test_every_call_is_listed_once_in_start_order_without_the_target_prefix():
    audit = audit_from_decision(decision_with(
        *called("c1", SEARCH), *called("c2", DETAILS), *called("c3", DETAILS)))

    assert audit == TurnAudit([
        ToolCall("hybrid_trip_search", "allowed"),
        ToolCall("get_package_details", "allowed"),
        ToolCall("get_package_details", "allowed"),
    ])
    assert audit.decision == "allow"


def test_parallel_calls_are_paired_by_call_id_and_kept_in_start_order():
    assert calls_of(
        before("c1", DETAILS), before("c2", DETAILS),
        done("c2"), failed("c1", DETAILS, "Aurora statement timed out"),
    ) == [ToolCall("get_package_details", "error"), ToolCall("get_package_details", "allowed")]


def test_a_turn_without_tools_records_an_empty_list_and_is_allowed():
    runtime_span = span(category="runtime", tools="hybrid_trip_search, get_package_details")
    audit = audit_from_decision(decision_with(runtime_span))
    assert audit == TurnAudit([]) and audit.decision == "allow"


def test_a_cedar_denial_is_a_refusal_by_policy():
    audit = audit_from_decision(decision_with(before("c1", HOLD_TOOL), denied("c1", HOLD_TOOL)))
    assert audit.tool_calls == [ToolCall("create_courtesy_hold", "refused", "policy")]
    assert audit.decision == "deny"


def test_a_cedar_denied_hold_then_a_details_lookup_keeps_both_and_is_a_denial():
    audit = audit_from_decision(decision_with(
        before("c1", HOLD_TOOL), denied("c1", HOLD_TOOL), *called("c2", DETAILS)))
    assert audit.tool_calls == [
        ToolCall("create_courtesy_hold", "refused", "policy"),
        ToolCall("get_package_details", "allowed"),
    ]
    assert audit.decision == "deny"


def test_a_failed_call_the_model_recovered_from_keeps_its_own_error():
    audit = audit_from_decision(decision_with(
        before("c1", DETAILS), failed("c1", DETAILS, "Aurora statement timed out"),
        *called("c2", DETAILS)))
    assert audit.tool_calls == [
        ToolCall("get_package_details", "error"), ToolCall("get_package_details", "allowed")]
    assert audit.decision == "error"


def test_an_interceptor_refusal_is_named_as_the_identity_check():
    (call,) = calls_of(before("c1", HOLD_TOOL), failed(
        "c1", HOLD_TOOL, "Tool execution failed: Identity Check Failed: no traveler"))
    assert (call.result, call.refused_by) == ("refused", "identity_check")


def test_any_other_tool_failure_is_an_error_with_no_layer():
    audit = audit_from_decision(decision_with(before("c1", BOOK_TOOL), failed(
        "c1", BOOK_TOOL, "AccessDeniedException: not authorized to invoke this gateway")))
    assert audit.tool_calls == [ToolCall("confirm_booking", "error")]
    assert audit.decision == "error"


def test_a_grant_refusal_with_its_deny_row_is_refused_by_the_workload_grant():
    audit = audit_from_decision(decision_with(before("c1", HOLD_TOOL), failed(
        "c1", HOLD_TOOL, "traveler_not_authorized", traveler_grant="deny",
        access_audit_id="authz_0123456789ab")))
    assert audit.tool_calls == [ToolCall("create_courtesy_hold", "refused", "workload_grant",
                                         "traveler_not_authorized", "authz_0123456789ab")]
    assert audit.decision == "deny"


@pytest.mark.parametrize("evidence", [
    {}, {"traveler_grant": "deny"}, {"access_audit_id": "authz_0123456789ab"},
    {"traveler_grant": "allow", "access_audit_id": "authz_0123456789ab"},
])
def test_the_holds_lambdas_grant_answer_without_its_evidence_names_no_layer(evidence):
    """An older Runtime or Lambda sends the answer without the deny row that proves the layer."""
    assert calls_of(before("c1", HOLD_TOOL), failed(
        "c1", HOLD_TOOL, "traveler_not_authorized", **evidence)) == [
        ToolCall("create_courtesy_hold", "error", refusal_code="traveler_not_authorized")]


def test_a_cancelled_second_write_is_refused_by_the_runtime_guard_and_denies_nothing():
    audit = audit_from_decision(decision_with(
        *called("c1", HOLD_TOOL),
        span("error", "runtime", call_id="c2", tool=HOLD_TOOL, arguments="{}",
             refused_by="runtime_guard", refusal_code="write_already_attempted")))
    assert audit.tool_calls == [
        ToolCall("create_courtesy_hold", "allowed"),
        ToolCall("create_courtesy_hold", "refused", "runtime_guard", "write_already_attempted"),
    ]
    assert audit.decision == "allow"


@pytest.mark.parametrize("layer", ["policy", "identity_check", "workload_grant"])
def test_a_span_naming_another_layer_is_not_that_layers_evidence(layer):
    (call,) = calls_of(before("c1", HOLD_TOOL), span(
        "error", "runtime", call_id="c1", tool=HOLD_TOOL, refused_by=layer))
    assert (call.result, call.refused_by) == ("error", None)


@pytest.mark.parametrize(("code", "layer"), [
    ("budget_ceiling_exceeded", "business_rule"),
    ("insufficient_inventory", "business_rule"),
    ("hold_expired", "business_rule"),
    ("hold_price_mismatch", "database_guard"),
    ("traveler_scope_mismatch", "database_guard"),
    ("journey_not_owned", "database_guard"),
    ("booking_amount_mismatch", "database_guard"),
])
def test_a_named_refusal_is_attributed_to_the_class_its_raising_code_supports(code, layer):
    (call,) = calls_of(before("c1", HOLD_TOOL), failed("c1", HOLD_TOOL, code))
    assert call == ToolCall("create_courtesy_hold", "refused", layer, code)


@pytest.mark.parametrize("code", ["execution_lease_lost", "invalid_package_inventory"])
def test_a_named_error_keeps_its_code_and_names_no_layer(code):
    (call,) = calls_of(before("c1", HOLD_TOOL), failed("c1", HOLD_TOOL, code))
    assert call == ToolCall("create_courtesy_hold", "error", refusal_code=code)


def test_free_text_is_never_stored_as_a_code():
    (call,) = calls_of(before("c1", DETAILS), failed("c1", DETAILS, "Aurora statement timed out"))
    assert call.refusal_code is None


def test_a_sold_out_hold_then_a_declined_second_hold_is_allowed():
    """The real Phase 4 sequence: Aurora refuses hold A, the Runtime declines hold B."""
    audit = audit_from_decision(decision_with(
        before("c1", HOLD_TOOL), failed("c1", HOLD_TOOL, "insufficient_inventory"),
        span("error", "runtime", call_id="c2", tool=HOLD_TOOL, arguments="{}",
             refused_by="runtime_guard", refusal_code="write_already_attempted")))
    assert audit.tool_calls == [
        ToolCall("create_courtesy_hold", "refused", "business_rule", "insufficient_inventory"),
        ToolCall("create_courtesy_hold", "refused", "runtime_guard", "write_already_attempted"),
    ]
    assert audit.decision == "allow"


def test_a_database_guard_refusal_is_a_denial():
    audit = audit_from_decision(decision_with(
        before("c1", BOOK_TOOL), failed("c1", BOOK_TOOL, "booking_amount_mismatch")))
    assert audit.decision == "deny"


def test_a_crashed_interceptor_is_stored_with_its_reason_not_as_a_mismatch():
    (call,) = calls_of(before("c1", HOLD_TOOL), failed(
        "c1", HOLD_TOOL, "Identity Check Failed: the identity check could not be completed."))
    assert call == ToolCall("create_courtesy_hold", "refused", "identity_check", "internal")


def test_a_call_whose_end_the_spans_do_not_show_is_an_error():
    assert calls_of(before("c1", DETAILS)) == [ToolCall("get_package_details", "error")]


def test_spans_without_call_ids_record_the_calls_as_unknown():
    """A Runtime deployed before call ids cannot say which result belongs to which call."""
    audit = audit_from_decision(decision_with(
        span(tool=DETAILS, arguments="{}"), span(result="found", cedar_decision="allow")))
    assert audit.tool_calls is None and audit.decision == "error"


def test_one_unmarked_call_span_makes_the_whole_list_unknown():
    assert calls_of(*called("c1", SEARCH), span(tool=DETAILS, arguments="{}")) is None


def test_the_runtime_policy_decision_without_a_denied_call_is_a_denial_of_unknown_calls():
    audit = audit_from_decision(decision_with(*called("c1", SEARCH), policy_decision="deny"))
    assert audit.tool_calls is None and audit.decision == "deny"


@pytest.mark.parametrize("tool", [None, ""])
def test_a_call_whose_spans_name_no_tool_records_the_calls_as_unknown(tool):
    assert calls_of(span(call_id="c1", tool=tool), done("c1")) is None


# ------------------------------------------------- the trace says what the row stores

GUARD = span("error", "runtime", "Second hold not sent: already attempted on this turn",
             call_id="c2", tool=HOLD_TOOL, arguments="{}",
             refused_by="runtime_guard", refusal_code="write_already_attempted")


def traced(*spans, **overrides):
    decision = decision_with(*spans, **overrides)
    return read_turn(decision).spans


def status_and_layers(traced_span):
    layers = [field["value"] for field in traced_span["telemetry"]["fields"]
              if field["label"] == "refused_by"]
    return traced_span["telemetry"]["status"], layers


@pytest.mark.parametrize(("text", "evidence", "layer"), [
    ("insufficient_inventory", {}, "business_rule"),
    ("booking_amount_mismatch", {}, "database_guard"),
    ("traveler_not_authorized",
     {"traveler_grant": "deny", "access_audit_id": "authz_0123456789ab"}, "workload_grant"),
    ("Tool execution failed: Identity Check Failed: the request carries no bearer token.", {},
     "identity_check"),
])
def test_a_call_refused_by_another_layer_shows_refused_and_that_layer(text, evidence, layer):
    spans = traced(before("c1", HOLD_TOOL), failed("c1", HOLD_TOOL, text, **evidence))
    assert status_and_layers(spans[1]) == ("refused", [layer])
    assert status_and_layers(spans[0]) == ("ok", [])


def test_the_runtime_guard_shows_refused_with_its_one_layer_field():
    spans = traced(*called("c1", HOLD_TOOL), GUARD)
    assert status_and_layers(spans[2]) == ("refused", ["runtime_guard"])


def test_a_cedar_denial_stays_denied():
    spans = traced(before("c1", HOLD_TOOL), denied("c1", HOLD_TOOL), policy_decision="deny")
    assert status_and_layers(spans[1]) == ("denied", [])


@pytest.mark.parametrize("text", [
    "execution_lease_lost", "Aurora statement timed out", "traveler_not_authorized",
])
def test_an_error_stays_an_error(text):
    spans = traced(before("c1", HOLD_TOOL), failed("c1", HOLD_TOOL, text))
    assert status_and_layers(spans[1]) == ("error", [])


def test_an_allowed_call_keeps_its_spans():
    spans = called("c1", SEARCH)
    assert traced(*spans) == spans


def test_with_the_calls_unknown_the_trace_is_as_the_runtime_sent_it():
    spans = [span(tool=HOLD_TOOL, arguments="{}"),
             span("error", "tool", tool=HOLD_TOOL, gateway_error="insufficient_inventory")]
    assert traced(*spans) == spans


def test_marking_a_refusal_leaves_the_runtimes_spans_unchanged():
    decision = decision_with(before("c1", HOLD_TOOL), failed("c1", HOLD_TOOL, "hold_expired"))
    sent = repr(decision.activities)
    read_turn(decision)
    assert repr(decision.activities) == sent


GRANT = {"traveler_grant": "deny", "access_audit_id": "authz_0123456789ab"}


@pytest.mark.parametrize(("tool", "text", "evidence", "title"), [
    (HOLD_TOOL, "insufficient_inventory", {}, "Hold refused: business rule"),
    (HOLD_TOOL, "traveler_not_authorized", GRANT, "Hold refused: workload grant"),
    (BOOK_TOOL, "booking_amount_mismatch", {}, "Booking refused: database guard"),
    (DETAILS, "Identity Check Failed: the request carries no bearer token.", {},
     "get_package_details refused: identity check"),
])
def test_a_refused_call_is_titled_with_its_tool_and_layer(tool, text, evidence, title):
    spans = traced(before("c1", tool), failed("c1", tool, text, **evidence))
    assert spans[1]["title"] == title


def test_the_guarded_retry_is_titled_with_its_layer():
    assert traced(*called("c1", HOLD_TOOL), GUARD)[2]["title"] == "Hold refused: runtime guard"


def test_an_error_and_a_cedar_denial_keep_their_titles():
    errored = traced(before("c1", HOLD_TOOL), failed("c1", HOLD_TOOL, "execution_lease_lost"))
    cedar = traced(before("c1", HOLD_TOOL), denied("c1", HOLD_TOOL), policy_decision="deny")
    assert (errored[1]["title"], cedar[1]["title"]) == (
        "Tool call failed", "Hold refused by Cedar policy")


def test_the_row_and_the_trace_come_from_one_reading_of_the_spans(monkeypatch):
    decision = decision_with(*called("c1", SEARCH), *called("c2", DETAILS))
    readings = iter([[(ToolCall("hybrid_trip_search", "allowed"), 1)],
                     [(ToolCall("get_package_details", "allowed"), 1)]])
    monkeypatch.setattr(audit_outcome, "_settled_calls", lambda _spans: next(readings))

    turn = read_turn(decision)

    assert turn.audit.tool_calls == [ToolCall("hybrid_trip_search", "allowed")]
    assert turn.spans == decision.activities


# ----------------------------------------------------- what the Phase 4 write unit stores

@pytest.fixture
def recorded(monkeypatch):
    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda **_kw: None)
    rows: list[dict] = []
    checks = MemoryStore.__new__(MemoryStore)
    checks.db = SimpleNamespace(execute=AsyncMock())

    async def record(**kwargs):
        # Only a row the store's own checks accept, as the migration 021 CHECKs would, is kept.
        await checks.write_audit(**kwargs)
        rows.append(kwargs)
        return "aud_test"

    def build(decision):
        agent = build_agent([], decision, [])
        agent.store.write_audit = record
        return agent

    build.rows = rows
    return build


def test_a_signed_in_turn_names_the_cognito_subject_and_each_call(recorded):
    agent = recorded(decision_with(*called("c1", SEARCH)))

    asyncio.run(agent.process_turn(
        "Find Tokyo trips", "trv_meridian_demo", None, 5, authorization_subject=SUB))

    (row,) = recorded.rows
    assert row["authorization_subject"] == SUB and row["authorization_provider"] == "cognito"
    assert row["tool_calls"] == [ToolCall("hybrid_trip_search", "allowed")]
    assert row["authorization_decision"] == "allow"
    assert row["operation"] == "production_turn"
    assert row["iam_identity"] == "arn:aws:sts::123:assumed-role/demo/session"


def test_a_denied_hold_is_stored_as_a_denial_not_an_allow(recorded):
    agent = recorded(decision_with(
        before("c1", HOLD_TOOL), denied("c1", HOLD_TOOL),
        policy_decision="deny", hold=None,
        hold_refused="No Cedar policy permits this hold.",
    ))

    asyncio.run(agent.process_hold("trv_meridian_demo", None, HOLD, authorization_subject=SUB))

    (row,) = recorded.rows
    assert row["tool_calls"] == [ToolCall("create_courtesy_hold", "refused", "policy")]
    assert row["authorization_decision"] == "deny"
    assert row["operation"] == "production_hold"


def test_a_turn_still_writes_its_row_when_the_spans_are_read_twice_differently(
        recorded, monkeypatch):
    readings = iter([[(ToolCall("hybrid_trip_search", "allowed"), 1)],
                     [(ToolCall("get_package_details", "allowed"), 1)]])
    monkeypatch.setattr(audit_outcome, "_settled_calls", lambda _spans: next(readings))
    agent = recorded(decision_with(*called("c1", SEARCH)))

    asyncio.run(agent.process_turn(
        "Find Tokyo trips", "trv_meridian_demo", None, 5, authorization_subject=SUB))

    (row,) = recorded.rows
    assert row["tool_calls"] == [ToolCall("hybrid_trip_search", "allowed")]


def test_a_confirmed_booking_records_its_operation(recorded):
    agent = recorded(decision_with(*called("c1", BOOK_TOOL), booking={"bookingId": "HLD-1"}))
    target = BookingTarget(booking_id="HLD-1", total_cents=500000, package_id="CTY-002",
                           duration="7 nights", travelers=2)

    asyncio.run(agent.process_booking("trv_meridian_demo", None, target, authorization_subject=SUB))

    (row,) = recorded.rows
    assert row["operation"] == "production_booking"
    assert row["tool_calls"] == [ToolCall("confirm_booking", "allowed")]


def outcome_span(activities):
    """The last span of a confirmed write: the backend's own account of its outcome."""
    last = activities[-1]
    layers = [field["value"] for field in last.telemetry.get("fields") or []
              if field["label"] == "refused_by"]
    return last.title, last.telemetry["status"], layers


@pytest.mark.parametrize(("spans", "overrides", "expected"), [
    ([before("c1", HOLD_TOOL), failed("c1", HOLD_TOOL, "insufficient_inventory")], {},
     ("Courtesy hold not placed: refused by business rule", "refused", ["business_rule"])),
    ([before("c1", HOLD_TOOL), failed("c1", HOLD_TOOL, "traveler_not_authorized", **GRANT)], {},
     ("Courtesy hold not placed: refused by workload grant", "refused", ["workload_grant"])),
    ([before("c1", HOLD_TOOL), failed("c1", HOLD_TOOL, "execution_lease_lost")], {},
     ("Courtesy hold not placed", "error", [])),
    ([before("c1", HOLD_TOOL), denied("c1", HOLD_TOOL)], {"policy_decision": "deny"},
     ("Courtesy hold not placed", "denied", [])),
])
def test_the_hold_outcome_says_refused_and_names_the_layer_its_row_records(
        recorded, spans, overrides, expected):
    agent = recorded(decision_with(*spans, hold=None, **overrides))

    outcome = asyncio.run(agent.process_hold("trv_meridian_demo", None, HOLD,
                                             authorization_subject=SUB))

    assert outcome_span(outcome.activities) == expected


@pytest.mark.parametrize(("text", "expected"), [
    ("booking_amount_mismatch",
     ("Booking not confirmed: refused by database guard", "refused", ["database_guard"])),
    ("booking_not_held",
     ("Booking not confirmed: refused by business rule", "refused", ["business_rule"])),
    ("Unknown booking", ("Booking not confirmed", "error", [])),
])
def test_the_booking_outcome_says_refused_and_names_the_layer_its_row_records(
        recorded, text, expected):
    agent = recorded(decision_with(before("c1", BOOK_TOOL), failed("c1", BOOK_TOOL, text),
                                   booking=None))
    target = BookingTarget(booking_id="HLD-1", total_cents=500000, package_id="CTY-002",
                           duration="7 nights", travelers=2)

    outcome = asyncio.run(agent.process_booking("trv_meridian_demo", None, target,
                                                authorization_subject=SUB))

    assert outcome_span(outcome.activities) == expected


def test_a_turn_from_a_runtime_without_call_ids_stores_unknown_calls(recorded):
    agent = recorded(decision_with(span(tool=SEARCH, arguments="{}")))

    asyncio.run(agent.process_turn("Find Tokyo trips", "trv_meridian_demo", None, 5))

    (row,) = recorded.rows
    assert row["tool_calls"] is None and row["authorization_decision"] == "error"


def test_without_a_signed_in_person_the_row_keeps_the_workload_subject(recorded):
    agent = recorded(decision_with())

    asyncio.run(agent.process_turn("Find Tokyo trips", "trv_meridian_demo", None, 5))

    (row,) = recorded.rows
    assert row["authorization_subject"] == "workload/demo"
    assert row["authorization_provider"] == "aws_iam"


def test_interleaved_turns_on_one_agent_keep_their_own_subjects(recorded):
    """The subject belongs to the turn, so a shared agent cannot swap two callers' subjects."""
    agent = recorded(decision_with(*called("c1", SEARCH)))
    both_started = threading.Barrier(2, timeout=5)
    invoke = agent.agentcore_runtime.invoke_turn

    def invoke_once_both_turns_started(*args, **kwargs):
        both_started.wait()
        return invoke(*args, **kwargs)

    agent.agentcore_runtime = SimpleNamespace(invoke_turn=invoke_once_both_turns_started)
    agent.store.prepare_embedding_vector = lambda *_args, **_kwargs: "[0.1,0.2]"

    async def two_turns():
        await asyncio.gather(
            agent.process_turn("Find Tokyo trips", "trv_meridian_demo", None, 5,
                               authorization_subject="sub-first"),
            agent.process_hold("trv_meridian_demo", None, HOLD,
                               authorization_subject="sub-second"),
        )

    asyncio.run(two_turns())

    subjects = {row["operation"]: row["authorization_subject"] for row in recorded.rows}
    assert subjects == {"production_turn": "sub-first", "production_hold": "sub-second"}


def test_the_agent_keeps_no_subject_of_its_own():
    assert not hasattr(concierge_mod.ProductionAgent, "verified_subject")


def test_the_audit_unit_refuses_to_run_without_audit_facts(recorded):
    agent = recorded(decision_with())
    async def attempt():
        read = await agent._authorized_read("hi", "trv_meridian_demo", None)
        await agent._audit_unit(read, "trv_meridian_demo", "production_turn", 0)

    with pytest.raises(RuntimeError, match="audit facts"):
        asyncio.run(attempt())


# --------------------------------------------- once the Runtime has acted, the row is written

BOOKING = BookingTarget(booking_id="HLD-1", total_cents=500000, package_id="CTY-002",
                        duration="7 nights", travelers=2)
# Each Phase 4 path that calls the Runtime, by the operation its row records.
RUNTIME_PATHS = {
    "production_turn": lambda agent: agent.process_turn(
        "Find Tokyo trips", "trv_meridian_demo", None, 5, authorization_subject=SUB),
    "production_hold": lambda agent: agent.process_hold(
        "trv_meridian_demo", None, HOLD, authorization_subject=SUB),
    "production_booking": lambda agent: agent.process_booking(
        "trv_meridian_demo", None, BOOKING, authorization_subject=SUB),
}


class TraceFailure(Exception):
    """The activity callback failing on an entry."""


class Unprintable(Exception):
    """An error whose text cannot be read."""

    def __str__(self):
        raise ValueError("this error has no text")


# Each unreadable error class and the turn_error that names it: the class alone, at most 64
# characters of it.
UNREADABLE = [(Unprintable, "Unprintable"), (type("U" * 200, (Unprintable,), {}), "U" * 64)]
UNREADABLE_IDS = ["short class name", "long class name"]
# What the log says when a row names an error by its class alone.
NAMED_BY_CLASS = "The audit row names the error by its class alone: its text could not be read"


# Whether a path remembers its turn when it fails after the Runtime returned: a turn that does
# not complete is not stored; a hold or booking that was placed is, because its write happened.
REMEMBERED_AFTER_A_FAILURE = {"production_turn": 0, "production_hold": 1,
                              "production_booking": 1}
PLACED = {"production_turn": {}, "production_hold": {"hold": {"bookingId": "HLD-1"}},
          "production_booking": {"booking": {"bookingId": "HLD-1"}}}


def failure_reaching_the_caller(agent, operation, answered_as=None):
    """Run the path and return the error it raised, with any outcome wrapper taken off.

    A hold or booking path answers some failures with what it knows about its write:
    ``PlacedWriteUnfinished`` for the step that failed after a placed write (``answered_as`` is
    that step), or ``WriteOutcomeUnknown`` (``answered_as`` is that class). The error itself is
    the wrapper's cause. With ``answered_as`` None the error reaches the caller unwrapped.
    """
    with pytest.raises(Exception) as raised:
        asyncio.run(RUNTIME_PATHS[operation](agent))
    error = raised.value
    if answered_as is None:
        assert not isinstance(error, (PlacedWriteUnfinished, WriteOutcomeUnknown))
        return error
    if answered_as is WriteOutcomeUnknown:
        assert isinstance(error, WriteOutcomeUnknown)
    else:
        assert isinstance(error, PlacedWriteUnfinished) and error.step == answered_as
    return error.__cause__


def unknown_for_a_write(operation):
    """How a Runtime failure that reported nothing about the write reaches each path's caller."""
    return WriteOutcomeUnknown if operation in ("production_hold", "production_booking") else None


def committed(agent, rows):
    """The rows whose write transaction committed; Amazon Aurora keeps no rolled back row."""
    return [row for row in rows if f"{row['transaction_id']}:commit" in agent.db.events]


def note_the_runtime_returned(agent):
    """A list that holds an entry once the agent's Runtime call has returned its decision."""
    returned = []
    invoke = agent.agentcore_runtime.invoke_turn

    def invoke_and_note(*args, **kwargs):
        decision = invoke(*args, **kwargs)
        returned.append(decision)
        return decision

    agent.agentcore_runtime = SimpleNamespace(invoke_turn=invoke_and_note)
    return returned


def pause_inside(owner, method):
    """Pause the turn inside ``owner.method`` until the test releases it."""
    original = getattr(owner, method)
    reached, release = asyncio.Event(), asyncio.Event()

    async def paused(*args, **kwargs):
        reached.set()
        await release.wait()
        return await original(*args, **kwargs)

    setattr(owner, method, paused)
    return reached, release


def fail_the_trace_once_the_runtime_returned(agent):
    """Make every trace entry after the Runtime's return fail; return the failures raised."""
    returned = note_the_runtime_returned(agent)
    raised = []

    def callback(entry):
        if returned:
            raised.append(TraceFailure(entry.title))
            raise raised[-1]

    agent.activity_callback = callback
    return raised


@pytest.mark.parametrize("operation", RUNTIME_PATHS)
def test_a_trace_that_fails_after_the_runtime_returned_writes_a_row_that_names_it(
        recorded, operation):
    agent = recorded(decision_with(*called("c1", SEARCH), **PLACED[operation]))
    raised = fail_the_trace_once_the_runtime_returned(agent)

    failure = failure_reaching_the_caller(
        agent, operation, "trace" if PLACED[operation] else None)

    assert isinstance(failure, TraceFailure)

    (row,) = committed(agent, recorded.rows)
    assert row["operation"] == operation
    assert row["tool_calls"] == [ToolCall("hybrid_trip_search", "allowed")]
    # The trace failed before the row was written, so the row names that error.
    assert row["turn_error"] == f"TraceFailure: {raised[0]}"
    assert row["authorization_decision"] == "error"
    assert len(remembered(agent)) == REMEMBERED_AFTER_A_FAILURE[operation]


@pytest.mark.parametrize("operation", ["production_hold", "production_booking"])
def test_a_refused_write_whose_trace_fails_stores_no_memory_and_writes_a_row_that_names_it(
        recorded, operation):
    agent = recorded(decision_with(*called("c1", SEARCH)))
    raised = fail_the_trace_once_the_runtime_returned(agent)

    with pytest.raises(TraceFailure):
        asyncio.run(RUNTIME_PATHS[operation](agent))

    (row,) = committed(agent, recorded.rows)
    assert row["operation"] == operation
    assert row["turn_error"] == f"TraceFailure: {raised[0]}"
    assert row["authorization_decision"] == "error"
    assert remembered(agent) == []


@pytest.mark.parametrize(("unreadable", "named"), UNREADABLE, ids=UNREADABLE_IDS)
def test_a_trace_failure_whose_text_cannot_be_read_writes_a_row_named_by_its_class(
        recorded, caplog, unreadable, named):
    agent = recorded(decision_with(*called("c1", SEARCH)))
    returned = note_the_runtime_returned(agent)
    failure = unreadable()

    def callback(_entry):
        if returned:
            raise failure

    agent.activity_callback = callback

    with pytest.raises(Unprintable) as raised:
        asyncio.run(RUNTIME_PATHS["production_turn"](agent))

    assert raised.value is failure
    (row,) = committed(agent, recorded.rows)
    assert row["tool_calls"] == [ToolCall("hybrid_trip_search", "allowed")]
    assert row["authorization_decision"] == "error" and row["turn_error"] == named
    assert errors_logged(caplog) == []
    (logged,) = warnings_logged(caplog)
    assert logged.getMessage().startswith(NAMED_BY_CLASS)
    assert "runtime_session=runtime-session-id" in logged.getMessage()
    assert last_line_class(logged.exc_text) == "ValueError", "the masked traceback is kept"


@pytest.mark.parametrize(("operation", "paused_in"), [
    ("production_turn", "trip refresh"),
    ("production_turn", "audit write"),
    ("production_hold", "audit write"),
    ("production_booking", "audit write"),
])
async def test_a_turn_cancelled_after_the_runtime_returned_still_writes_its_row(
        recorded, operation, paused_in):
    agent = recorded(decision_with(*called("c1", SEARCH)))
    owner, method = (agent.db, "execute") if paused_in == "trip refresh" else (
        agent.store, "write_audit")
    reached, release = pause_inside(owner, method)
    # The turn runs as a task of its own, as the stream's producer and a request handler do;
    # a cancellation reaches the turn by cancelling that task.
    turn = asyncio.create_task(RUNTIME_PATHS[operation](agent))
    await asyncio.wait_for(reached.wait(), 5)

    turn.cancel()
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await turn
    (row,) = committed(agent, recorded.rows)
    assert row["operation"] == operation
    assert row["tool_calls"] == [ToolCall("hybrid_trip_search", "allowed")]
    # A cancellation during the refresh precedes the row, which names it. One that arrives
    # during the write takes effect once the row is committed, so the row was built without it.
    assert row["turn_error"] == ("CancelledError" if paused_in == "trip refresh" else None)


@pytest.mark.parametrize("telemetry", [
    "unreadable", ["status", "ok"], 7,
    {"status": "ok", "fields": "call_id=c2"},
    {"status": "ok", "fields": {"label": "call_id", "value": "c2"}},
    {"status": "ok", "fields": 7},
    {"status": "ok", "fields": ["call_id=c2"]},
    {"status": "ok", "fields": [{"label": "result", "value": "found"}, None]},
])
def test_a_span_that_cannot_be_read_makes_the_calls_unknown_and_raises_nothing(telemetry):
    unreadable = {**span(), "telemetry": telemetry}
    sent = [*called("c1", SEARCH), unreadable]

    turn = read_turn(decision_with(*sent))

    assert turn.audit.tool_calls is None and turn.audit.decision == "error"
    assert turn.spans == sent


def test_a_span_whose_telemetry_is_not_a_dict_still_gets_the_row_written(recorded):
    agent = recorded(decision_with(*called("c1", SEARCH), {**span(), "telemetry": "unreadable"}))

    # The trace cannot show that span either; its error reaches the caller after the row.
    with pytest.raises(ValidationError):
        asyncio.run(agent.process_turn("Find Tokyo trips", "trv_meridian_demo", None, 5))

    (row,) = committed(agent, recorded.rows)
    assert row["tool_calls"] is None and row["authorization_decision"] == "error"


UNSTORABLE = ["a\x00b", "a\ud800b"]


def unstorable_label_spans(label, value):
    """Call spans whose one stored label holds ``value``, which jsonb cannot store."""
    if label == "tool":
        return [before("c1", value), done("c1")]
    if label == "refusal_code":
        return [before("c1", HOLD_TOOL), span(
            call_id="c1", tool=HOLD_TOOL, refused_by="runtime_guard", refusal_code=value)]
    return [before("c1", HOLD_TOOL), failed(
        "c1", HOLD_TOOL, "traveler_not_authorized", traveler_grant="deny", access_audit_id=value)]


@pytest.mark.parametrize("value", UNSTORABLE, ids=["NUL", "lone surrogate"])
@pytest.mark.parametrize("label", ["tool", "refusal_code", "access_audit_id"])
def test_a_call_label_jsonb_cannot_store_makes_the_calls_unknown(label, value):
    sent = unstorable_label_spans(label, value)

    turn = read_turn(decision_with(*sent))

    assert turn.audit.tool_calls is None and turn.audit.decision == "error"
    assert turn.spans == sent


@pytest.mark.parametrize("value", UNSTORABLE, ids=["NUL", "lone surrogate"])
@pytest.mark.parametrize("label", ["tool", "refusal_code", "access_audit_id"])
def test_a_call_label_jsonb_cannot_store_still_gets_the_row_written(recorded, label, value):
    agent = recorded(decision_with(*unstorable_label_spans(label, value)))

    asyncio.run(agent.process_turn("Find Tokyo trips", "trv_meridian_demo", None, 5))

    (row,) = committed(agent, recorded.rows)
    assert row["tool_calls"] is None and row["authorization_decision"] == "error"


# ------------------------- the audit row stands alone; memory keeps what was delivered

class MemoryInsertFailed(Exception):
    """A conversation memory insert failing inside its transaction."""


class CatalogUnavailable(Exception):
    """The live catalog refresh failing after the Runtime returned."""


class AuditWriteFailed(Exception):
    """The audit row's insert failing."""


TURN_IDS = ("traveler=trv_meridian_demo", "conversation=conv-test",
            "runtime_session=runtime-session-id", "trace=abc123")


def fail_the_trip_refresh(agent, package_id):
    """Make the live catalog refresh fail for a reply that names ``package_id``."""
    execute = agent.db.execute

    async def refresh(sql, params=(), **kwargs):
        if package_id in params:
            raise CatalogUnavailable(package_id)
        return await execute(sql, params, **kwargs)

    agent.db.execute = refresh


def recall_reads_back_what_was_committed(agent):
    """Session recall and the shown trips serve only committed turns, as Amazon Aurora does."""
    async def session(_conversation_id):
        return {"turns": [{"role": role, "content": turn[key]} for turn in remembered(agent)
                          for role, key in (("user", "message"), ("assistant", "reply"))]}

    async def shown_packages(_conversation_id, *, transaction_id):
        shortlists = [turn["shown"] for turn in remembered(agent) if turn["shown"]]
        return shortlists[-1] if shortlists else []

    agent.traveler_memory.recall_session_context = session
    agent.store.recall_shown_packages = shown_packages
    agent.store.format_memory_context = lambda _profile, turns, _facts, _similar: "\n".join(
        f"{turn['role']}: {turn['content']}" for turn in turns)


def answer_each_turn(agent, *decisions):
    """Answer each Runtime call with the next decision; return the context each call carried."""
    contexts, answers = [], iter(decisions)

    def invoke_turn(_conversation_id, _traveler_id, _message, context, **_kwargs):
        contexts.append(context)
        return next(answers)

    agent.agentcore_runtime = SimpleNamespace(invoke_turn=invoke_turn)
    return contexts


def errors_logged(caplog):
    return [record for record in caplog.records
            if record.name == concierge_mod.__name__ and record.levelno >= logging.ERROR]


def warnings_logged(caplog):
    return [record for record in caplog.records
            if record.name == concierge_mod.__name__ and record.levelno == logging.WARNING]


@pytest.mark.parametrize("operation", RUNTIME_PATHS)
def test_the_audit_row_commits_in_its_own_transaction_before_memory_is_written(
        recorded, operation):
    agent = recorded(decision_with(*called("c1", SEARCH)))

    asyncio.run(RUNTIME_PATHS[operation](agent))

    (row,) = committed(agent, recorded.rows)
    ((memory_tx, _turn),) = agent.traveler_memory.turns
    events = agent.db.events
    assert events.index(f"{row['transaction_id']}:commit") < events.index(f"{memory_tx}:open")


@pytest.mark.parametrize("operation", RUNTIME_PATHS)
def test_a_memory_insert_that_fails_leaves_the_audit_row_committed(recorded, operation):
    agent = recorded(decision_with(*called("c1", SEARCH)))
    persist = agent.traveler_memory.persist_turn

    async def insert_fails(*args):
        await persist(*args)
        raise MemoryInsertFailed("conversation_messages insert failed")

    agent.traveler_memory.persist_turn = insert_fails

    with pytest.raises(MemoryInsertFailed):
        asyncio.run(RUNTIME_PATHS[operation](agent))

    (row,) = committed(agent, recorded.rows)
    assert row["operation"] == operation
    assert row["tool_calls"] == [ToolCall("hybrid_trip_search", "allowed")]
    assert remembered(agent) == []


def test_a_failed_trip_refresh_writes_a_row_that_names_it_and_stores_no_memory_for_the_turn(
        recorded):
    agent = recorded(decision_with(*called("c1", SEARCH)))
    fail_the_trip_refresh(agent, "pkg-1")

    with pytest.raises(CatalogUnavailable):
        asyncio.run(RUNTIME_PATHS["production_turn"](agent))

    (row,) = committed(agent, recorded.rows)
    assert row["tool_calls"] == [ToolCall("hybrid_trip_search", "allowed")]
    # The refresh failed before the row was written, so the row names its error.
    assert row["authorization_decision"] == "error"
    assert row["turn_error"] == "CatalogUnavailable: pkg-1"
    assert row["rows_returned"] == 0
    assert agent.traveler_memory.turns == []


def test_the_next_turn_recalls_the_trips_of_completed_turns_and_not_of_the_incomplete_one(recorded):
    agent = recorded(decision_with())
    contexts = answer_each_turn(
        agent,
        decision_with(*called("c1", SEARCH),
                      message="Kyoto Ryokan Stay (KYO-001) suits a quiet week.",
                      packages=[{"package_id": "KYO-001", "name": "Kyoto Ryokan Stay"}],
                      recommended_package_ids=["KYO-001"]),
        decision_with(*called("c2", SEARCH),
                      message="Tokyo Indie Walk (TKY-001) fits your dates.",
                      packages=[{"package_id": "TKY-001", "name": "Tokyo Indie Walk"}],
                      recommended_package_ids=["TKY-001"]),
        decision_with(message="Which of those should I hold?", packages=[],
                      recommended_package_ids=[]),
    )
    recall_reads_back_what_was_committed(agent)
    fail_the_trip_refresh(agent, "TKY-001")
    turn = RUNTIME_PATHS["production_turn"]

    asyncio.run(turn(agent))
    with pytest.raises(CatalogUnavailable):
        asyncio.run(turn(agent))
    asyncio.run(turn(agent))

    recalled = contexts[2]
    assert "Previously shown trips" in recalled and "- KYO-001: " in recalled
    assert "TKY-001" not in recalled and "Tokyo Indie Walk" not in recalled


@pytest.mark.parametrize(("operation", "cancelled"), [
    ("production_turn", "before the write"),
    ("production_turn", "during the write"),
    ("production_hold", "during the write"),
    ("production_booking", "during the write"),
])
async def test_a_cancellation_is_never_replaced_by_a_failing_audit_write(
        recorded, caplog, operation, cancelled):
    agent = recorded(decision_with(*called("c1", SEARCH)))

    async def write_fails(**_kwargs):
        raise AuditWriteFailed("agent_audit_log insert failed")

    agent.store.write_audit = write_fails
    owner, method = (agent.db, "execute") if cancelled == "before the write" else (
        agent.store, "write_audit")
    reached, release = pause_inside(owner, method)
    turn = asyncio.create_task(RUNTIME_PATHS[operation](agent))
    await asyncio.wait_for(reached.wait(), 5)

    turn.cancel()
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await turn
    (logged,) = errors_logged(caplog)
    message = logged.getMessage()
    assert message.startswith("The audit row was not written: its write failed")
    assert all(ids in message for ids in (f"operation={operation}", *TURN_IDS))
    assert last_line_class(logged.exc_text) == "AuditWriteFailed", "the masked traceback is kept"


async def test_a_turn_awaited_inside_a_cancellation_handler_still_fails_on_a_failed_audit_write(
        recorded):
    agent = recorded(decision_with(*called("c1", SEARCH)))

    async def write_fails(**_kwargs):
        raise AuditWriteFailed("agent_audit_log insert failed")

    agent.store.write_audit = write_fails

    try:
        raise asyncio.CancelledError
    except asyncio.CancelledError:
        with pytest.raises(AuditWriteFailed):
            await RUNTIME_PATHS["production_turn"](agent)

    assert remembered(agent) == []


@pytest.mark.parametrize("operation", RUNTIME_PATHS)
async def test_a_cancellation_during_the_memory_unit_rolls_memory_back_and_keeps_the_row(
        recorded, operation):
    agent = recorded(decision_with(*called("c1", SEARCH)))
    reached, release = pause_inside(agent.traveler_memory, "persist_turn")
    turn = asyncio.create_task(RUNTIME_PATHS[operation](agent))
    await asyncio.wait_for(reached.wait(), 5)

    turn.cancel()
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await turn
    (row,) = committed(agent, recorded.rows)
    assert row["operation"] == operation
    assert remembered(agent) == []


async def test_an_audit_write_cancelled_at_teardown_is_logged_with_the_turns_ids(
        recorded, caplog):
    agent = recorded(decision_with(*called("c1", SEARCH)))
    reached, _release = pause_inside(agent.store, "write_audit")
    turn = asyncio.create_task(RUNTIME_PATHS["production_turn"](agent))
    await asyncio.wait_for(reached.wait(), 5)

    # As asyncio.run does at exit: every pending task is cancelled, the write's own included.
    for task in asyncio.all_tasks() - {asyncio.current_task()}:
        task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await turn
    assert committed(agent, recorded.rows) == []
    (logged,) = errors_logged(caplog)
    message = logged.getMessage()
    assert message.startswith("The audit row may not have been written: its write was cancelled")
    assert all(ids in message for ids in ("operation=production_turn", *TURN_IDS))


@pytest.mark.parametrize("operation", RUNTIME_PATHS)
def test_the_memory_specialists_entry_reaches_the_trace_after_its_commit(recorded, operation):
    agent = recorded(decision_with(*called("c1", SEARCH)))
    events = agent.db.events
    agent.activity_callback = lambda entry: events.append(f"trace:{entry.title}")

    asyncio.run(RUNTIME_PATHS[operation](agent))

    ((memory_tx, _turn),) = agent.traveler_memory.turns
    traced = events.index(f"trace:{FakeMemoryAgent.PERSIST_ENTRY}")
    assert events.index(f"{memory_tx}:commit") < traced


@pytest.mark.parametrize("operation", RUNTIME_PATHS)
def test_a_trace_that_rejects_the_memory_entry_leaves_the_rows_committed(recorded, operation):
    agent = recorded(decision_with(*called("c1", SEARCH)))

    def callback(entry):
        if entry.title == FakeMemoryAgent.PERSIST_ENTRY:
            raise TraceFailure(entry.title)

    agent.activity_callback = callback

    with pytest.raises(TraceFailure):
        asyncio.run(RUNTIME_PATHS[operation](agent))

    (row,) = committed(agent, recorded.rows)
    assert row["operation"] == operation
    assert len(remembered(agent)) == 1


# ------------------------------- a turn whose Runtime call fails still writes its one row

RUNTIME_ARN = f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/rt-1"
# The request URL botocore names in a transport error: the Runtime ARN, percent-encoded.
DATA_PLANE = ("https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3A"
              f"bedrock-agentcore%3Aus-east-1%3A{ACCOUNT}%3Aruntime%2Frt-1/invocations"
              "?qualifier=DEFAULT")
STARTED = span(category="runtime", title="AgentCore Runtime: turn started")
HOLD_PLACED = {"type": "hold", "hold": {"bookingId": "HLD-1", "status": "held"},
               "policyDecision": "allow"}
# What the Runtime streams when a tool cannot be set up (Task 16): the trace hook's span for the
# failed call, then the error run raised once the stop hook had ended the agent loop.
SETUP_FAILED = failed("c2", DETAILS, "Error: the client session is not running")
SETUP_ERROR = {"type": "error", "error_type": "ToolSetupError", "message": (
    "Tool MeridianHolds___get_package_details could not run (MCPClientInitializationError): "
    "the client session is not running")}
THROTTLED = ("An error occurred (ThrottlingException) when calling the ConverseStream operation: "
             "Too many requests")


def activity(entry):
    return {"type": "activity", **entry}


def masked(error):
    """The error's text as a row stores it: the account id in its request URL masked."""
    assert ACCOUNT in str(error)
    return str(error).replace(ACCOUNT, "<acct>")


def sse(*events):
    """Runtime events as the data plane streams them."""
    return b"".join(f"data: {json.dumps(event)}\n\n".encode() for event in events)


def real_runtime(agent):
    """Give the agent the real Runtime adapter over a fake data plane; return the data plane."""
    adapter = AgentCoreRuntimeAdapter(runtime_arn=RUNTIME_ARN, qualifier="DEFAULT",
                                      region="us-east-1")
    adapter._client = MagicMock()
    agent.agentcore_runtime = adapter
    return adapter._client


def runtime_streams(agent, *reads):
    """Answer the agent's Runtime call with a stream whose reads return or raise ``reads``."""
    body = MagicMock()
    body.read.side_effect = [*reads, b""]
    real_runtime(agent).invoke_agent_runtime.return_value = {"response": body}


SETUP_FAILURE_STREAM = sse(activity(STARTED), activity(before("c2", DETAILS)),
                           activity(SETUP_FAILED), SETUP_ERROR)


@pytest.mark.parametrize("operation", RUNTIME_PATHS)
def test_a_tool_setup_error_after_a_placed_hold_records_both_calls_and_reaches_the_caller(
        recorded, operation):
    agent = recorded(decision_with())
    runtime_streams(agent, sse(activity(STARTED), *map(activity, called("c1", HOLD_TOOL)),
                               HOLD_PLACED, activity(before("c2", DETAILS)),
                               activity(SETUP_FAILED), SETUP_ERROR))

    # The stream reported a placed hold, so the hold path says it was placed; the booking path
    # has no report about its booking.
    failure = failure_reaching_the_caller(
        agent, operation,
        "reply" if operation == "production_hold" else unknown_for_a_write(operation))

    (row,) = committed(agent, recorded.rows)
    assert row["operation"] == operation
    assert row["tool_calls"] == [ToolCall("create_courtesy_hold", "allowed"),
                                 ToolCall("get_package_details", "error")]
    assert row["authorization_decision"] == "error"
    assert row["turn_error"] == f"ToolSetupError: {SETUP_ERROR['message']}"
    assert row["rows_returned"] == 0
    assert isinstance(failure, RuntimeError)
    assert str(failure) == f"AgentCore Runtime error: {SETUP_ERROR['message']}"


def test_a_cedar_refusal_then_a_runtime_error_is_recorded_as_a_denial(recorded):
    agent = recorded(decision_with())
    runtime_streams(agent, sse(
        activity(STARTED), activity(before("c1", HOLD_TOOL)), activity(denied("c1", HOLD_TOOL)),
        {"type": "hold", "hold": None, "refused": "No Cedar policy permits this hold.",
         "policyDecision": "deny"},
        {"type": "error", "error_type": "ModelThrottledException", "message": THROTTLED}))

    with pytest.raises(RuntimeError, match="ThrottlingException"):
        asyncio.run(RUNTIME_PATHS["production_hold"](agent))

    (row,) = committed(agent, recorded.rows)
    assert row["tool_calls"] == [ToolCall("create_courtesy_hold", "refused", "policy")]
    assert row["authorization_decision"] == "deny"
    assert row["turn_error"] == f"ModelThrottledException: {THROTTLED}"


@pytest.mark.parametrize("failure", [EndpointConnectionError(endpoint_url=DATA_PLANE),
                                     ReadTimeoutError(endpoint_url=DATA_PLANE)],
                         ids=["unreachable", "timed out"])
@pytest.mark.parametrize("operation", RUNTIME_PATHS)
def test_a_runtime_that_answers_nothing_writes_a_row_with_unknown_calls_and_error(
        recorded, operation, failure):
    agent = recorded(decision_with())
    real_runtime(agent).invoke_agent_runtime.side_effect = failure
    # A timeout may follow a request the Runtime received; a connection never made proves not.
    sent = isinstance(failure, ReadTimeoutError)

    raised = failure_reaching_the_caller(
        agent, operation, unknown_for_a_write(operation) if sent else None)

    (row,) = committed(agent, recorded.rows)
    assert row["operation"] == operation
    assert row["tool_calls"] is None and row["authorization_decision"] == "error"
    assert row["turn_error"] == f"{type(failure).__name__}: {masked(failure)}"
    assert raised is failure


def test_a_stream_that_breaks_after_some_spans_records_unknown_calls(recorded):
    agent = recorded(decision_with())
    broken = ReadTimeoutError(endpoint_url=DATA_PLANE)
    runtime_streams(agent, sse(activity(STARTED), *map(activity, called("c1", SEARCH))), broken)

    with pytest.raises(ReadTimeoutError):
        asyncio.run(RUNTIME_PATHS["production_turn"](agent))

    # More calls may have run after the break, so the spans received cannot list them all.
    (row,) = committed(agent, recorded.rows)
    assert row["tool_calls"] is None and row["authorization_decision"] == "error"
    assert row["turn_error"] == f"ReadTimeoutError: {masked(broken)}"


@pytest.mark.parametrize(("event", "raised_as", "turn_error"), [
    ({"type": "error", "code": "authorization", "error_type": "CallerRefused",
      "message": "No signed-in caller: no bearer token was forwarded."}, RuntimeError,
     "CallerRefused: No signed-in caller: no bearer token was forwarded."),
    ({"type": "error", "code": "token_expired", "error_type": "CallerRefused",
      "message": "The caller's access token has expired. Sign in again."}, CallerTokenExpired,
     "CallerTokenExpired: The caller's access token has expired. Sign in again."),
], ids=["caller refused", "token expired"])
def test_a_runtime_error_before_any_span_records_unknown_calls(
        recorded, event, raised_as, turn_error):
    agent = recorded(decision_with())
    runtime_streams(agent, sse(event))

    with pytest.raises(raised_as):
        asyncio.run(RUNTIME_PATHS["production_turn"](agent))

    (row,) = committed(agent, recorded.rows)
    assert row["tool_calls"] is None and row["authorization_decision"] == "error"
    assert row["turn_error"] == turn_error


@pytest.mark.parametrize("operation", RUNTIME_PATHS)
def test_a_turn_whose_runtime_call_fails_stores_no_memory_even_after_a_placed_hold(
        recorded, operation):
    agent = recorded(decision_with())
    runtime_streams(agent, sse(activity(STARTED), *map(activity, called("c1", HOLD_TOOL)),
                               HOLD_PLACED, activity(before("c2", DETAILS)),
                               activity(SETUP_FAILED), SETUP_ERROR))

    failure = failure_reaching_the_caller(
        agent, operation,
        "reply" if operation == "production_hold" else unknown_for_a_write(operation))

    assert isinstance(failure, RuntimeError) and "could not run" in str(failure)
    (row,) = committed(agent, recorded.rows)
    assert row["operation"] == operation
    assert agent.traveler_memory.turns == []
    # The read unit and the audit unit; no memory unit was opened.
    assert agent.db.tx_count == 2


@pytest.mark.parametrize("operation", RUNTIME_PATHS)
def test_a_failing_audit_write_never_replaces_the_runtime_error(recorded, caplog, operation):
    agent = recorded(decision_with())
    runtime_streams(agent, SETUP_FAILURE_STREAM)

    async def write_fails(**_kwargs):
        raise AuditWriteFailed("agent_audit_log insert failed")

    agent.store.write_audit = write_fails

    failure = failure_reaching_the_caller(agent, operation, unknown_for_a_write(operation))

    assert isinstance(failure, RuntimeError) and "could not run" in str(failure)

    (logged,) = errors_logged(caplog)
    message = logged.getMessage()
    assert message.startswith("The audit row was not written: its write failed")
    assert all(ids in message for ids in (
        f"operation={operation}", "traveler=trv_meridian_demo", "conversation=conv-test",
        "runtime_session=rt-conv-test-"))
    assert last_line_class(logged.exc_text) == "AuditWriteFailed", "the masked traceback is kept"


@pytest.mark.parametrize("operation", RUNTIME_PATHS)
async def test_a_failed_turn_cancelled_during_its_audit_write_still_commits_the_row(
        recorded, operation):
    agent = recorded(decision_with())
    runtime_streams(agent, SETUP_FAILURE_STREAM)
    reached, release = pause_inside(agent.store, "write_audit")
    turn = asyncio.create_task(RUNTIME_PATHS[operation](agent))
    await asyncio.wait_for(reached.wait(), 5)

    turn.cancel()
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await turn
    (row,) = committed(agent, recorded.rows)
    assert row["operation"] == operation
    assert row["tool_calls"] == [ToolCall("get_package_details", "error")]


async def test_a_turn_cancelled_during_the_runtime_call_writes_a_row_with_unknown_calls(
        recorded):
    agent = recorded(decision_with())
    started, release = threading.Event(), threading.Event()

    def invoke_turn(*_args, **_kwargs):
        started.set()
        release.wait(5)
        return decision_with()

    agent.agentcore_runtime = SimpleNamespace(invoke_turn=invoke_turn)
    turn = asyncio.create_task(RUNTIME_PATHS["production_turn"](agent))
    await asyncio.to_thread(started.wait, 5)

    turn.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await turn
    finally:
        release.set()
    (row,) = committed(agent, recorded.rows)
    assert row["tool_calls"] is None and row["authorization_decision"] == "error"
    assert row["turn_error"] == "CancelledError"
    assert agent.traveler_memory.turns == []


async def test_a_failing_audit_write_during_a_cancelled_runtime_call_is_logged_once(
        recorded, caplog):
    agent = recorded(decision_with())
    started, release = threading.Event(), threading.Event()

    def invoke_turn(*_args, **_kwargs):
        started.set()
        release.wait(5)
        return decision_with()

    async def write_fails(**_kwargs):
        raise AuditWriteFailed("agent_audit_log insert failed")

    agent.agentcore_runtime = SimpleNamespace(invoke_turn=invoke_turn)
    agent.store.write_audit = write_fails
    turn = asyncio.create_task(RUNTIME_PATHS["production_turn"](agent))
    await asyncio.to_thread(started.wait, 5)

    turn.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await turn
    finally:
        release.set()
    (logged,) = errors_logged(caplog)
    assert logged.getMessage().startswith(
        "The audit row was not written: its write failed while the turn was cancelled")
    assert "runtime_session=unknown" in logged.getMessage()
    assert last_line_class(logged.exc_text) == "AuditWriteFailed", "the masked traceback is kept"


@pytest.mark.parametrize(("unreadable", "named"), UNREADABLE, ids=UNREADABLE_IDS)
def test_a_runtime_error_whose_text_cannot_be_read_writes_a_row_named_by_its_class(
        recorded, caplog, unreadable, named):
    agent = recorded(decision_with())
    failure = unreadable()
    real_runtime(agent).invoke_agent_runtime.side_effect = failure

    with pytest.raises(Unprintable) as raised:
        asyncio.run(RUNTIME_PATHS["production_turn"](agent))

    assert raised.value is failure
    (row,) = committed(agent, recorded.rows)
    assert row["tool_calls"] is None and row["authorization_decision"] == "error"
    assert row["turn_error"] == named
    assert errors_logged(caplog) == []
    (logged,) = warnings_logged(caplog)
    assert logged.getMessage().startswith(NAMED_BY_CLASS)
    assert "runtime_session=unknown" in logged.getMessage()
    assert last_line_class(logged.exc_text) == "ValueError", "the masked traceback is kept"


def test_a_runtime_error_with_a_lone_surrogate_writes_a_row_with_valid_utf8(recorded):
    agent = recorded(decision_with())
    # The event's JSON escapes the surrogate, so the adapter reads it back unpaired.
    runtime_streams(agent, sse(activity(STARTED), {
        "type": "error", "error_type": "ClientError", "message": "model said \ud800 then stopped"}))

    with pytest.raises(RuntimeTurnError):
        asyncio.run(RUNTIME_PATHS["production_turn"](agent))

    (row,) = committed(agent, recorded.rows)
    assert row["turn_error"] == "ClientError: model said \ufffd then stopped"
    assert row["turn_error"].encode("utf-8") and row["authorization_decision"] == "error"


def test_a_stream_that_ends_without_a_result_records_unknown_calls(recorded):
    agent = recorded(decision_with())
    runtime_streams(agent, sse(activity(STARTED), *map(activity, called("c1", SEARCH))))

    with pytest.raises(RuntimeError, match="returned no concierge message"):
        asyncio.run(RUNTIME_PATHS["production_turn"](agent))

    (row,) = committed(agent, recorded.rows)
    assert row["tool_calls"] is None and row["authorization_decision"] == "error"
    assert row["turn_error"] == "RuntimeError: AgentCore Runtime returned no concierge message."


# ---------------------------------------------- a failed turn's row says why it failed

TOOLS_LISTED = span(category="gateway", title="AgentCore Gateway: tools listed", tools="4")
MEMORY_READY = span(category="memory_short", title="AgentCore Memory session", actor="traveler")
IAM_REFUSAL = (
    "An error occurred (AccessDeniedException) when calling the ConverseStream operation: "
    "User: arn:aws:sts::123456789012:assumed-role/MeridianConcierge/session is not authorized "
    "to perform: bedrock:InvokeModelWithResponseStream")


@pytest.mark.parametrize("operation", RUNTIME_PATHS)
def test_a_completed_turn_stores_no_turn_error(recorded, operation):
    agent = recorded(decision_with(*called("c1", SEARCH), **PLACED[operation]))

    asyncio.run(RUNTIME_PATHS[operation](agent))

    (row,) = committed(agent, recorded.rows)
    assert row["turn_error"] is None and row["authorization_decision"] == "allow"


@pytest.mark.parametrize("operation", RUNTIME_PATHS)
def test_a_failed_turn_whose_calls_were_all_allowed_is_an_error_that_names_it(
        recorded, operation):
    agent = recorded(decision_with())
    runtime_streams(agent, sse(
        activity(STARTED), *map(activity, called("c1", SEARCH)),
        {"type": "error", "error_type": "ModelThrottledException", "message": THROTTLED}))

    failure = failure_reaching_the_caller(agent, operation, unknown_for_a_write(operation))

    assert isinstance(failure, RuntimeTurnError)
    (row,) = committed(agent, recorded.rows)
    assert row["tool_calls"] == [ToolCall("hybrid_trip_search", "allowed")]
    assert row["authorization_decision"] == "error"
    assert row["turn_error"] == f"ModelThrottledException: {THROTTLED}"


def test_a_model_call_refused_by_iam_is_an_error_that_names_access_denied(recorded):
    agent = recorded(decision_with())
    runtime_streams(agent, sse(
        activity(STARTED), activity(TOOLS_LISTED), activity(MEMORY_READY),
        {"type": "error", "error_type": "ClientError", "message": IAM_REFUSAL}))

    with pytest.raises(RuntimeTurnError, match="AccessDeniedException"):
        asyncio.run(RUNTIME_PATHS["production_turn"](agent))

    (row,) = committed(agent, recorded.rows)
    assert row["tool_calls"] == [] and row["authorization_decision"] == "error"
    assert row["turn_error"] == (
        "ClientError: An error occurred (AccessDeniedException) when calling the ConverseStream "
        "operation: User: arn:aws:sts::<acct>:assumed-role/MeridianConcierge/session is not "
        "authorized to perform: bedrock:InvokeModelWithResponseStream")


def test_a_runtime_error_after_only_the_start_span_lists_no_calls_and_is_an_error(recorded):
    agent = recorded(decision_with())
    runtime_streams(agent, sse(
        activity(STARTED),
        {"type": "error", "error_type": "MCPClientInitializationError",
         "message": "the client initialization failed"}))

    with pytest.raises(RuntimeTurnError):
        asyncio.run(RUNTIME_PATHS["production_turn"](agent))

    (row,) = committed(agent, recorded.rows)
    assert row["tool_calls"] == [] and row["authorization_decision"] == "error"
    assert row["turn_error"] == "MCPClientInitializationError: the client initialization failed"


SETUP_ERROR_UNNAMED = {key: value for key, value in SETUP_ERROR.items() if key != "error_type"}


@pytest.mark.parametrize(("event", "named"), [
    (SETUP_ERROR, "ToolSetupError"),
    (SETUP_ERROR_UNNAMED, "RuntimeTurnError"),
    ({**SETUP_ERROR, "error_type": "not a class name"}, "RuntimeTurnError"),
    ({**SETUP_ERROR, "error_type": "E" * 65}, "RuntimeTurnError"),
    ({**SETUP_ERROR, "error_type": 7}, "RuntimeTurnError"),
], ids=["named by the Runtime", "from a Runtime deployed before error_type", "not a class name",
        "a name too long", "not text"])
@pytest.mark.parametrize("operation", RUNTIME_PATHS)
def test_a_runtime_error_is_named_by_the_class_the_runtime_sent_or_else_runtime_turn_error(
        recorded, operation, event, named):
    agent = recorded(decision_with())
    runtime_streams(agent, sse(activity(STARTED), activity(before("c2", DETAILS)),
                               activity(SETUP_FAILED), event))

    failure = failure_reaching_the_caller(agent, operation, unknown_for_a_write(operation))

    assert isinstance(failure, RuntimeTurnError)

    (row,) = committed(agent, recorded.rows)
    assert row["tool_calls"] == [ToolCall("get_package_details", "error")]
    assert row["turn_error"] == f"{named}: {SETUP_ERROR['message']}"


def words(count):
    return " ".join(["word"] * count)


@pytest.mark.parametrize(("message", "stored"), [
    (words(60), words(60)),
    ("x" * 300, "x" * 300),
    # Cut at the last word that fits before the "...": 59 words are 294 characters.
    (words(100), words(59) + "..."),
    # A space just past the room still ends the room on a whole word.
    ("a" * 297 + " " + "b" * 10, "a" * 297 + "..."),
    # One word longer than the room has no boundary to cut at.
    ("x" * 301, "x" * 297 + "..."),
    ("  padded  ", "padded"),
], ids=["short", "exactly 300", "long", "space just past the room", "one long word", "padded"])
def test_a_message_is_cut_at_a_word_boundary_to_at_most_300_characters(message, stored):
    assert turn_error_of(RuntimeError(message)) == f"RuntimeError: {stored}"
    assert len(stored) <= 300


def test_account_ids_are_masked_before_the_message_is_measured():
    # 302 characters as raised and 296 once the account id is masked, so nothing is cut.
    message = words(54) + " arn:aws:iam::123456789012:role/x"
    assert len(message) == 302

    stored = turn_error_of(RuntimeError(message))

    assert stored == "RuntimeError: " + words(54) + " arn:aws:iam::<acct>:role/x"


@pytest.mark.parametrize("error", [asyncio.CancelledError(), RuntimeError("   "), KeyError()],
                         ids=["cancelled", "blank", "no message"])
def test_an_error_with_no_message_is_named_by_its_class_alone(error):
    assert turn_error_of(error) == type(error).__name__


def test_a_class_name_longer_than_64_characters_keeps_its_first_64():
    error = type("E" * 200, (RuntimeError,), {})("boom")
    assert turn_error_of(error) == "E" * 64 + ": boom"


@pytest.mark.parametrize(("message", "stored"), [
    ("bad\x00byte", "RuntimeError: badbyte"),
    (" \x00 ", "RuntimeError"),
    (f"role {ACCOUNT[:6]}\x00{ACCOUNT[6:]}", "RuntimeError: role <acct>"),
    # UTF-8 cannot encode a lone surrogate; each one becomes one U+FFFD.
    ("bad \ud800 byte \udfff", "RuntimeError: bad \ufffd byte \ufffd"),
], ids=["a NUL inside the text", "a NUL as the only character", "a NUL inside an account id",
        "lone surrogates"])
def test_text_postgresql_cannot_store_is_made_storable_before_it_is_masked(message, stored):
    assert turn_error_of(RuntimeError(message)) == stored


# Text with lone surrogates, which the default text strategy never draws.
WITH_SURROGATES = st.lists(st.one_of(st.text(max_size=50), st.characters(categories=["Cs"])),
                           max_size=40).map("".join)


@given(name=st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,199}", fullmatch=True),
       message=st.one_of(st.text(max_size=1000), WITH_SURROGATES), from_runtime=st.booleans())
# Random draws rarely pair a long name with a long message, so the limits are pinned too.
@example(name="E" * 200, message="word " * 200, from_runtime=False)
@example(name="E" * 200, message="word " * 200, from_runtime=True)
@example(name="E", message="a\x00b\ud800c\udfff", from_runtime=False)
@example(name="E", message="a\x00b\ud800c\udfff", from_runtime=True)
def test_every_turn_error_passes_the_checks_write_audit_applies(name, message, from_runtime):
    if from_runtime:
        error = RuntimeTurnError("x", decision_with(), error_type=name, event_message=message)
    else:
        error = type(name, (RuntimeError,), {})(message)

    stored = turn_error_of(error)

    assert turn_error_problem(stored, "error") is None
    # PostgreSQL text holds valid UTF-8 without NUL characters.
    assert "\x00" not in stored and stored.encode("utf-8")


# ------------------------------------------------------- the subject reaches the agent

@pytest.mark.parametrize(("authentication", "expected"), [("cognito", "sub-1"), ("bearer", None),
                                                          ("loopback-development", None)])
async def test_the_chat_route_passes_only_a_verified_subject(monkeypatch, authentication, expected):
    seen = {}

    async def fake(*_args, **kwargs):
        seen.update(kwargs)
        return [], [], "ok", "conv", []

    monkeypatch.setattr(chat_router, "production_search", fake)
    await chat_router.chat(
        chat_router.ChatRequest(message="Find Tokyo trips", phase=4),
        HttpPrincipal("sub-1", "trv_meridian_demo", authentication))

    assert seen["authorization_subject"] == expected


async def test_hold_and_booking_routes_pass_the_verified_subject(monkeypatch):
    hold = AsyncMock(return_value=chat_router.OrderResponse(message="ok", activities=[]))
    booking = AsyncMock(return_value=chat_router.BookingResponse(message="ok", activities=[]))
    monkeypatch.setattr(chat_router, "production_hold", hold)
    monkeypatch.setattr(chat_router, "production_booking", booking)
    principal = HttpPrincipal("sub-jordan", "trv_meridian_demo", "cognito")

    await chat_router.process_order(
        chat_router.OrderRequest(product_id="CTY-002", phase=4), principal)
    await chat_router.confirm_booking(chat_router.BookingRequest(booking_id="HLD-1"), principal)

    assert hold.call_args.kwargs["authorization_subject"] == "sub-jordan"
    assert booking.call_args.kwargs["authorization_subject"] == "sub-jordan"


# --------------------------------------- a subject the caller sends never reaches the row

VERIFIED = "sub-verified-in-test"
FORGED = "sub-forged-by-the-caller"
FORGED_BODY = {"authorization_subject": FORGED, "authorization_provider": "cognito",
               "subject_id": FORGED, "sub": FORGED}
FORGED_HEADERS = {"X-Authorization-Subject": FORGED, "X-Cognito-Sub": FORGED,
                  "X-Amzn-Oidc-Identity": FORGED}
WORKLOAD = ("aws_iam", "workload/demo")


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def serve(monkeypatch, recorded, decision):
    """The real app, route and Phase 4 agent; only the platform and Aurora calls are fakes."""
    for name in ("MERIDIAN_API_TOKEN", "MERIDIAN_API_TRAVELER_ID",
                 "MERIDIAN_ALLOW_INSECURE_LOCALHOST", "MERIDIAN_AGENTCORE_AUTH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setattr(http_auth, "get_cognito_verifier", lambda: None)
    monkeypatch.setattr(concierge_mod, "create_production_agent", lambda: recorded(decision))
    return TestClient(app)


@pytest.fixture
def served(monkeypatch, recorded):
    return serve(monkeypatch, recorded, decision_with(*called("c1", SEARCH)))


def test_the_trace_the_route_returns_names_each_refusal_its_row_records(monkeypatch, recorded):
    client = serve(monkeypatch, recorded, decision_with(
        before("c1", HOLD_TOOL), failed("c1", HOLD_TOOL, "insufficient_inventory"), GUARD))

    response = client.post("/api/chat", json={"message": "Hold the Tokyo trip", "phase": 4})

    assert response.status_code == 200, response.text
    (row,) = recorded.rows
    shown = [field["value"] for entry in response.json()["activities"]
             if (entry.get("telemetry") or {}).get("status") == "refused"
             for field in entry["telemetry"]["fields"] if field["label"] == "refused_by"]
    assert shown == [call.refused_by for call in row["tool_calls"]]
    assert shown == ["business_rule", "runtime_guard"]


def audited_turn(client, recorded, credential_headers):
    """Post one Phase 4 turn that claims a forged subject; return the one audit row written."""
    response = client.post(
        "/api/chat", headers={**FORGED_HEADERS, **credential_headers},
        json={"message": "Find Tokyo trips", "phase": 4, **FORGED_BODY})
    assert response.status_code == 200, response.text
    (row,) = recorded.rows
    assert FORGED not in repr(row)
    return row


def test_a_signed_in_caller_cannot_name_another_subject(served, recorded, signing_key,
                                                         monkeypatch):
    verifier = CognitoVerifier(CONFIG, signing_key_for=lambda _token: signing_key.public_key())
    monkeypatch.setattr(http_auth, "get_cognito_verifier", lambda: verifier)
    token = token_for(signing_key, (VERIFIED, "trv_meridian_demo"))

    row = audited_turn(served, recorded, {"Authorization": f"Bearer {token}"})

    assert (row["authorization_provider"], row["authorization_subject"]) == ("cognito", VERIFIED)


def test_a_loopback_caller_cannot_put_a_subject_in_the_row(served, recorded):
    row = audited_turn(served, recorded, {})

    assert (row["authorization_provider"], row["authorization_subject"]) == WORKLOAD


def test_a_shared_token_caller_cannot_put_a_subject_in_the_row(served, recorded, monkeypatch):
    monkeypatch.setenv("MERIDIAN_API_TOKEN", "shared-token-for-test")

    row = audited_turn(served, recorded, {"Authorization": "Bearer shared-token-for-test"})

    assert (row["authorization_provider"], row["authorization_subject"]) == WORKLOAD


# ------------------------------------- the catalog tools' calls, read from the Runtime's spans
# Each case runs the Runtime's own TraceHooks over the tool's answer, then reads the row the way
# the backend does, so the answer codes are pinned from the Lambda payload to the audit row.


ANSWER_CODES = {
    "currency_convert": ("MeridianCatalog___currency_convert", "unsupported_currency"),
    "get_package_details": ("MeridianHolds___get_package_details", "unknown_package"),
    "loyalty_balance": ("MeridianHolds___loyalty_balance", "loyalty_balance_unavailable"),
}


@pytest.mark.parametrize("tool", CATALOG_TOOLS)
def test_a_catalog_call_that_answers_is_allowed(monkeypatch, tool):
    audit = audit_from_decision(_traced_turn(monkeypatch, [
        (f"MeridianCatalog___{tool}", _answer({"packages": [], "summary": "an answer"}))]))
    assert audit.tool_calls == [ToolCall(tool, "allowed")] and audit.decision == "allow"


@pytest.mark.parametrize(("tool", "code"), ANSWER_CODES.values(), ids=ANSWER_CODES)
def test_a_factual_no_is_an_allowed_call_not_an_error(monkeypatch, tool, code):
    audit = audit_from_decision(_traced_turn(monkeypatch, [(tool, _answer({"error": code}))]))
    assert audit.tool_calls == [ToolCall(tool.split("___")[1], "allowed")]
    assert audit.decision == "allow"


@pytest.mark.parametrize("tool", ["create_courtesy_hold", "confirm_booking"])
def test_a_governed_write_carrying_a_catalog_answer_is_an_error_in_the_audit_row(
    monkeypatch, tool
):
    audit = audit_from_decision(_traced_turn(monkeypatch, [
        (f"MeridianHolds___{tool}", _answer({"error": "unknown_package", "package_id": "X"}))]))
    assert audit.tool_calls == [ToolCall(tool, "error")] and audit.decision == "error"


@pytest.mark.parametrize("tool", CATALOG_TOOLS)
def test_a_catalog_tool_that_failed_is_an_error_with_no_code(monkeypatch, tool):
    failed = {"error": "catalog_tool_failed", "tool": tool}
    audit = audit_from_decision(_traced_turn(monkeypatch, [
        (f"MeridianCatalog___{tool}", _answer(failed))]))
    assert audit.tool_calls == [ToolCall(tool, "error")] and audit.decision == "error"


def test_a_failed_loyalty_read_is_an_error_in_the_audit_row(monkeypatch):
    audit = audit_from_decision(_traced_turn(monkeypatch, [
        ("MeridianHolds___loyalty_balance", _answer({"error": "loyalty_balance_failed"}))]))
    assert audit.tool_calls == [ToolCall("loyalty_balance", "error")] and audit.decision == "error"


def test_an_argument_the_catalog_cannot_use_is_an_error(monkeypatch):
    audit = audit_from_decision(_traced_turn(monkeypatch, [
        ("MeridianCatalog___hybrid_trip_search", _answer({"error": "query is required"}))]))
    assert audit.tool_calls == [ToolCall("hybrid_trip_search", "error")]


def test_a_loyalty_read_the_grant_refused_is_refused_by_the_workload_grant(monkeypatch):
    refusal = {"error": "traveler_not_authorized",
               "governance": {"allowed": False, "decision": "deny", "subject": "AROA1",
                              "access_audit_id": "authz_0123456789ab"}}
    audit = audit_from_decision(_traced_turn(monkeypatch, [
        ("MeridianHolds___loyalty_balance", _answer(refusal))]))
    assert audit.tool_calls == [ToolCall("loyalty_balance", "refused", "workload_grant",
                                         "traveler_not_authorized", "authz_0123456789ab")]
    assert audit.decision == "deny"


def test_the_code_table_stays_the_holds_lambdas_codes():
    answers_and_failures = {"unsupported_currency", "unknown_package",
                            "loyalty_balance_unavailable", "catalog_tool_failed"}
    assert not answers_and_failures & set(CODE_CLASSES)
