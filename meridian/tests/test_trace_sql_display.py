"""A trace shows the SQL that ran with placeholders, and the values it bound beside it."""

import asyncio
from types import SimpleNamespace

import pytest

import backend.routers.chat as chat_router
from backend.agents.phase_04_production import concierge as concierge_mod
from backend.agents.phase_04_production.memory_agent import MemoryAgent
from backend.agents.phase_05_workflow.nodes import WorkflowNodes
from backend.db.rds_data_client import ScopeTimings
from backend.retrieval import availability
from backend.search_utils import display_sql, numbered_sql, parse_search_query, search_title
from tests.test_production_transaction_boundaries import build_agent, runtime_decision


def test_placeholders_are_numbered_and_the_values_listed_apart():
    shown = display_sql(
        "SELECT a FROM t WHERE trip_type = %s AND price_per_person <= %s LIMIT %s",
        ("City Breaks", 2000.0, 5),
    )
    assert shown == (
        "SELECT a FROM t WHERE trip_type = $1 AND price_per_person <= $2 LIMIT $3\n"
        "-- Values: $1 = 'City Breaks', $2 = 2000.0, $3 = 5"
    )


def test_a_hostile_value_never_appears_in_the_statement_text():
    hostile = "x'; DROP TABLE trip_packages;--"
    shown = display_sql("SELECT 1 WHERE a = %s", (hostile,))
    statement, _, values = shown.partition("\n-- Values:")
    assert "DROP" not in statement
    assert "x''; DROP TABLE trip_packages;--" in values


def test_a_value_with_line_breaks_stays_on_the_values_line():
    hostile = "a\nDROP TABLE t;\r\nSELECT 2\tx\x00"
    shown = display_sql("SELECT 1 WHERE a = %s", (hostile,))
    assert shown.split("\n") == [
        "SELECT 1 WHERE a = $1",
        "-- Values: $1 = 'a\\nDROP TABLE t;\\r\\nSELECT 2\\tx\\x00'",
    ]


def test_literal_backslashes_and_unicode_line_separators_stay_on_the_values_line():
    hostile = "x\\n y z\x85"
    shown = display_sql("SELECT 1 WHERE a = %s", (hostile,))
    assert len(shown.splitlines()) == 2
    assert shown.splitlines()[1] == "-- Values: $1 = 'x\\\\n\\u2028y\\u2029z\\x85'"


def test_none_booleans_and_long_text_are_shown_without_flooding_the_trace():
    shown = display_sql("SELECT %s, %s, %s", (None, True, "v" * 200))
    assert "$1 = NULL, $2 = true, $3 = '" + "v" * 40 + "... (200 characters)'" in shown


def test_a_statement_without_values_is_just_the_statement():
    assert display_sql("SELECT   1\n  FROM   t") == "SELECT 1 FROM t"


def test_a_statement_that_already_uses_numbered_placeholders_keeps_them():
    assert display_sql("SELECT $1, $1", ("it's",)) == "SELECT $1, $1\n-- Values: $1 = 'it''s'"


def test_comment_lines_survive_so_they_stay_comments():
    shown = numbered_sql("-- read unit\nSELECT set_config('a', %s, true);")
    assert shown.splitlines()[0] == "-- read unit"
    assert "$1" in shown


@pytest.mark.parametrize(("sql", "values"), [("SELECT %s", ()), ("SELECT 1", ("extra",))])
def test_a_mismatch_between_placeholders_and_values_is_an_error(sql, values):
    with pytest.raises(ValueError, match="binds"):
        display_sql(sql, values)


def test_search_titles_name_the_filter_that_ran():
    assert search_title(parse_search_query("Show me city trips under $2,000")) == (
        "Trip type filter: City Breaks")
    assert search_title(parse_search_query("Tokyo culture")) == "Filter search: Tokyo culture"


PACKAGE_ROW = {
    "package_id": "AML-002", "name": "Amalfi Coast Villa Week", "operator": "Costa",
    "price_per_person": 1599.0, "description": "Villa", "image_url": "", "trip_type": "Beach",
    "destination": "Positano", "region": "Europe", "durations": ["5 nights"],
    "availability": {"5 nights": 3}, "highlights": [], "similarity": 0.4,
}


def split_values(sql: str) -> tuple[str, str]:
    statement, _, values = sql.partition("\n-- Values: ")
    return statement, values


class EmptyStore:
    async def recall_short_term(self, conversation_id, limit, transaction_id=None):
        return []

    async def recall_preferences(self, traveler_id, limit, transaction_id=None):
        return []

    async def recall_similar_interactions(self, *args, **kwargs):
        return []


