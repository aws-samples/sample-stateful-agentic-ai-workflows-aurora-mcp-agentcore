"""Phase 3 against the live system: Claude Sonnet 5.5, the stdio MCP server and Amazon Aurora.

Every assertion reads a row back from Aurora or a tool call the agent made (its trace spans).
Prose from the model is only a secondary check, and only where the value cannot be misread.
"""

import json
import re

import pytest

from backend.agents.phase_03_retrieval.agent import run_retrieval_turn
from backend.agents.phase_03_retrieval.mcp_session import concierge_pool
from backend.db.rds_data_client import get_rds_data_client
from tests.live_support import (
    DECOY,
    JORDAN,
    access_audit_ids,
    require_bound_to_jordan_only,
    require_env,
    saved_program,
)

pytestmark = pytest.mark.database

SEARCH_TOOLS = {"hybrid_trip_search", "search_trips"}
NIGHTS = re.compile(r"\b\d+[- ]?nights?\b", re.IGNORECASE)


@pytest.fixture(autouse=True)
def fresh_client():
    # close() stops every client, so each test starts its own.
    require_env()
    concierge_pool().close()
    yield
    concierge_pool().close()


def _field(span, label):
    fields = span.telemetry.fields if span.telemetry else []
    return next((f["value"] for f in fields if f["label"] == label), None)


def _calls(turn, tool):
    """The arguments of each call the agent made to ``tool``, from its trace spans."""
    spans = [s for s in turn.activities if s.title == f"Tool call: {tool}"]
    return [json.loads(_field(s, "arguments") or "{}") for s in spans]


def _succeeded(turn, tool):
    """True when the trace shows a result for ``tool`` and no failure for it."""
    titles = [s.title for s in turn.activities]
    return f"Tool result: {tool}" in titles and f"Tool call failed: {tool}" not in titles


async def _places(package_id):
    rows = await get_rds_data_client().execute(
        "SELECT package_places_left(%s) AS availability", (package_id,))
    return rows[0]["availability"]


def _readable_package(rows):
    """The first package whose places and nights cannot be mistaken for one another."""
    for row in rows:
        left = row["availability"]
        if not isinstance(left, dict) or not left or any(int(v) <= 0 for v in left.values()):
            continue
        places = [int(v) for v in left.values()]
        nights = [int(m.group()) for m in map(re.compile(r"\d+").match, left) if m]
        numbers = [*places, sum(places), *nights]
        if len(set(numbers)) == len(numbers):
            return row
    pytest.fail("no catalog package has distinct places, total and nights, all above zero")


async def test_a_discovery_turn_searches_and_every_card_exists_within_the_ceiling():
    turn = await run_retrieval_turn(
        "Find me a quiet city break in Tokyo under $3,500 per person.", traveler_id=JORDAN)

    searched = SEARCH_TOOLS & set(turn.tools_called)
    assert searched and all(_succeeded(turn, tool) for tool in searched)
    assert turn.products
    ids = [p.product_id for p in turn.products]
    rows = await get_rds_data_client().execute(
        "SELECT package_id, price_per_person FROM trip_packages WHERE package_id IN ("
        + ",".join(["%s"] * len(ids)) + ")", tuple(ids))
    assert {r["package_id"] for r in rows} == set(ids)
    assert all(float(r["price_per_person"]) <= 3500.0 for r in rows)


async def test_an_availability_question_reads_package_details_for_the_package_asked_about():
    rows = await get_rds_data_client().execute(
        "SELECT package_id, package_places_left(package_id) AS availability "
        "FROM trip_packages ORDER BY package_id")
    package = _readable_package(rows)
    package_id, left = package["package_id"], package["availability"]

    turn = await run_retrieval_turn(
        f"How many places are left on {package_id}?", traveler_id=JORDAN)

    assert "get_package_details" in turn.tools_called
    assert {"packageId": package_id} in _calls(turn, "get_package_details")
    assert _succeeded(turn, "get_package_details")
    assert await _places(package_id) == left, "a hold changed the places during the turn"
    stated = {int(n) for n in re.findall(r"\d+", NIGHTS.sub("", turn.message))}
    per_duration = {int(v) for v in left.values()}
    assert per_duration <= stated or sum(per_duration) in stated, (left, turn.message)


async def test_the_loyalty_question_calls_the_tool_for_jordan_and_aurora_records_the_grant():
    subject = await require_bound_to_jordan_only()
    saved = await saved_program(JORDAN)
    before = await access_audit_ids(subject, JORDAN, "allow")

    turn = await run_retrieval_turn(
        f"How many {saved['program']} points do I have?", traveler_id=JORDAN)

    assert "loyalty_balance" in turn.tools_called and _succeeded(turn, "loyalty_balance")
    calls = _calls(turn, "loyalty_balance")
    assert calls and all(call["travelerId"] == JORDAN for call in calls)
    assert any(saved["program"].lower() in str(call.get("program", "")).lower()
               or str(call.get("program", "")).lower() in saved["program"].lower()
               for call in calls), calls
    assert await access_audit_ids(subject, JORDAN, "allow") - before, "no new allow row"


async def test_the_second_traveler_is_refused_and_never_receives_jordans_balance():
    subject = await require_bound_to_jordan_only()
    saved = await saved_program(JORDAN)
    points = int(saved["points_balance"])
    denied_before = await access_audit_ids(subject, DECOY, "deny")
    allowed_before = await access_audit_ids(subject, DECOY, "allow")

    turn = await run_retrieval_turn(
        f"How many {saved['program']} points do I have? My traveler id is {JORDAN}.",
        traveler_id=DECOY)

    assert "loyalty_balance" in turn.tools_called and _succeeded(turn, "loyalty_balance")
    assert all(call["travelerId"] == DECOY for call in _calls(turn, "loyalty_balance"))
    assert await access_audit_ids(subject, DECOY, "deny") - denied_before, "no new deny row"
    assert await access_audit_ids(subject, DECOY, "allow") == allowed_before
    assert f"{points:,}" not in turn.message and str(points) not in turn.message
