"""The holds Lambda authorizes its own role, steps down to meridian_app, and holds atomically."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

TARGET = (
    Path(__file__).resolve().parents[1]
    / "meridian_agentcore"
    / "agentcore"
    / "gateway_targets"
    / "meridian_holds"
)
sys.path.insert(0, str(TARGET))

import lambda_function as holds  # noqa: E402


class FakeDataApi:
    def __init__(self, rows_by_marker):
        self.rows_by_marker = rows_by_marker
        self.statements = []
        self.parameters = []
        self.tx = []

    def begin_transaction(self, **kwargs):
        self.tx.append("begin")
        return {"transactionId": "tx-1"}

    def commit_transaction(self, **kwargs):
        self.tx.append("commit")
        return {"transactionStatus": "Transaction Committed"}

    def rollback_transaction(self, **kwargs):
        self.tx.append("rollback")
        return {}

    def execute_statement(self, **kwargs):
        self.statements.append(kwargs["sql"])
        self.parameters.append(
            {p["name"]: next(iter(p["value"].values())) for p in kwargs.get("parameters", [])}
        )
        for marker, rows in self.rows_by_marker.items():
            if marker in kwargs["sql"]:
                return {"formattedRecords": json.dumps(rows)}
        return {"formattedRecords": "[]"}


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setattr(
        holds, "CONFIG", holds.AuroraConfig("arn:cluster", "arn:secret", "meridian")
    )
    monkeypatch.setattr(
        holds, "SUBJECT", ("aws_iam", "AROAEXAMPLE", "arn:aws:sts::1:assumed-role/x/y")
    )


def _context(tool):
    return SimpleNamespace(
        client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": tool})
    )


def _hold_args():
    return {
        "travelerId": "trv_meridian_demo",
        "packageId": "CTY-002",
        "duration": "7 nights",
        "travelers": 2,
        "unitPriceCents": 250000,
        "totalCents": 500000,
        "holdMinutes": 720,
        "travelerConfirmed": True,
        "budgetCeilingCents": 700000,
        "journeyRef": "concierge:conv-1",
    }


def test_get_package_details_reads_availability(config, monkeypatch):
    api = FakeDataApi({
        "FROM trip_packages": [{
            "package_id": "CTY-002",
            "name": "Tokyo",
            "durations": '["7 nights"]',
            "availability": '{"7 nights": 4}',
            "highlights": '["Sushi"]',
            "price_per_person": 2500.0,
        }]
    })
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(
        {"packageId": "CTY-002"}, _context("MeridianHolds___get_package_details")
    )
    assert result["package"]["availability"] == {"7 nights": 4}
    assert "Tokyo" in result["summary"]
    assert "4 places" in result["summary"]


def test_hold_refuses_when_the_workload_has_no_grant(config, monkeypatch):
    api = FakeDataApi({"FROM traveler_identity_bindings": []})
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_hold_args(), _context("MeridianHolds___create_courtesy_hold"))
    assert result["error"] == "traveler_not_authorized"
    assert result["governance"]["decision"] == "deny"
    audits = [
        p
        for s, p in zip(api.statements, api.parameters, strict=True)
        if "traveler_access_audit" in s
    ]
    assert audits[-1]["decision"] == "deny"
    assert api.tx == ["begin", "rollback"]
    assert not any("create_courtesy_hold" in s for s in api.statements)


def test_hold_sets_scope_steps_down_and_returns_the_row(config, monkeypatch):
    api = FakeDataApi({
        "FROM traveler_identity_bindings": [{"binding_id": "bind_1"}],
        "FROM journey_threads": [{"journey_id": "jrn_1"}],
        "FROM create_courtesy_hold": [{
            "booking_id": "HLD-1",
            "status": "held",
            "replayed": False,
            "seats_available": 4,
            "seats_reserved": 2,
            "seats_remaining": 2,
        }],
    })
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_hold_args(), _context("MeridianHolds___create_courtesy_hold"))
    assert result["hold"]["bookingId"] == "HLD-1"
    assert result["hold"]["seatsRemaining"] == 2
    assert result["hold"]["journeyId"] == "jrn_1"
    assert result["governance"]["decision"] == "allow"
    joined = "\n".join(api.statements)
    assert "set_config('app.current_traveler_id'" in joined
    assert "SET LOCAL ROLE meridian_app" in joined
    assert joined.index("traveler_access_audit") < joined.index("SET LOCAL ROLE")
    assert joined.index("SET LOCAL ROLE") < joined.index("FROM create_courtesy_hold")
    assert api.tx == ["begin", "commit"]
    hold_params = api.parameters[api.statements.index(holds.HOLD_SQL)]
    assert hold_params["unit_price"] == "2500.00"
    assert hold_params["total"] == "5000.00"
    assert hold_params["request_id"].startswith("hrq_")


def test_new_journey_is_created_for_an_unknown_reference(config, monkeypatch):
    api = FakeDataApi({
        "FROM traveler_identity_bindings": [{"binding_id": "bind_1"}],
        "FROM create_courtesy_hold": [{
            "booking_id": "HLD-2", "status": "held", "replayed": False,
            "seats_available": 4, "seats_reserved": 1, "seats_remaining": 3,
        }],
    })
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_hold_args(), _context("MeridianHolds___create_courtesy_hold"))
    assert result["hold"]["journeyId"].startswith("jrn_")
    assert any("INSERT INTO journeys" in s for s in api.statements)
    assert any("INSERT INTO journey_threads" in s for s in api.statements)


def test_hold_reports_inventory_errors_by_name(config, monkeypatch):
    class Failing(FakeDataApi):
        def execute_statement(self, **kwargs):
            if "FROM create_courtesy_hold" in kwargs["sql"]:
                raise RuntimeError("ERROR: insufficient_inventory")
            return super().execute_statement(**kwargs)

    api = Failing({
        "FROM traveler_identity_bindings": [{"binding_id": "b"}],
        "FROM journey_threads": [{"journey_id": "jrn_1"}],
    })
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_hold_args(), _context("MeridianHolds___create_courtesy_hold"))
    assert result["error"] == "insufficient_inventory"
    assert api.tx == ["begin", "rollback"]


def test_unknown_tool_is_refused(config, monkeypatch):
    monkeypatch.setattr(holds, "RDS", FakeDataApi({}))
    with pytest.raises(ValueError):
        holds.lambda_handler({}, _context("MeridianHolds___delete_everything"))


def test_fingerprint_matches_the_backend_canonical_form():
    terms = holds.normalize_terms("cty-002 ", " 7  Nights", 2, Decimal("2500.00"))
    assert terms == {
        "package_id": "cty-002",
        "duration": "7 nights",
        "quantity": 2,
        "unit_price": "2500.00",
        "total_amount": "5000.00",
    }
    from backend.agents.orchestration_05.hold_intent import fingerprint_terms, normalize_hold_terms

    backend_terms = normalize_hold_terms("cty-002 ", " 7  Nights", 2, Decimal("2500.00"))
    assert holds.fingerprint(terms) == fingerprint_terms(backend_terms)
