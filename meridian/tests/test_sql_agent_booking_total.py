"""The Phase 1 booking estimate reads the Data API's string prices."""

from types import SimpleNamespace

from backend.agents.phase_01_sql import agent as sql_agent


class FakeDb:
    def __init__(self, price):
        self.price = price

    async def execute_one(self, _query, params):
        return {"package_id": params[0], "name": "Kyoto", "price_per_person": self.price}


def make_agent(monkeypatch, price):
    monkeypatch.setattr(sql_agent, "get_rds_data_client", lambda: FakeDb(price))
    monkeypatch.setattr(sql_agent, "bedrock_model", lambda: None)
    monkeypatch.setattr(sql_agent, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))
    return sql_agent.SQLAgent()


async def test_a_string_price_for_two_travelers_totals_forty_eight_hundred(monkeypatch):
    agent = make_agent(monkeypatch, "2400.00")

    result = await agent._calculate_booking_total(
        [{"package_id": "CTY-002", "travelers_count": 2}]
    )

    assert result["subtotal"] == 4800.00
    assert result["items"][0]["total"] == 4800.00
    assert result["items"][0]["unit_price"] == 2400.00


async def test_a_traveler_count_sent_as_text_is_read_as_a_number(monkeypatch):
    agent = make_agent(monkeypatch, "100.50")

    result = await agent._calculate_booking_total(
        [{"package_id": "CTY-002", "travelers_count": "3"}]
    )

    assert result["subtotal"] == 301.50
