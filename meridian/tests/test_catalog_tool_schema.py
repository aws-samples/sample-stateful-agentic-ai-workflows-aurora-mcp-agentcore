"""The Gateway tool schema is generated from the function signatures and matches FastMCP's."""

import asyncio
import json
from typing import List, Optional

import pytest

from backend.mcp import concierge_server
from backend.tools import catalog_tools
from backend.tools.tool_schema import catalog_schema, render, tool_schema
from scripts import generate_catalog_tool_schema as generator

SCHEMA_FILE = generator.SCHEMA_PATH


def test_the_committed_file_is_exactly_what_the_functions_generate():
    assert SCHEMA_FILE.read_text(encoding="utf-8") == render(catalog_schema())


def test_the_file_lists_the_catalog_tools_in_contract_order():
    names = [tool["name"] for tool in json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))]
    assert names == list(catalog_tools.CATALOG_FUNCTIONS)


def _fastmcp_tools():
    registered = asyncio.run(concierge_server.mcp.list_tools())
    return {tool.name: tool.inputSchema for tool in registered}


def _shape(prop):
    """A property's type, array item type and default, with Optional flattened.

    A served ``default: null`` (an Optional argument) is published as no default.
    """
    members = [m for m in prop.get("anyOf", [prop]) if m.get("type") != "null"]
    member = members[0]
    items = member.get("items", {}).get("type")
    default = prop.get("default")
    return member["type"], items, default


def _shapes(properties):
    return {name: _shape(prop) for name, prop in properties.items()}


# hybrid_trip_search is not registered on the stdio server at this commit
# (backend/mcp/concierge_server.py registers the other five), so FastMCP serves no schema to
# compare; its published schema is asserted directly below.
STDIO_TOOLS = ["search_trips", "compare_packages", "price_range", "region_inventory",
               "currency_convert"]


def test_the_stdio_server_serves_exactly_the_tools_compared_here():
    assert set(_fastmcp_tools()) >= set(STDIO_TOOLS)
    assert "hybrid_trip_search" not in _fastmcp_tools()


@pytest.mark.parametrize("name", STDIO_TOOLS)
def test_each_published_schema_agrees_with_the_schema_the_stdio_server_serves(name):
    published = {t["name"]: t for t in catalog_schema()}[name]["inputSchema"]
    served = _fastmcp_tools()[name]
    assert list(published["properties"]) == list(served["properties"])
    assert _shapes(published["properties"]) == _shapes(served["properties"])
    assert published.get("required", []) == served.get("required", [])
    for prop in published["properties"].values():
        assert ("default" in prop) == (prop.get("default") is not None)


def test_search_trips_limit_bounds_come_from_the_declared_bounds_not_fastmcp():
    published = {t["name"]: t for t in catalog_schema()}["search_trips"]["inputSchema"]
    limit = published["properties"]["limit"]
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 20, 5)
    assert "minimum" not in _fastmcp_tools()["search_trips"]["properties"]["limit"]


def test_hybrid_trip_search_schema_is_asserted_directly():
    schema = {t["name"]: t for t in catalog_schema()}["hybrid_trip_search"]["inputSchema"]
    assert list(schema["properties"]) == ["query", "limit"]
    assert schema["properties"]["query"]["type"] == "string"
    limit = schema["properties"]["limit"]
    assert (limit["type"], limit["minimum"], limit["maximum"], limit["default"]) == (
        "integer", 1, 20, 5)
    assert schema["required"] == ["query"]
    assert schema["additionalProperties"] is False


def test_a_required_argument_is_required_and_a_default_one_is_not():
    schema = {t["name"]: t for t in catalog_schema()}
    assert schema["hybrid_trip_search"]["inputSchema"]["required"] == ["query"]
    assert "required" not in schema["search_trips"]["inputSchema"]
    assert schema["region_inventory"]["inputSchema"]["required"] == ["region"]


def test_bounds_and_defaults_come_from_the_declared_bounds():
    limit = {t["name"]: t for t in catalog_schema()}["hybrid_trip_search"][
        "inputSchema"]["properties"]["limit"]
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 20, 5)


def test_descriptions_come_from_the_docstring_and_args_block():
    schema = {t["name"]: t for t in catalog_schema()}["price_range"]
    assert schema["description"].startswith("Return the price range")
    assert "matched ILIKE" in schema["inputSchema"]["properties"]["destination"]["description"]


def test_no_property_uses_a_keyword_the_gateway_schema_does_not_accept():
    allowed = {"type", "description", "items", "minimum", "maximum", "default"}
    for tool in catalog_schema():
        assert set(tool["inputSchema"]) <= {"type", "properties", "required",
                                            "additionalProperties"}
        for prop in tool["inputSchema"]["properties"].values():
            assert set(prop) <= allowed, (tool["name"], prop)


def test_an_unsupported_annotation_is_refused_with_the_function_and_argument():
    async def odd(values: dict) -> dict:
        """Odd.

        Args:
            values: anything.
        """

    with pytest.raises(ValueError, match="odd.*values"):
        tool_schema(odd)


def test_optional_and_list_annotations_are_flattened():
    async def sample(ids: List[str], kind: Optional[str] = None) -> dict:
        """Sample tool.

        Args:
            ids: package ids.
            kind: optional kind.
        """

    schema = tool_schema(sample)
    assert schema["inputSchema"]["properties"]["ids"] == {
        "type": "array", "items": {"type": "string"}, "description": "package ids."}
    assert schema["inputSchema"]["properties"]["kind"] == {
        "type": "string", "description": "optional kind."}
    assert schema["inputSchema"]["required"] == ["ids"]


def test_check_mode_fails_when_the_file_is_stale(tmp_path, monkeypatch):
    stale = tmp_path / "tool-schema.json"
    stale.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(generator, "SCHEMA_PATH", stale)
    assert generator.main(["--check"]) == 1
    assert generator.main([]) == 0
    assert generator.main(["--check"]) == 0
