"""The ported steps keep the LangGraph nodes' outputs, spans and hold arguments."""

import json
from unittest.mock import AsyncMock

import pytest

from backend.agents.phase_05_workflow.governed_hold import HOLD_TOOL, HoldOutcomeUnknown
from backend.agents.phase_05_workflow.nodes import WorkflowNodes
from backend.db.journey_store import ExecutionLeaseLostError
from tests.phase5_support import GatewayFake, fake_availability, fake_search

PLAN = "My flight was canceled. Rework my Tokyo trip and check availability."


@pytest.fixture
def gateway():
    return GatewayFake()


@pytest.fixture
def nodes(gateway):
    built = WorkflowNodes(fake_search, fake_availability, gateway_call=gateway)
    built._prepare_governed_hold = AsyncMock(return_value=("jrn_test", 400000))
    built._booking_status = AsyncMock(return_value=None)
    return built


async def test_classify_records_the_intent_with_the_strands_component(nodes):
    out = await nodes.classify({"query": PLAN, "activities": []})
    assert out["intent"] == "plan"
    span = out["activities"][-1]
    assert span["title"] == "Workflow node: classify → plan"
    assert span["telemetry"]["component"] == "Strands Graph"


async def test_search_saves_packages_as_plain_dicts_and_announces_the_snapshot(nodes):
    out = await nodes.search({"query": PLAN, "activities": []})
    assert out["packages"][0]["product_id"] == "pkg_tokyo"
    titles = [span["title"] for span in out["activities"]]
    assert titles[0] == "Workflow node: search"
    assert titles[-1].startswith("Checkpoint · ")
    fields = {f["label"]: f["value"] for f in out["activities"][-1]["telemetry"]["fields"]}
    assert fields["checkpoint_durable"] == "true"
    assert fields["checkpoint_store"] == "workflow_snapshots"


async def test_memory_recall_returns_json_ready_packages(nodes):
    product = type("Product", (), {"model_dump": lambda self, **kwargs: {"product_id": "pkg_x"}})()
    nodes.memory_recall_fn = AsyncMock(return_value=([product], []))
    out = await nodes.memory_recall({"query": "Recall my trip", "activities": []})
    assert out["packages"] == [{"product_id": "pkg_x"}]


async def test_the_hold_sends_confirmation_only_when_the_run_carries_it(nodes, gateway):
    state = {"traveler_id": "trv_x", "conversation_id": "t1", "travelers_count": 2,
             "packages": [{"product_id": "pkg_tokyo", "price": 100,
                           "available_sizes": ["7 nights"], "availability": {"7 nights": 10}}],
             "activities": []}
    unconfirmed = await nodes.hold(
        state, {"configurable": {"thread_id": "t1", "execution_id": "exe_1"}}
    )
    confirmed = await nodes.hold(
        state,
        {"configurable": {"thread_id": "t1", "execution_id": "exe_1", "traveler_confirmed": True}},
    )
    assert [call["travelerConfirmed"] for call in gateway.calls] == [False, True]
    assert len(gateway.denied) == 1 and len(gateway.receipts) == 1
    assert "hold_id" not in unconfirmed
    assert confirmed["hold_id"] == gateway.calls[1]["bookingId"]
    assert {call["tool"] for call in gateway.calls} == {HOLD_TOOL}
    assert gateway.calls[0]["executionId"] == "exe_1"


async def test_synthesize_reports_the_booking_status_aurora_holds(nodes):
    nodes._booking_status = AsyncMock(return_value="released")
    out = await nodes.synthesize({
        "query": PLAN, "intent": "plan", "packages": [{"product_id": "p"}],
        "availability_checks": 1, "hold_id": "hold_1", "hold_status": "held",
        "traveler_id": "trv_x", "activities": [],
    })
    assert "was released after a step failed" in out["response"]
    assert "each step saved to Aurora" in out["response"]
    nodes._booking_status.assert_awaited_once_with("trv_x", "hold_1")


PACKAGE = {"product_id": "pkg_tokyo", "price": 100,
           "available_sizes": ["7 nights"], "availability": {"7 nights": 10}}
