"""Live-test cleanup removes booking_action_audit rows before the rows they reference.

Migration 028 gives booking_action_audit foreign keys to hold_requests, bookings and journeys, so
a cleanup that deletes those first fails against the live cluster and leaves its rows behind.
These checks run offline: the helper's statement order, and that no live test or script still
carries its own delete list.
"""

import re
from pathlib import Path

import pytest

from tests import live_support

ROOT = Path(__file__).resolve().parents[1]
BOOKING_DELETE = re.compile(r"DELETE FROM (?:hold_requests|booking_lines|bookings)\b")
HELPER_USERS = (
    "tests/test_gateway_login_aurora.py",
    "tests/test_holds_release_aurora.py",
    "tests/test_hold_request_identity_aurora.py",
    "tests/test_journey_document.py",
    "tests/test_order_hold.py",
)


class Recorder:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple]] = []

    async def execute(self, sql, params=(), **_kwargs):
        self.statements.append((sql, tuple(params)))
        return []


async def test_a_booking_is_deleted_after_everything_that_references_it():
    client = Recorder()
    await live_support.delete_booking_rows(client, "HLD-1")
    tables = [re.search(r"DELETE FROM (\w+)", sql).group(1) for sql, _ in client.statements]
    assert tables == ["booking_action_audit", "hold_requests", "booking_lines", "bookings"]
    assert {params for _, params in client.statements} == {("HLD-1",)}


async def test_a_journeys_audit_and_hold_rows_go_before_the_journey():
    client = Recorder()
    await live_support.delete_journey_rows(client, "jrn_1")
    assert [sql.split(" WHERE ")[0] for sql, _ in client.statements] == [
        "DELETE FROM booking_action_audit", "DELETE FROM hold_requests"]
    assert {params for _, params in client.statements} == {("jrn_1",)}


@pytest.mark.parametrize("path", HELPER_USERS)
def test_a_live_test_removes_booking_rows_only_through_the_helper(path):
    source = (ROOT / path).read_text(encoding="utf-8")
    assert not BOOKING_DELETE.search(source), f"{path} keeps its own delete list"
    assert "delete_booking_rows" in source or "delete_journey_rows" in source


def test_the_demo_validator_deletes_audit_rows_first():
    source = (ROOT / "scripts/validate_demo.py").read_text(encoding="utf-8")
    assert '("booking_action_audit", "hold_requests", "booking_lines", "bookings")' in source
