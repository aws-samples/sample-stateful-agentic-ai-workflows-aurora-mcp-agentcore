"""Exercise inventory interleavings against real PostgreSQL in an isolated local cluster."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

import psycopg
import pytest


HOLD = "SELECT * FROM create_courtesy_hold(%s, %s, %s, %s, %s, 'pkg', '2 nights', 1, 100, 100, %s)"


def hold(connection, number, expiry):
    return connection.execute(
        HOLD, (f"b{number}", f"t{number}", f"j{number}", f"r{number}", f"fp{number}", expiry)
    ).fetchone()


def seed_hold(database):
    with database.connect("t1") as connection:
        expiry = connection.execute("SELECT clock_timestamp() + interval '1 hour'").fetchone()[0]
        hold(connection, 1, expiry)


def test_a_transaction_started_before_expiry_cannot_confirm_reallocated_inventory(booking_db):
    seed_hold(booking_db)
    with booking_db.connect("t1") as confirmation:
        confirmation.execute("SELECT CURRENT_TIMESTAMP")
        with booking_db.connect() as admin:
            admin.execute(
                "UPDATE bookings SET hold_expires_at = clock_timestamp() WHERE booking_id = 'b1'"
            )
        with booking_db.connect("t2") as allocation:
            expiry = allocation.execute("SELECT clock_timestamp() + interval '1 hour'").fetchone()[
                0
            ]
            hold(allocation, 2, expiry)
        with pytest.raises(psycopg.errors.RaiseException, match="hold_expired"):
            confirmation.execute("SELECT * FROM confirm_booking('b1', 't1', 100)")
    with booking_db.connect() as check:
        assert check.execute(
            "SELECT booking_id, status FROM bookings ORDER BY booking_id"
        ).fetchall() == [
            ("b1", "held"),
            ("b2", "held"),
        ]


def test_allocation_waits_for_an_uncommitted_confirmation_then_counts_it(booking_db):
    seed_hold(booking_db)
    with booking_db.connect() as admin:
        admin.execute(
            "UPDATE bookings SET hold_expires_at = clock_timestamp() + interval '1 second' "
            "WHERE booking_id = 'b1'"
        )

    def allocate():
        with booking_db.connect("t2") as connection:
            expiry = connection.execute("SELECT clock_timestamp() + interval '1 hour'").fetchone()[
                0
            ]
            return hold(connection, 2, expiry)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with booking_db.connect("t1") as confirmation:
            confirmation.execute("SELECT * FROM confirm_booking('b1', 't1', 100)")
            confirmation.execute("SELECT pg_sleep(1.05)")
            pending = pool.submit(allocate)
            try:
                with pytest.raises(FutureTimeout):
                    pending.result(timeout=0.2)
            finally:
                confirmation.commit()
        with pytest.raises(psycopg.errors.RaiseException, match="insufficient_inventory"):
            pending.result(timeout=5)
    with booking_db.connect() as check:
        assert check.execute("SELECT booking_id, status FROM bookings").fetchall() == [
            ("b1", "confirmed"),
        ]


@pytest.mark.parametrize("operation", ["hold", "confirm"])
def test_business_functions_reject_missing_agent_scope(booking_db, operation):
    seed_hold(booking_db)
    with booking_db.connect("t1", agent=None) as connection:
        with pytest.raises(psycopg.errors.RaiseException, match="booking_agent_not_authorized"):
            if operation == "confirm":
                connection.execute("SELECT * FROM confirm_booking('b1', 't1', 100)")
            else:
                hold(connection, 1, None)


@pytest.mark.parametrize("login", ["meridian_backend", "meridian_workflow", "meridian_gateway"])
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE bookings SET status = 'confirmed' WHERE booking_id = 'b1'",
        "UPDATE booking_lines SET travelers_count = 10 WHERE booking_id = 'b1'",
        "UPDATE hold_requests SET fingerprint = 'forged' WHERE booking_id = 'b1'",
    ],
)
def test_service_roles_cannot_mutate_business_rows_directly(booking_db, login, statement):
    seed_hold(booking_db)
    with booking_db.connect("t1", login=login) as connection:
        connection.execute("SET LOCAL ROLE meridian_app")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(statement)


@pytest.mark.parametrize("login", ["meridian_backend", "meridian_workflow"])
def test_only_the_gateway_login_can_execute_business_functions(booking_db, login):
    seed_hold(booking_db)
    with booking_db.connect("t1", login=login) as connection:
        connection.execute("SET LOCAL ROLE meridian_app")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT * FROM confirm_booking('b1', 't1', 100)")


def test_the_gateway_can_confirm_and_replay_but_cannot_release_a_confirmation(booking_db):
    seed_hold(booking_db)
    with booking_db.connect("t1", login="meridian_gateway") as connection:
        row = connection.execute("SELECT * FROM confirm_booking('b1', 't1', 100)").fetchone()
        assert row[1:3] == ("confirmed", False)
    with booking_db.connect("t1", login="meridian_gateway") as connection:
        replay = connection.execute("SELECT * FROM confirm_booking('b1', 't1', 100)").fetchone()
        assert replay[1:3] == ("confirmed", True)
        assert replay[3:] == row[3:]
        with pytest.raises(psycopg.errors.RaiseException, match="booking_not_held"):
            connection.execute("SELECT * FROM release_courtesy_hold('j1', 'r1', 't1')")


def test_release_is_scoped_and_replays_the_original_result(booking_db):
    seed_hold(booking_db)
    with booking_db.connect("t2", login="meridian_gateway") as connection:
        with pytest.raises(psycopg.errors.RaiseException, match="hold_not_found"):
            connection.execute("SELECT * FROM release_courtesy_hold('j1', 'r1', 't2')")
    with booking_db.connect("t1", login="meridian_gateway") as connection:
        row = connection.execute("SELECT * FROM release_courtesy_hold('j1', 'r1', 't1')").fetchone()
        assert row == ("b1", False)
    with booking_db.connect("t1", login="meridian_gateway") as connection:
        assert connection.execute(
            "SELECT * FROM release_courtesy_hold('j1', 'r1', 't1')"
        ).fetchone() == (
            "b1",
            True,
        )
    with booking_db.connect() as check:
        assert check.execute("SELECT status FROM bookings WHERE booking_id = 'b1'").fetchone() == (
            "released",
        )
