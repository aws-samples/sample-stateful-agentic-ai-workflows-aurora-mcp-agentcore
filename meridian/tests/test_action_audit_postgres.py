"""The production target commits action evidence and booking rows in one real transaction."""

import json
import re
from types import SimpleNamespace

import psycopg
from psycopg.rows import dict_row
import pytest

from tests.test_holds_lambda import holds


class LocalDataApi:
    """Data API parameter/transaction adapter over a socket-only PostgreSQL connection."""

    def __init__(self, connection, *, fail_audit=False, lose_commit=False):
        self.connection = connection
        self.fail_audit = fail_audit
        self.lose_commit = lose_commit

    def begin_transaction(self, **_kwargs):
        self.connection.execute("BEGIN")
        return {"transactionId": "local-tx"}

    def execute_statement(self, *, sql, parameters, transactionId=None, **_kwargs):
        if self.fail_audit and "INSERT INTO booking_action_audit" in sql:
            raise RuntimeError("audit insert unavailable")
        values = {
            p["name"]: None if p["value"].get("isNull") else next(iter(p["value"].values()))
            for p in parameters
        }
        statement = re.sub(r"(?<!:):([a-z_]+)\b", r"%(\1)s", sql)
        cursor = self.connection.execute(statement, values)
        return {
            "formattedRecords": json.dumps(
                cursor.fetchall() if cursor.description else [], default=str
            )
        }

    def commit_transaction(self, **_kwargs):
        self.connection.commit()
        if self.lose_commit:
            raise TimeoutError("commit acknowledgement lost")
        return {"transactionStatus": "Transaction Committed"}

    def rollback_transaction(self, **_kwargs):
        self.connection.rollback()
        return {}


@pytest.fixture
def target(booking_db, monkeypatch):
    with booking_db.connect() as admin:
        admin.execute(
            "INSERT INTO traveler_identity_bindings "
            "(binding_id, identity_provider, subject_id, traveler_id) "
            "VALUES ('binding', 'aws_iam', 'workload', 't1')"
        )
    with psycopg.connect(**booking_db.config, row_factory=dict_row) as connection:
        connection.execute("SET SESSION AUTHORIZATION meridian_gateway")
        connection.commit()
        api = LocalDataApi(connection)
        monkeypatch.setattr(holds, "RDS", api)
        monkeypatch.setattr(holds, "CONFIG", holds.AuroraConfig("local", "local", "local"))
        monkeypatch.setattr(holds, "SUBJECT", ("aws_iam", "workload", "local-role"))
        monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
        yield api


def invoke(tool="create_courtesy_hold", **overrides):
    context = SimpleNamespace(
        aws_request_id="lambda-request",
        client_context=SimpleNamespace(
            custom={
                "bedrockAgentCoreToolName": f"MeridianHolds___{tool}",
                "bedrockAgentCoreAwsRequestId": "gateway-request",
            }
        ),
    )
    return holds.lambda_handler(
        {
            "travelerId": "t1",
            "packageId": "pkg",
            "duration": "2 nights",
            "travelers": 1,
            "unitPriceCents": 10000,
            "totalCents": 10000,
            "holdMinutes": 15,
            "travelerConfirmed": True,
            "budgetCeilingCents": 400000,
            "journeyRef": "conv-audit",
            "verifiedSubject": "subject-one",
            "authorizationRef": "binding-ref",
            **overrides,
        },
        context,
    )


def test_lost_commit_reply_keeps_the_booking_and_subject_audit_then_replays(target, booking_db):
    target.lose_commit = True
    assert invoke() == {"error": "hold_outcome_unknown"}
    with booking_db.connect() as check:
        original = check.execute("SELECT booking_id, hold_expires_at FROM bookings").fetchone()
        assert original
        assert check.execute(
            "SELECT verified_subject, gateway_request_id, lambda_request_id, "
            "authorization_ref, replayed FROM booking_action_audit"
        ).fetchall() == [
            ("subject-one", "gateway-request", "lambda-request", "binding-ref", False),
        ]
    target.lose_commit = False
    assert invoke()["hold"]["replayed"] is True
    with booking_db.connect() as check:
        assert check.execute("SELECT booking_id, hold_expires_at FROM bookings").fetchall() == [
            original
        ]
        assert check.execute(
            "SELECT replayed FROM booking_action_audit ORDER BY recorded_at"
        ).fetchall() == [
            (False,),
            (True,),
        ]


def test_a_failed_audit_insert_rolls_back_all_business_rows(target, booking_db):
    target.fail_audit = True
    assert invoke() == {"error": "hold_failed"}
    with booking_db.connect() as check:
        for table in ("bookings", "booking_lines", "hold_requests", "booking_action_audit"):
            assert check.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize(
    "tool, operation, status",
    [
        ("confirm_booking", "confirm", "confirmed"),
        ("release_hold", "release", "released"),
    ],
)
def test_action_and_replay_keep_atomic_caller_evidence(target, booking_db, tool, operation, status):
    booking = invoke()["hold"]["bookingId"]
    with booking_db.connect() as check:
        journey, request = check.execute(
            "SELECT journey_id, hold_request_id FROM hold_requests WHERE booking_id = %s",
            (booking,),
        ).fetchone()
    args = dict(bookingId=booking, journeyId=journey, holdRequestId=request)
    target.lose_commit = True
    result = invoke(tool, **args)
    assert result == {
        "error": "booking_outcome_unknown" if operation == "confirm" else "release_outcome_unknown"
    }
    target.lose_commit = False
    replay = invoke(tool, **args)
    receipt = replay["booking" if operation == "confirm" else "release"]
    assert receipt["status"] == status and receipt["replayed"] is True
    with booking_db.connect() as check:
        assert check.execute("SELECT count(*) FROM bookings").fetchone() == (1,)
        rows = check.execute(
            "SELECT operation, replayed FROM booking_action_audit ORDER BY recorded_at"
        ).fetchall()
        assert rows == [("hold", False), (operation, False), (operation, True)]


def test_action_audit_is_scoped_and_append_only_for_service_roles(target, booking_db):
    invoke()
    with booking_db.connect("t2", login="meridian_gateway") as other:
        assert other.execute("SELECT count(*) FROM booking_action_audit").fetchone() == (0,)
    for login in ("meridian_gateway", "meridian_backend", "meridian_workflow"):
        for verb in ("DELETE FROM", "UPDATE"):
            with booking_db.connect("t1", login=login) as connection:
                statement = f"{verb} booking_action_audit"
                if verb == "UPDATE":
                    statement += " SET replayed = true"
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    connection.execute(statement)
