"""Read a workflow snapshot's JSON in windows the Data API can return.

The RDS Data API returns at most 64 KB per row and 1 MiB per response. A
snapshot read as one ``snapshot::TEXT`` value fails once it passes 64 KB, so
these reads return the text in 16,000-character windows. A window stays under
64 KB even when every character takes four bytes in UTF-8.
"""

from typing import Awaitable, Callable, Dict, List, Optional

WINDOW_CHARS = 16_000
MAX_SNAPSHOT_BYTES = 900_000

Query = Callable[[str, tuple], Awaitable[List[Dict]]]

_WINDOWS = f"""
SELECT w.part, substr(one.doc, w.part * {WINDOW_CHARS} + 1, {WINDOW_CHARS}) AS chunk
  FROM one, generate_series(0, (length(one.doc) - 1) / {WINDOW_CHARS}) AS w(part)
 ORDER BY w.part
"""
LATEST_BY_KEY_SQL = """
WITH one AS (
    SELECT snapshot::TEXT AS doc FROM workflow_snapshots
     WHERE storage_key = %s ORDER BY snapshot_seq DESC LIMIT 1
)""" + _WINDOWS
BY_SEQ_SQL = """
WITH one AS (
    SELECT snapshot::TEXT AS doc FROM workflow_snapshots WHERE snapshot_seq = %s::BIGINT
)""" + _WINDOWS


def _joined(rows: List[Dict]) -> Optional[str]:
    if not rows:
        return None
    for expected, row in enumerate(rows):
        if int(row["part"]) != expected:
            raise ValueError(
                f"snapshot window {expected} is missing; refusing a truncated snapshot"
            )
    return "".join(row["chunk"] for row in rows)


async def latest_snapshot_text(query: Query, storage_key: str) -> Optional[str]:
    """Return the newest snapshot's text for ``storage_key``, or None.

    Args:
        query: Runs one statement with parameters, in the caller's transaction.
        storage_key: The Strands storage key.

    Raises:
        ValueError: A window is missing from the result.
    """
    return _joined(await query(LATEST_BY_KEY_SQL, (storage_key,)))


async def snapshot_text(query: Query, seq: str) -> Optional[str]:
    """Return the text of snapshot ``seq``, or None.

    Args:
        query: Runs one statement with parameters, in the caller's transaction.
        seq: The ``snapshot_seq`` value, as text.

    Raises:
        ValueError: A window is missing from the result.
    """
    return _joined(await query(BY_SEQ_SQL, (seq,)))
