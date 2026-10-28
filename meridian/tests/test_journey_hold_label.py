"""The hold section's label counts the rows it read; it does not assert a fixed number."""

from backend.db.journey_document import _hold, _hold_label


def _row(request_id):
    return {
        "status": "held", "hold_request_id": request_id, "booking_id": f"b-{request_id}",
        "execution_id": "exec-1", "hold_created_at": "2026-09-06 12:00:00+00",
        "hold_expires_at": "2026-09-06 12:15:00+00", "observed_at": "2026-09-06 12:05:00+00",
        "package_id": "PKG-1", "duration": "2 nights", "travelers_count": 2,
        "unit_price": "100.00", "total_amount": "200.00",
    }


def _query(rows):
    async def query(sql, params):
        return rows

    return query


def test_the_label_names_the_count_and_its_plural():
    assert _hold_label(1) == "1 hold record for this journey"
    assert _hold_label(2) == "2 hold records for this journey"


async def test_one_row_is_labelled_one_record():
    hold = await _hold(_query([_row("r1")]), "jrn", {})
    assert (hold["label"], hold["hold_records"]) == ("1 hold record for this journey", 1)


async def test_two_requests_are_never_labelled_as_one_hold():
    hold = await _hold(_query([_row("r1"), _row("r2")]), "jrn", {})
    assert (hold["label"], hold["hold_records"]) == ("2 hold records for this journey", 2)


async def test_one_hold_with_two_booking_lines_is_still_one_hold():
    hold = await _hold(_query([_row("r1"), _row("r1")]), "jrn", {})
    assert (hold["label"], hold["hold_records"]) == ("1 hold record for this journey", 1)
