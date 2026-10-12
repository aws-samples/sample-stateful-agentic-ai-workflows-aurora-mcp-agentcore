"""Window arithmetic for reading and writing BYTEA over the RDS Data API.

The Data API returns at most 64 KB per row. Values are therefore read in
windows through SQL ``substring`` and written by appending, so neither a
response row nor a request body approaches its limit.
"""

# Half the 64 KB row ceiling. base64 inflates bytes by 4/3 on the wire, so a
# 32 KiB window arrives as roughly 43.7 KB and leaves room for the JSON
# envelope and column metadata.
MAX_ROW_BYTES = 32768


def window_offsets(total: int, window: int = MAX_ROW_BYTES) -> list[tuple[int, int]]:
    """Return 1-based (offset, length) pairs covering ``total`` bytes.

    Args:
        total: Size of the stored value, from ``octet_length(blob)``.
        window: Maximum bytes per window.

    Returns:
        Pairs for SQL ``substring(blob FROM offset FOR length)``, in order.
        PostgreSQL's ``substring`` is 1-based, so the first offset is 1.

    Raises:
        ValueError: If ``window`` is not positive.
    """
    if window <= 0:
        raise ValueError("window must be positive")
    offsets: list[tuple[int, int]] = []
    position = 0
    while position < total:
        length = min(window, total - position)
        offsets.append((position + 1, length))
        position += length
    return offsets


def split_for_write(payload: bytes, window: int = MAX_ROW_BYTES) -> list[bytes]:
    """Split ``payload`` into append-sized segments.

    Args:
        payload: The serialized value to store.
        window: Maximum bytes per segment.

    Returns:
        Segments in order. An empty payload yields one empty segment so the
        caller always has an insert to perform.

    Raises:
        ValueError: If ``window`` is not positive.
    """
    if window <= 0:
        raise ValueError("window must be positive")
    if not payload:
        return [b""]
    return [payload[i:i + window] for i in range(0, len(payload), window)]
