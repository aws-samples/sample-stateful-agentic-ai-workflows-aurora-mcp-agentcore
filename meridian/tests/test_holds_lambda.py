"""The holds Lambda authorizes its own role, steps down to meridian_app, and holds atomically."""

from __future__ import annotations

import hashlib
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
    assert "\u00b7" not in result["summary"]
    assert result["summary"].startswith("Tokyo, ")


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


CATALOG_PRICE = {"FROM trip_packages": [{"unit_price_cents": 250000}]}
# Jordan-like saved facts: a $3,500 per-person cap allows $7,000 for the party of two.
SAVED_BUDGET = {"FROM traveler_preferences": [
    {"preference_key": "budget_cap", "preference_value": "Max $3,500 per person"},
]}
# The held booking a confirmation checks the saved budget against.
BOOKED_PARTY = {"FROM booking_lines bl": [{"travelers_count": 2, "status": "held"}]}

RECEIPT = [{
    "status": "held",
    "created_at": "2026-09-10 12:00:00+00",
    "hold_expires_at": "2026-09-11 00:00:00+00",
    "observed_at": "2026-09-10 12:00:01+00",
}]


def test_hold_sets_scope_steps_down_and_returns_the_row(config, monkeypatch):
    api = FakeDataApi({
        **CATALOG_PRICE,
        **SAVED_BUDGET,
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
        "FROM bookings": RECEIPT,
    })
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_hold_args(), _context("MeridianHolds___create_courtesy_hold"))
    assert result["hold"]["bookingId"] == "HLD-1"
    assert result["hold"]["seatsRemaining"] == 2
    assert result["hold"]["journeyId"] == "jrn_1"
    assert result["hold"]["expiresAt"] == "2026-09-11 00:00:00+00"
    assert result["hold"]["createdAt"] == "2026-09-10 12:00:00+00"
    assert result["governance"]["decision"] == "allow"
    joined = "\n".join(api.statements)
    assert "set_config('app.current_traveler_id'" in joined
    assert "set_config('app.thread_id'" in joined
    assert "SET LOCAL ROLE meridian_app" in joined
    assert joined.index("traveler_access_audit") < joined.index("SET LOCAL ROLE")
    assert joined.index("SET LOCAL ROLE") < joined.index("FROM create_courtesy_hold")
    assert joined.index("FROM create_courtesy_hold") < joined.index("FROM bookings")
    assert "journey_executions" not in joined
    assert api.tx == ["begin", "commit"]
    hold_params = api.parameters[api.statements.index(holds.HOLD_SQL)]
    assert hold_params["unit_price"] == "2500.00"
    assert hold_params["total"] == "5000.00"
    assert hold_params["request_id"].startswith("hrq_")


def test_a_workflow_caller_replays_its_checkpointed_identity_under_its_lease(config, monkeypatch):
    api = FakeDataApi({
        **CATALOG_PRICE,
        **SAVED_BUDGET,
        "FROM traveler_identity_bindings": [{"binding_id": "bind_1"}],
        "FROM journey_threads": [{"journey_id": "jrn_1"}],
        "FROM journey_executions": [{"execution_id": "exe_1"}],
        "FROM create_courtesy_hold": [{
            "booking_id": "HLD-CHK", "status": "held", "replayed": True,
            "seats_available": None, "seats_reserved": None, "seats_remaining": None,
        }],
        "FROM bookings": RECEIPT,
    })
    monkeypatch.setattr(holds, "RDS", api)
    args = {**_hold_args(), "journeyRef": "phase5-thread", "holdRequestId": "hrq_checkpointed",
            "bookingId": "HLD-CHK", "executionId": "exe_1"}
    result = holds.lambda_handler(args, _context("MeridianHolds___create_courtesy_hold"))
    assert result["hold"]["replayed"] is True
    assert result["hold"]["bookingId"] == "HLD-CHK"
    assert result["hold"]["holdRequestId"] == "hrq_checkpointed"
    hold_params = api.parameters[api.statements.index(holds.HOLD_SQL)]
    assert hold_params["request_id"] == "hrq_checkpointed"
    assert hold_params["booking_id"] == "HLD-CHK"
    joined = "\n".join(api.statements)
    assert "FOR UPDATE" in joined and "set_config('app.execution_id'" in joined
    assert joined.index("journey_executions") < joined.index("FROM create_courtesy_hold")


