"""price_range: real Aurora MIN/AVG/MAX for a destination, never a fabricated
seasonal multiplier. Replaces the old seasonal_price_band tool, which
multiplied the real aggregate by an invented seasonal factor with no
disclosure in its payload or the rendered chat reply.
"""

import asyncio

from backend.mcp import concierge_server
from backend.routers.chat import _format_domain_reply, _summarize_domain_result


class _CatalogDb:
    def __init__(self, row):
        self.row = row
        self.reads: list[tuple[str, tuple]] = []

    async def execute_one(self, sql, params):
        self.reads.append((sql, params))
        return self.row


def test_price_range_returns_the_real_sql_aggregate_with_no_multiplier(monkeypatch):
    row = {
        "min_price": 1199.0,
        "avg_price": 1949.5,
        "max_price": 3299.0,
        "sample_size": 4,
    }
    db = _CatalogDb(row)
    monkeypatch.setattr(concierge_server, "_db", lambda: db)

    result = asyncio.run(concierge_server.price_range("Tokyo"))

    # Every number must equal the SQL aggregate exactly - no seasonal
    # multiplier applied anywhere.
    assert result["low"] == row["min_price"]
    assert result["average"] == row["avg_price"]
    assert result["high"] == row["max_price"]
    assert result["sample_size"] == 4
    assert result["destination"] == "Tokyo"
    assert "not seasonal" in result["note"].lower()
    assert "month" not in result
    assert "season" not in result
    (_sql, params), = db.reads
    assert params == ("%Tokyo%", "%Tokyo%")


def test_price_range_reports_zero_sample_without_inventing_numbers(monkeypatch):
    db = _CatalogDb({"sample_size": 0})
    monkeypatch.setattr(concierge_server, "_db", lambda: db)

    result = asyncio.run(concierge_server.price_range("Atlantis"))

    assert result == {"destination": "Atlantis", "sample_size": 0}
    assert "low" not in result
    assert "high" not in result


def test_price_range_reply_names_the_real_numbers_and_discloses_no_seasonality():
    reply = _format_domain_reply(
        "price_range",
        {
            "destination": "Tokyo",
            "low": 1199.0,
            "average": 1949.5,
            "high": 3299.0,
            "sample_size": 4,
            "note": "Not seasonal: the catalog holds one price per package, "
            "not per-month or per-season prices.",
        },
    )

    assert "$1,199" in reply
    assert "$1,950" in reply or "$1,949" in reply
    assert "$3,299" in reply
    assert "sample=4" in reply
    assert "Not seasonal" in reply
    assert "peak" not in reply.lower()
    assert "shoulder" not in reply.lower()
    assert "off-season" not in reply.lower()


def test_price_range_reply_names_the_destination_when_no_data_exists():
    reply = _format_domain_reply("price_range", {"destination": "Atlantis", "sample_size": 0})
    assert reply == "No pricing data for Atlantis."


def test_price_range_summary_has_no_season_label():
    summary = _summarize_domain_result("price_range", {"low": 1199.0, "high": 3299.0})
    assert summary == "range low=1199.0 · high=3299.0"
