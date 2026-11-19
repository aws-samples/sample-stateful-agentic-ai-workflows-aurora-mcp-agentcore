"""A real Aurora cluster for the conformance suite.

The suite originally ran against an in-memory FakeCluster. A fake cluster
tells you the saver is self-consistent, which is not the claim being made:
the claim is that a checkpoint written here survives the process, and only
Aurora can answer that. Every test in this package writes to the live
cluster over the Data API under thread ids generated for this run and this worker
(``thread_ids.py``), and removes only those ids afterwards. A thread another run or an
earlier tutorial owns, even one named ``thread-1``, is never read or deleted.

Requires migration 007 and AWS credentials.
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest_asyncio

from backend.db.rds_data_client import get_rds_data_client
from examples.langgraph.tests.conformance.thread_ids import GENERATED_THREADS


async def _purge(client, thread_ids: tuple[str, ...]) -> None:
    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
        for thread_id in thread_ids:
            await client.execute(
                f"DELETE FROM {table} WHERE thread_id = %s", (thread_id,)
            )


@pytest_asyncio.fixture
async def cluster() -> AsyncIterator[object]:
    """The live cluster, with this run's generated threads cleared before and after."""
    client = get_rds_data_client()
    await _purge(client, GENERATED_THREADS)
    try:
        yield client
    finally:
        await _purge(client, GENERATED_THREADS)
