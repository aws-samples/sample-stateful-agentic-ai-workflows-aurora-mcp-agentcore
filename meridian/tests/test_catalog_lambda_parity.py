"""The Lambda's port of the catalog tools and hybrid search stays identical to the modules."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.db.embedding_service import EmbeddingService
from backend.db.rds_data_client import RDSDataClient
from backend.retrieval import hybrid
from backend.search_utils import parse_price_ceiling
from backend.tools import catalog_tools
from tests.test_catalog_lambda import (
    HYBRID_ROWS,
    TARGET,
    FakeBedrock,
    FakeDataApi,
    call,
    catalog,
)


@pytest.mark.parametrize("name", ["LEXICAL_SQL", "HYDRATE_SQL", "SEMANTIC_SQL"])
def test_each_statement_is_the_modules_to_the_character(name):
    assert getattr(catalog, name) == getattr(hybrid, name), (
        f"copy hybrid.{name} into the Lambda verbatim: " + repr(getattr(hybrid, name)))


@pytest.mark.parametrize("limit", range(1, 31))
def test_the_candidate_pool_is_the_modules(limit):
    assert catalog.candidate_limit(limit) == hybrid.candidate_limit(limit)


MERGE_CASES = [
    ([], []),
    ([{"package_id": "A", "similarity": 0.9}], []),
    ([], [{"package_id": "B", "lexical_score": 0.3}]),
    ([{"package_id": "A", "similarity": 0.9}, {"package_id": "B", "similarity": 0.8}],
     [{"package_id": "B", "lexical_score": 0.4}, {"package_id": "C", "lexical_score": 0.2}]),
    ([{"package_id": "A", "similarity": 0.9}], [{"package_id": "A"}]),
]


@pytest.mark.parametrize(("semantic", "lexical"), MERGE_CASES)
def test_merge_by_package_is_the_modules(semantic, lexical):
    assert catalog.merge_by_package(semantic, lexical) == hybrid.merge_by_package(
        semantic, lexical)


GATE_ROWS = [
    {"package_id": "A", "price_per_person": 1000.0},
    {"package_id": "B", "price_per_person": 3000.0},
    {"package_id": "C", "price_per_person": 1500.0},
    {"package_id": "D", "price_per_person": 1200.0},
    {"package_id": "E", "price_per_person": None},
]


@pytest.mark.parametrize("ceiling", [None, 1200.0, 1199.99, 5000.0])
@pytest.mark.parametrize("places", [
    {"A": 1, "B": 1, "C": 0, "D": 5, "E": 1},
    {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0},
    {"A": 2, "B": 2, "C": 2, "D": 2, "E": 2},
    {"A": 1},
])
def test_the_price_and_stock_gate_is_the_modules(ceiling, places):
    assert catalog.apply_gate(GATE_ROWS, ceiling, places) == hybrid.apply_gate(
        GATE_ROWS, ceiling, places)


@pytest.mark.parametrize("availability", [
    {"7 nights": 3, "4 nights": 0}, {"quantity": 2}, {"quantity": "x"}, '{"a": 2}', "not json",
    None, [], {"a": "x", "b": 1}, {},
])
def test_total_places_is_the_modules(availability):
    assert catalog.total_places(availability) == hybrid.total_places(availability)
    rows = [{"package_id": "A", "availability": availability}]
    assert catalog.places_by_package(rows) == hybrid.places_by_package(rows)


@pytest.mark.parametrize("candidate", [
    {"name": "N", "destination": "D", "trip_type": "T", "operator": "O", "description": "X"},
    {"name": "N"},
    {"name": None, "destination": None, "trip_type": None, "operator": None, "description": None},
])
def test_the_rerank_document_is_the_modules(candidate):
    assert catalog.rerank_document(candidate) == hybrid.rerank_document(candidate)


RANKED_ROWS = [{"package_id": "A", "pre_rerank_position": 0, "similarity": 0.9},
               {"package_id": "B", "pre_rerank_position": 1, "similarity": 0.8}]


@pytest.mark.parametrize("ranked", [
    [], [{"index": -1}], [{"index": 0}, {"index": 0}], [{"index": 99}],
    [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.2}], [{"index": 1}],
])
def test_the_rerank_reading_and_the_fallback_are_the_modules(ranked):
    assert catalog.apply_rerank(RANKED_ROWS, ranked) == hybrid._apply_rerank(RANKED_ROWS, ranked)
    for limit in (1, 5):
        assert catalog.candidate_order(RANKED_ROWS, limit) == hybrid._candidate_order(
            RANKED_ROWS, limit)


def test_the_payload_is_the_modules():
    found = {**json.loads(json.dumps(HYBRID_ROWS["ts_rank"][1])), "durations": ["7 nights"],
             "availability": {"quantity": 1}, "pre_rerank_position": 1, "rank_delta": 1}
    assert catalog.package_payload(found) == hybrid.package_payload(found)


@pytest.mark.parametrize("query", [
    "Tokyo trips under $2,000", "Tokyo trips under $3.5k", "Tokyo trips under 4k",
    "luxury below $2999.99", "city break less than 1200", "adventure < $800",
    "Tokyo trips under 3 nights", "a trip for under 5 kids", "under $", "Tokyo culture trip",
    "less than 3 nights, under $2,000", "Tokyo trips under $12,500",
])
def test_the_price_ceiling_parser_is_the_modules(query):
    assert catalog.parse_price_ceiling(query) == parse_price_ceiling(query)


def test_the_fx_table_and_the_unknown_currency_code_are_the_modules():
    assert catalog.FX_PER_USD == catalog_tools.FX_PER_USD
    assert catalog.UNSUPPORTED_CURRENCY == catalog_tools.UNSUPPORTED_CURRENCY


def test_the_text_columns_and_the_like_escape_are_the_modules():
    assert catalog.TEXT_COLUMNS == catalog_tools.TEXT_COLUMNS
    for text in ("100% Tokyo", "a_b\\c", "plain"):
        assert catalog._escape_like(text) == catalog_tools._escape_like(text)


PACKAGE_COLUMNS_NULL = {"operator": None, "description": None, "image_url": None,
                        "region": None, "destination": None, "highlights": None}

WIRE_PACKAGES = [
    {"package_id": "A", "name": "Alpha", "operator": "Op", "price_per_person": "1899.00",
     "description": "d", "image_url": "u", "trip_type": "City Breaks", "destination": "Tokyo",
     "region": "Asia", "durations": '["7 nights"]', "availability": '{"7 nights": 3}',
     "highlights": '["h1", "h2", "h3", "h4"]'},
    {"package_id": "B", "name": "Beta", "price_per_person": "2400.50", "trip_type": "City Breaks",
     "durations": None, "availability": '{"quantity": 2}', **PACKAGE_COLUMNS_NULL},
]


def _both(monkeypatch, rows_by_marker, **bedrock):
    """The Lambda and the backend client over the same wire rows and the same Bedrock answers.

    Each side gets its own fake ``rds-data`` client and its own fake Bedrock, so the requests
    each one made can be compared as well as its answer.
    """
    lambda_api, module_api = FakeDataApi(rows_by_marker), FakeDataApi(rows_by_marker)
    lambda_bedrock, module_bedrock = FakeBedrock(**bedrock), FakeBedrock(**bedrock)
    monkeypatch.setattr(catalog, "CONFIG", ("c", "s", "d"))
    monkeypatch.setattr(catalog, "RDS", lambda_api)
    monkeypatch.setattr(catalog, "BEDROCK", lambda_bedrock)
    client = RDSDataClient(cluster_arn="c", secret_arn="s", database="d", region="us-east-1")
    client.client = module_api
    monkeypatch.setattr(catalog_tools, "get_rds_data_client", lambda: client)
    monkeypatch.setattr(hybrid, "get_rds_data_client", lambda: client)
    service = EmbeddingService()
    service._bedrock_client = module_bedrock
    monkeypatch.setattr(hybrid, "get_embedding_service", lambda: service)
    return SimpleNamespace(lambda_api=lambda_api, module_api=module_api,
                           lambda_bedrock=lambda_bedrock, module_bedrock=module_bedrock)


def _asked(api):
    """Each statement (whitespace folded) and its bound values, as the Data API received them."""
    return [(" ".join(r["sql"].split()), r.get("parameters", [])) for r in api.requests]


def test_compare_packages_gives_the_same_rows_through_both_paths(monkeypatch):
    world = _both(monkeypatch, {"FROM trip_packages": WIRE_PACKAGES})

    via_module = asyncio.run(catalog_tools.compare_packages(["A", "B", "C", "D", "E"]))
    via_lambda = call("compare_packages", {"package_ids": ["A", "B", "C", "D", "E"]})["result"]

    assert via_lambda == via_module
    assert via_lambda[0]["price_per_person"] == 1899.0 and via_lambda[1]["durations"] == []
    assert _asked(world.lambda_api) == _asked(world.module_api)


def test_an_unknown_currency_gets_the_same_answer_through_both_paths(monkeypatch):
    _both(monkeypatch, {})
    via_module = asyncio.run(catalog_tools.currency_convert(1.0, "XXX", "USD"))
    assert call("currency_convert", {"amount": 1.0, "from_ccy": "XXX", "to_ccy": "USD"}) == (
        via_module)


@pytest.mark.parametrize(("amount", "source", "target"), [
    (100, "eur", "JPY"), (0, "USD", "USD"), (12.345, "GBP", "inr"), (1, "CHF", "MXN"),
])
def test_a_supported_conversion_gives_the_same_answer_through_both_paths(
        monkeypatch, amount, source, target):
    _both(monkeypatch, {})
    via_module = asyncio.run(catalog_tools.currency_convert(amount, source, target))
    assert call("currency_convert", {"amount": amount, "from_ccy": source,
                                     "to_ccy": target}) == via_module


@pytest.mark.parametrize("arguments", [
    {"trip_type": None, "max_price_per_person": None, "limit": 5},
    {"trip_type": "City Breaks", "max_price_per_person": None, "limit": 5},
    {"trip_type": None, "max_price_per_person": 2000.0, "limit": 5},
    {"trip_type": "Beach & Resort", "max_price_per_person": 1500.0, "limit": 3},
    {"trip_type": None, "max_price_per_person": None, "limit": 500},
    {"text": "100% Tokyo"},
    {"text": "a_b\\c"},
    {"trip_type": "City Breaks", "text": "Kyoto"},
])
@pytest.mark.parametrize("wire", [WIRE_PACKAGES, []], ids=["rows", "no rows"])
def test_search_trips_gives_the_same_whole_result_through_both_paths(
        monkeypatch, arguments, wire):
    """The statement, the bound values and the packages, from real Data API rows.

    The rows carry DECIMAL as a string and NULL as null, as the JSON format returns them; the
    backend turns them into a float price and empty strings, and so must the Lambda.
    """
    world = _both(monkeypatch, {"FROM trip_packages": wire})

    via_module = asyncio.run(catalog_tools.search_trips(**arguments))
    via_lambda = call("search_trips", {k: v for k, v in arguments.items() if v is not None})

    assert via_lambda == via_module
    assert _asked(world.lambda_api) == _asked(world.module_api)


def test_search_trips_turns_a_decimal_string_into_a_float_and_a_null_into_nothing(monkeypatch):
    _both(monkeypatch, {"FROM trip_packages": WIRE_PACKAGES})
    second = call("search_trips", {})["packages"][1]
    assert second["price_per_person"] == 2400.5
    assert (second["operator"], second["description"], second["image_url"]) == ("", "", "")
    assert (second["region"], second["destination"]) == ("", "")


@pytest.mark.parametrize("wire", [
    [{"min_price": 1199.0, "avg_price": 1949.555, "max_price": 3299.0, "sample_size": 4}],
    [{"min_price": None, "avg_price": None, "max_price": None, "sample_size": 0}],
    [],
], ids=["rows", "none matched", "no row"])
@pytest.mark.parametrize("destination", ["Tokyo", "100% Tokyo", "a_b"])
def test_price_range_gives_the_same_whole_result_through_both_paths(
        monkeypatch, wire, destination):
    world = _both(monkeypatch, {"MIN(price_per_person)": wire})

    via_module = asyncio.run(catalog_tools.price_range(destination))
    via_lambda = call("price_range", {"destination": destination})

    assert via_lambda == via_module
    assert _asked(world.lambda_api) == _asked(world.module_api)


REGION_ROWS = [
    {"package_id": "A", "name": "a", "trip_type": "City Breaks",
     "availability": '{"a": 3, "b": 1}'},
    {"package_id": "B", "name": "b", "trip_type": None, "availability": None},
    {"package_id": "C", "name": "c", "trip_type": "City Breaks",
     "availability": '{"quantity": 2}'},
]


@pytest.mark.parametrize("wire", [REGION_ROWS, []], ids=["rows", "none"])
@pytest.mark.parametrize("trip_type", [None, "City Breaks"])
def test_region_inventory_gives_the_same_whole_result_through_both_paths(
        monkeypatch, wire, trip_type):
    world = _both(monkeypatch, {"WHERE region ILIKE": wire})

    via_module = asyncio.run(catalog_tools.region_inventory("Asia", trip_type))
    arguments = {"region": "Asia", **({"trip_type": trip_type} if trip_type else {})}
    via_lambda = call("region_inventory", arguments)

    assert via_lambda == via_module
    assert _asked(world.lambda_api) == _asked(world.module_api)


def test_a_bad_availability_value_fails_the_module_and_the_tool_alike(monkeypatch):
    bad = [{"package_id": "A", "name": "a", "trip_type": "City", "availability": '{"a": "many"}'}]
    _both(monkeypatch, {"WHERE region ILIKE": bad})
    with pytest.raises(ValueError, match="invalid literal"):
        asyncio.run(catalog_tools.region_inventory("Asia"))
    assert call("region_inventory", {"region": "Asia"}) == {
        "error": "catalog_tool_failed", "tool": "region_inventory"}


NULL_HYDRATE = [
    {**HYBRID_ROWS["WHERE package_id IN"][0], "operator": None, "description": None,
     "highlights": None},
    *HYBRID_ROWS["WHERE package_id IN"][1:],
]
HYBRID_ROW_SETS = {"complete rows": HYBRID_ROWS,
                   "NULL operator, description and highlights": {
                       **HYBRID_ROWS, "WHERE package_id IN": NULL_HYDRATE}}


@pytest.mark.parametrize("rows", HYBRID_ROW_SETS.values(), ids=HYBRID_ROW_SETS)
def test_hybrid_search_with_null_columns_gives_the_same_answer_through_both_paths(
        monkeypatch, rows):
    _both(monkeypatch, rows, rerank_order=[1, 0])
    via_lambda = call("hybrid_trip_search", {"query": "Japan under $2,500", "limit": 5})
    via_module = asyncio.run(catalog_tools.hybrid_trip_search("Japan under $2,500", limit=5))
    assert via_lambda == via_module
    assert via_lambda["packages"]


BEDROCK_CASES = {
    "rerank applied": {"rerank_order": [1, 0]},
    "rerank failing": {"rerank_error": RuntimeError("rerank offline")},
    "no usable rerank index": {"rerank_order": [99]},
    "duplicate rerank index": {"rerank_order": [1, 1, 0]},
    "embedding failing": {"embed_error": RuntimeError("embed offline"), "rerank_order": [0]},
    "one transient embedding failure": {"embed_flakes": 1, "rerank_order": [1, 0]},
    "two embedding failures": {"embed_flakes": 2, "rerank_order": [0]},
}


@pytest.mark.parametrize("bedrock", BEDROCK_CASES.values(), ids=BEDROCK_CASES)
@pytest.mark.parametrize(("query", "limit"), [
    ("Japan under $2,500", 5), ("Tokyo", 5), ("Tokyo", 1), ("Tokyo under $1", 5)])
def test_hybrid_search_gives_the_same_answer_and_asks_bedrock_the_same_through_both_paths(
        monkeypatch, bedrock, query, limit):
    """The same wire rows and Bedrock answers through the Lambda and the module.

    The module runs the real EmbeddingService over its own Bedrock fake, so the request bodies
    and the reading of Cohere's replies are exercised on both sides; the two lists of Bedrock
    requests (model id, query, documents, top_n, truncation, retries) must be equal.
    """
    world = _both(monkeypatch, HYBRID_ROWS, **bedrock)

    via_lambda = call("hybrid_trip_search", {"query": query, "limit": limit})
    via_module = asyncio.run(catalog_tools.hybrid_trip_search(query, limit=limit))

    assert via_lambda == via_module
    assert world.lambda_bedrock.requests == world.module_bedrock.requests
    assert _asked(world.lambda_api) == _asked(world.module_api)


def test_one_transient_embedding_failure_still_searches_both_arms(monkeypatch):
    world = _both(monkeypatch, HYBRID_ROWS, embed_flakes=1, rerank_order=[0])
    result = call("hybrid_trip_search", {"query": "Tokyo", "limit": 5})
    assert result["candidates"]["semantic"] == 3
    embeds = [m for m, body in world.lambda_bedrock.requests if "embed" in m]
    assert len(embeds) == 2


def test_the_published_tool_names_are_the_lambdas_and_the_contracts():
    published = [t["name"] for t in json.loads((TARGET / "tool-schema.json").read_text())]
    assert published == list(catalog.TOOLS) == list(catalog_tools.CATALOG_FUNCTIONS)


def test_the_query_cap_is_the_modules():
    assert catalog.MAX_QUERY_CHARS == catalog_tools.MAX_QUERY_CHARS == 500


@pytest.mark.parametrize(("tool", "argument", "message"), [
    ("price_range", "destination", "destination is required"),
    ("region_inventory", "region", "region is required"),
])
@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_a_blank_place_is_refused_the_same_way_through_both_paths(
        monkeypatch, tool, argument, message, value):
    world = _both(monkeypatch, {"MIN(price_per_person)": [], "WHERE region ILIKE": []})

    via_module = asyncio.run(getattr(catalog_tools, tool)(value))

    assert via_module == {"error": message}
    assert call(tool, {argument: value}) == via_module
    assert world.lambda_api.requests == [] and world.module_api.requests == []


@pytest.mark.parametrize("wire", [
    [{"min_price": 1199.0, "avg_price": 1949.555, "max_price": 3299.0, "sample_size": 4}], [],
], ids=["rows", "no row"])
def test_a_padded_destination_is_stripped_the_same_way_through_both_paths(monkeypatch, wire):
    world = _both(monkeypatch, {"MIN(price_per_person)": wire})

    via_module = asyncio.run(catalog_tools.price_range("  Tokyo \n"))

    assert via_module.get("destination") == "Tokyo"
    assert call("price_range", {"destination": "  Tokyo \n"}) == via_module
    assert _asked(world.lambda_api) == _asked(world.module_api)
    assert world.module_api.parameters()["p0"] == {"stringValue": "%Tokyo%"}


@pytest.mark.parametrize("wire", [REGION_ROWS, []], ids=["rows", "none"])
def test_a_padded_region_is_stripped_the_same_way_through_both_paths(monkeypatch, wire):
    world = _both(monkeypatch, {"WHERE region ILIKE": wire})

    via_module = asyncio.run(catalog_tools.region_inventory(" Asia ", "City Breaks"))

    assert via_module["region"] == "Asia"
    assert call("region_inventory", {"region": " Asia ", "trip_type": "City Breaks"}) == via_module
    assert _asked(world.lambda_api) == _asked(world.module_api)


@pytest.mark.parametrize(("query", "message"), [
    ("", "query is required"),
    ("   ", "query is required"),
    ("x" * 501, "query is longer than 500 characters"),
    (" " + "x" * 500, "query is longer than 500 characters"),
])
def test_a_blank_or_over_long_query_is_refused_the_same_way_through_both_paths(
        monkeypatch, query, message):
    world = _both(monkeypatch, HYBRID_ROWS, rerank_order=[0])

    via_module = asyncio.run(catalog_tools.hybrid_trip_search(query))
    spans = asyncio.run(catalog_tools.hybrid_trip_search_with_spans(query))[1]

    assert via_module == {"error": message} and spans == []
    assert call("hybrid_trip_search", {"query": query}) == via_module
    for side in (world.lambda_api, world.module_api, world.lambda_bedrock,
                 world.module_bedrock):
        assert side.requests == []


def test_a_query_of_exactly_the_cap_and_a_padded_query_are_searched_the_same_way(monkeypatch):
    for query in ("x" * 500, "  Japan under $2,500  "):
        world = _both(monkeypatch, HYBRID_ROWS, rerank_order=[1, 0])

        via_module = asyncio.run(catalog_tools.hybrid_trip_search(query))

        assert "error" not in via_module
        assert call("hybrid_trip_search", {"query": query}) == via_module
        assert _asked(world.lambda_api) == _asked(world.module_api)
        assert world.lambda_bedrock.requests == world.module_bedrock.requests


def test_the_modules_bedrock_client_fails_fast_exactly_as_the_lambdas_does():
    """Either side drifting from connect 2 s, read 5 s and one attempt in all fails here."""
    service = EmbeddingService(region="us-east-1")
    module = service.bedrock_client.meta.config
    lambda_config = catalog.BEDROCK_CONFIG

    assert (module.connect_timeout, module.read_timeout) == (
        lambda_config.connect_timeout, lambda_config.read_timeout) == (2, 5)
    assert module.retries["total_max_attempts"] == lambda_config.retries["total_max_attempts"] == 1
    assert catalog.EMBEDDING_ATTEMPTS == EmbeddingService.EMBEDDING_ATTEMPTS
