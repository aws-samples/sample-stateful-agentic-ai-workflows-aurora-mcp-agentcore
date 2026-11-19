"""The journey document reports a lapsed held row as expired, by the workflow's own rule."""

from __future__ import annotations

import pytest

from backend.db import journey_document
from backend.db.journey_document import HOLD_SQL, _hold


def _row(status: str, lapsed: bool) -> dict:
    return {
        "hold_request_id": "hrq_1", "booking_id": "HLD-1", "execution_id": "exe_1",
        "status": status, "lapsed": lapsed, "hold_created_at": "2026-11-19 11:30:00+00",
        "hold_expires_at": "2026-11-19 11:45:00+00", "confirmed_at": None,
        "observed_at": "2026-11-19 12:00:00+00", "package_id": "p", "duration": "7 nights",
        "travelers_count": 2, "unit_price": "100.00", "total_amount": "200.00",
    }


async def _status(row: dict) -> str:
    async def q(sql, params):
        return [row]

    return (await _hold(q, "jrn_1", None))["status"]


@pytest.mark.parametrize(("stored", "lapsed", "reported"), [
    ("held", True, "expired"),
    ("held", False, "held"),
    ("confirmed", True, "confirmed"),
    ("released", True, "released"),
    ("expired", True, "expired"),
])
async def test_the_document_reports_the_effective_hold_status(stored, lapsed, reported):
    assert await _status(_row(stored, lapsed)) == reported


def test_the_hold_read_asks_auroras_clock_whether_the_hold_lapsed():
    assert "hold_expires_at <= CURRENT_TIMESTAMP" in HOLD_SQL
    assert journey_document.effective_hold_status("held", lapsed=True) == "expired"
