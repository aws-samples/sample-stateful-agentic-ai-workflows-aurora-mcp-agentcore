"""Phase 3, the Gateway and the contract offer one set of tools with one set of arguments."""

import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

from backend.agents import concierge_definition as definition
from backend.agents.phase_03_retrieval import mcp_session
from backend.agents.phase_03_retrieval.tools import LOCAL_TOOLS, local_tools
from backend.mcp import concierge_server
from backend.tools import catalog_tools
from scripts import stage_concierge_runtime
from tests.gateway_interceptor_support import load_interceptor
from tests.test_catalog_lambda import HYBRID_ROWS, call
from tests.test_catalog_lambda_parity import WIRE_PACKAGES, _both

ROOT = Path(__file__).resolve().parents[1]
AGENTCORE = ROOT / "meridian_agentcore" / "agentcore"
TEMPLATE = AGENTCORE / "agentcore.template.json"
sys.path.insert(0, str(ROOT / "meridian_agentcore" / "app" / "MeridianConcierge"))

import turn_trace  # noqa: E402

# Release 3 adds release_hold to the Gateway. Until then the contract lists it and no surface
# offers it. Release 3 deletes this set, and every test below then holds for every tool.
NOT_DEPLOYED_UNTIL_RELEASE_3 = {"release_hold"}


def gateway_schemas() -> dict[str, dict]:
    """``Target___tool`` to its tool definition, for every tool of every target in the template."""
    spec = json.loads(TEMPLATE.read_text())
    found = {}
    for target in spec["agentCoreGateways"][0]["targets"]:
        definitions = target.get("toolDefinitions")
        if definitions is None:
            folder = re.sub(r"(?<!^)(?=[A-Z])", "_", target["name"]).lower()
            definitions = json.loads(
                (AGENTCORE / "gateway_targets" / folder / "tool-schema.json").read_text())
        for tool in definitions:
            found[f"{target['name']}___{tool['name']}"] = tool
    return found


def gateway_names() -> set[str]:
    return {name.split("___")[-1] for name in gateway_schemas()}


def phase3_arguments() -> dict[str, set[str]]:
    served = {tool.name: set(tool.inputSchema["properties"])
              for tool in asyncio.run(concierge_server.mcp.list_tools())}
    local = {tool.tool_name: set(tool.tool_spec["inputSchema"]["json"]["properties"])
             for tool in local_tools([])}
    return {**served, **local}


def test_release_hold_is_the_only_tool_the_gateway_does_not_offer_yet():
    assert set(definition.PHASE4_TOOLS) - gateway_names() == NOT_DEPLOYED_UNTIL_RELEASE_3


def test_phase_three_offers_exactly_the_contracts_phase_three_tools():
    assert set(phase3_arguments()) == set(definition.PHASE3_TOOLS)
    assert set(mcp_session.MCP_TOOLS) | set(LOCAL_TOOLS) == set(definition.PHASE3_TOOLS)


def test_the_gateway_offers_the_contracts_phase_four_tools():
    assert gateway_names() == set(definition.PHASE4_TOOLS) - NOT_DEPLOYED_UNTIL_RELEASE_3


def test_phase_three_is_the_gateway_without_the_write_tools():
    assert set(phase3_arguments()) == gateway_names() - set(definition.WRITE_TOOLS)


def test_each_tool_lives_on_the_target_the_spec_names():
    schemas = gateway_schemas()
    for name in definition.CATALOG_TOOLS:
        assert f"MeridianCatalog___{name}" in schemas, name
    for name in definition.DETAIL_TOOLS + ("loyalty_balance", "create_courtesy_hold",
                                           "confirm_booking"):
        assert f"MeridianHolds___{name}" in schemas, name


def test_phase_three_offers_the_argument_names_the_contract_lists():
    for name, arguments in phase3_arguments().items():
        assert arguments == set(definition.TOOL_ARGUMENTS[name]), name


def test_the_gateway_offers_the_argument_names_the_contract_lists():
    for name, schema in gateway_schemas().items():
        short = name.split("___")[-1]
        offered = set(schema["inputSchema"]["properties"])
        assert offered == set(definition.TOOL_ARGUMENTS[short]), name


def test_every_deployed_traveler_tool_requires_the_pinned_argument_because_cedar_names_it():
    for name, schema in gateway_schemas().items():
        if name.split("___")[-1] in definition.TRAVELER_TOOLS:
            assert definition.TRAVELER_ARGUMENT in schema["inputSchema"]["required"], name


