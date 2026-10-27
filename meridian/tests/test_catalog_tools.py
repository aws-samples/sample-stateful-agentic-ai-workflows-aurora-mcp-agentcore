"""search_trips binds every value as a Data API parameter and never writes one into the SQL."""

from decimal import Decimal

import pytest

import backend.routers.chat as chat_router
from backend.tools import catalog_tools

ROW = {
    "package_id": "CTY-002", "name": "Tokyo Culture & Cuisine", "trip_type": "City Breaks",
    "destination": "Tokyo", "region": "Asia", "price_per_person": Decimal("1949.00"),
    "operator": "JAL Premium", "description": "Marunouchi stay", "image_url": "",
    "durations": ["5 nights"], "availability": {"5 nights": 3}, "highlights": ["Lounge"],
}


class RecordingDb:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = [ROW] if rows is None else rows

    async def execute(self, sql, params=None, transaction_id=None):
        self.calls.append((sql, tuple(params or ())))
        return self.rows


@pytest.fixture
def db(monkeypatch):
    recording = RecordingDb()
    monkeypatch.setattr(catalog_tools, "get_rds_data_client", lambda: recording)
    return recording


async def test_trip_type_and_price_are_bound_parameters(db):
    found = await catalog_tools.search_trips(trip_type="City Breaks", max_price_per_person=2000.0)

    sql, params = db.calls[0]
    assert "trip_type = %s AND price_per_person <= %s" in sql
    assert "ORDER BY price_per_person ASC LIMIT %s" in sql
    assert params == ("City Breaks", 2000.0, 5)
    assert "City Breaks" not in sql
    assert found["parameters"] == ["City Breaks", 2000.0, 5]
    assert "trip_type = $1 AND price_per_person <= $2" in found["sql"]
    assert "%s" not in found["sql"]


async def test_a_hostile_trip_type_is_only_ever_a_parameter(db):
    hostile = "City'; DROP TABLE trip_packages;--"
    found = await catalog_tools.search_trips(trip_type=hostile)

    sql, params = db.calls[0]
    assert hostile in params
    assert "DROP" not in sql and "DROP" not in found["sql"]


async def test_free_text_searches_five_columns_with_five_parameters(db):
    found = await catalog_tools.search_trips(text="villa", limit=3)

    sql, params = db.calls[0]
    assert sql.count("ILIKE %s") == 5
    assert params == ("%villa%",) * 5 + (3,)
    assert "villa" not in found["sql"]


async def test_no_filter_still_binds_the_limit(db):
    await catalog_tools.search_trips()
    sql, params = db.calls[0]
    assert " WHERE " not in sql
    assert params == (5,)


@pytest.mark.parametrize(("asked", "bound"), [(500, 20), (0, 1), (-4, 1), (7, 7)])
async def test_the_limit_is_clamped(db, asked, bound):
    await catalog_tools.search_trips(limit=asked)
    assert db.calls[0][1][-1] == bound


async def test_a_zero_budget_is_a_filter_not_a_missing_one(db):
    await catalog_tools.search_trips(max_price_per_person=0.0)
    assert "price_per_person <= %s" in db.calls[0][0]


async def test_packages_use_the_catalog_shape_with_plain_numbers(db):
    found = await catalog_tools.search_trips(trip_type="City Breaks")
    package = found["packages"][0]
    assert package["package_id"] == "CTY-002"
    assert package["price_per_person"] == 1949.0 and isinstance(package["price_per_person"], float)
    assert package["availability"] == {"5 nights": 3}


async def test_phase_1_runs_the_tool_and_traces_placeholders_and_values(db):
    _products, activities = await chat_router.sql_search(
        "Show me city trips under $2,000 per traveler.")

    span = next(a for a in activities if a.title == "Trip type filter: City Breaks")
    assert span.sql_query.endswith("-- Values: $1 = 'City Breaks', $2 = 2000.0, $3 = 5")
    assert "'City Breaks'" not in span.sql_query.split("\n-- Values:")[0]


async def test_phase_1_text_fallback_is_traced_with_its_patterns(db):
    _products, activities = await chat_router.sql_search("Tokyo culture trip")

    span = next(a for a in activities if a.title == "Filter search: Tokyo culture trip")
    assert "$6" in span.sql_query
    assert "'%Tokyo culture trip%'" in span.sql_query


async def test_like_wildcards_in_free_text_are_escaped_and_declared(db):
    found = await catalog_tools.search_trips(text=r"100%_a\b", limit=3)

    sql, params = db.calls[0]
    assert sql.count("ILIKE %s ESCAPE '\\'") == 5
    assert params == (r"%100\%\_a\\b%",) * 5 + (3,)
    assert found["parameters"][:5] == [r"%100\%\_a\\b%"] * 5
