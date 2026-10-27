"""Phase 2 sends typed arguments to meridian-concierge; postgres-mcp is off the live path."""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, List, get_type_hints

import pytest

import backend.routers.chat as chat_router
from backend.mcp import concierge_server
from backend.tools import catalog_tools

ROW = {
    "package_id": "CTY-002", "name": "Tokyo Culture & Cuisine", "trip_type": "City Breaks",
    "destination": "Tokyo", "region": "Asia", "price_per_person": 1949.0,
    "operator": "JAL Premium", "description": "Marunouchi stay", "image_url": "",
    "durations": ["5 nights"], "availability": {"5 nights": 3}, "highlights": [],
}


class RecordingDb:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, params=None, transaction_id=None):
        self.calls.append((sql, tuple(params or ())))
        return [ROW]


class FakeConcierge:
    """The MCP client: ``search_trips`` runs the real tool over a recording database."""

    tools = [{"name": "search_trips"}, {"name": "compare_packages"}]

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call(self, tool: str, arguments: dict) -> Any:
        self.calls.append((tool, arguments))
        return await catalog_tools.search_trips(**arguments)


@pytest.fixture
def wired(monkeypatch):
    db, client = RecordingDb(), FakeConcierge()
    monkeypatch.setattr(catalog_tools, "get_rds_data_client", lambda: db)

    @asynccontextmanager
    async def session():
        yield client

    monkeypatch.setattr(chat_router, "concierge_mcp_session", session)
    return db, client


def titles(activities) -> list[str]:
    return [a.title for a in activities]


async def test_a_filter_prompt_sends_typed_values_through_the_custom_server(wired):
    db, client = wired
    products, activities, _text = await chat_router.mcp_search(
        "Show me city trips under $2,000 per traveler.", traveler_id="trv_meridian_demo")

    assert client.calls == [("search_trips", {
        "limit": 5, "trip_type": "City Breaks", "max_price_per_person": 2000.0})]
    assert [p.product_id for p in products] == ["CTY-002"]
    assert "meridian-concierge: search_trips" in titles(activities)
    assert not any("postgres-mcp" in title for title in titles(activities))


async def test_the_span_shows_placeholders_and_the_bound_values(wired):
    _db, _client = wired
    _products, activities, _text = await chat_router.mcp_search(
        "Show me city trips under $2,000 per traveler.", traveler_id="trv_meridian_demo")

    span = next(a for a in activities if a.title == "meridian-concierge: search_trips")
    assert span.sql_query.endswith("-- Values: $1 = 'City Breaks', $2 = 2000.0, $3 = 5")
    assert "trip_type = $1" in span.sql_query
    assert span.details.endswith("result: found 1 trips")


async def test_text_with_a_quote_reaches_the_server_as_an_argument_only(wired):
    db, client = wired
    _products, activities, _text = await chat_router.mcp_search(
        "Dad's villa retreat", traveler_id="trv_meridian_demo")

    assert client.calls[0][1]["text"] == "Dad's villa retreat"
    executed_sql, params = db.calls[0]
    assert "villa" not in executed_sql and "%Dad's villa retreat%" in params
    span = next(a for a in activities if a.title == "meridian-concierge: search_trips")
    assert "villa" not in span.sql_query.split("\n-- Values:")[0]


async def test_a_pure_domain_prompt_does_not_search(wired, monkeypatch):
    _db, client = wired

    async def domain(query, *, traveler_id):
        return {"tool": "currency_convert", "args": {"amount": 1}, "result": {"converted": 1}}

    monkeypatch.setattr(chat_router, "_call_domain_tool", domain)
    await chat_router.mcp_search(
        "Convert 2500 US dollars to euros", traveler_id="trv_meridian_demo")

    assert client.calls == []


async def test_search_plus_a_domain_tool_is_one_server_with_one_discovery_span(wired, monkeypatch):
    async def domain(query, *, traveler_id):
        return {"tool": "currency_convert",
                "args": {"amount": 2500, "from_ccy": "USD", "to_ccy": "EUR"},
                "result": {"amount": 2500.0, "from": "USD", "to": "EUR", "converted": 2300.0}}

    monkeypatch.setattr(chat_router, "_call_domain_tool", domain)
    _products, activities, _text = await chat_router.mcp_search(
        "Show me city trips under $2,000 in euros", traveler_id="trv_meridian_demo")

    discovery = [t for t in titles(activities) if t.startswith("MCP server discovered")]
    assert discovery == ["MCP server discovered: meridian-concierge (custom)"]
    assert "MCP turn complete: 1 server" in titles(activities)


