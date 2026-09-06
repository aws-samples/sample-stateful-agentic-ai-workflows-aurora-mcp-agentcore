"""Window arithmetic for Data API blob reads.

The Data API caps a returned row at 64 KB. Windows are half that so the row
still fits once base64 expansion and JSON envelope overhead are counted:
base64 inflates by 4/3, so 32 KiB of bytes becomes roughly 43.7 KB on the
wire, comfortably inside the limit.
"""

import pytest

from backend.db.blob_windows import MAX_ROW_BYTES, split_for_write, window_offsets


def test_window_is_half_the_row_limit() -> None:
    assert MAX_ROW_BYTES == 32768
    assert MAX_ROW_BYTES * 4 / 3 < 64 * 1024


def test_empty_payload_has_no_windows() -> None:
    assert window_offsets(0) == []


def test_single_window_when_under_limit() -> None:
    assert window_offsets(100) == [(1, 100)]


def test_offsets_are_one_based_for_sql_substring() -> None:
    assert window_offsets(MAX_ROW_BYTES + 10)[0] == (1, MAX_ROW_BYTES)


def test_second_window_starts_after_the_first() -> None:
    offsets = window_offsets(MAX_ROW_BYTES + 10)
    assert offsets[1] == (MAX_ROW_BYTES + 1, 10)


def test_exact_multiple_produces_no_empty_trailing_window() -> None:
    assert len(window_offsets(MAX_ROW_BYTES * 3)) == 3


def test_split_reassembles_to_the_original() -> None:
    payload = bytes(range(256)) * 400  # 102400 bytes, spans four windows
    assert b"".join(split_for_write(payload)) == payload


def test_split_respects_the_window_size() -> None:
    payload = b"x" * (MAX_ROW_BYTES * 2 + 5)
    parts = split_for_write(payload)
    assert [len(p) for p in parts] == [MAX_ROW_BYTES, MAX_ROW_BYTES, 5]


def test_split_of_empty_payload_is_one_empty_part() -> None:
    assert split_for_write(b"") == [b""]


def test_rejects_non_positive_window() -> None:
    with pytest.raises(ValueError, match="window must be positive"):
        window_offsets(10, window=0)
