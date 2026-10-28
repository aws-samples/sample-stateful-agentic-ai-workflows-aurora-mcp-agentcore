"""The Phase 1 reference SQL agent exposes only read tools and no booking writer."""

from types import SimpleNamespace

from backend.agents.phase_01_sql import agent as sql_agent

READ_TOOLS = {
    "_lookup_trip_package",
    "_search_trip_packages",
    "_check_departure_availability",
    "_calculate_booking_total",
}


def test_the_sql_agent_exposes_only_read_tools(monkeypatch):
    monkeypatch.setattr(sql_agent, "get_rds_data_client", lambda: SimpleNamespace())
    monkeypatch.setattr(sql_agent, "bedrock_model", lambda: None)
    monkeypatch.setattr(sql_agent, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))

    agent = sql_agent.SQLAgent()

    assert {tool.__name__ for tool in agent.agent.tools} == READ_TOOLS
    assert not hasattr(agent, "process_booking")
    assert not hasattr(agent, "_process_booking")