def test_the_interceptor_pins_exactly_the_deployed_traveler_tools():
    pinned = set(load_interceptor().DEFAULT_PINNED_TOOLS)
    wanted = {name for name in gateway_schemas()
              if name.split("___")[-1] in definition.TRAVELER_TOOLS}
    assert pinned == wanted


def test_cedar_permits_the_reads_and_binds_the_traveler_tools():
    statements = {p["name"]: p["statement"] for p in json.loads(
        TEMPLATE.read_text())["policyEngines"][0]["policies"]}

    def named(statement):
        return {part.split('"')[1] for part in statement.split("AgentCore::Action::")[1:]}

    reads = set(gateway_schemas()) - {
        n for n in gateway_schemas() if n.split("___")[-1] in ("create_courtesy_hold",
                                                                "confirm_booking")}
    assert named(statements["meridian_read_tools"]) == reads
    pinned = set(load_interceptor().DEFAULT_PINNED_TOOLS)
    assert named(statements["meridian_traveler_binding"]) == pinned


def test_the_runtime_traces_and_polices_exactly_the_deployed_tools():
    deployed = set(definition.PHASE4_TOOLS) - NOT_DEPLOYED_UNTIL_RELEASE_3
    assert set(turn_trace.SPANS) == set(turn_trace.POLICY_FOR) == deployed


def test_the_runtimes_copy_of_the_definition_is_the_source():
    assert stage_concierge_runtime.check() == []


@pytest.mark.parametrize("phase", [3, 4])
def test_each_prompt_names_every_tool_its_phase_offers(phase):
    text = definition.system_prompt(phase)
    offered = definition.PHASE3_TOOLS if phase == 3 else (
        set(definition.PHASE4_TOOLS) - NOT_DEPLOYED_UNTIL_RELEASE_3)
    for name in offered:
        assert name in text, (phase, name)


PRICE_FIELDS = {"price_per_person", "low", "average", "high", "converted", "amount"}


def price_fields(value, path=""):
    """``path -> value`` for every price-bearing field in a tool answer, at any depth."""
    found = {}
    if isinstance(value, dict):
        for key, item in value.items():
            if key in PRICE_FIELDS:
                found[f"{path}.{key}"] = item
            else:
                found.update(price_fields(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.update(price_fields(item, f"{path}[{index}]"))
    return found


PRICE_CASES = {
    "search_trips": ({"FROM trip_packages": [
        {**WIRE_PACKAGES[0], "price_per_person": "1900.00"}, WIRE_PACKAGES[1]]}, {}),
    "compare_packages": ({"FROM trip_packages": [
        {**WIRE_PACKAGES[0], "price_per_person": "1900.00"}, WIRE_PACKAGES[1]]},
        {"package_ids": ["A", "B"]}),
    "price_range": ({"MIN(price_per_person)": [
        {"min_price": 1200.0, "avg_price": 1950.0, "max_price": 3300.0, "sample_size": 4}]},
        {"destination": "Tokyo"}),
    "currency_convert": ({}, {"amount": 100, "from_ccy": "USD", "to_ccy": "USD"}),
    "hybrid_trip_search": (HYBRID_ROWS, {"query": "Japan under $2,500", "limit": 5}),
}


def in_process_answer(tool, arguments):
    """The tool's answer on the surface Phase 3 reaches it by: the MCP server, else the function.

    The server validates arguments against the signature, so an integer amount arrives as a
    float, as it does from a model; ``hybrid_trip_search`` is a local tool, called directly.
    """
    if tool in {t.name for t in asyncio.run(concierge_server.mcp.list_tools())}:
        _, structured = asyncio.run(concierge_server.mcp.call_tool(tool, arguments))
        return structured.get("result", structured)
    return asyncio.run(catalog_tools.CATALOG_FUNCTIONS[tool](**arguments))


@pytest.mark.parametrize("tool", PRICE_CASES)
def test_every_price_is_the_same_json_number_on_the_module_and_the_lambda(monkeypatch, tool):
    rows, arguments = PRICE_CASES[tool]
    _both(monkeypatch, rows, rerank_order=[1, 0])
    via_module = in_process_answer(tool, arguments)
    via_lambda = call(tool, arguments)
    via_lambda = via_lambda["result"] if tool == "compare_packages" else via_lambda

    module_prices, lambda_prices = price_fields(via_module), price_fields(via_lambda)
    assert module_prices, tool
    assert {k: json.dumps(v) for k, v in lambda_prices.items()} == {
        k: json.dumps(v) for k, v in module_prices.items()}, tool
    assert all(isinstance(v, (int, float)) and not isinstance(v, bool)
               for v in lambda_prices.values()), tool
