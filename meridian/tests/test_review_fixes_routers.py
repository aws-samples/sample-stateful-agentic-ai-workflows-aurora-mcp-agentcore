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
        self.targets.append(args["to_currency"])
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
    asyncio.run(chat_mod._currency_call(cli, query + " 100 USD".lower(), []))
    assert cli.targets == [expected]


@pytest.mark.parametrize("query, names, expected", [
    ("price range for paris", chat_mod._PRICE_RANGE_DESTINATIONS, "Paris"),
    ("inventory in asia", chat_mod._INVENTORY_REGIONS, "Asia"),
])
def test_named_in_matches_explicit_location_names(query, names, expected):
    assert chat_mod._named_in(query, names) == expected


@pytest.mark.parametrize("query, names", [
    ("what is the price range comparison across trips", chat_mod._PRICE_RANGE_DESTINATIONS),
    ("inventory in malaysia", chat_mod._INVENTORY_REGIONS),
    ("how many asian packages", chat_mod._INVENTORY_REGIONS),
    ("inventory for europeans", chat_mod._INVENTORY_REGIONS),
    ("a parisian weekend", chat_mod._PRICE_RANGE_DESTINATIONS),
])
def test_named_in_asks_when_no_supported_location_was_named(query, names):
    with pytest.raises(chat_mod.DomainInputError, match="Please name"):
        chat_mod._named_in(query, names)


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
    assert receipt.durable_snapshots is False


def test_the_callers_own_thread_is_counted(monkeypatch):
    db = _ReceiptDb(owned_threads={"thread-mine"})

    receipt = _receipt(monkeypatch, db, "thread-mine")

    assert _snapshot_line(receipt).count == 7
    assert db.admin_keys == ["thread-mine"]
    assert receipt.durable_snapshots is True


def test_the_ownership_lookup_opens_no_scoped_session_of_its_own(monkeypatch):
    db = _ReceiptDb(owned_threads={"thread-mine"})

    _receipt(monkeypatch, db, "thread-mine")

    assert db.sessions == ["memory_agent", "booking_agent"]


@pytest.mark.parametrize("key", ["budget_cap", "Budget", "per_person_range"])
@pytest.mark.parametrize("value", [
    "flexible", "$75 or $5000", "$75 per traveler, $3200 total", "75 for 3 kids",
    "\u20ac500", "500 CAD", "$0", "500000 yen", "3000 baht", ".5k", "9" * 310,
])
def test_a_budget_that_is_not_one_amount_or_range_is_refused_with_a_dollar_prompt(
    monkeypatch, key, value
):
    calls = []

    async def authorize(principal, traveler_id):
        return "trv_meridian_demo"

    class Store:
        async def update_preference(self, *args, **kwargs):
            calls.append(args)

    monkeypatch.setattr(memory_mod, "authorize_traveler", authorize)
    monkeypatch.setattr(memory_mod, "get_memory_store", lambda: Store())

    with pytest.raises(HTTPException) as refused:
        asyncio.run(memory_mod.update_memory_fact(
            "me", key, memory_mod.MemoryFactUpdate(value=value), principal=_principal()))

    assert refused.value.status_code == 422
    assert "one dollar amount or one range" in refused.value.detail
    assert "$75 per traveler" in refused.value.detail
    assert calls == []


@pytest.mark.parametrize("value", [
    "$75", "$3,200", "Prefers $2k-3.5k per person", "US$75", "75 USD", "75", "around 3k",
    "3000 per person", "2000-3500", "USD3000", "$75 for 3 kids",
])
def test_a_budget_with_a_dollar_amount_is_saved_as_typed(monkeypatch, value):
    saved = []

    async def authorize(principal, traveler_id):
        return "trv_meridian_demo"

    class Store:
        async def update_preference(self, traveler, key, text, transaction_id=None):
            saved.append((key, text))
            return {"key": key, "value": text}

    class Db:
        @contextlib.asynccontextmanager
        async def scoped_session(self, **kwargs):
            yield "tx"

    monkeypatch.setattr(memory_mod, "authorize_traveler", authorize)
    monkeypatch.setattr(memory_mod, "get_memory_store", lambda: Store())
    monkeypatch.setattr(memory_mod, "get_rds_data_client", lambda: Db())
    monkeypatch.setattr(memory_mod, "get_agentcore_identity", lambda: SimpleNamespace(
        authorization_context=lambda: None))

    result = asyncio.run(memory_mod.update_memory_fact(
        "me", "budget_cap", memory_mod.MemoryFactUpdate(value=value), principal=_principal()))

    assert saved == [("budget_cap", value)]
    assert result.value == value


