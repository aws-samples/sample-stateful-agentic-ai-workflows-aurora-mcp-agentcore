"""Journey evidence dates must not depend on the presenter's timezone."""
from datetime import datetime, timezone

import pytest

from backend.db.journey_document import _iso


@pytest.mark.parametrize("value", [
    "2026-09-20 00:07:17.625721",
    "2026-09-20 00:07:17.625721+00",
    datetime(2026, 9, 20, 0, 7, 17, 625721, tzinfo=timezone.utc),
])
def test_database_timestamp_is_explicit_utc(value):
    assert _iso(value) == "2026-09-20T00:07:17.625721+00:00"


def test_absent_timestamp_stays_absent():
    assert _iso(None) is None
