"""persist_preference must supply the primary key traveler_preferences requires.

``preference_id`` is ``VARCHAR(50) PRIMARY KEY`` with no default, so an INSERT
that omits it fails before ON CONFLICT can run. The stand-alone memory MCP
server used to omit it, which made the tool unusable for every new fact.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from backend.mcp import memory_server


class _CapturingDb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, str | None]] = []
        self.scoped: list[dict] = []

    @asynccontextmanager
    async def scoped_session(self, **kwargs):
        self.scoped.append(kwargs)
        yield "txn-1"

    async def execute(self, sql, params=None, transaction_id=None):
        self.calls.append((sql, params, transaction_id))
        return []


def test_persist_preference_allocates_a_preference_id(monkeypatch) -> None:
    db = _CapturingDb()
    monkeypatch.setattr(memory_server, "_store", lambda: SimpleNamespace(db=db))
    monkeypatch.setattr(memory_server, "_authorization", lambda: "authz")

    result = asyncio.run(
        memory_server.persist_preference(
            "trv_meridian_demo", "dining", "shellfish_allergy", "severe", 0.9
        )
    )

    assert result["ok"] is True
    (sql, params, transaction_id), = db.calls
    assert transaction_id == "txn-1"
    assert "preference_id, traveler_id" in sql
    assert params[0].startswith("pref_") and len(params[0]) <= 50
    assert params[1:5] == ("trv_meridian_demo", "dining", "shellfish_allergy", "severe")
    assert db.scoped == [
        {"traveler_id": "trv_meridian_demo", "agent_type": memory_server.DEFAULT_AGENT_TYPE,
         "authorization": "authz"}
    ]


def test_persist_preference_ids_are_unique_per_call(monkeypatch) -> None:
    db = _CapturingDb()
    monkeypatch.setattr(memory_server, "_store", lambda: SimpleNamespace(db=db))
    monkeypatch.setattr(memory_server, "_authorization", lambda: "authz")
    for _ in range(2):
        asyncio.run(memory_server.persist_preference("trv_meridian_demo", "t", "k", "v"))
    first, second = (params[0] for _sql, params, _tx in db.calls)
    assert first != second