def test_a_lost_lease_refuses_the_hold_before_any_write(config, monkeypatch):
    api = FakeDataApi({
        **CATALOG_PRICE,
        **SAVED_BUDGET,
        "FROM traveler_identity_bindings": [{"binding_id": "bind_1"}],
        "FROM journey_threads": [{"journey_id": "jrn_1"}],
        "FROM journey_executions": [],
    })
    monkeypatch.setattr(holds, "RDS", api)
    args = {**_hold_args(), "executionId": "exe_gone"}
    result = holds.lambda_handler(args, _context("MeridianHolds___create_courtesy_hold"))
    assert result["error"] == "execution_lease_lost"
    assert not any("create_courtesy_hold" in s for s in api.statements)
    assert api.tx == ["begin", "rollback"]


def test_new_journey_is_created_for_an_unknown_reference(config, monkeypatch):
    api = FakeDataApi({
        **CATALOG_PRICE,
        **SAVED_BUDGET,
        "FROM traveler_identity_bindings": [{"binding_id": "bind_1"}],
        "FROM create_courtesy_hold": [{
            "booking_id": "HLD-2", "status": "held", "replayed": False,
            "seats_available": 4, "seats_reserved": 1, "seats_remaining": 3,
        }],
        "FROM bookings": RECEIPT,
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
        **CATALOG_PRICE,
        **SAVED_BUDGET,
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
    from backend.agents.phase_05_workflow.hold_intent import fingerprint_terms, normalize_hold_terms

    backend_terms = normalize_hold_terms("cty-002 ", " 7  Nights", 2, Decimal("2500.00"))
    assert holds.fingerprint(terms) == fingerprint_terms(backend_terms)


def _booking_args():
    return {
        "travelerId": "trv_meridian_demo",
        "bookingId": "HLD-1",
        "totalCents": 500000,
        "travelerConfirmed": True,
        "budgetCeilingCents": 700000,
        "journeyRef": "concierge:conv-1",
    }


BOOKING_RECEIPT = [{
    "status": "confirmed",
    "created_at": "2026-09-10 12:00:00+00",
    "confirmed_at": "2026-09-10 13:00:00+00",
    "hold_expires_at": "2026-09-11 00:00:00+00",
    "total_amount": "5000.00",
    "package_id": "CTY-002",
    "duration": "7 nights",
    "travelers_count": 2,
    "observed_at": "2026-09-10 13:00:01+00",
}]


def test_confirm_booking_sets_scope_steps_down_and_returns_the_confirmed_row(config, monkeypatch):
    api = FakeDataApi({
        "FROM traveler_identity_bindings": [{"binding_id": "bind_1"}],
        **BOOKED_PARTY,
        **SAVED_BUDGET,
        "FROM confirm_booking": [{
            "booking_id": "HLD-1", "status": "confirmed", "replayed": False,
            "confirmed_at": "2026-09-10 13:00:00+00",
            "hold_expires_at": "2026-09-11 00:00:00+00", "total_amount": "5000.00",
        }],
        "FROM bookings b": BOOKING_RECEIPT,
    })
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_booking_args(), _context("MeridianHolds___confirm_booking"))
    booking = result["booking"]
    assert booking["bookingId"] == "HLD-1"
    assert booking["status"] == "confirmed"
    assert booking["replayed"] is False
    assert booking["confirmedAt"] == "2026-09-10 13:00:00+00"
    assert booking["expiresAt"] == "2026-09-11 00:00:00+00"
    assert booking["totalAmount"] == "5000.00"
    assert booking["packageId"] == "CTY-002" and booking["travelers"] == 2
    assert result["governance"]["decision"] == "allow"
    assert "Confirmed booking HLD-1" in result["summary"]
    joined = "\n".join(api.statements)
    assert joined.index("traveler_access_audit") < joined.index("SET LOCAL ROLE meridian_app")
    assert joined.index("SET LOCAL ROLE meridian_app") < joined.index("FROM confirm_booking")
    assert joined.index("FROM confirm_booking") < joined.index("FROM bookings b")
    assert api.tx == ["begin", "commit"]
    params = api.parameters[api.statements.index(holds.CONFIRM_SQL)]
    assert params == {"booking_id": "HLD-1", "traveler": "trv_meridian_demo", "total": "5000.00"}


def test_confirm_booking_replays_an_already_confirmed_booking(config, monkeypatch):
    api = FakeDataApi({
        "FROM traveler_identity_bindings": [{"binding_id": "bind_1"}],
        **BOOKED_PARTY,
        **SAVED_BUDGET,
        "FROM confirm_booking": [{
            "booking_id": "HLD-1", "status": "confirmed", "replayed": True,
            "confirmed_at": "2026-09-10 13:00:00+00",
            "hold_expires_at": "2026-09-11 00:00:00+00", "total_amount": "5000.00",
        }],
        "FROM bookings b": BOOKING_RECEIPT,
    })
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_booking_args(), _context("MeridianHolds___confirm_booking"))
    assert result["booking"]["replayed"] is True
    assert result["summary"].startswith("Already confirmed booking HLD-1")


