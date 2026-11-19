"""Thread ids for one run of the conformance suite.

The upstream suite names its threads ``thread-1``, ``thread-2`` and ``t1``. Those names are
common: an earlier tutorial, another participant or another worker of this run may own the same
rows in a shared cluster. Each process therefore builds its own ids from a random run token, and
the fixture in ``conftest.py`` clears only those ids.
"""

from __future__ import annotations

import os
import uuid

UPSTREAM_NAMES = ("thread-1", "thread-2", "t1")


def new_run_token(worker: str | None = None) -> str:
    """A token unique to this run and this pytest-xdist worker."""
    worker = worker if worker is not None else os.environ.get("PYTEST_XDIST_WORKER", "main")
    return f"conf-{uuid.uuid4().hex[:12]}-{worker}"


def thread_ids(token: str) -> dict[str, str]:
    """Map each upstream thread name to this run's id for it."""
    return {name: f"{token}-{name}" for name in UPSTREAM_NAMES}


RUN_TOKEN = new_run_token()
IDS = thread_ids(RUN_TOKEN)
THREAD_1, THREAD_2, THREAD_T1 = IDS["thread-1"], IDS["thread-2"], IDS["t1"]
GENERATED_THREADS = tuple(IDS.values())
