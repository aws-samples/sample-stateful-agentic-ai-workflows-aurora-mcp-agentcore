"""Snapshot text comes back in windows, each under the Data API's 64 KB row limit."""

import pytest

from backend.db import snapshot_text
from backend.db.snapshot_text import WINDOW_CHARS, latest_snapshot_text, snapshot_text as by_seq


def _windows(doc: str):
    return [{"part": i, "chunk": doc[i * WINDOW_CHARS:(i + 1) * WINDOW_CHARS]}
            for i in range((len(doc) - 1) // WINDOW_CHARS + 1)]


async def test_windows_join_back_into_the_snapshot():
    doc = '{"data": {"state": {"padding": "' + "é" * 40_000 + '"}}}'
    seen = []

    async def query(sql, params):
        seen.append((sql, params))
        return _windows(doc)

    assert await latest_snapshot_text(query, "session/t/scopes/x") == doc
    assert seen[0][1] == ("session/t/scopes/x",)
    assert f"{WINDOW_CHARS}" in seen[0][0]


async def test_no_row_reads_as_none():
    async def query(sql, params):
        return []

    assert await by_seq(query, "42") is None


async def test_a_missing_window_fails_instead_of_returning_a_truncated_snapshot():
    async def query(sql, params):
        return [{"part": 0, "chunk": "{"}, {"part": 2, "chunk": "}"}]

    with pytest.raises(ValueError, match="window 1"):
        await by_seq(query, "42")


def test_a_window_stays_under_the_row_limit_at_four_bytes_a_character():
    assert snapshot_text.WINDOW_CHARS * 4 < 64 * 1024