def test_confirm_booking_reports_business_errors_by_name(config, monkeypatch):
    class Failing(FakeDataApi):
        def execute_statement(self, **kwargs):
            if "FROM confirm_booking" in kwargs["sql"]:
                raise RuntimeError("ERROR: hold_expired")
            return super().execute_statement(**kwargs)

    api = Failing({
        "FROM traveler_identity_bindings": [{"binding_id": "b"}], **BOOKED_PARTY, **SAVED_BUDGET,
    })
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_booking_args(), _context("MeridianHolds___confirm_booking"))
    assert result == {"error": "hold_expired"}
    assert api.tx == ["begin", "rollback"]


def test_confirm_booking_refuses_when_the_workload_has_no_grant(config, monkeypatch):
    api = FakeDataApi({"FROM traveler_identity_bindings": []})
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_booking_args(), _context("MeridianHolds___confirm_booking"))
    assert result["error"] == "traveler_not_authorized"
    assert api.tx == ["begin", "rollback"]
    assert not any("confirm_booking" in s for s in api.statements)


class RequestLedgerApi(FakeDataApi):
    """Answers the hold_requests lookup by request id, like the table does."""

    def __init__(self, ledger, rows_by_marker):
        super().__init__(rows_by_marker)
        self.ledger = ledger

    def execute_statement(self, **kwargs):
        if "FROM hold_requests hr" in kwargs["sql"]:
            self.statements.append(kwargs["sql"])
            params = {p["name"]: next(iter(p["value"].values())) for p in kwargs["parameters"]}
            self.parameters.append(params)
            rows = self.ledger.get(params["request_id"], [])
            return {"formattedRecords": json.dumps(rows)}
        return super().execute_statement(**kwargs)


HOLD_ROW = {
    "FROM create_courtesy_hold": [{
        "booking_id": "HLD-NEW", "status": "held", "replayed": False,
        "seats_available": 4, "seats_reserved": 2, "seats_remaining": 2,
    }],
    "FROM bookings": RECEIPT,
}


def _default_request_id():
    key = "concierge:conv-1|cty-002|7 nights|2"
    return "hrq_" + hashlib.sha256(key.encode()).hexdigest()[:12]


def _next_id(previous, booking_id):
    return "hrq_" + hashlib.sha256(f"{previous}|{booking_id}".encode()).hexdigest()[:12]


def _ledger_api(ledger):
    return RequestLedgerApi(ledger, {
        **CATALOG_PRICE,
        **SAVED_BUDGET,
        "FROM traveler_identity_bindings": [{"binding_id": "bind_1"}],
        "FROM journey_threads": [{"journey_id": "jrn_1"}],
        **HOLD_ROW,
    })


def _requested_id(api):
    return api.parameters[api.statements.index(holds.HOLD_SQL)]["request_id"]


def _hold(api, monkeypatch, args=None):
    monkeypatch.setattr(holds, "RDS", api)
    return holds.lambda_handler(
        args or _hold_args(), _context("MeridianHolds___create_courtesy_hold")
    )


