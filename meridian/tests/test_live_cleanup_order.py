"""Live-test cleanup keeps the evidence an audit export may already hold.

Migration 028 gives booking_action_audit foreign keys to hold_requests, bookings and journeys.
A live test gives its holds back through the governed release and keeps every row; only plain-SQL
fixtures that no action audit row references may be deleted, through a helper that refuses
otherwise. These checks run offline: the helper's behaviour, and a scan that no database test or
live_support code deletes from those tables on its own.
"""

import re
from pathlib import Path

import pytest

from tests import live_support

ROOT = Path(__file__).resolve().parents[1]
AUDIT_TABLES = ("booking_action_audit", "agent_audit_log", "traveler_access_audit")
PROTECTED_TABLES = (*AUDIT_TABLES, "bookings", "booking_lines", "hold_requests", "journeys")


class Recorder:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple]] = []

    async def execute(self, sql, params=(), **_kwargs):
        self.statements.append((sql, tuple(params)))
        return []


class AuditedRecorder(Recorder):
    """Reports booking_action_audit rows for the ids it is told are audited."""

    def __init__(self, audited=(), login="meridian_admin") -> None:
        super().__init__()
        self.audited, self.login = set(audited), login

    async def execute(self, sql, params=(), **_kwargs):
        await super().execute(sql, params)
        if "current_user" in sql:
            return [{"login": self.login}]
        if "FROM booking_action_audit" in sql:
            return [{"n": 1 if params[0] in self.audited else 0}]
        return []


def _deleted(client):
    return [re.search(r"DELETE FROM (\w+)", sql).group(1)
            for sql, _ in client.statements if sql.startswith("DELETE")]


async def test_a_synthetic_fixture_is_deleted_children_first_and_never_from_an_audit_table():
    client = AuditedRecorder()

    await live_support.delete_unaudited_fixture(client, booking_ids=["HLD-1"], journey_ids=["j1"])

    assert _deleted(client) == ["hold_requests", "booking_lines", "bookings",
                                "hold_requests", "journeys"]
    assert not set(_deleted(client)) & set(AUDIT_TABLES)


@pytest.mark.parametrize(("booking", "journey"), [("HLD-A", "j1"), ("HLD-1", "jrn-A")])
async def test_a_fixture_an_audit_row_references_is_refused_before_any_delete(booking, journey):
    client = AuditedRecorder(audited={"HLD-A", "jrn-A"})

    with pytest.raises(AssertionError, match="not deleting evidence"):
        await live_support.delete_unaudited_fixture(
            client, booking_ids=[booking], journey_ids=[journey])

    assert _deleted(client) == []


def database_tests() -> list[Path]:
    return [path for path in sorted((ROOT / "tests").glob("*.py"))
            if path.name != Path(__file__).name
            and "pytest.mark.database" in path.read_text(encoding="utf-8")]


FORBIDDEN_DELETE = re.compile(
    r"DELETE\s+FROM\s+(?:(?:" + "|".join(PROTECTED_TABLES) + r")(?!\w)|\{)(?![^\n]*WHERE false)")
HELPER = re.compile(
    r"async def delete_unaudited_fixture.*?(?=\n(?:async def |def |class |@|[A-Z_]+ =))", re.DOTALL)


def test_the_guard_finds_the_database_tests():
    names = {path.name for path in database_tests()}

    assert {"test_gateway_login_aurora.py", "test_order_hold.py",
            "test_migration_033_live.py"} <= names


def test_no_database_test_deletes_evidence_an_audit_export_could_hold():
    offenders = [f"{path.name}: {line.strip()}"
                 for path in database_tests()
                 for line in path.read_text(encoding="utf-8").splitlines()
                 if FORBIDDEN_DELETE.search(line)]

    assert offenders == []


def test_live_support_deletes_only_inside_the_refusing_helper():
    source = (ROOT / "tests/live_support.py").read_text(encoding="utf-8")
    outside = HELPER.sub("", source, count=1)

    assert "delete_unaudited_fixture" in source and HELPER.search(source)
    assert not FORBIDDEN_DELETE.search(outside)
    assert "delete_booking_rows" not in source and "delete_journey_rows" not in source


def test_no_database_test_calls_the_proofs_purge_or_the_old_helpers():
    offenders = [path.name for path in database_tests()
                 if re.search(r"\b_purge\b|delete_booking_rows|delete_journey_rows",
                              path.read_text(encoding="utf-8"))]

    assert offenders == []


def test_the_demo_validator_deletes_audit_rows_first():
    source = (ROOT / "scripts/validate_demo.py").read_text(encoding="utf-8")
    assert '("booking_action_audit", "hold_requests", "booking_lines", "bookings")' in source


def test_the_local_runtime_live_test_gives_its_hold_back_and_deletes_no_row():
    source = (ROOT / "tests/test_workflow_runtime_local_aurora.py").read_text(encoding="utf-8")

    assert "_purge" not in source and "DELETE FROM" not in source
    assert "release_journey" in source and "held_holds" in source


@pytest.mark.parametrize("login", ["meridian_backend", "meridian_app", "meridian_gateway",
                                   "meridian_backend_clone"])
async def test_a_restricted_login_cannot_run_the_fixture_cleanup(login):
    client = AuditedRecorder(login=login)

    with pytest.raises(AssertionError, match="needs the master login"):
        await live_support.delete_unaudited_fixture(client, booking_ids=["HLD-1"])

    assert _deleted(client) == []


async def test_the_master_check_runs_once_per_client():
    client = AuditedRecorder()

    await live_support.delete_unaudited_fixture(client, booking_ids=["HLD-1"])
    await live_support.delete_unaudited_fixture(client, booking_ids=["HLD-2"])

    assert sum("current_user" in sql for sql, _ in client.statements) == 1