def test_a_non_budget_preference_needs_no_dollar_amount(monkeypatch):
    saved = []

    async def authorize(principal, traveler_id):
        return "trv_meridian_demo"

    class Store:
        async def update_preference(self, traveler, key, text, transaction_id=None):
            saved.append((key, text))
            return {"key": key, "value": text}

    class Db:
        @contextlib.asynccontextmanager
        async def scoped_session(self, **kwargs):
            yield "tx"

    monkeypatch.setattr(memory_mod, "authorize_traveler", authorize)
    monkeypatch.setattr(memory_mod, "get_memory_store", lambda: Store())
    monkeypatch.setattr(memory_mod, "get_rds_data_client", lambda: Db())
    monkeypatch.setattr(memory_mod, "get_agentcore_identity", lambda: SimpleNamespace(
        authorization_context=lambda: None))

    asyncio.run(memory_mod.update_memory_fact(
        "me", "seat", memory_mod.MemoryFactUpdate(value="aisle"), principal=_principal()))
    assert saved == [("seat", "aisle")]


def _profile_with(monkeypatch, facts):
    async def authorize(principal, traveler_id):
        return "trv_meridian_demo"

    class Store:
        async def recall_preferences(self, traveler, limit=50, transaction_id=None):
            return facts

        async def recall_profile(self, traveler, transaction_id=None):
            return None

    class Db:
        @contextlib.asynccontextmanager
        async def scoped_session(self, **kwargs):
            yield "tx"

    monkeypatch.setattr(memory_mod, "authorize_traveler", authorize)
    monkeypatch.setattr(memory_mod, "get_memory_store", lambda: Store())
    monkeypatch.setattr(memory_mod, "get_rds_data_client", lambda: Db())
    monkeypatch.setattr(memory_mod, "get_agentcore_identity", lambda: SimpleNamespace(
        authorization_context=lambda: None))
    return asyncio.run(memory_mod.get_memory_profile("me", _principal()))


@pytest.mark.parametrize(("facts", "cents", "unclear"), [
    ([{"key": "budget_cap", "value": "$3,200"}], 320000, False),
    ([{"key": "budget_cap", "value": "$75 or $5000"}], None, True),
    ([{"key": "budget", "value": "flexible"}], None, True),
    ([{"key": "seat", "value": "aisle"}], None, False),
    ([], None, False),
    ([{"key": "budget_cap", "value": "9" * 310}], None, True),
    ([{"key": "budget_cap", "value": "500000 yen"}], None, True),
    ([{"key": "budget_cap", "value": ".5k"}], None, True),
])
def test_the_profile_says_when_a_saved_budget_is_unclear(monkeypatch, facts, cents, unclear):
    profile = _profile_with(monkeypatch, [
        {**fact, "source": None, "confidence": None} for fact in facts
    ])
    assert profile.budget_ceiling_per_traveler_cents == cents
    assert profile.budget_unclear is unclear


def test_the_profile_survives_a_parser_that_returns_infinity(monkeypatch):
    """A non-finite amount from the parser is an unclear budget, not a 500."""
    from backend.agents import budget as budget_mod

    monkeypatch.setattr(budget_mod, "parse_usd_amounts", lambda _text: [float("inf")])
    profile = _profile_with(monkeypatch, [{"key": "budget_cap", "value": "$75"}])
    assert profile.budget_ceiling_per_traveler_cents is None
    assert profile.budget_unclear is True