@pytest.mark.parametrize(
    "stale",
    [
        {"status": "held", "lapsed": True},
        {"status": "released", "lapsed": False},
        {"status": "expired", "lapsed": True},
    ],
)
def test_holding_again_after_the_old_hold_lapsed_takes_a_new_request(
    config, monkeypatch, stale
):
    first = _default_request_id()
    api = _ledger_api({first: [{"booking_id": "HLD-OLD", **stale}]})
    result = _hold(api, monkeypatch)
    assert _requested_id(api) == _next_id(first, "HLD-OLD")
    assert result["hold"]["holdRequestId"] == _next_id(first, "HLD-OLD")
    lookup_params = api.parameters[api.statements.index(next(
        s for s in api.statements if "FROM hold_requests" in s
    ))]
    assert lookup_params["journey"] == "jrn_1"


def test_the_next_request_follows_every_dead_request_in_the_chain(config, monkeypatch):
    first = _default_request_id()
    second = _next_id(first, "HLD-1")
    third = _next_id(second, "HLD-2")
    api = _ledger_api({
        first: [{"booking_id": "HLD-1", "status": "held", "lapsed": True}],
        second: [{"booking_id": "HLD-2", "status": "released", "lapsed": False}],
    })
    _hold(api, monkeypatch)
    assert _requested_id(api) == third


def test_a_live_default_request_still_replays(config, monkeypatch):
    first = _default_request_id()
    api = _ledger_api({first: [{"booking_id": "HLD-1", "status": "held", "lapsed": False}]})
    _hold(api, monkeypatch)
    assert _requested_id(api) == first


def test_a_confirmed_default_request_still_replays(config, monkeypatch):
    first = _default_request_id()
    api = _ledger_api({first: [{"booking_id": "HLD-1", "status": "confirmed", "lapsed": True}]})
    _hold(api, monkeypatch)
    assert _requested_id(api) == first


def test_an_explicit_request_id_is_never_advanced(config, monkeypatch):
    api = _ledger_api({"hrq_mine": [{"booking_id": "HLD-1", "status": "held", "lapsed": True}]})
    args = {**_hold_args(), "holdRequestId": "hrq_mine", "bookingId": "HLD-1"}
    _hold(api, monkeypatch, args)
    assert _requested_id(api) == "hrq_mine"
    assert not any(s == holds.HOLD_REQUEST_SQL for s in api.statements), "no chain walk"


def test_the_request_lookup_runs_under_the_journey_lock_and_the_traveler_scope(
    config, monkeypatch
):
    api = _ledger_api({})
    _hold(api, monkeypatch)
    joined = "\n".join(api.statements)
    assert joined.index("pg_advisory_xact_lock") < joined.index("FROM hold_requests")
    assert joined.index("SET LOCAL ROLE") < joined.index("FROM hold_requests")
    assert joined.index("FROM hold_requests") < joined.index("FROM create_courtesy_hold")


def test_a_replayed_hold_past_its_expiry_reports_expired(config, monkeypatch):
    api = _ledger_api({})
    api.rows_by_marker["FROM create_courtesy_hold"] = [{
        "booking_id": "HLD-CHK", "status": "held", "replayed": True,
        "seats_available": None, "seats_reserved": None, "seats_remaining": None,
    }]
    api.rows_by_marker["FROM bookings"] = [{**RECEIPT[0], "lapsed": True}]
    args = {**_hold_args(), "holdRequestId": "hrq_mine"}
    result = _hold(api, monkeypatch, args)
    assert result["hold"]["replayed"] is True
    assert result["hold"]["status"] == "expired"


def test_a_replayed_hold_within_its_expiry_stays_held(config, monkeypatch):
    api = _ledger_api({})
    api.rows_by_marker["FROM create_courtesy_hold"] = [{
        "booking_id": "HLD-CHK", "status": "held", "replayed": True,
        "seats_available": None, "seats_reserved": None, "seats_remaining": None,
    }]
    api.rows_by_marker["FROM bookings"] = [{**RECEIPT[0], "lapsed": False}]
    result = _hold(api, monkeypatch, {**_hold_args(), "holdRequestId": "hrq_mine"})
    assert result["hold"]["status"] == "held"


def _template_hold_tools() -> list[dict]:
    template = json.loads(
        (TARGET.parents[1] / "agentcore.template.json").read_text(encoding="utf-8")
    )
    targets = template["agentCoreGateways"][0]["targets"]
    return next(t for t in targets if t["name"] == "MeridianHolds")["toolDefinitions"]