def memory_spans():
    agent = MemoryAgent.__new__(MemoryAgent)
    spans = []
    agent.activity_callback = spans.append
    agent.store = EmptyStore()
    agent._transaction_id = None
    agent._prepared_query_vector = None
    agent._query_vector_prepared = False
    return agent, spans


async def test_memory_recall_spans_bind_ids_and_limits():
    agent, spans = memory_spans()
    await agent.recall_session_context("conv-1")
    await agent.recall_traveler_preferences("trv_1")
    await agent.recall_similar_interactions("trv_1", "quiet villa")

    shown = [split_values(span.sql_query) for span in spans]
    assert shown[0][1] == "$1 = 'conv-1', $2 = 6"
    assert shown[1][1] == "$1 = 'trv_1', $2 = 8"
    similar = shown[2][1]
    assert similar.startswith("$1 = '[query embedding") and similar.endswith("$2 = 'trv_1', $3 = 3")
    for statement, _values in shown:
        assert "conv-1" not in statement and "trv_1" not in statement


async def test_availability_span_binds_the_package_id(monkeypatch):
    class OneRow:
        async def execute(self, sql, params=None, transaction_id=None):
            return [dict(PACKAGE_ROW)]

    monkeypatch.setattr(availability, "get_rds_data_client", lambda: OneRow())
    _products, activities, _message = await availability.retrieval_availability_search(
        "Which trip lengths are still available?", package_id="AML-002")

    span = next(a for a in activities if a.title == "Availability: Checking duration inventory")
    assert span.sql_query == (
        "SELECT package_places_left(package_id) AS availability, durations "
        "FROM trip_packages WHERE package_id = $1\n-- Values: $1 = 'AML-002'")


async def test_compared_package_hydration_binds_the_ids(monkeypatch):
    class Db:
        async def execute(self, sql, params=None, transaction_id=None):
            return [{"package_id": pid} for pid in params]

    monkeypatch.setattr(chat_router, "get_rds_data_client", lambda: Db())
    activities: list = []
    await chat_router._hydrate_compared(["CTY-002", "CTY-019"], activities)

    span = next(a for a in activities
                if a.title == "Hydrated compared packages into product cards")
    statement, values = split_values(span.sql_query)
    assert "package_id IN ($1, $2)" in statement and "CTY-002" not in statement
    assert values == "$1 = 'CTY-002', $2 = 'CTY-019'"


def test_the_rls_read_unit_binds_the_traveler(monkeypatch):
    agent = build_agent([], runtime_decision(), [])
    spans = []
    agent.activity_callback = spans.append
    scope = SimpleNamespace(
        iam_identity="arn:aws:sts::123456789012:assumed-role/r/s",
        authorization=SimpleNamespace(provider="aws_iam", subject_id="AROATEST"),
    )

    agent._identity_spans(scope, "trv_meridian_demo", ScopeTimings())

    span = next(s for s in spans if s.title == "Aurora RLS: short read unit")
    statement, values = split_values(span.sql_query)
    assert "set_config('app.current_traveler_id', $1, true)" in statement
    assert "SET LOCAL ROLE meridian_app" in statement
    assert "trv_meridian_demo" not in statement
    assert values == "$1 = 'trv_meridian_demo', $2 = 'concierge_agent'"


def test_the_catalog_refresh_span_binds_the_package_ids(monkeypatch):
    agent = build_agent([], runtime_decision(), [])
    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda **_kw: None)

    _packages, activities, *_ = asyncio.run(
        agent.process_turn("Plan Tokyo", "trv_meridian_demo", None, 5))

    span = next(a for a in activities
                if a.title == "Refreshed discussed trips from the live catalog")
    statement, values = split_values(span.sql_query)
    assert "package_id IN ($1)" in statement
    assert values == "$1 = 'pkg-1'"


def test_the_reconciled_hold_span_shows_numbered_placeholders():
    span = WorkflowNodes._reconciled_span(
        {"package_id": "CTY-002", "duration": "5 nights", "hold_request_id": "HR-1", "quantity": 2},
        {"hold_id": "HLD-1", "status": "held", "journey_id": "J-1",
         "expires_at": "2026-10-01T00:00:00Z", "created_at": "2026-10-01T00:00:00Z",
         "observed_at": "2026-10-01T00:00:00Z"},
    )
    assert "%s" not in span["sql_query"]
    assert "hr.journey_id = $1 AND hr.hold_request_id = $2" in span["sql_query"]
