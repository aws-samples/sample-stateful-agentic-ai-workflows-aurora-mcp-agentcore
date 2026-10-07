"""The Phase 2 turn summary counts only MCP servers that actually answered.

Phase 2's argument is that the agent reached Aurora through two MCP servers,
so the closing "MCP turn complete - N servers" span is proof, not decoration.
It counted the custom meridian-concierge server whenever a domain intent was
detected. When that call raised, or came back empty, the trace still claimed
the server had been used - the reply said the tool failed while the proof
underneath said it succeeded.

The domain call is the boundary to a spawned MCP subprocess, so it is replaced
here. Pure-domain prompts are used throughout: they never open the generic
postgres-mcp session, so the custom server is the only one that can count.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.routers import chat as chat_router

CURRENCY_PROMPT = "Convert 2500 US dollars to euros"


def _summary_count(activities: list) -> int:
    """Servers claimed by the turn-complete span."""
    titles = [a.title for a in activities if "MCP turn complete" in (a.title or "")]
    assert len(titles) == 1, f"expected one turn-complete span, got {titles}"
    return int(titles[0].split(":")[1].strip().split()[0])


async def _run(monkeypatch, domain_call: Any) -> tuple[list, Optional[str]]:
    async def fake_domain_tool(query: str, *, traveler_id: str) -> Any:
        if isinstance(domain_call, Exception):
            raise domain_call
        return domain_call

    monkeypatch.setattr(chat_router, "_call_domain_tool", fake_domain_tool)
    _products, activities, domain_text = await chat_router.mcp_search(
        CURRENCY_PROMPT, traveler_id="trv_meridian_demo"
    )
    return activities, domain_text


async def test_a_custom_server_that_raised_is_not_counted(monkeypatch) -> None:
    activities, domain_text = await _run(monkeypatch, RuntimeError("subprocess exited"))

    assert "failed" in (domain_text or ""), "the reply should say the tool failed"
    assert _summary_count(activities) == 0, (
        "the proof claimed a server answered while the reply said it failed"
    )


async def test_a_custom_server_that_returned_nothing_is_not_counted(monkeypatch) -> None:
    activities, _ = await _run(monkeypatch, None)
    assert _summary_count(activities) == 0


async def test_a_custom_server_that_answered_is_counted(monkeypatch) -> None:
    """The positive control: a completed call must still be claimed."""
    answered = {
        "tool": "currency_convert",
        "args": {"amount": 2500, "from_ccy": "USD", "to_ccy": "EUR"},
        "result": {"amount": 2500.0, "from": "USD", "to": "EUR", "converted": 2300.0},
    }
    activities, _ = await _run(monkeypatch, answered)
    assert _summary_count(activities) == 1


@pytest.mark.parametrize("count,expected", [(0, "0 servers"), (1, "1 server"), (2, "2 servers")])
def test_the_summary_pluralises_by_the_counted_servers(count: int, expected: str) -> None:
    """Guards the wording the counting feeds, so a zero reads naturally."""
    title = f"MCP turn complete: {count} server{'' if count == 1 else 's'}"
    assert title.endswith(expected)