def test_the_template_and_the_tool_schema_file_define_the_same_tools():
    schema_file = json.loads((TARGET / "tool-schema.json").read_text(encoding="utf-8"))
    assert _template_hold_tools() == schema_file


@pytest.mark.parametrize("tools_source", ["template", "file"])
def test_hold_numeric_arguments_reject_zero_and_negative_values(tools_source):
    if tools_source == "template":
        tools = _template_hold_tools()
    else:
        tools = json.loads((TARGET / "tool-schema.json").read_text(encoding="utf-8"))
    hold = next(t for t in tools if t["name"] == "create_courtesy_hold")
    properties = hold["inputSchema"]["properties"]
    for name in ("unitPriceCents", "totalCents", "holdMinutes", "travelers"):
        assert properties[name]["minimum"] == 1, name


def _authorized_api(**extra):
    return FakeDataApi({
        "FROM traveler_identity_bindings": [{"binding_id": "bind_1"}],
        "FROM journey_threads": [{"journey_id": "jrn_1"}],
        **extra,
    })


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"unitPriceCents": 100, "totalCents": 200}, "hold_price_mismatch"),
        ({"unitPriceCents": 250001, "totalCents": 500002}, "hold_price_mismatch"),
        ({"totalCents": 100}, "hold_total_mismatch"),
        ({"totalCents": 500001}, "hold_total_mismatch"),
    ],
)
def test_hold_refuses_terms_that_do_not_match_the_catalog(config, monkeypatch, overrides, error):
    api = _authorized_api(**CATALOG_PRICE, **SAVED_BUDGET)
    monkeypatch.setattr(holds, "RDS", api)
    args = {**_hold_args(), **overrides}
    result = holds.lambda_handler(args, _context("MeridianHolds___create_courtesy_hold"))
    assert result == {"error": error}
    assert api.tx == ["begin", "rollback"]
    assert not any("FROM create_courtesy_hold" in s for s in api.statements)


def test_hold_reads_the_catalog_price_before_stepping_down_from_the_workload_role(
    config, monkeypatch
):
    api = _authorized_api(**CATALOG_PRICE, **SAVED_BUDGET)
    monkeypatch.setattr(holds, "RDS", api)
    holds.lambda_handler(_hold_args(), _context("MeridianHolds___create_courtesy_hold"))
    joined = "\n".join(api.statements)
    assert joined.index("traveler_access_audit") < joined.index("FROM trip_packages")
    assert joined.index("FROM trip_packages") < joined.index("SET LOCAL ROLE")
    price_params = api.parameters[api.statements.index(holds.PRICE_SQL)]
    assert price_params == {"package_id": "CTY-002"}


def test_hold_refuses_an_unknown_package_before_booking(config, monkeypatch):
    api = _authorized_api(**{"FROM trip_packages": []})
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_hold_args(), _context("MeridianHolds___create_courtesy_hold"))
    assert result == {"error": "invalid_package_inventory"}
    assert api.tx == ["begin", "rollback"]


def test_a_price_mismatch_is_not_revealed_to_an_unauthorized_caller(config, monkeypatch):
    api = FakeDataApi({"FROM traveler_identity_bindings": [], **CATALOG_PRICE})
    monkeypatch.setattr(holds, "RDS", api)
    args = {**_hold_args(), "unitPriceCents": 1, "totalCents": 2}
    result = holds.lambda_handler(args, _context("MeridianHolds___create_courtesy_hold"))
    assert result["error"] == "traveler_not_authorized"
    assert not any("FROM trip_packages" in s for s in api.statements)


# -------------------------------------------------- saved budget, enforced at the target


def _saved_cap(value):
    return {"FROM traveler_preferences": [
        {"preference_key": "budget_cap", "preference_value": value},
    ]}


@pytest.mark.parametrize("claimed_ceiling", [500000, 9_999_999_00])
def test_a_hold_above_the_saved_budget_is_refused_whatever_ceiling_the_caller_sends(
    config, monkeypatch, claimed_ceiling
):
    api = _authorized_api(**CATALOG_PRICE, **_saved_cap("Max $2,000 per person"), **HOLD_ROW)
    monkeypatch.setattr(holds, "RDS", api)
    args = {**_hold_args(), "budgetCeilingCents": claimed_ceiling}
    result = holds.lambda_handler(args, _context("MeridianHolds___create_courtesy_hold"))
    assert result == {"error": "budget_ceiling_exceeded"}
    assert api.tx == ["begin", "rollback"]
    assert not any("FROM create_courtesy_hold" in s for s in api.statements)
    joined = "\n".join(api.statements)
    assert joined.index("SET LOCAL ROLE meridian_app") < joined.index("FROM traveler_preferences")
    assert api.parameters[api.statements.index(holds.PREFERENCES_SQL)] == {
        "traveler": "trv_meridian_demo"
    }


