"""Migration 032 moves the booking write functions to an owner that row-level security binds."""

import pathlib

from scripts.init_aurora_schema import split_sql

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts" / "migrations" / "032_booking_function_owner.sql"
)
OWNER = "meridian_booking_owner"
FUNCTIONS = (
    "create_courtesy_hold(text, text, text, text, text, text, text, integer, numeric, numeric, "
    "timestamptz)",
    "confirm_booking(text, text, numeric)",
    "release_courtesy_hold(text, text, text)",
)
TABLE_GRANTS = {
    "grant select on trip_packages to meridian_booking_owner",
    "grant select on journeys to meridian_booking_owner",
    "grant select, insert on hold_requests to meridian_booking_owner",
    "grant select, insert, update (status, confirmed_at) on bookings to meridian_booking_owner",
    "grant select, insert on booking_lines to meridian_booking_owner",
}


def _statements() -> list[str]:
    code = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.lstrip().startswith("--")
    )
    flat = (" ".join(s.split()).lower().rstrip(";") for s in split_sql(code))
    return [s.replace("( ", "(").replace(" )", ")") for s in flat]


def test_the_owner_role_cannot_log_in_or_bypass_rls():
    text = " ".join(MIGRATION.read_text().split()).lower()
    create = f"create role {OWNER} nologin nobypassrls noinherit nocreatedb nocreaterole"
    assert text.count(f"create role {OWNER}") == 1 and create in text
    for attribute in ("rolcanlogin", "rolsuper", "rolbypassrls", "rolinherit"):
        assert attribute in text.split("elsif exists", 1)[1].split("raise exception", 1)[0]
    assert " bypassrls" not in text and " superuser" not in text


def test_each_write_function_changes_owner():
    owners = {s for s in _statements() if s.startswith("alter function")}
    assert owners == {f"alter function {f} owner to {OWNER}" for f in FUNCTIONS}


def test_the_owner_gets_only_the_table_privileges_the_functions_use():
    grants = {s for s in _statements() if s.startswith("grant") and " on " in s
              and "schema" not in s and "sequence" not in s and "function" not in s}
    assert grants == TABLE_GRANTS


def test_reads_across_travelers_are_explicit_select_policies():
    policies = [s for s in _statements() if s.startswith("create policy")]
    assert sorted(policies) == [
        f"create policy booking_lines_owner_read on booking_lines for select to {OWNER} "
        "using (true)",
        f"create policy bookings_owner_read on bookings for select to {OWNER} using (true)",
    ]


def test_schema_create_is_granted_only_for_the_owner_change_and_revoked_after():
    statements = _statements()
    grant = statements.index(f"grant create on schema public to {OWNER}")
    revoke = statements.index(f"revoke create on schema public from {OWNER}")
    alters = [i for i, s in enumerate(statements) if s.startswith("alter function")]
    assert grant < min(alters) and max(alters) < revoke


def test_the_master_inherits_the_owner_so_later_migrations_can_change_the_functions():
    text = " ".join(MIGRATION.read_text().split()).lower()
    assert f"grant {OWNER} to %i with inherit true, set true" in text
    assert "current_user" in text


def test_no_blanket_grants():
    assert not any("grant all" in s or " delete" in s for s in _statements())