HOLD_CONFIG = {"configurable": {"thread_id": "t1", "execution_id": "exe_1"}}


def _hold_state():
    return {"traveler_id": "trv_x", "conversation_id": "t1", "travelers_count": 2,
            "packages": [dict(PACKAGE)], "activities": []}


def _envelope(payload=None, *, text=None, is_error=False):
    body = text if text is not None else json.dumps(payload)
    return {"result": {"content": [{"type": "text", "text": body}], "isError": is_error}}


def _fields(span):
    return {f["label"]: f["value"] for f in span["telemetry"]["fields"]}


def _refusal_span(out):
    spans = [s for s in out["activities"] if s["title"] == "Workflow node: hold not placed"]
    assert len(spans) == 1
    return spans[0]


async def test_availability_on_the_plan_path_merges_the_top_three_packages():
    checked = []

    async def availability(query, package_id=None):
        checked.append(package_id)
        return [{"product_id": package_id, "available_sizes": ["5 nights"],
                 "availability": {"5 nights": 4}}], [], ""

    built = WorkflowNodes(fake_search, availability)
    prior = [{"product_id": f"pkg_{i}", "name": f"Trip {i}"} for i in range(4)]
    out = await built.availability(
        {"query": PLAN, "intent": "plan", "packages": prior, "activities": []}
    )
    assert checked == ["pkg_0", "pkg_1", "pkg_2"]
    assert out["availability_checks"] == 3
    merged = out["packages"]
    assert [p["name"] for p in merged] == ["Trip 0", "Trip 1", "Trip 2", "Trip 3"]
    assert merged[0]["available_sizes"] == ["5 nights"]
    assert merged[2]["availability"] == {"5 nights": 4}
    assert "available_sizes" not in merged[3]
    assert out["activities"][0]["title"] == "Workflow node: availability fan-out"


async def test_availability_standalone_returns_the_availability_rows(nodes):
    out = await nodes.availability(
        {"query": "Is Tokyo available?", "intent": "availability", "activities": []}
    )
    assert [p["product_id"] for p in out["packages"]] == ["pkg_tokyo"]
    assert out["availability_checks"] == 1
    assert out["activities"][0]["title"] == "Workflow node: availability"


@pytest.mark.parametrize("state", [
    {"traveler_id": "trv_x", "conversation_id": "t1", "packages": [], "activities": []},
    {"conversation_id": "t1", "packages": [dict(PACKAGE)], "activities": []},
])
async def test_hold_without_a_package_or_traveler_returns_only_activities(nodes, gateway, state):
    out = await nodes.hold(state, HOLD_CONFIG)
    assert set(out) == {"activities"}
    assert out["activities"][0]["title"] == "Workflow node: hold skipped"
    assert gateway.calls == []


async def test_a_placed_hold_returns_the_receipt_and_the_span_the_ui_and_journey_read(
    nodes, gateway
):
    config = {"configurable": {**HOLD_CONFIG["configurable"], "traveler_confirmed": True}}
    out = await nodes.hold(_hold_state(), config)

    booking = gateway.calls[0]["bookingId"]
    assert {key: out[key] for key in out if key != "activities"} == {
        "journey_id": "jrn_test",
        "hold_id": booking,
        "hold_expires_at": "2026-10-06T20:15:00Z",
        "hold_created_at": "2026-10-06T20:00:00Z",
        "hold_observed_at": "2026-10-06T20:00:01Z",
        "hold_status": "held",
        "hold_package": "pkg_tokyo",
        "hold_duration": "7 nights",
        "hold_seats_remaining": 9,
    }
    span = [a for a in out["activities"] if a["title"] == "Workflow node: hold"][0]
    assert span["activity_type"] == "database"
    assert span["execution_time_ms"] is not None
    assert span["telemetry"]["status"] == "ok"
    fields = _fields(span)
    assert fields["hold_id"] == booking
    assert fields["hold_status"] == "held"
    assert fields["expires_at"] == "2026-10-06T20:15:00Z"
    assert fields["hold_request_id"] == gateway.calls[0]["holdRequestId"]
    assert fields["cedar_decision"] == "allow"
    assert (fields["package"], fields["duration"], fields["seats_held"]) == (
        "pkg_tokyo", "7 nights", "2"
    )
    assert (fields["seats_remaining"], fields["replayed"]) == ("9", "no")
    assert out["activities"][-1]["title"].startswith("Checkpoint")