def test_a_hold_within_the_saved_budget_for_the_party_runs(config, monkeypatch):
    api = _authorized_api(**CATALOG_PRICE, **_saved_cap("$2.5k per person"), **HOLD_ROW)
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_hold_args(), _context("MeridianHolds___create_courtesy_hold"))
    assert result["hold"]["bookingId"] == "HLD-NEW"


def test_without_a_saved_budget_the_whole_trip_default_applies(config, monkeypatch):
    api = _authorized_api(**CATALOG_PRICE, **HOLD_ROW)
    monkeypatch.setattr(holds, "RDS", api)
    monkeypatch.delenv(holds.DEFAULT_BUDGET_ENV, raising=False)
    refused = holds.lambda_handler(_hold_args(), _context("MeridianHolds___create_courtesy_hold"))
    assert refused == {"error": "budget_ceiling_exceeded"}, "$5,000 is over the $4,000 default"
    monkeypatch.setenv(holds.DEFAULT_BUDGET_ENV, "500000")
    held = holds.lambda_handler(_hold_args(), _context("MeridianHolds___create_courtesy_hold"))
    assert held["hold"]["bookingId"] == "HLD-NEW"


def test_a_confirmation_above_the_saved_budget_for_the_booked_party_is_refused(
    config, monkeypatch
):
    api = FakeDataApi({
        "FROM traveler_identity_bindings": [{"binding_id": "bind_1"}],
        **BOOKED_PARTY, **_saved_cap("$2,000 per person"),
    })
    monkeypatch.setattr(holds, "RDS", api)
    args = {**_booking_args(), "budgetCeilingCents": 9_999_999_00}
    result = holds.lambda_handler(args, _context("MeridianHolds___confirm_booking"))
    assert result == {"error": "budget_ceiling_exceeded"}
    assert not any("FROM confirm_booking" in s for s in api.statements)
    assert api.parameters[api.statements.index(holds.BOOKED_PARTY_SQL)] == {
        "b": "HLD-1", "t": "trv_meridian_demo"
    }


def test_an_already_confirmed_booking_replays_even_after_the_budget_was_lowered(
    config, monkeypatch
):
    api = FakeDataApi({
        "FROM traveler_identity_bindings": [{"binding_id": "bind_1"}],
        "FROM booking_lines bl": [{"travelers_count": 2, "status": "confirmed"}],
        **_saved_cap("$100 per person"),
        "FROM confirm_booking": [{
            "booking_id": "HLD-1", "status": "confirmed", "replayed": True,
            "confirmed_at": "2026-09-10 13:00:00+00",
            "hold_expires_at": "2026-09-11 00:00:00+00", "total_amount": "5000.00",
        }],
        "FROM bookings b": BOOKING_RECEIPT,
    })
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_booking_args(), _context("MeridianHolds___confirm_booking"))
    assert result["booking"]["replayed"] is True
    assert not any("FROM traveler_preferences" in s for s in api.statements)


def test_confirming_a_booking_this_traveler_does_not_hold_is_refused_by_name(config, monkeypatch):
    api = FakeDataApi({"FROM traveler_identity_bindings": [{"binding_id": "bind_1"}]})
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_booking_args(), _context("MeridianHolds___confirm_booking"))
    assert result == {"error": "booking_not_found"}
    assert not any("FROM confirm_booking" in s for s in api.statements)


FACT_SETS = [
    [],
    [{"key": "budget_cap", "value": "$3,200"}],
    [{"key": "per_person_range", "value": "Prefers $2k-3.5k per person"},
     {"key": "budget_cap", "value": "$3,200"}],
    [{"key": "per_person_range", "value": "Prefers $2k-3.5k per person"}],
    [{"key": "Budget", "value": "around 1.5k"}],
    [{"key": "budget_range", "value": "$90 to $2,750.50"}],
    [{"key": "budget_cap", "value": "no limit"}, {"key": "budget", "value": "$4,000"}],
    [{"key": "budget_cap", "value": "$3,000"}, {"key": "budget_cap", "value": "$1,000"}],
    [{"key": "home_airport", "value": "JFK"}],
]