async def test_a_malformed_tool_answer_is_an_error_not_an_empty_search(monkeypatch):
    class Broken(FakeConcierge):
        async def call(self, tool, arguments):
            return ["not", "a", "dict"]

    @asynccontextmanager
    async def session():
        yield Broken()

    monkeypatch.setattr(chat_router, "concierge_mcp_session", session)
    with pytest.raises(RuntimeError, match="unexpected shape"):
        await chat_router.mcp_search("Show me city trips under $2,000",
                                     traveler_id="trv_meridian_demo")


def test_search_trips_is_a_registered_mcp_tool():
    names = {tool.name for tool in asyncio.run(concierge_server.mcp.list_tools())}
    assert "search_trips" in names


async def test_the_registered_tool_delegates_to_the_shared_function(monkeypatch):
    db = RecordingDb()
    monkeypatch.setattr(catalog_tools, "get_rds_data_client", lambda: db)

    found = await concierge_server.search_trips(trip_type="City Breaks", limit=2)

    assert db.calls[0][1] == ("City Breaks", 2)
    assert found["parameters"] == ["City Breaks", 2]


async def test_registered_tool_serializes_through_fastmcp(monkeypatch):
    """FastMCP validates the arguments and encodes Decimal prices, dicts and lists."""
    import json
    from decimal import Decimal

    db = RecordingDb()
    monkeypatch.setattr(catalog_tools, "get_rds_data_client", lambda: db)
    row = {**ROW, "price_per_person": Decimal("1949.00")}

    async def execute(sql, params=None, transaction_id=None):
        db.calls.append((sql, tuple(params or ())))
        return [row]

    db.execute = execute

    content = await concierge_server.mcp.call_tool(
        "search_trips",
        {"trip_type": "City Breaks", "max_price_per_person": 2500, "limit": 3},
    )
    blocks = content[0] if isinstance(content, tuple) else content
    decoded = json.loads(blocks[0].text)

    assert decoded["packages"][0]["package_id"] == "CTY-002"
    assert decoded["packages"][0]["price_per_person"] == 1949.0
    assert decoded["packages"][0]["durations"] == ["5 nights"]
    assert decoded["parameters"] == ["City Breaks", 2500.0, 3]
    assert "$1" in decoded["sql"]
    assert db.calls[0][1] == ("City Breaks", 2500.0, 3)


# The JSON Schema each Python type hint becomes, as the deck's slide on typed MCP tools states.
SCHEMA_OF_HINT = {
    float: {"type": "number"},
    str: {"type": "string"},
    List[str]: {"type": "array", "items": {"type": "string"}},
}


@pytest.mark.parametrize("tool", ["currency_convert", "compare_packages"])
async def test_list_tools_derives_each_input_schema_from_the_type_hints(tool):
    listed = {entry.name: entry for entry in await concierge_server.mcp.list_tools()}
    hints = get_type_hints(getattr(concierge_server, tool))
    hints.pop("return")
    schema = listed[tool].inputSchema

    assert schema["type"] == "object"
    assert schema["required"] == list(hints)
    derived = {name: {key: value for key, value in prop.items() if key != "title"}
               for name, prop in schema["properties"].items()}
    assert derived == {name: SCHEMA_OF_HINT[hint] for name, hint in hints.items()}


async def test_call_tool_returns_a_structured_result_for_each(monkeypatch):
    from decimal import Decimal

    class CatalogDb:
        async def execute(self, sql, params=None, transaction_id=None):
            return [{**ROW, "price_per_person": Decimal("1949.00"),
                     "highlights": ["Tsukiji", "Ginza", "Senso-ji", "Shibuya"]}]

    monkeypatch.setattr(concierge_server, "_db", lambda: CatalogDb())

    converted = await concierge_server.mcp.call_tool(
        "currency_convert", {"amount": 100, "from_ccy": "USD", "to_ccy": "EUR"})
    compared = await concierge_server.mcp.call_tool(
        "compare_packages", {"package_ids": ["CTY-002"]})

    # The text blocks carry the same values: one block for a dict, one per row for a list.
    assert json.loads(converted[0][0].text) == converted[1]["result"]
    assert [json.loads(block.text) for block in compared[0]] == compared[1]["result"]
    assert converted[1] == {"result": {
        "from": "USD", "to": "EUR", "amount": 100.0, "converted": 92.0, "rate": 0.92,
        "note": "indicative rate, not for settlement"}}
    assert compared[1] == {"result": [{
        "package_id": "CTY-002", "name": "Tokyo Culture & Cuisine", "destination": "Tokyo",
        "region": "Asia", "operator": "JAL Premium", "trip_type": "City Breaks",
        "price_per_person": 1949.0, "durations": ["5 nights"],
        "top_highlights": ["Tsukiji", "Ginza", "Senso-ji"]}]}
