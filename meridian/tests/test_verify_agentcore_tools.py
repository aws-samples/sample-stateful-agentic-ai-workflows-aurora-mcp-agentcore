"""verify_agentcore expects the tools the Gateway offers after Release 2, and no others."""

from types import SimpleNamespace

from backend.agents.concierge_definition import CATALOG_TOOLS
from scripts import verify_agentcore


def test_the_expected_tools_are_the_catalog_target_and_the_holds_target():
    assert verify_agentcore.EXPECTED_GATEWAY_TOOLS == (
        {f"MeridianCatalog___{name}" for name in CATALOG_TOOLS}
        | {"MeridianHolds___get_package_details", "MeridianHolds___loyalty_balance",
           "MeridianHolds___create_courtesy_hold", "MeridianHolds___confirm_booking"})
    assert not any("Semantic" in name for name in verify_agentcore.EXPECTED_GATEWAY_TOOLS)


def _check(monkeypatch, offered):
    class FakeAdapter:
        def __init__(self, **kwargs):
            pass

        def list_tools(self):
            return [{"name": name} for name in offered], {}

    monkeypatch.setattr("backend.agentcore.gateway.AgentCoreGatewayAdapter", FakeAdapter)
    cfg = SimpleNamespace(gateway_url="https://example.invalid/mcp", region="us-east-1")
    return verify_agentcore.check_gateway_tools(cfg)


def test_a_gateway_with_every_expected_tool_passes(monkeypatch):
    ok, _, summary = _check(monkeypatch, sorted(verify_agentcore.EXPECTED_GATEWAY_TOOLS))
    assert ok and summary == "10 tools"


def test_a_gateway_missing_one_expected_tool_fails(monkeypatch):
    offered = sorted(verify_agentcore.EXPECTED_GATEWAY_TOOLS - {"MeridianHolds___loyalty_balance"})
    ok, names, _ = _check(monkeypatch, offered)
    assert not ok and "loyalty_balance" not in names


def test_a_gateway_still_offering_the_retired_search_tool_fails_and_names_it(monkeypatch):
    retired = "SemanticTripSearchLambda___semantic_trip_search"
    offered = sorted(verify_agentcore.EXPECTED_GATEWAY_TOOLS | {retired})
    ok, names, summary = _check(monkeypatch, offered)
    assert not ok and retired in names and "unexpected" in summary and retired in summary


def test_a_missing_tool_is_named_in_the_summary(monkeypatch):
    missing = "MeridianHolds___loyalty_balance"
    offered = sorted(verify_agentcore.EXPECTED_GATEWAY_TOOLS - {missing})
    ok, _, summary = _check(monkeypatch, offered)
    assert not ok and "missing" in summary and missing in summary