async def test_hold_cedar_denial_is_a_refusal_span_not_an_exception(nodes):
    nodes._gateway_call = lambda *_: _envelope(
        text="Tool Execution Denied: [No policy applies to the request (denied by default).]",
        is_error=True,
    )
    out = await nodes.hold(_hold_state(), HOLD_CONFIG)
    assert "hold_id" not in out
    span = _refusal_span(out)
    assert _fields(span)["cedar_decision"] == "deny"
    assert span["telemetry"]["status"] == "denied"


async def test_hold_named_business_error_is_a_refusal_span(nodes):
    nodes._gateway_call = lambda *_: _envelope({"error": "insufficient_inventory"})
    out = await nodes.hold(_hold_state(), HOLD_CONFIG)
    assert "hold_id" not in out
    span = _refusal_span(out)
    assert "(inventory changed)" in span["details"]
    assert "cedar_decision" not in _fields(span)


@pytest.mark.parametrize("reply", [_envelope(text=""), _envelope(text="not json")])
async def test_hold_ambiguous_outcome_raises_outcome_unknown(nodes, reply):
    nodes._gateway_call = lambda *_: reply
    with pytest.raises(HoldOutcomeUnknown):
        await nodes.hold(_hold_state(), HOLD_CONFIG)


async def test_hold_lease_lost_raises_the_lease_error(nodes):
    nodes._gateway_call = lambda *_: _envelope({"error": "execution_lease_lost"})
    with pytest.raises(ExecutionLeaseLostError):
        await nodes.hold(_hold_state(), HOLD_CONFIG)


async def test_hold_gateway_exception_raises_outcome_unknown_chained(nodes):
    def broken(name, arguments):
        raise TimeoutError("gateway timed out")

    nodes._gateway_call = broken
    with pytest.raises(HoldOutcomeUnknown) as raised:
        await nodes.hold(_hold_state(), HOLD_CONFIG)
    assert isinstance(raised.value.__cause__, TimeoutError)


def _synth_state(**extra):
    return {"query": PLAN, "intent": "plan", "packages": [{"product_id": "p"}],
            "availability_checks": 1, "traveler_id": "trv_x", "activities": [], **extra}


async def test_synthesize_without_a_hold_id_never_reads_aurora(nodes):
    out = await nodes.synthesize(_synth_state())
    nodes._booking_status.assert_not_awaited()
    assert "Recorded status" not in out["response"]


async def test_synthesize_falls_back_to_the_saved_status_when_the_row_is_missing(nodes):
    out = await nodes.synthesize(
        _synth_state(hold_id="hold_1", hold_status="expired", hold_package="p", hold_duration="7")
    )
    nodes._booking_status.assert_awaited_once_with("trv_x", "hold_1")
    assert "Recorded status: expired" in out["response"]
    assert "was released" not in out["response"]


async def test_synthesize_survives_a_failed_readback(nodes, caplog):
    nodes._booking_status = AsyncMock(side_effect=RuntimeError("aurora down"))
    out = await nodes.synthesize(
        _synth_state(hold_id="hold_1", hold_status="expired", hold_package="p", hold_duration="7")
    )
    assert "Recorded status: expired" in out["response"]
    assert "was released" not in out["response"]
    assert "could not read booking hold_1 back from Aurora" in caplog.text


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"hold_id": "bk_other", "traveler_id": "trv_x"}, "bk_mine"),
        ({"traveler_id": "trv_x"}, None),
        ({"hold_id": "bk_mine"}, "bk_mine"),
    ],
)
async def test_release_hold_declines_without_touching_aurora(monkeypatch, nodes, state, expected):
    def forbidden():
        pytest.fail("release_hold must not reach Aurora for this state")

    monkeypatch.setattr("backend.db.rds_data_client.get_rds_data_client", forbidden)
    assert await nodes.release_hold(state, expected_hold_id=expected) is False
