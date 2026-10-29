"""Router fixes: memory value validation, whole-word pickers, stream 403, receipt ownership."""

from __future__ import annotations

import asyncio
import contextlib
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import main
from backend.authorization import TravelerAuthorizationError
from backend.http_auth import HttpPrincipal, require_http_principal
from backend.routers import chat as chat_mod
from backend.routers import diagnostics as diag_mod
from backend.routers import memory as memory_mod


def _principal():
    return HttpPrincipal("test", "trv_meridian_demo", "test")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(main.app.dependency_overrides, require_http_principal, _principal)
    return TestClient(main.app, raise_server_exceptions=False)


def test_a_blank_preference_value_is_refused_without_a_write(monkeypatch):
    calls = []

    async def authorize(principal, traveler_id):
        return "trv_meridian_demo"

    class Store:
        async def update_preference(self, *args, **kwargs):
            calls.append(args)

    monkeypatch.setattr(memory_mod, "authorize_traveler", authorize)
    monkeypatch.setattr(memory_mod, "get_memory_store", lambda: Store())
    update = memory_mod.MemoryFactUpdate(value="   ")

    with pytest.raises(HTTPException) as refused:
        asyncio.run(memory_mod.update_memory_fact(
            "me", "budget", update, principal=_principal()))

    assert refused.value.status_code == 422
    assert calls == []


class _Currency:
    def __init__(self):
        self.targets = []

    async def call(self, tool, args):
        self.targets.append(args["to_ccy"])
        return {"converted": 1.0}


@pytest.mark.parametrize("query, expected", [
    ("convert europe prices to pounds", "GBP"),
    ("convert these prices to yen for europe trips", "JPY"),
    ("convert prices to euros", "EUR"),
    ("convert prices to eur", "EUR"),
    ("convert prices", "EUR"),
    ("show prices in EUR and GBP".lower(), "EUR"),
    ("show prices in pounds and yen", "GBP"),
    ("show prices in gbp or euros", "EUR"),
])
def test_the_currency_target_matches_whole_words(query, expected):
    cli = _Currency()
    asyncio.run(chat_mod._currency_call(cli, query, []))
    assert cli.targets == [expected]


@pytest.mark.parametrize("query, names, default, expected", [
    ("what is the price range comparison across trips",
     chat_mod._PRICE_RANGE_DESTINATIONS, "Tokyo", "Tokyo"),
    ("price range for paris", chat_mod._PRICE_RANGE_DESTINATIONS, "Tokyo", "Paris"),
    ("inventory in malaysia", chat_mod._INVENTORY_REGIONS, "Europe", "Europe"),
    ("inventory in asia", chat_mod._INVENTORY_REGIONS, "Europe", "Asia"),
    ("how many asian packages", chat_mod._INVENTORY_REGIONS, "Europe", "Asia"),
    ("inventory for europeans", chat_mod._INVENTORY_REGIONS, "Asia", "Europe"),
    ("a parisian weekend", chat_mod._PRICE_RANGE_DESTINATIONS, "Tokyo", "Paris"),
])
def test_named_in_matches_word_starts(query, names, default, expected):
    assert chat_mod._named_in(query, names, default) == expected


def _frames(response):
    return [json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith("data: ")]


def test_a_revoked_grant_ends_the_book_stream_with_a_403_event(client, monkeypatch):
    async def revoked(traveler_id, booking_id):
        raise TravelerAuthorizationError(SimpleNamespace(
            provider="aws_iam", traveler_id=traveler_id))

    monkeypatch.setattr(chat_mod, "_traveler_booking", revoked)

    response = client.post("/api/chat/book/stream", json={"booking_id": "HLD-1"})

    last = _frames(response)[-1]
    assert last["type"] == "error"
    assert last["status"] == 403
    assert last["message"] == "This traveler is not authorized for the current workload."


class _ReceiptDb:
    def __init__(self, owned_threads):
        self.owned = owned_threads
        self.admin_keys = []
        self.sessions = []

    @contextlib.asynccontextmanager
    async def scoped_session(self, **kw):
        self.sessions.append(kw["agent_type"])
        yield "tx"

    async def execute(self, sql, params=(), transaction_id=None):
        if "to_regclass" in sql:
            return [{"present": True}]
        if "journey_threads" in sql:
            return [{"journey_id": "j1"}] if params[0] in self.owned else []
        return [{"n": 0}]


def _receipt(monkeypatch, db, thread_id):
    async def authorize(principal, traveler_id):
        return "trv_meridian_demo"

    async def admin(_db, kind, *, window=None, key=None):
        if kind == "workflow_snapshots":
            db.admin_keys.append(key)
        return 7

    monkeypatch.setattr(diag_mod, "authorize_traveler", authorize)
    monkeypatch.setattr(diag_mod, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(diag_mod, "admin_count", admin)
    monkeypatch.setattr(
        diag_mod, "get_agentcore_identity",
        lambda: SimpleNamespace(authorization_context=lambda: None))
    request = diag_mod.SessionReceiptRequest(conversation_id=thread_id)
    return asyncio.run(diag_mod.session_receipt(request, principal=_principal()))


def _snapshot_line(receipt):
    return next(line for line in receipt.lines if line.table == "workflow_snapshots")


def test_a_thread_the_caller_does_not_own_is_not_counted(monkeypatch):
    db = _ReceiptDb(owned_threads={"thread-mine"})

    receipt = _receipt(monkeypatch, db, "thread-theirs")

    line = _snapshot_line(receipt)
    assert line.count is None
    assert "not one of this traveler's journeys" in line.detail
    assert db.admin_keys == []
    assert receipt.durable_checkpoints is False


def test_the_callers_own_thread_is_counted(monkeypatch):
    db = _ReceiptDb(owned_threads={"thread-mine"})

    receipt = _receipt(monkeypatch, db, "thread-mine")

    assert _snapshot_line(receipt).count == 7
    assert db.admin_keys == ["thread-mine"]
    assert receipt.durable_checkpoints is True


def test_the_ownership_lookup_opens_no_scoped_session_of_its_own(monkeypatch):
    db = _ReceiptDb(owned_threads={"thread-mine"})

    _receipt(monkeypatch, db, "thread-mine")

    assert db.sessions == ["memory_agent", "booking_agent"]