@pytest.mark.parametrize("facts", FACT_SETS)
@pytest.mark.parametrize("travelers", [1, 2, 5])
def test_the_target_derives_the_ceiling_exactly_as_the_backend_does(
    monkeypatch, facts, travelers
):
    from backend.agents.budget import budget_ceiling_from_facts

    monkeypatch.delenv(holds.DEFAULT_BUDGET_ENV, raising=False)
    assert holds.saved_ceiling_cents(facts, travelers) == budget_ceiling_from_facts(
        facts, travelers
    )
    monkeypatch.setenv(holds.DEFAULT_BUDGET_ENV, "123400")
    assert holds.saved_ceiling_cents(facts, travelers) == budget_ceiling_from_facts(
        facts, travelers
    )


# ------------------------------------- reconcile a committed request before price checks


def _saved_request(price_cents=250000, quantity=2):
    terms = holds.normalize_terms("CTY-002", "7 nights", quantity, Decimal(price_cents) / 100)
    return {"SELECT fingerprint FROM hold_requests": [{"fingerprint": holds.fingerprint(terms)}]}


REPLAY_ROW = {
    "FROM create_courtesy_hold": [{
        "booking_id": "HLD-SAVED", "status": "held", "replayed": True,
        "seats_available": None, "seats_reserved": None, "seats_remaining": None,
    }],
    "FROM bookings": RECEIPT,
}


def test_a_retry_of_a_committed_hold_replays_after_the_catalog_price_changed(
    config, monkeypatch
):
    """The response to the first call was lost; the price moved before the retry."""
    api = _authorized_api(
        **{"FROM trip_packages": [{"unit_price_cents": 260000}]},
        **_saved_cap("$100 per person"),
        **_saved_request(),
        **REPLAY_ROW,
    )
    monkeypatch.setattr(holds, "RDS", api)
    args = {**_hold_args(), "holdRequestId": "hrq_saved", "bookingId": "HLD-SAVED"}
    result = holds.lambda_handler(args, _context("MeridianHolds___create_courtesy_hold"))
    assert result["hold"]["replayed"] is True
    assert result["hold"]["bookingId"] == "HLD-SAVED"
    assert result["hold"]["holdRequestId"] == "hrq_saved"
    assert result["hold"]["expiresAt"] == RECEIPT[0]["hold_expires_at"], "the original expiry"
    assert api.tx == ["begin", "commit"]
    lookup = api.parameters[api.statements.index(holds.REQUEST_FINGERPRINT_SQL)]
    assert lookup == {"journey": "jrn_1", "request_id": "hrq_saved"}
    assert not any("FROM traveler_preferences" in s for s in api.statements)


def test_altered_terms_under_a_saved_request_id_are_still_checked_and_refused(
    config, monkeypatch
):
    api = _authorized_api(
        **{"FROM trip_packages": [{"unit_price_cents": 260000}]},
        **SAVED_BUDGET,
        **_saved_request(quantity=1),
        **REPLAY_ROW,
    )
    monkeypatch.setattr(holds, "RDS", api)
    args = {**_hold_args(), "holdRequestId": "hrq_saved", "bookingId": "HLD-SAVED"}
    result = holds.lambda_handler(args, _context("MeridianHolds___create_courtesy_hold"))
    assert result == {"error": "hold_price_mismatch"}
    assert not any("FROM create_courtesy_hold" in s for s in api.statements)


def test_a_new_request_at_a_stale_price_is_still_refused(config, monkeypatch):
    api = _authorized_api(
        **{"FROM trip_packages": [{"unit_price_cents": 260000}]}, **SAVED_BUDGET, **HOLD_ROW
    )
    monkeypatch.setattr(holds, "RDS", api)
    args = {**_hold_args(), "holdRequestId": "hrq_fresh", "bookingId": "HLD-FRESH"}
    result = holds.lambda_handler(args, _context("MeridianHolds___create_courtesy_hold"))
    assert result == {"error": "hold_price_mismatch"}
