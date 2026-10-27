"""Transactions for database tests that write evidence: always rolled back, never committed.

``agent_audit_log`` and ``traveler_access_audit`` are sealed: no role may update or delete a row.
A test that committed a row there would leave it in the evidence for good, so a test writes and
reads back inside one of these transactions and the rows vanish with the rollback.
"""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


class _RollBack(Exception):
    """Ends every scoped block, because scoped_session commits a block that ends normally."""


@asynccontextmanager
async def uncommitted_scope(client: Any, **scope: Any) -> AsyncIterator[str]:
    """A traveler-scoped transaction, RLS pinned and the app role assumed, that is rolled back.

    scoped_session rolls back on any exception, so the block always ends by raising one. A
    failure inside the block is rolled back the same way and reaches the test unchanged.

    Args:
        client: The RDS Data API client.
        **scope: The arguments for ``client.scoped_session``.

    Yields:
        The transaction id.
    """
    try:
        async with client.scoped_session(**scope) as tx:
            yield tx
            raise _RollBack
    except _RollBack:
        pass


@asynccontextmanager
async def uncommitted_transaction(client: Any) -> AsyncIterator[str]:
    """A transaction on the client's own login that is rolled back whatever happens.

    Args:
        client: The RDS Data API client.

    Yields:
        The transaction id.
    """
    tx = client.begin_transaction()
    try:
        yield tx
    finally:
        client.rollback_transaction(tx)
