"""Characterization of the trace one-liner for every custom MCP tool."""
import pytest

from backend.routers.chat import _summarize_domain_result


@pytest.mark.parametrize(
    ("tool", "result", "expected"),
    [
        ("search_trips", {"packages": [{}, {}, {}]}, "found 3 trips"),
        ("search_trips", {"packages": None}, "found 0 trips"),
        ("search_trips", {}, "found 0 trips"),
        ("search_trips", {"packages": 5}, "summarize failed: TypeError"),
        ("search_trips", ["x"], "shape=list"),
        ("compare_packages", [{}, {}], "compared 2 packages"),
        ("compare_packages", {"a": 1}, "unexpected shape: dict"),
        (
            "currency_convert",
            {"conversions": [{}, {}], "to": "EUR"},
            "converted 2 package prices to EUR",
        ),
        (
            "currency_convert",
            {"amount": 100, "from": "USD", "converted": 92.5, "to": "EUR"},
            "100 USD = 92.5 EUR",
        ),
        ("currency_convert", "oops", "shape=str"),
        ("loyalty_balance", {"error": "not signed in"}, "refused, not signed in"),
        ("loyalty_balance", {"points_balance": 12500, "tier": "gold"}, "12,500 pts, tier=gold"),
        ("loyalty_balance", {"tier": "base"}, "0 pts, tier=base"),
        ("loyalty_balance", {"points_balance": "x", "tier": "t"}, "summarize failed: ValueError"),
        ("loyalty_balance", [], "shape=list"),
        ("price_range", {"low": 1199.0, "high": 3299.0}, "range low=1199.0, high=3299.0"),
        ("price_range", None, "shape=NoneType"),
        (
            "region_inventory",
            {"package_count": 7, "total_departure_slots": 21},
            "7 packages, 21 slots",
        ),
        ("region_inventory", 3, "shape=int"),
        ("unknown_tool", {"a": 1}, "shape=dict"),
    ],
)
def test_summary_text_is_stable(tool, result, expected):
    assert _summarize_domain_result(tool, result) == expected
