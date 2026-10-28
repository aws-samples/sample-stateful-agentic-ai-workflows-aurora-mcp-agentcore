"""search_trips binds every value as a Data API parameter and never writes one into the SQL."""

import asyncio
import inspect
from decimal import Decimal

import pytest

import backend.routers.chat as chat_router
from backend.db.rds_data_client import RDSDataClient
from backend.mcp import concierge_server
from backend.retrieval import hybrid
from backend.tools import catalog_tools
from tests import test_retrieval_characterization as characterization
from tests.data_api_support import names_the_data_api_binds

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
    assert "ORDER BY price_per_person ASC, package_id ASC LIMIT %s" in sql
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


async def test_like_wildcards_in_free_text_are_escaped(db):
    found = await catalog_tools.search_trips(text=r"100%_a\b", limit=3)

    sql, params = db.calls[0]
    assert sql.count("ILIKE %s") == 5
    assert params == (r"%100\%\_a\\b%",) * 5 + (3,)
    assert found["parameters"][:5] == [r"%100\%\_a\\b%"] * 5


async def test_every_free_text_parameter_is_bound_by_the_data_api(db):
    await catalog_tools.search_trips(trip_type="Wine", max_price_per_person=900.0, text="cellar")

    sql, params = db.calls[0]
    converted = RDSDataClient(region="us-east-1")._convert_sql_placeholders(sql, len(params))
    assert names_the_data_api_binds(converted) == [f"p{i}" for i in range(len(params))]


class _Db:
    def __init__(self, rows=None, row=None):
        self.rows, self.row, self.reads = rows or [], row, []

    async def execute(self, sql, params=None, transaction_id=None):
        self.reads.append((" ".join(sql.split()), tuple(params or ())))
        return self.rows

    async def execute_one(self, sql, params=None, transaction_id=None):
        self.reads.append((" ".join(sql.split()), tuple(params or ())))
        return self.row


def _patch_db(monkeypatch, db):
    monkeypatch.setattr(catalog_tools, "get_rds_data_client", lambda: db)


def test_the_server_registers_the_shared_functions_not_copies():
    for name in ("compare_packages", "price_range", "region_inventory", "currency_convert"):
        assert getattr(concierge_server, name) is getattr(catalog_tools, name)


def test_the_registry_lists_the_catalog_tools_in_contract_order():
    assert tuple(catalog_tools.CATALOG_FUNCTIONS) == (
        "search_trips", "compare_packages", "price_range", "region_inventory",
        "currency_convert", "hybrid_trip_search",
    )
    for name, function in catalog_tools.CATALOG_FUNCTIONS.items():
        assert function.__name__ == name


def test_compare_packages_reads_at_most_four_and_shapes_each_row(monkeypatch):
    row = {"package_id": "CTY-002", "name": "Tokyo", "destination": "Tokyo", "region": "Asia",
           "operator": "Nippon", "trip_type": "City Breaks", "price_per_person": 2500,
           "durations": ["7 nights"], "highlights": ["a", "b", "c", "d"]}
    db = _Db(rows=[row])
    _patch_db(monkeypatch, db)

    result = asyncio.run(catalog_tools.compare_packages(["A", "B", "C", "D", "E"]))

    (sql, params), = db.reads
    assert sql.count("%s") == 4 and params == ("A", "B", "C", "D")
    assert result == [{
        "package_id": "CTY-002", "name": "Tokyo", "destination": "Tokyo", "region": "Asia",
        "operator": "Nippon", "trip_type": "City Breaks", "price_per_person": 2500.0,
        "durations": ["7 nights"], "top_highlights": ["a", "b", "c"],
    }]


def test_compare_packages_with_no_ids_reads_nothing(monkeypatch):
    db = _Db()
    _patch_db(monkeypatch, db)
    assert asyncio.run(catalog_tools.compare_packages([])) == []
    assert db.reads == []


def test_price_range_returns_the_aggregate_and_says_it_is_not_seasonal(monkeypatch):
    db = _Db(row={"min_price": 1199.0, "avg_price": 1949.5, "max_price": 3299.0, "sample_size": 4})
    _patch_db(monkeypatch, db)

    result = asyncio.run(catalog_tools.price_range("Tokyo"))

    assert (result["low"], result["average"], result["high"]) == (1199.0, 1949.5, 3299.0)
    assert result["sample_size"] == 4 and "not seasonal" in result["note"].lower()
    assert db.reads[0][1] == ("%Tokyo%", "%Tokyo%")


def test_price_range_with_no_rows_invents_no_numbers(monkeypatch):
    _patch_db(monkeypatch, _Db(row={"sample_size": 0}))
    assert asyncio.run(catalog_tools.price_range("Atlantis")) == {
        "destination": "Atlantis", "sample_size": 0}


def test_region_inventory_sums_places_and_filters_by_trip_type(monkeypatch):
    rows = [
        {"package_id": "A", "trip_type": "City Breaks", "availability": {"2 nights": 3, "4": 1}},
        {"package_id": "B", "trip_type": "Beach & Resort", "availability": {"7": 2}},
        {"package_id": "C", "trip_type": None, "availability": None},
    ]
    db = _Db(rows=rows)
    _patch_db(monkeypatch, db)

    result = asyncio.run(catalog_tools.region_inventory("Asia", "City Breaks"))

    assert result["package_count"] == 3 and result["total_departure_slots"] == 6
    assert result["package_count_by_trip_type"] == {
        "City Breaks": 1, "Beach & Resort": 1, "Other": 1}
    sql, params = db.reads[0]
    assert sql.endswith("AND trip_type = %s") and params == ("%Asia%", "City Breaks")


def test_currency_convert_quotes_through_usd():
    result = asyncio.run(catalog_tools.currency_convert(100.0, "eur", "JPY"))
    assert result["from"] == "EUR" and result["to"] == "JPY"
    assert result["converted"] == round(100.0 / 0.92 * 156.4, 2)
    assert result["rate"] == round(156.4 / 0.92, 6)


def test_an_unknown_currency_is_answered_with_its_code_and_the_known_ones():
    result = asyncio.run(catalog_tools.currency_convert(1.0, "XXX", "USD"))
    assert catalog_tools.UNSUPPORTED_CURRENCY == "unsupported_currency"
    assert result == {"error": "unsupported_currency",
                      "supported": sorted(catalog_tools.FX_PER_USD)}


@pytest.fixture
def hybrid_world(monkeypatch):
    db = characterization.RecordingDb()
    monkeypatch.setattr(hybrid, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(hybrid, "get_embedding_service", characterization.FixedEmbeddings)
    return db


def test_hybrid_trip_search_returns_ranked_packages_counts_and_the_rerank_flag(hybrid_world):
    result = asyncio.run(catalog_tools.hybrid_trip_search("Tokyo city stay", limit=5))

    # FixedEmbeddings' reranker puts the second candidate first.
    first, second = characterization.SECOND, characterization.ROW
    assert [p["package_id"] for p in result["packages"]] == [
        first["package_id"], second["package_id"]]
    assert result["candidates"] == {"semantic": 2, "lexical": 1, "merged": 2}
    assert result["reranked"] is True
    top = result["packages"][0]
    assert set(top) == set(hybrid.package_payload(first))
    assert top["price_per_person"] == first["price_per_person"]
    assert top["trip_type"] == first["trip_type"] and top["operator"] == first["operator"]


def test_hybrid_trip_search_says_so_when_the_rerank_is_down(monkeypatch, hybrid_world):
    monkeypatch.setattr(hybrid, "get_embedding_service", characterization.FailingRerank)

    result = asyncio.run(catalog_tools.hybrid_trip_search("Tokyo city stay"))

    assert result["reranked"] is False
    assert [p["package_id"] for p in result["packages"]] == [
        characterization.ROW["package_id"], characterization.SECOND["package_id"]]


def test_hybrid_trip_search_bounds_the_limit(monkeypatch):
    seen = []

    async def fake(query, limit=5):
        seen.append(limit)
        counts = {"semantic": 0, "lexical": 0, "merged": 0, "eligible": 0, "reranked": False}
        return [], [], counts

    monkeypatch.setattr(hybrid, "retrieval_rows", fake)
    for requested in (0, -3, 500):
        asyncio.run(catalog_tools.hybrid_trip_search("x", requested))
    assert seen == [1, 1, catalog_tools.MAX_LIMIT]


def test_the_server_loyalty_tool_is_named_for_the_pinned_argument():
    names = list(inspect.signature(concierge_server.loyalty_balance).parameters)
    assert names == ["travelerId", "program"]


def test_loyalty_from_programs_never_invents_a_balance():
    programs = {"a": {"program": "Marriott Bonvoy", "points_balance": 100, "tier": "Gold",
                      "member_id": "M1"}}
    found = concierge_server.loyalty_from_programs("trv_x", "bonvoy", programs)
    assert found["points_balance"] == 100 and found["tier"] == "Gold"
    missing = concierge_server.loyalty_from_programs("trv_x", "United MileagePlus", programs)
    assert missing["error"] == "loyalty_balance_unavailable"
    assert "points_balance" not in missing
    assert concierge_server.loyalty_from_programs("trv_x", "bonvoy", None)["error"] == (
        "loyalty_balance_unavailable")
