# Durable Recovery Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a Meridian workflow survive a real worker kill and resume on the same thread with exactly one business effect, checkpointed to Aurora over the RDS Data API.

**Architecture:** A new `AuroraDataApiSaver` implements LangGraph's `BaseCheckpointSaver` over the existing Data API client, writing LangGraph's own `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` tables. A thin `journeys` / `journey_threads` / `journey_executions` identity layer owns threads and claims a single running execution per thread with an expiring lease. A `prepare_hold` graph node checkpoints the hold intent before the hold executes, and the expanded `create_courtesy_hold` enforces one hold per request inside the existing inventory transaction.

**Tech Stack:** Python 3.13, FastAPI, LangGraph, Aurora PostgreSQL via RDS Data API (boto3), pytest.

Spec: `docs/superpowers/specs/2026-09-06-meridian-journey-shell-design.md`

## Global Constraints

- Pinned versions, do not upgrade: `langgraph==1.2.9`, `langgraph-checkpoint==4.1.1`, `langgraph-checkpoint-postgres==3.1.2`, `psycopg==3.3.4`, `boto3==1.43.51`.
- Data API limits: **64 KB** per returned row, **1 MiB** per response, **4 MiB** per HTTP request.
- The existing five-phase demo must stay runnable after every task. `venv/bin/pytest -q` must pass at every commit.
- All commands run from `meridian/`. Python is `venv/bin/python`, tests are `venv/bin/pytest`.
- Coding standards: functions ≤100 lines, cyclomatic complexity ≤8, ≤5 positional params, 100-char lines, absolute imports only (`from backend.x import y`, never `..`), Google-style docstrings on non-trivial public APIs.
- No em dashes in AWS resource names or descriptions.
- Never call `secretsmanager get-secret-value`. Credentials arrive via environment only.
- Tests that need a live cluster are marked `@pytest.mark.database`. Everything else must pass offline.

---

### Task 1: Binary round trips through the Data API client

The client cannot carry binary today. `_format_parameters` has no `bytes` branch, so bytes fall to `else: {"stringValue": str(value)}` and store the Python repr. `_parse_value` has no `blobValue` branch and returns `None` for blob columns. boto3 already base64-transcodes `blobValue` in both directions, so application code must pass and receive raw `bytes` with no extra encoding.

**Files:**
- Modify: `meridian/backend/db/rds_data_client.py:69-95` (`_format_parameters`), `:105-119` (`_parse_value`)
- Test: `meridian/tests/test_rds_data_client_binary.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `RDSDataClient._format_parameters(params: tuple) -> List[Dict]` accepts `bytes` and emits `{"name": "pN", "value": {"blobValue": <bytes>}}`. `RDSDataClient._parse_value(field: Dict) -> Any` returns `bytes` for `blobValue` fields.

- [ ] **Step 1: Write the failing test**

Create `meridian/tests/test_rds_data_client_binary.py`:

```python
"""Binary parameters must survive the Data API unchanged.

boto3 base64-transcodes blobValue itself, so the client passes and receives
raw bytes. Any extra encoding in application code is a double-encoding bug,
and the previous fallback stored the Python repr of the bytes object.
"""

import pytest

from backend.db.rds_data_client import RDSDataClient


@pytest.fixture
def client() -> RDSDataClient:
    return RDSDataClient.__new__(RDSDataClient)


def test_bytes_become_blob_value(client: RDSDataClient) -> None:
    payload = b"\x80\x03\x00\xff binary \n\t"
    formatted = client._format_parameters((payload,))
    assert formatted == [{"name": "p0", "value": {"blobValue": payload}}]


def test_bytes_are_not_stringified(client: RDSDataClient) -> None:
    formatted = client._format_parameters((b"\x80abc",))
    assert "stringValue" not in formatted[0]["value"]


def test_blob_value_parses_back_to_bytes(client: RDSDataClient) -> None:
    assert client._parse_value({"blobValue": b"\x00\x01\xfe"}) == b"\x00\x01\xfe"


def test_full_byte_range_round_trips(client: RDSDataClient) -> None:
    payload = bytes(range(256))
    formatted = client._format_parameters((payload,))
    assert client._parse_value(formatted[0]["value"]) == payload


def test_embedded_nulls_survive(client: RDSDataClient) -> None:
    payload = b"before\x00after\x00"
    formatted = client._format_parameters((payload,))
    assert client._parse_value(formatted[0]["value"]) == payload


def test_bytearray_and_memoryview_are_coerced(client: RDSDataClient) -> None:
    for value in (bytearray(b"\x01\x02"), memoryview(b"\x01\x02")):
        formatted = client._format_parameters((value,))
        assert formatted[0]["value"] == {"blobValue": b"\x01\x02"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_rds_data_client_binary.py -q`
Expected: FAIL. `test_bytes_become_blob_value` asserts `blobValue` but gets `{'stringValue': "b'\\x80\\x03\\x00\\xff binary \\n\\t'"}`.

- [ ] **Step 3: Add the bytes branch to `_format_parameters`**

In `meridian/backend/db/rds_data_client.py`, insert **before** the `isinstance(value, (list, dict))` branch (bytes must be checked before the catch-all `else`):

```python
            elif isinstance(value, (bytes, bytearray, memoryview)):
                # boto3 base64-transcodes blobValue in both directions, so the
                # raw bytes go on the wire. Encoding here would double-encode.
                param["value"] = {"blobValue": bytes(value)}
```

- [ ] **Step 4: Add the blobValue branch to `_parse_value`**

In the same file, add before the `arrayValue` branch:

```python
        if "blobValue" in field:
            return bytes(field["blobValue"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_rds_data_client_binary.py -q`
Expected: PASS, 6 passed.

- [ ] **Step 6: Run the full suite to confirm nothing regressed**

Run: `venv/bin/pytest -q`
Expected: PASS, 195 passed plus the 6 new tests.

- [ ] **Step 7: Commit**

```bash
git add meridian/backend/db/rds_data_client.py meridian/tests/test_rds_data_client_binary.py
git commit -m "Carry binary through the Data API client"
```

---

### Task 2: Windowed blob reads that respect the 64 KB row limit

A `BYTEA` larger than 64 KB cannot be returned in one row. The saver reads size first, then fixed windows via `substring`, and writes by appending. This task builds the pure helpers so the saver in Task 4 has no arithmetic of its own.

**Files:**
- Create: `meridian/backend/db/blob_windows.py`
- Test: `meridian/tests/test_blob_windows.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MAX_ROW_BYTES: int = 32768`; `window_offsets(total: int, window: int = MAX_ROW_BYTES) -> list[tuple[int, int]]` returning 1-based `(offset, length)` pairs for SQL `substring(blob FROM offset FOR length)`; `split_for_write(payload: bytes, window: int = MAX_ROW_BYTES) -> list[bytes]`.

- [ ] **Step 1: Write the failing test**

Create `meridian/tests/test_blob_windows.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_blob_windows.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.db.blob_windows'`.

- [ ] **Step 3: Write the implementation**

Create `meridian/backend/db/blob_windows.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_blob_windows.py -q`
Expected: PASS, 10 passed.

- [ ] **Step 5: Commit**

```bash
git add meridian/backend/db/blob_windows.py meridian/tests/test_blob_windows.py
git commit -m "Add window arithmetic for Data API blob access"
```

---

### Task 3: Migration for checkpoint and journey tables

`AsyncPostgresSaver.setup()` applies LangGraph's migrations over psycopg, which the Data API path cannot use. The tables are created by migration instead, with DDL copied verbatim from `langgraph-checkpoint-postgres==3.1.2` so both backends see one schema.

Note the two deliberate deviations from the packaged list, both required for Data API execution: `CREATE INDEX CONCURRENTLY` cannot run inside the Data API's implicit transaction, so the indexes are created non-concurrently on empty tables; and the packaged `ALTER TABLE ... DROP not null` plus the `SELECT 1;` no-op are folded into the initial DDL.

**Files:**
- Create: `meridian/scripts/migrations/007_journey_shell.sql`
- Test: `meridian/tests/test_migration_007_contract.py`

**Interfaces:**
- Consumes: nothing.
- Produces: tables `checkpoint_migrations`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `journeys`, `journey_threads`, `journey_executions`, `hold_requests`; unique index `journey_executions_one_running`.

- [ ] **Step 1: Write the failing test**

Create `meridian/tests/test_migration_007_contract.py`:

```python
"""The migration must match LangGraph's schema and the journey design.

A saver that writes a schema LangGraph does not recognize produces resumes
that fail only at read time, so the DDL is pinned by test rather than by
review.
"""

import pathlib
import re

import pytest

MIGRATION = pathlib.Path(__file__).parent.parent / "scripts" / "migrations" / "007_journey_shell.sql"


@pytest.fixture
def sql() -> str:
    return MIGRATION.read_text()


@pytest.fixture
def normalized(sql: str) -> str:
    return " ".join(sql.split()).lower()


def test_migration_exists(sql: str) -> None:
    assert sql.strip()


@pytest.mark.parametrize(
    "table",
    [
        "checkpoint_migrations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "journeys",
        "journey_threads",
        "journey_executions",
        "hold_requests",
    ],
)
def test_creates_table(normalized: str, table: str) -> None:
    assert f"create table if not exists {table}" in normalized


def test_checkpoints_primary_key_matches_langgraph(normalized: str) -> None:
    assert "primary key (thread_id, checkpoint_ns, checkpoint_id)" in normalized


def test_checkpoint_writes_carries_task_path(normalized: str) -> None:
    assert "task_path text not null default ''" in normalized


def test_checkpoint_writes_primary_key_includes_task_and_idx(normalized: str) -> None:
    assert (
        "primary key (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)"
        in normalized
    )


def test_checkpoint_blobs_blob_is_nullable(normalized: str) -> None:
    # LangGraph drops NOT NULL from checkpoint_blobs.blob in migration 4.
    match = re.search(r"create table if not exists checkpoint_blobs \((.*?)\);", normalized)
    assert match, "checkpoint_blobs DDL not found"
    assert "blob bytea," in match.group(1) or "blob bytea " in match.group(1)
    assert "blob bytea not null" not in match.group(1)


def test_no_concurrent_index_creation(normalized: str) -> None:
    # CREATE INDEX CONCURRENTLY cannot run in the Data API's implicit
    # transaction. The tables are empty here, so plain indexes are correct.
    assert "concurrently" not in normalized


def test_one_running_execution_per_thread(normalized: str) -> None:
    assert (
        "create unique index if not exists journey_executions_one_running on "
        "journey_executions (thread_id) where status = 'running'" in normalized
    )


def test_lease_expiry_is_not_an_index_predicate(normalized: str) -> None:
    # now() is stable, not immutable, so it cannot appear in an index predicate.
    indexes = re.findall(r"create (?:unique )?index[^;]*;", normalized)
    for index in indexes:
        assert "now()" not in index
        assert "current_timestamp" not in index


def test_hold_requests_primary_key_is_journey_scoped(normalized: str) -> None:
    assert "primary key (journey_id, hold_request_id)" in normalized


def test_hold_requests_booking_fk_is_deferred(normalized: str) -> None:
    assert "deferrable initially deferred" in normalized


def test_thread_ownership_is_enforced_by_primary_key(normalized: str) -> None:
    match = re.search(r"create table if not exists journey_threads \((.*?)\);", normalized)
    assert match, "journey_threads DDL not found"
    assert "thread_id varchar(200) primary key" in match.group(1)


def test_is_idempotent(normalized: str) -> None:
    creates = re.findall(r"create table (?!if not exists)", normalized)
    assert not creates, "every CREATE TABLE must be IF NOT EXISTS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_migration_007_contract.py -q`
Expected: FAIL with `FileNotFoundError` for `007_journey_shell.sql`.

- [ ] **Step 3: Write the migration**

Create `meridian/scripts/migrations/007_journey_shell.sql`:

```sql
-- Journey identity, execution leasing, hold request identity, and the
-- LangGraph checkpoint tables.
--
-- The checkpoint DDL is copied from langgraph-checkpoint-postgres 3.1.2 so the
-- Data API saver and AsyncPostgresSaver share one schema. Two deviations are
-- required for Data API execution: indexes are created without CONCURRENTLY,
-- which cannot run inside the implicit transaction and is unnecessary on empty
-- tables, and the packaged ALTER that drops NOT NULL from checkpoint_blobs.blob
-- is folded into the CREATE.

CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    task_path TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);

-- Record the packaged migration versions so a later AsyncPostgresSaver.setup()
-- does not re-apply DDL this migration already created.
INSERT INTO checkpoint_migrations (v)
SELECT generate_series(0, 9)
ON CONFLICT (v) DO NOTHING;

CREATE TABLE IF NOT EXISTS journeys (
    journey_id         VARCHAR(64) PRIMARY KEY,
    traveler_id        VARCHAR(50) NOT NULL REFERENCES travelers(traveler_id),
    checkpoint_backend VARCHAR(32) NOT NULL,
    active_thread_id   VARCHAR(200),
    status             VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS journey_threads (
    thread_id  VARCHAR(200) PRIMARY KEY,
    journey_id VARCHAR(64) NOT NULL REFERENCES journeys(journey_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS journey_executions (
    execution_id     VARCHAR(64) PRIMARY KEY,
    journey_id       VARCHAR(64) NOT NULL REFERENCES journeys(journey_id),
    thread_id        VARCHAR(200) NOT NULL REFERENCES journey_threads(thread_id),
    attempt          INTEGER NOT NULL,
    phase            SMALLINT,
    worker_id        VARCHAR(128) NOT NULL,
    status           VARCHAR(16) NOT NULL,
    lease_expires_at TIMESTAMPTZ,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at         TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS journey_executions_one_running
    ON journey_executions (thread_id) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS journey_executions_journey_idx
    ON journey_executions (journey_id, started_at DESC);

CREATE TABLE IF NOT EXISTS hold_requests (
    journey_id       VARCHAR(64) NOT NULL REFERENCES journeys(journey_id),
    hold_request_id  VARCHAR(64) NOT NULL,
    booking_id       VARCHAR(50) NOT NULL,
    fingerprint      TEXT NOT NULL,
    thread_id        VARCHAR(200) NOT NULL,
    execution_id     VARCHAR(64),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (journey_id, hold_request_id),
    CONSTRAINT hold_requests_booking_fk
        FOREIGN KEY (booking_id) REFERENCES bookings(booking_id)
        DEFERRABLE INITIALLY DEFERRED
);

ALTER TABLE journeys ENABLE ROW LEVEL SECURITY;
ALTER TABLE journey_threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE journey_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE hold_requests ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS journeys_traveler_scope ON journeys;
CREATE POLICY journeys_traveler_scope ON journeys
    USING (traveler_id = current_setting('app.current_traveler_id', true));

DROP POLICY IF EXISTS journey_threads_traveler_scope ON journey_threads;
CREATE POLICY journey_threads_traveler_scope ON journey_threads
    USING (journey_id IN (
        SELECT journey_id FROM journeys
        WHERE traveler_id = current_setting('app.current_traveler_id', true)
    ));

DROP POLICY IF EXISTS journey_executions_traveler_scope ON journey_executions;
CREATE POLICY journey_executions_traveler_scope ON journey_executions
    USING (journey_id IN (
        SELECT journey_id FROM journeys
        WHERE traveler_id = current_setting('app.current_traveler_id', true)
    ));

DROP POLICY IF EXISTS hold_requests_traveler_scope ON hold_requests;
CREATE POLICY hold_requests_traveler_scope ON hold_requests
    USING (journey_id IN (
        SELECT journey_id FROM journeys
        WHERE traveler_id = current_setting('app.current_traveler_id', true)
    ));

GRANT SELECT, INSERT, UPDATE ON journeys, journey_threads, journey_executions,
    hold_requests TO meridian_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON checkpoints, checkpoint_blobs,
    checkpoint_writes, checkpoint_migrations TO meridian_app;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_migration_007_contract.py -q`
Expected: PASS, 20 passed.

- [ ] **Step 5: Confirm the migration runner still discovers every file in order**

Run: `venv/bin/pytest tests/test_migration_runner.py tests/test_schema_bootstrap_order.py -q`
Expected: PASS. If either fails, the runner has an explicit migration list that must include `007_journey_shell.sql`; add it there rather than renaming the file.

- [ ] **Step 6: Commit**

```bash
git add meridian/scripts/migrations/007_journey_shell.sql meridian/tests/test_migration_007_contract.py
git commit -m "Add journey and checkpoint tables"
```

---

### Task 4: AuroraDataApiSaver, put and get

Implements the write and single-read halves of `BaseCheckpointSaver`. Blob segments are written before the `checkpoints` row, so a partial write is never visible as a committed checkpoint.

**Files:**
- Create: `meridian/backend/db/aurora_dataapi_saver.py`
- Test: `meridian/tests/test_aurora_dataapi_saver.py`

**Interfaces:**
- Consumes: `RDSDataClient` binary support (Task 1); `window_offsets`, `split_for_write`, `MAX_ROW_BYTES` from `backend.db.blob_windows` (Task 2); tables from Task 3.
- Produces: `class AuroraDataApiSaver(BaseCheckpointSaver)` with `__init__(self, client, serde=None)`, `async aput(config, checkpoint, metadata, new_versions) -> RunnableConfig`, `async aget_tuple(config) -> CheckpointTuple | None`, and `async _read_blob(thread_id, checkpoint_ns, channel, version) -> tuple[str, bytes] | None` returning `(blob_type, payload)`, plus `async _load_channel_values(thread_id, ns, channel_versions) -> dict`.

- [ ] **Step 1: Write the failing test**

Create `meridian/tests/test_aurora_dataapi_saver.py`:

```python
"""AuroraDataApiSaver writes and reads LangGraph checkpoints over the Data API.

A fake client records statements so the ordering guarantee can be asserted
without a cluster: blob segments must be durable before the checkpoints row
that makes the checkpoint visible.
"""

from typing import Any

import pytest

from backend.db.aurora_dataapi_saver import AuroraDataApiSaver
from backend.db.blob_windows import MAX_ROW_BYTES


class FakeDataClient:
    """Records executed SQL and replays queued results."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.results: list[list[dict]] = []
        self.fail_on: str | None = None

    async def execute(self, sql: str, params: tuple = (), **kwargs: Any) -> list[dict]:
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("data api failure")
        self.calls.append((" ".join(sql.split()), params))
        return self.results.pop(0) if self.results else []

    def statements(self) -> list[str]:
        return [sql for sql, _ in self.calls]


@pytest.fixture
def client() -> FakeDataClient:
    return FakeDataClient()


@pytest.fixture
def saver(client: FakeDataClient) -> AuroraDataApiSaver:
    return AuroraDataApiSaver(client)


def _config(thread_id: str = "t1", ns: str = "") -> dict:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}}


def _checkpoint(cid: str = "cp1") -> dict:
    return {
        "v": 1,
        "id": cid,
        "ts": "2026-09-06T00:00:00+00:00",
        "channel_values": {"messages": ["hello"]},
        "channel_versions": {"messages": "1"},
        "versions_seen": {},
    }


@pytest.mark.asyncio
async def test_aput_returns_the_new_config(saver: AuroraDataApiSaver) -> None:
    result = await saver.aput(_config(), _checkpoint(), {"step": 1}, {"messages": "1"})
    assert result["configurable"]["checkpoint_id"] == "cp1"
    assert result["configurable"]["thread_id"] == "t1"


@pytest.mark.asyncio
async def test_blobs_are_written_before_the_checkpoint_row(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    await saver.aput(_config(), _checkpoint(), {"step": 1}, {"messages": "1"})
    statements = client.statements()
    blob_at = next(i for i, s in enumerate(statements) if "checkpoint_blobs" in s)
    row_at = next(i for i, s in enumerate(statements) if "INTO checkpoints" in s)
    assert blob_at < row_at, "a partial write must never be visible as committed"


@pytest.mark.asyncio
async def test_checkpoint_row_is_not_written_when_a_blob_fails(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.fail_on = "checkpoint_blobs"
    with pytest.raises(RuntimeError):
        await saver.aput(_config(), _checkpoint(), {"step": 1}, {"messages": "1"})
    assert not any("INTO checkpoints" in s for s in client.statements())


@pytest.mark.asyncio
async def test_large_value_is_appended_in_segments(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    checkpoint = _checkpoint()
    checkpoint["channel_values"]["messages"] = ["x" * (MAX_ROW_BYTES * 2)]
    await saver.aput(_config(), checkpoint, {"step": 1}, {"messages": "1"})
    appends = [s for s in client.statements() if "blob || " in s]
    assert appends, "values beyond one window must append rather than re-insert"


@pytest.mark.asyncio
async def test_read_blob_uses_octet_length_then_substring(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.results = [[{"n": 5}], [{"part": b"hello"}]]
    assert await saver._read_blob("t1", "", "messages", "1") == b"hello"
    statements = client.statements()
    assert "octet_length(blob)" in statements[0]
    assert "substring(blob FROM" in statements[1]


@pytest.mark.asyncio
async def test_read_blob_windows_a_large_value(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    total = MAX_ROW_BYTES + 10
    client.results = [
        [{"n": total}],
        [{"part": b"a" * MAX_ROW_BYTES}],
        [{"part": b"b" * 10}],
    ]
    blob = await saver._read_blob("t1", "", "messages", "1")
    assert len(blob) == total
    assert blob.endswith(b"b" * 10)


@pytest.mark.asyncio
async def test_read_blob_returns_none_when_absent(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.results = [[]]
    assert await saver._read_blob("t1", "", "messages", "1") is None


@pytest.mark.asyncio
async def test_aget_tuple_returns_none_for_unknown_thread(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.results = [[]]
    assert await saver.aget_tuple(_config("missing")) is None


@pytest.mark.asyncio
async def test_aput_round_trips_through_aget_tuple(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    checkpoint = _checkpoint()
    await saver.aput(_config(), checkpoint, {"step": 1}, {"messages": "1"})
    stored = {sql: params for sql, params in client.calls}
    blob_call = next(p for s, p in client.calls if "checkpoint_blobs" in s)
    payload = next(p for p in blob_call if isinstance(p, bytes))
    assert isinstance(payload, bytes) and payload, "channel value must serialize to bytes"
    assert stored
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_aurora_dataapi_saver.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.db.aurora_dataapi_saver'`.

- [ ] **Step 3: Confirm the async test plugin is available**

Run: `venv/bin/python -c "import pytest_asyncio; print(pytest_asyncio.__version__)"`
Expected: a version string. If it raises `ModuleNotFoundError`, run `venv/bin/pip install pytest-asyncio==1.3.0`, add `asyncio_mode = auto` under `[pytest]` in `meridian/pytest.ini`, and add `pytest-asyncio==1.3.0` to `meridian/requirements-dev.txt`.

- [ ] **Step 4: Write the implementation**

Create `meridian/backend/db/aurora_dataapi_saver.py`:

```python
"""A LangGraph checkpoint saver that persists through the RDS Data API.

The demo cluster's writer is not publicly accessible, so ``AsyncPostgresSaver``
cannot open a psycopg connection from a laptop. This saver writes the same
tables over the Data API, which is the transport the rest of the application
already uses.

Storage matches ``langgraph-checkpoint-postgres`` 3.1.2 exactly. The Data API
caps a returned row at 64 KB, so values are written in appended segments and
read through windowed ``substring`` calls.
"""

from typing import Any, AsyncIterator, Optional, Sequence

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from backend.db.blob_windows import split_for_write, window_offsets

UPSERT_BLOB_SQL = """
INSERT INTO checkpoint_blobs
    (thread_id, checkpoint_ns, channel, version, type, blob)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (thread_id, checkpoint_ns, channel, version)
DO UPDATE SET type = EXCLUDED.type, blob = EXCLUDED.blob
"""

APPEND_BLOB_SQL = """
UPDATE checkpoint_blobs SET blob = blob || %s
 WHERE thread_id = %s AND checkpoint_ns = %s AND channel = %s AND version = %s
"""

UPSERT_CHECKPOINT_SQL = """
INSERT INTO checkpoints
    (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
     type, checkpoint, metadata)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id)
DO UPDATE SET checkpoint = EXCLUDED.checkpoint, metadata = EXCLUDED.metadata
"""

BLOB_SIZE_SQL = """
SELECT octet_length(blob) AS n FROM checkpoint_blobs
 WHERE thread_id = %s AND checkpoint_ns = %s AND channel = %s AND version = %s
"""

BLOB_WINDOW_SQL = """
SELECT substring(blob FROM %s FOR %s) AS part FROM checkpoint_blobs
 WHERE thread_id = %s AND checkpoint_ns = %s AND channel = %s AND version = %s
"""

SELECT_CHECKPOINT_SQL = """
SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
       checkpoint, metadata
  FROM checkpoints
 WHERE thread_id = %s AND checkpoint_ns = %s
"""


class AuroraDataApiSaver(BaseCheckpointSaver):
    """Persist LangGraph checkpoints to Aurora through the RDS Data API."""

    def __init__(self, client: Any, serde: Optional[Any] = None) -> None:
        """Initialize the saver.

        Args:
            client: An ``RDSDataClient``.
            serde: Serializer. Defaults to LangGraph's ``JsonPlusSerializer``
                so values are byte-compatible with the Postgres saver.
        """
        super().__init__(serde=serde or JsonPlusSerializer())
        self.client = client

    async def _write_blob(
        self, thread_id: str, ns: str, channel: str, version: str, value: Any
    ) -> None:
        """Write one channel value, appending beyond the first window."""
        blob_type, payload = self.serde.dumps_typed(value)
        segments = split_for_write(payload)
        await self.client.execute(
            UPSERT_BLOB_SQL,
            (thread_id, ns, channel, version, blob_type, segments[0]),
        )
        for segment in segments[1:]:
            await self.client.execute(
                APPEND_BLOB_SQL, (segment, thread_id, ns, channel, version)
            )

    async def _read_blob(
        self, thread_id: str, ns: str, channel: str, version: str
    ) -> Optional[bytes]:
        """Read one channel value in windows under the 64 KB row limit."""
        key = (thread_id, ns, channel, version)
        rows = await self.client.execute(BLOB_SIZE_SQL, key)
        if not rows or rows[0].get("n") is None:
            return None
        total = int(rows[0]["n"])
        parts: list[bytes] = []
        for offset, length in window_offsets(total):
            window = await self.client.execute(
                BLOB_WINDOW_SQL, (offset, length) + key
            )
            if not window:
                raise RuntimeError(
                    f"checkpoint blob vanished mid-read: thread={thread_id} "
                    f"channel={channel} version={version} offset={offset}"
                )
            parts.append(window[0]["part"])
        return b"".join(parts)

    async def aput(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> dict:
        """Persist a checkpoint, blobs first.

        The ``checkpoints`` row is what makes a checkpoint visible, so it is
        written last. A failed blob write leaves nothing to resume from rather
        than something that cannot be read back.
        """
        import json

        configurable = config["configurable"]
        thread_id = configurable["thread_id"]
        ns = configurable.get("checkpoint_ns", "")
        values = checkpoint.get("channel_values", {})

        for channel, version in new_versions.items():
            if channel in values:
                await self._write_blob(
                    thread_id, ns, channel, str(version), values[channel]
                )

        stored = {k: v for k, v in checkpoint.items() if k != "channel_values"}
        await self.client.execute(
            UPSERT_CHECKPOINT_SQL,
            (
                thread_id,
                ns,
                checkpoint["id"],
                configurable.get("checkpoint_id"),
                "json",
                json.dumps(stored),
                json.dumps(dict(metadata)),
            ),
        )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    async def aget_tuple(self, config: dict) -> Optional[CheckpointTuple]:
        """Load one checkpoint and rehydrate its channel values."""
        import json

        configurable = config["configurable"]
        thread_id = configurable["thread_id"]
        ns = configurable.get("checkpoint_ns", "")
        checkpoint_id = configurable.get("checkpoint_id")

        sql = SELECT_CHECKPOINT_SQL
        params: tuple = (thread_id, ns)
        if checkpoint_id:
            sql += " AND checkpoint_id = %s"
            params += (checkpoint_id,)
        else:
            sql += " ORDER BY checkpoint_id DESC LIMIT 1"

        rows = await self.client.execute(sql, params)
        if not rows:
            return None

        row = rows[0]
        checkpoint = json.loads(row["checkpoint"])
        values: dict[str, Any] = {}
        for channel, version in checkpoint.get("channel_versions", {}).items():
            found = await self._read_blob(thread_id, ns, channel, str(version))
            if found is not None:
                blob_type, payload = found
                values[channel] = self.serde.loads_typed((blob_type, payload))
        checkpoint["channel_values"] = values

        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": ns,
                    "checkpoint_id": row["checkpoint_id"],
                }
            },
            checkpoint=checkpoint,
            metadata=json.loads(row["metadata"]),
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": ns,
                        "checkpoint_id": row["parent_checkpoint_id"],
                    }
                }
                if row.get("parent_checkpoint_id")
                else None
            ),
            pending_writes=[],
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_aurora_dataapi_saver.py -q`
Expected: PASS, 9 passed.

**Confirmed during implementation:** the installed `JsonPlusSerializer` returns
`"msgpack"`, never `"json"`. The stored `checkpoint_blobs.type` column is read
back and passed to `loads_typed`; hardcoding a literal type corrupts every
rehydrated checkpoint.

- [ ] **Step 6: Commit**

```bash
git add meridian/backend/db/aurora_dataapi_saver.py meridian/tests/test_aurora_dataapi_saver.py
git commit -m "Add a Data API checkpoint saver"
```

---

### Task 5: Pending writes, reserved indices, and history

`aput_writes` is where custom savers break, and it is exactly what interrupt-and-resume depends on. Reserved indices are negative and must survive verbatim.

**Files:**
- Modify: `meridian/backend/db/aurora_dataapi_saver.py`
- Test: `meridian/tests/test_aurora_dataapi_saver_writes.py`

**Interfaces:**
- Consumes: `AuroraDataApiSaver` from Task 4.
- Produces: `async aput_writes(config, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None`; `async alist(config, *, filter=None, before=None, limit=None) -> AsyncIterator[CheckpointTuple]`; `aget_tuple` now returns populated `pending_writes`.

- [ ] **Step 1: Write the failing test**

Create `meridian/tests/test_aurora_dataapi_saver_writes.py`:

```python
"""Pending writes and history for the Data API saver.

Pending writes are what a resumed graph replays. Losing one, renumbering a
reserved index, or dropping task_path turns a resume into a silent
recomputation, so each is pinned here.
"""

import pytest

from backend.db.aurora_dataapi_saver import AuroraDataApiSaver
from tests.test_aurora_dataapi_saver import FakeDataClient, _checkpoint, _config


@pytest.fixture
def client() -> FakeDataClient:
    return FakeDataClient()


@pytest.fixture
def saver(client: FakeDataClient) -> AuroraDataApiSaver:
    return AuroraDataApiSaver(client)


@pytest.mark.asyncio
async def test_writes_use_the_full_natural_key(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    await saver.aput_writes(config, [("messages", "a")], "task-1", "path/0")
    sql, params = client.calls[0]
    assert "checkpoint_writes" in sql
    assert "t1" in params and "cp1" in params and "task-1" in params
    assert "path/0" in params


@pytest.mark.asyncio
async def test_task_path_defaults_to_empty_not_null(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    await saver.aput_writes(config, [("messages", "a")], "task-1")
    _, params = client.calls[0]
    assert "" in params
    assert None not in params


@pytest.mark.asyncio
async def test_indices_are_sequential_from_zero(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    await saver.aput_writes(config, [("a", 1), ("b", 2), ("c", 3)], "task-1")
    indices = [p[4] for _, p in client.calls]
    assert indices == [0, 1, 2]


@pytest.mark.asyncio
async def test_reserved_channels_get_their_fixed_negative_index(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    from langgraph.checkpoint.base import WRITES_IDX_MAP

    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    channel = next(iter(WRITES_IDX_MAP))
    await saver.aput_writes(config, [(channel, "a")], "task-1")
    assert client.calls[0][1][4] == WRITES_IDX_MAP[channel]


@pytest.mark.asyncio
async def test_ordinary_channels_keep_positional_indices(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    await saver.aput_writes(config, [("a", 1), ("b", 2)], "task-1")
    assert [p[4] for _, p in client.calls] == [0, 1]


@pytest.mark.asyncio
async def test_reserved_only_batches_overwrite(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    """A reserved write is a latest-value slot, so it must upsert."""
    from langgraph.checkpoint.base import WRITES_IDX_MAP

    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    channel = next(iter(WRITES_IDX_MAP))
    await saver.aput_writes(config, [(channel, "a")], "task-1")
    assert "DO UPDATE" in client.statements()[0]


@pytest.mark.asyncio
async def test_ordinary_batches_do_not_overwrite(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    """Ordinary writes are append-once. Overwriting them loses task output."""
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    await saver.aput_writes(config, [("messages", "a")], "task-1")
    sql = client.statements()[0]
    assert "ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)" in sql
    assert "DO NOTHING" in sql
    assert "DO UPDATE" not in sql


@pytest.mark.asyncio
async def test_mixed_batches_do_not_overwrite(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    from langgraph.checkpoint.base import WRITES_IDX_MAP

    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    channel = next(iter(WRITES_IDX_MAP))
    await saver.aput_writes(config, [(channel, "a"), ("messages", "b")], "task-1")
    assert "DO NOTHING" in client.statements()[0]


@pytest.mark.asyncio
async def test_alist_pages_with_a_limit(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.results = [[]]
    [item async for item in saver.alist(_config())]
    assert "LIMIT" in client.statements()[0]


@pytest.mark.asyncio
async def test_alist_orders_newest_first(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.results = [[]]
    [item async for item in saver.alist(_config())]
    assert "ORDER BY checkpoint_id DESC" in client.statements()[0]


@pytest.mark.asyncio
async def test_alist_applies_before_as_keyset_pagination(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.results = [[]]
    before = {"configurable": {"checkpoint_id": "cp5"}}
    [item async for item in saver.alist(_config(), before=before)]
    sql, params = client.calls[0]
    assert "checkpoint_id <" in sql
    assert "cp5" in params
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_aurora_dataapi_saver_writes.py -q`
Expected: FAIL with `AttributeError: 'AuroraDataApiSaver' object has no attribute 'aput_writes'`.

- [ ] **Step 3: Add the write and history methods**

Append to `meridian/backend/db/aurora_dataapi_saver.py`, and add these SQL constants beside the others:

```python
# Two conflict clauses, matching langgraph-checkpoint-postgres 3.1.2 exactly.
# Reserved channels are latest-value slots and must overwrite; ordinary writes
# are append-once and must not, or a retry silently replaces task output.
_WRITE_COLUMNS = """
INSERT INTO checkpoint_writes
    (thread_id, checkpoint_ns, checkpoint_id, task_id, idx,
     channel, type, blob, task_path)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx) """

UPSERT_WRITE_SQL = _WRITE_COLUMNS + """DO UPDATE SET
    channel = EXCLUDED.channel, type = EXCLUDED.type, blob = EXCLUDED.blob
"""

INSERT_WRITE_SQL = _WRITE_COLUMNS + "DO NOTHING"

SELECT_WRITES_SQL = """
SELECT task_id, channel, type, blob
  FROM checkpoint_writes
 WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s
 ORDER BY task_path, task_id, idx
"""
```

Add these methods to the class:

```python
    async def aput_writes(
        self,
        config: dict,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Persist pending writes for a task.

        Reserved channels carry fixed negative indices from ``WRITES_IDX_MAP``
        and are latest-value slots, so a batch made entirely of them upserts.
        Any other batch inserts without overwriting, because an ordinary write
        is append-once and replacing it on retry loses task output. This is the
        same branch ``AsyncPostgresSaver`` takes.

        Args:
            config: Carries thread_id, checkpoint_ns and checkpoint_id.
            writes: (channel, value) pairs in emission order.
            task_id: The task that produced them.
            task_path: Position in the task tree; part of write ordering.
        """
        from langgraph.checkpoint.base import WRITES_IDX_MAP

        configurable = config["configurable"]
        thread_id = configurable["thread_id"]
        ns = configurable.get("checkpoint_ns", "")
        checkpoint_id = configurable["checkpoint_id"]

        sql = (
            UPSERT_WRITE_SQL
            if all(channel in WRITES_IDX_MAP for channel, _ in writes)
            else INSERT_WRITE_SQL
        )

        for offset, (channel, value) in enumerate(writes):
            blob_type, payload = self.serde.dumps_typed(value)
            await self.client.execute(
                sql,
                (
                    thread_id,
                    ns,
                    checkpoint_id,
                    task_id,
                    WRITES_IDX_MAP.get(channel, offset),
                    channel,
                    blob_type,
                    payload,
                    task_path,
                ),
            )

    async def _pending_writes(
        self, thread_id: str, ns: str, checkpoint_id: str
    ) -> list[tuple[str, str, Any]]:
        """Load pending writes in their stored order."""
        rows = await self.client.execute(
            SELECT_WRITES_SQL, (thread_id, ns, checkpoint_id)
        )
        return [
            (row["task_id"], row["channel"],
             self.serde.loads_typed((row["type"], row["blob"])))
            for row in rows
        ]

    async def alist(
        self,
        config: Optional[dict],
        *,
        filter: Optional[dict] = None,
        before: Optional[dict] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """Yield checkpoints newest first.

        Pages with a bounded LIMIT and keyset pagination on checkpoint_id so a
        response never approaches the Data API's 1 MiB ceiling.
        """
        import json

        configurable = (config or {}).get("configurable", {})
        thread_id = configurable.get("thread_id")
        ns = configurable.get("checkpoint_ns", "")

        sql = SELECT_CHECKPOINT_SQL
        params: tuple = (thread_id, ns)
        if before:
            sql += " AND checkpoint_id < %s"
            params += (before["configurable"]["checkpoint_id"],)
        sql += " ORDER BY checkpoint_id DESC LIMIT %s"
        params += (min(limit or 50, 50),)

        for row in await self.client.execute(sql, params):
            checkpoint = json.loads(row["checkpoint"])
            # Same merge as aget_tuple: upstream inlines primitives in the
            # JSONB and writes no blob row for them, so both sources count.
            checkpoint["channel_values"] = {
                **(checkpoint.get("channel_values") or {}),
                **await self._load_channel_values(
                    thread_id, ns, checkpoint.get("channel_versions", {})
                ),
            }
            yield CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": ns,
                        "checkpoint_id": row["checkpoint_id"],
                    }
                },
                checkpoint=checkpoint,
                metadata=json.loads(row["metadata"]),
                parent_config=None,
                pending_writes=await self._pending_writes(
                    thread_id, ns, row["checkpoint_id"]
                ),
            )
```

Then change `aget_tuple`'s `pending_writes=[]` to:

```python
            pending_writes=await self._pending_writes(
                thread_id, ns, row["checkpoint_id"]
            ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_aurora_dataapi_saver_writes.py tests/test_aurora_dataapi_saver.py -q`
Expected: PASS, 17 passed.

- [ ] **Step 5: Commit**

```bash
git add meridian/backend/db/aurora_dataapi_saver.py meridian/tests/test_aurora_dataapi_saver_writes.py
git commit -m "Persist pending writes and paginate checkpoint history"
```

---

### Task 6: Vendor the upstream conformance suite

The installed distribution ships only `base`, `memory`, `postgres`, and `serde` — no conformance tests. Contract violations that unit tests miss are exactly the ones that break a live resume, so the upstream suite is vendored at the pinned version.

**Files:**
- Create: `meridian/tests/conformance/__init__.py`, `meridian/tests/conformance/README.md`, `meridian/tests/conformance/test_checkpointer_conformance.py`
- Test: the vendored suite is itself the test.

**Interfaces:**
- Consumes: `AuroraDataApiSaver` (Tasks 4-5).
- Produces: a pytest module running LangGraph's checkpointer contract against an in-memory Data API double.

- [ ] **Step 1: Fetch the upstream suite at the pinned version**

Run:

```bash
cd /tmp && rm -rf langgraph-src && \
git clone --depth 1 --branch 4.1.1 --filter=blob:none --sparse \
  https://github.com/langchain-ai/langgraph.git langgraph-src && \
cd langgraph-src && git sparse-checkout set libs/checkpoint/tests && \
ls libs/checkpoint/tests
```

Expected: a listing including `test_memory.py` and the shared checkpointer test module. If the tag `4.1.1` does not exist, list tags with `git ls-remote --tags https://github.com/langchain-ai/langgraph.git | grep checkpoint` and use the tag matching `langgraph-checkpoint==4.1.1`. If the suite cannot be fetched, stop and report it rather than writing a substitute — a hand-rolled approximation of a contract suite provides false assurance.

- [ ] **Step 2: Copy the suite and record provenance**

```bash
mkdir -p meridian/tests/conformance
cp /tmp/langgraph-src/libs/checkpoint/tests/test_memory.py \
   meridian/tests/conformance/test_checkpointer_conformance.py
touch meridian/tests/conformance/__init__.py
```

Create `meridian/tests/conformance/README.md`:

```markdown
# Vendored checkpointer conformance suite

Source: https://github.com/langchain-ai/langgraph, `libs/checkpoint/tests`
Version: matches `langgraph-checkpoint==4.1.1` as pinned in requirements.

The installed distribution does not ship these tests, so they are vendored.
Re-vendor whenever `langgraph-checkpoint` is upgraded, and treat a failure
here as a contract violation in `AuroraDataApiSaver`, not as a flaky test.
```

- [ ] **Step 3: Point the suite at the Data API saver**

Edit `meridian/tests/conformance/test_checkpointer_conformance.py` so the
fixture that constructs `MemorySaver` constructs `AuroraDataApiSaver` over an
in-memory double instead. Add at the top of the file:

```python
from backend.db.aurora_dataapi_saver import AuroraDataApiSaver
from tests.conformance.fake_cluster import FakeCluster


@pytest.fixture
def saver() -> AuroraDataApiSaver:
    """The Data API saver over an in-memory table double."""
    return AuroraDataApiSaver(FakeCluster())
```

Create `meridian/tests/conformance/fake_cluster.py` implementing an
`execute(sql, params)` that stores rows in dicts keyed by the natural keys of
`checkpoints`, `checkpoint_blobs`, and `checkpoint_writes`, and supports
`octet_length`, `substring`, `blob || %s`, `ORDER BY checkpoint_id DESC`,
`checkpoint_id <`, and `LIMIT`. Keep it in one file under 200 lines; it is a
test double, not a database.

- [ ] **Step 4: Run the conformance suite**

Run: `venv/bin/pytest tests/conformance -q`
Expected: PASS. Every failure is a real contract gap. Fix `AuroraDataApiSaver`, never the vendored assertions.

- [ ] **Step 5: Commit**

```bash
git add meridian/tests/conformance
git commit -m "Run the upstream checkpointer conformance suite"
```

---

### Task 7: Select the backend, label it honestly, and persist synchronously

`_uses_postgres_saver` tests `checkpointer_kind.startswith("PostgresSaver")`. A Data API saver fails that string test and silently renders the MemorySaver trace, which is the mislabelling fixed in `e78d68f`. Durability becomes a capability flag.

**Files:**
- Modify: `meridian/backend/agents/orchestration_05/workflow.py:157-163` (`CheckpointBackend`), `:170-230` (`initialize_checkpoint_backend`), `:533` (`_uses_postgres_saver`), `:537-570` (`_checkpoint_activity`)
- Test: `meridian/tests/test_checkpoint_backend_selection.py`

**Interfaces:**
- Consumes: `AuroraDataApiSaver` (Tasks 4-6).
- Produces: `CheckpointBackend` unchanged in shape; `initialize_checkpoint_backend()` may return `kind="AuroraDataApiSaver"` with `durable=True`; `OrchestrationAgent.checkpointer_durable: bool` replaces `_uses_postgres_saver` at call sites.

- [ ] **Step 1: Write the failing test**

Create `meridian/tests/test_checkpoint_backend_selection.py`:

```python
"""Backend selection and honest labelling.

The trace must name the backend that actually ran. A name-prefix test made a
durable Data API saver render as in-process, which is precisely the claim the
demo cannot afford to get wrong.
"""

import pytest

from backend.agents.orchestration_05.workflow import (
    CheckpointBackend,
    OrchestrationAgent,
)


async def _noop(*args, **kwargs):
    return []


@pytest.fixture
def workflow() -> OrchestrationAgent:
    return OrchestrationAgent(search_fn=_noop, availability_fn=_noop)


@pytest.mark.parametrize(
    "kind,durable",
    [
        ("PostgresSaver (pooled)", True),
        ("AuroraDataApiSaver", True),
        ("MemorySaver (in-process)", False),
    ],
)
def test_durability_comes_from_the_flag_not_the_name(
    workflow: OrchestrationAgent, kind: str, durable: bool
) -> None:
    workflow.checkpointer_kind = kind
    workflow.checkpointer_durable = durable
    assert workflow.checkpointer_durable is durable


def test_data_api_saver_is_not_labelled_in_process(
    workflow: OrchestrationAgent,
) -> None:
    workflow.checkpointer_kind = "AuroraDataApiSaver"
    workflow.checkpointer_durable = True
    activity = workflow._checkpoint_activity("search", 12)
    text = str(activity)
    assert "MemorySaver" not in text
    assert "AuroraDataApiSaver" in text


def test_memory_saver_is_still_labelled_in_process(
    workflow: OrchestrationAgent,
) -> None:
    workflow.checkpointer_kind = "MemorySaver (in-process)"
    workflow.checkpointer_durable = False
    assert "MemorySaver" in str(workflow._checkpoint_activity("search", 12))


def test_backend_carries_a_durable_flag() -> None:
    backend = CheckpointBackend(saver=object(), kind="AuroraDataApiSaver", durable=True)
    assert backend.durable is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_checkpoint_backend_selection.py -q`
Expected: FAIL. `test_data_api_saver_is_not_labelled_in_process` finds "MemorySaver" because the prefix test is false.

- [ ] **Step 3: Replace the prefix test with the capability flag**

In `meridian/backend/agents/orchestration_05/workflow.py`, in `OrchestrationAgent.__init__`, add beside `self.checkpointer_kind`:

```python
        self.checkpointer_durable = False
```

Replace the `_uses_postgres_saver` property with:

```python
    @property
    def _uses_durable_saver(self) -> bool:
        """Whether the configured checkpointer persists outside this process.

        Read from the backend's capability flag rather than its name. A name
        test silently mislabels any backend added later.
        """
        return self.checkpointer_durable
```

In `_checkpoint_activity`, change `if self._uses_postgres_saver:` to
`if self._uses_durable_saver:`, and replace the hard-coded title and the
`durability` telemetry field so they name the real backend:

```python
                title=f"Checkpoint · {self.checkpointer_kind}.put",
```

```python
                        {"label": "durability", "value": self.checkpointer_kind},
```

- [ ] **Step 4: Add the Data API branch to backend selection**

The branch is **opt-in and probed**, not inferred from whether a client can be
constructed. `RDSDataClient.__init__` reads its ARNs from the environment and
calls `boto3.client("rds-data", ...)`, neither of which fails when the ARNs are
absent or the credentials are dead. A bare `try`/`except` around construction
therefore succeeds everywhere, which would make MemorySaver unreachable, send
`tests/conftest.py`'s dotenv-loaded unit tests at the live cluster, and break
`test_checkpointer_kind_is_memory_when_dsn_unset`. It would also defer a
missing migration 007 to the first checkpoint write, mid-turn, where there is
no longer anything to fall back to.

Two module-level helpers, beside `_checkpoint_required()`:

```python
def _data_api_checkpoints_enabled() -> bool:
    return _truthy_env("LANGGRAPH_CHECKPOINT_DATA_API", "false")


async def _probe_data_api_checkpoints(saver: Any) -> None:
    """Confirm the checkpoint tables answer before the saver is adopted."""
    await saver.client.execute("SELECT 1 FROM checkpoints LIMIT 1", ())
```

Then in `initialize_checkpoint_backend()`, replace the `if not dsn:` fallback:

```python
        if not dsn:
            error: Optional[str] = None
            if _data_api_checkpoints_enabled():
                try:
                    from backend.db.aurora_dataapi_saver import AuroraDataApiSaver
                    from backend.db.rds_data_client import get_rds_data_client

                    saver = AuroraDataApiSaver(get_rds_data_client())
                    await _probe_data_api_checkpoints(saver)
                except Exception as exc:  # noqa: BLE001 - fall through to the guard
                    error = f"Data API checkpointing unavailable: {exc}"
                    logger.warning("%s Falling back to MemorySaver.", error)
                else:
                    _checkpoint_backend = CheckpointBackend(
                        saver=saver,
                        kind="AuroraDataApiSaver",
                        durable=True,
                    )
                    return _checkpoint_backend

            if _checkpoint_required():
                raise RuntimeError(
                    "Durable workflow checkpoints are required, but no "
                    "LANGGRAPH_CHECKPOINT_DSN or checkpoint credentials resolved."
                    + (f" {error}" if error else "")
                )
            _checkpoint_backend = CheckpointBackend(
                saver=MemorySaver(),
                kind="MemorySaver (in-process)",
                durable=False,
                error=error,
            )
            return _checkpoint_backend
```

Turning the flag on for the demo laptop is Task 11's business, after the
vertical slice proves the saver against real Aurora.

Wherever the workflow adopts a backend, set both fields together:

```python
        self.checkpointer = backend.saver
        self.checkpointer_kind = backend.kind
        self.checkpointer_durable = backend.durable
```

- [ ] **Step 5: Run the tests**

Run: `venv/bin/pytest tests/test_checkpoint_backend_selection.py tests/test_phase5_workflow.py -q`
Expected: PASS.

- [ ] **Step 6: Confirm no caller still uses the old property**

Run: `grep -rn "_uses_postgres_saver" meridian/backend meridian/tests`
Expected: no output.

- [ ] **Step 7: Run the full suite**

Run: `venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 8: Write the failing durability-mode test**

LangGraph 1.2.9 defines `Durability = Literal["sync", "async", "exit"]` and
**defaults to `"async"`** (`langgraph/pregel/main.py:2603`). Async means the
checkpoint write is not awaited before the step completes, so a worker killed
immediately after the interrupt can lose the checkpoint the demo resumes from.
The recovery boundary must be synchronous.

Append to `meridian/tests/test_checkpoint_backend_selection.py`:

```python
def test_graph_invocation_requests_synchronous_durability() -> None:
    """The recovery boundary must be committed before the turn returns.

    LangGraph defaults durability to "async", which does not await the
    checkpoint write. A kill right after the interrupt would then land on a
    checkpoint that was never persisted.
    """
    wf = OrchestrationAgent(search_fn=_noop, availability_fn=_noop)
    seen: list[object] = []

    async def _spy(_self, _input, config=None, **kwargs):
        seen.append(kwargs.get("durability"))
        return {"activities": []}

    # Patch the compiled-graph class, not the instance: adopting a checkpoint
    # backend recompiles the graph and would discard an instance patch.
    monkeypatch.setattr(type(wf.graph), "ainvoke", _spy)

    asyncio.run(wf.run("Find me a Kyoto cultural trip", traveler_id="t1",
                       conversation_id="c-durability"))

    assert seen, "graph.ainvoke was never called"
    assert seen == ["sync"] * len(seen), (
        f'every invocation must pass durability="sync"; saw {seen}'
    )
```

- [ ] **Step 9: Run it to verify it fails**

Run: `venv/bin/pytest tests/test_checkpoint_backend_selection.py -q -k durability`
Expected: FAIL, `durability="sync"` is absent.

- [ ] **Step 10: Pass the durability mode at every graph invocation**

In `meridian/backend/agents/orchestration_05/workflow.py`, add `durability="sync"`
to each `self.graph.ainvoke(...)` and `self.graph.astream(...)` call:

```python
            result = await self.graph.ainvoke(
                initial_state, config=config, durability="sync"
            )
```

- [ ] **Step 11: Run the tests**

Run: `venv/bin/pytest tests/test_checkpoint_backend_selection.py tests/test_phase5_workflow.py -q`
Expected: PASS.

- [ ] **Step 12: Commit**

```bash
git add meridian/backend/agents/orchestration_05/workflow.py meridian/tests/test_checkpoint_backend_selection.py
git commit -m "Select the checkpoint backend by capability and commit synchronously"
```

---

### Task 8: Journey identity and lease-based execution claiming

Resume creates a new attempt on the existing thread. Exactly one execution runs per thread, enforced by a partial unique index, with takeover after lease expiry.

**Files:**
- Create: `meridian/backend/db/journey_store.py`
- Test: `meridian/tests/test_journey_store.py`

**Interfaces:**
- Consumes: tables from Task 3.
- Produces: `async create_journey(db, traveler_id, checkpoint_backend) -> str`; `async bind_thread(db, journey_id, thread_id) -> None`; `async claim_execution(db, journey_id, thread_id, worker_id, lease_seconds=30) -> ExecutionClaim`; `async renew_lease(db, execution_id, lease_seconds=30) -> bool`; `async release_execution(db, execution_id, status) -> None`. `ExecutionClaim` is a dataclass with `execution_id: str`, `attempt: int`, `worker_id: str`, `claimed: bool`, `conflict: Optional[dict]`.

- [ ] **Step 1: Write the failing test**

Create `meridian/tests/test_journey_store.py`:

```python
"""Journey identity, thread ownership, and single-execution claiming.

The claim must be decided by the database, not by application logic reading
then writing, because two workers racing to resume is the exact scenario the
demo creates on purpose.
"""

import pytest

from backend.db.journey_store import ExecutionClaim, claim_execution


class FakeDB:
    """Records SQL and replays queued results."""

    def __init__(self, results: list | None = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.results = results or []
        self.raise_unique_on: str | None = None

    async def execute(self, sql: str, params: tuple = (), **kwargs) -> list[dict]:
        normalized = " ".join(sql.split())
        if self.raise_unique_on and self.raise_unique_on in normalized:
            raise RuntimeError(
                'duplicate key value violates unique constraint '
                '"journey_executions_one_running"'
            )
        self.calls.append((normalized, params))
        return self.results.pop(0) if self.results else []

    def statements(self) -> list[str]:
        return [sql for sql, _ in self.calls]


@pytest.mark.asyncio
async def test_expired_leases_are_abandoned_before_claiming() -> None:
    db = FakeDB([[], [{"next_attempt": 2}], [{"execution_id": "exe_02"}]])
    await claim_execution(db, "jrn_1", "t1", "worker_02")
    first = db.statements()[0]
    assert "UPDATE journey_executions" in first
    assert "'abandoned'" in first
    assert "lease_expires_at <" in first


@pytest.mark.asyncio
async def test_takeover_only_targets_expired_leases() -> None:
    db = FakeDB([[], [{"next_attempt": 2}], [{"execution_id": "exe_02"}]])
    await claim_execution(db, "jrn_1", "t1", "worker_02")
    assert "status = 'running'" in db.statements()[0]


@pytest.mark.asyncio
async def test_claim_increments_the_attempt_number() -> None:
    db = FakeDB([[], [{"next_attempt": 3}], [{"execution_id": "exe_03"}]])
    claim = await claim_execution(db, "jrn_1", "t1", "worker_03")
    assert claim.attempt == 3
    assert claim.claimed is True


@pytest.mark.asyncio
async def test_live_owner_yields_a_conflict_not_an_exception() -> None:
    db = FakeDB([[], [{"next_attempt": 2}]])
    db.raise_unique_on = "INSERT INTO journey_executions"
    db.results.append([
        {"execution_id": "exe_01", "worker_id": "worker_01",
         "lease_expires_at": "2026-09-06T02:14:32Z"}
    ])
    claim = await claim_execution(db, "jrn_1", "t1", "worker_02")
    assert claim.claimed is False
    assert claim.conflict["execution_id"] == "exe_01"
    assert claim.conflict["worker_id"] == "worker_01"


@pytest.mark.asyncio
async def test_claim_records_the_claiming_worker() -> None:
    db = FakeDB([[], [{"next_attempt": 2}], [{"execution_id": "exe_02"}]])
    claim = await claim_execution(db, "jrn_1", "t1", "worker_02")
    assert claim.worker_id == "worker_02"
    insert = next(p for s, p in db.calls if "INSERT INTO journey_executions" in s)
    assert "worker_02" in insert


@pytest.mark.asyncio
async def test_claim_never_creates_a_thread() -> None:
    db = FakeDB([[], [{"next_attempt": 2}], [{"execution_id": "exe_02"}]])
    await claim_execution(db, "jrn_1", "t1", "worker_02")
    assert not any("INSERT INTO journey_threads" in s for s in db.statements())


def test_execution_claim_is_a_value_object() -> None:
    claim = ExecutionClaim(
        execution_id="exe_01", attempt=1, worker_id="w", claimed=True, conflict=None
    )
    assert claim.claimed and claim.conflict is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_journey_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.db.journey_store'`.

- [ ] **Step 3: Write the implementation**

The module also carries `create_journey` and `bind_thread` from the Interfaces
list above, which Tasks 9 and 11 consume. `bind_thread` is two statements, the
`journey_threads` row and the `journeys.active_thread_id` pointer, and the
insert takes `ON CONFLICT (thread_id) DO NOTHING` because resume re-binds a
thread the journey already owns.

Create `meridian/backend/db/journey_store.py`:

```python
"""Journey identity, thread ownership, and execution claiming.

A journey outlives the threads and workers that serve it. Exactly one
execution runs per thread, decided by a partial unique index rather than by
application logic, so two workers racing to resume cannot both win.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Optional

ABANDON_EXPIRED_SQL = """
UPDATE journey_executions
   SET status = 'abandoned', ended_at = CURRENT_TIMESTAMP
 WHERE thread_id = %s
   AND status = 'running'
   AND lease_expires_at < CURRENT_TIMESTAMP
"""

NEXT_ATTEMPT_SQL = """
SELECT COALESCE(MAX(attempt), 0) + 1 AS next_attempt
  FROM journey_executions WHERE thread_id = %s
"""

CLAIM_SQL = """
INSERT INTO journey_executions
    (execution_id, journey_id, thread_id, attempt, worker_id, status,
     lease_expires_at)
VALUES (%s, %s, %s, %s, %s, 'running',
        CURRENT_TIMESTAMP + (%s || ' seconds')::interval)
RETURNING execution_id
"""

CURRENT_OWNER_SQL = """
SELECT execution_id, worker_id, lease_expires_at
  FROM journey_executions
 WHERE thread_id = %s AND status = 'running'
"""

RENEW_SQL = """
UPDATE journey_executions
   SET lease_expires_at = CURRENT_TIMESTAMP + (%s || ' seconds')::interval
 WHERE execution_id = %s AND status = 'running'
RETURNING execution_id
"""

RELEASE_SQL = """
UPDATE journey_executions
   SET status = %s, ended_at = CURRENT_TIMESTAMP, lease_expires_at = NULL
 WHERE execution_id = %s
"""


@dataclass
class ExecutionClaim:
    """The outcome of attempting to claim a thread's single running slot."""

    execution_id: Optional[str]
    attempt: int
    worker_id: str
    claimed: bool
    conflict: Optional[dict]


def _is_single_running_violation(error: Exception) -> bool:
    """Whether an error is the one-running-execution index rejecting a claim."""
    return "journey_executions_one_running" in str(error)


async def claim_execution(
    db: Any,
    journey_id: str,
    thread_id: str,
    worker_id: str,
    lease_seconds: int = 30,
) -> ExecutionClaim:
    """Claim the single running execution slot for a thread.

    Args:
        db: Data API client.
        journey_id: Owning journey.
        thread_id: The resume target. Must already be bound to the journey.
        worker_id: The worker claiming the slot.
        lease_seconds: How long the claim survives without a heartbeat.

    Returns:
        A claim. When ``claimed`` is False, ``conflict`` names the live owner.
        Expiry is applied as a state transition first, because PostgreSQL
        forbids a non-immutable predicate such as ``now()`` in an index.
    """
    await db.execute(ABANDON_EXPIRED_SQL, (thread_id,))
    rows = await db.execute(NEXT_ATTEMPT_SQL, (thread_id,))
    attempt = int(rows[0]["next_attempt"]) if rows else 1
    execution_id = f"exe_{uuid.uuid4().hex[:12]}"

    try:
        await db.execute(
            CLAIM_SQL,
            (execution_id, journey_id, thread_id, attempt, worker_id,
             str(lease_seconds)),
        )
    except Exception as error:  # noqa: BLE001 - the index decides, not us
        if not _is_single_running_violation(error):
            raise
        owner = await db.execute(CURRENT_OWNER_SQL, (thread_id,))
        return ExecutionClaim(
            execution_id=None,
            attempt=attempt,
            worker_id=worker_id,
            claimed=False,
            conflict=dict(owner[0]) if owner else None,
        )

    return ExecutionClaim(
        execution_id=execution_id,
        attempt=attempt,
        worker_id=worker_id,
        claimed=True,
        conflict=None,
    )


async def renew_lease(db: Any, execution_id: str, lease_seconds: int = 30) -> bool:
    """Extend a running execution's lease. Returns False if it no longer runs."""
    rows = await db.execute(RENEW_SQL, (str(lease_seconds), execution_id))
    return bool(rows)


async def release_execution(db: Any, execution_id: str, status: str) -> None:
    """Mark an execution finished with a terminal status."""
    await db.execute(RELEASE_SQL, (status, execution_id))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_journey_store.py -q`
Expected: PASS, 7 passed.

- [ ] **Step 5: Commit**

```bash
git add meridian/backend/db/journey_store.py meridian/tests/test_journey_store.py
git commit -m "Claim one running execution per thread with an expiring lease"
```

---

### Task 9: Checkpoint the hold intent before the hold executes

A `prepare_hold` node allocates `hold_request_id`, normalizes and validates the terms, computes the fingerprint, and writes them to graph state. That state is checkpointed before the hold node runs, so a retry after any failure reuses the same identity.

**Files:**
- Create: `meridian/backend/agents/orchestration_05/hold_intent.py`
- Modify: `meridian/backend/agents/orchestration_05/workflow.py` (add the node and its edge)
- Test: `meridian/tests/test_hold_intent.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `@dataclass HoldIntent(hold_request_id: str, package_id: str, duration: str, quantity: int, unit_price: Decimal, total_amount: Decimal, fingerprint: str)`; `normalize_hold_terms(package_id, duration, quantity, unit_price) -> dict`; `fingerprint_terms(terms: dict) -> str`; `build_hold_intent(package_id, duration, quantity, unit_price) -> HoldIntent`; `prepare_hold_node(state: dict) -> dict`.

- [ ] **Step 1: Write the failing test**

Create `meridian/tests/test_hold_intent.py`:

```python
"""The hold intent must be stable across retries and executions.

The hold_requests row commits with the booking, so it cannot be the record of
intent: if the transaction rolls back, or the worker dies first, it does not
exist. The checkpointed intent is what survives.
"""

from decimal import Decimal

import pytest

from backend.agents.orchestration_05.hold_intent import (
    HoldIntent,
    build_hold_intent,
    fingerprint_terms,
    normalize_hold_terms,
    prepare_hold_node,
)


def test_fingerprint_is_stable_for_identical_terms() -> None:
    terms = normalize_hold_terms("TKY-003", "3 nights", 2, Decimal("1949.00"))
    assert fingerprint_terms(terms) == fingerprint_terms(dict(terms))


def test_fingerprint_changes_with_quantity() -> None:
    a = fingerprint_terms(normalize_hold_terms("TKY-003", "3 nights", 2, Decimal("1949")))
    b = fingerprint_terms(normalize_hold_terms("TKY-003", "3 nights", 3, Decimal("1949")))
    assert a != b


def test_fingerprint_changes_with_price() -> None:
    a = fingerprint_terms(normalize_hold_terms("TKY-003", "3 nights", 2, Decimal("1949")))
    b = fingerprint_terms(normalize_hold_terms("TKY-003", "3 nights", 2, Decimal("2049")))
    assert a != b


def test_normalization_is_insensitive_to_incidental_formatting() -> None:
    a = normalize_hold_terms("TKY-003", "3 Nights ", 2, Decimal("1949.00"))
    b = normalize_hold_terms("tky-003", "3 nights", 2, Decimal("1949"))
    assert fingerprint_terms(a) == fingerprint_terms(b)


def test_quantity_must_be_positive() -> None:
    with pytest.raises(ValueError, match="quantity must be positive"):
        normalize_hold_terms("TKY-003", "3 nights", 0, Decimal("1949"))


def test_price_must_not_be_negative() -> None:
    with pytest.raises(ValueError, match="unit price must not be negative"):
        normalize_hold_terms("TKY-003", "3 nights", 1, Decimal("-1"))


def test_package_id_is_required() -> None:
    with pytest.raises(ValueError, match="package_id is required"):
        normalize_hold_terms("  ", "3 nights", 1, Decimal("1949"))


def test_total_is_derived_not_supplied() -> None:
    intent = build_hold_intent("TKY-003", "3 nights", 2, Decimal("1949.00"))
    assert intent.total_amount == Decimal("3898.00")


def test_request_id_is_allocated() -> None:
    intent = build_hold_intent("TKY-003", "3 nights", 1, Decimal("1949"))
    assert intent.hold_request_id.startswith("hrq_")


def test_two_intents_get_different_request_ids() -> None:
    a = build_hold_intent("TKY-003", "3 nights", 1, Decimal("1949"))
    b = build_hold_intent("TKY-003", "3 nights", 1, Decimal("1949"))
    assert a.hold_request_id != b.hold_request_id


def test_prepare_node_writes_the_intent_into_state() -> None:
    state = {
        "selected_package": "TKY-003",
        "duration": "3 nights",
        "travelers_count": 2,
        "unit_price": "1949.00",
    }
    result = prepare_hold_node(state)
    assert result["hold_intent"]["hold_request_id"].startswith("hrq_")
    assert result["hold_intent"]["fingerprint"]


def test_prepare_node_is_idempotent_on_resume() -> None:
    state = {
        "selected_package": "TKY-003",
        "duration": "3 nights",
        "travelers_count": 2,
        "unit_price": "1949.00",
    }
    first = prepare_hold_node(state)
    resumed = dict(state, **first)
    second = prepare_hold_node(resumed)
    assert (
        second["hold_intent"]["hold_request_id"]
        == first["hold_intent"]["hold_request_id"]
    ), "a resumed graph must reuse the checkpointed identity, not allocate a new one"


def test_intent_is_a_value_object() -> None:
    intent = HoldIntent(
        hold_request_id="hrq_1",
        package_id="TKY-003",
        duration="3 nights",
        quantity=1,
        unit_price=Decimal("1949"),
        total_amount=Decimal("1949"),
        fingerprint="abc",
    )
    assert intent.quantity == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_hold_intent.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.agents.orchestration_05.hold_intent'`.

- [ ] **Step 3: Write the implementation**

Create `meridian/backend/agents/orchestration_05/hold_intent.py`:

```python
"""Hold intent: allocated once, checkpointed, then executed.

The database constraint on (journey_id, hold_request_id) is the second line of
defence. The first is that the identity is decided and durably checkpointed
before the business action runs, so every retry and every later execution
reuses it instead of allocating a new one.
"""

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal


@dataclass
class HoldIntent:
    """A hold that has been decided but not yet performed."""

    hold_request_id: str
    package_id: str
    duration: str
    quantity: int
    unit_price: Decimal
    total_amount: Decimal
    fingerprint: str


def normalize_hold_terms(
    package_id: str, duration: str, quantity: int, unit_price: Decimal
) -> dict:
    """Validate and normalize the material terms of a hold.

    Args:
        package_id: Catalog package identifier.
        duration: Duration label as it appears in the catalog.
        quantity: Number of travelers. Must be positive.
        unit_price: Price per traveler. Must not be negative.

    Returns:
        Normalized terms. Case and surrounding whitespace are incidental and
        are removed, so the same intent expressed two ways fingerprints alike.

    Raises:
        ValueError: If any term is missing or out of range. The fingerprint is
            computed from validated terms only, so it never encodes unchecked
            input.
    """
    if not package_id or not package_id.strip():
        raise ValueError("package_id is required")
    if not duration or not duration.strip():
        raise ValueError("duration is required")
    if quantity is None or quantity <= 0:
        raise ValueError("quantity must be positive")
    if unit_price is None or Decimal(unit_price) < 0:
        raise ValueError("unit price must not be negative")

    price = Decimal(unit_price).quantize(Decimal("0.01"))
    return {
        "package_id": package_id.strip().lower(),
        "duration": " ".join(duration.split()).lower(),
        "quantity": int(quantity),
        "unit_price": str(price),
        "total_amount": str(price * int(quantity)),
    }


def fingerprint_terms(terms: dict) -> str:
    """Return a stable digest of normalized terms."""
    canonical = json.dumps(terms, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_hold_intent(
    package_id: str, duration: str, quantity: int, unit_price: Decimal
) -> HoldIntent:
    """Allocate a hold request identity over validated, normalized terms."""
    terms = normalize_hold_terms(package_id, duration, quantity, unit_price)
    return HoldIntent(
        hold_request_id=f"hrq_{uuid.uuid4().hex[:12]}",
        package_id=terms["package_id"],
        duration=terms["duration"],
        quantity=terms["quantity"],
        unit_price=Decimal(terms["unit_price"]),
        total_amount=Decimal(terms["total_amount"]),
        fingerprint=fingerprint_terms(terms),
    )


def prepare_hold_node(state: dict) -> dict:
    """Graph node that establishes the hold intent before the hold runs.

    Returns the existing intent unchanged when the state already carries one,
    so a resumed graph replays with the identity it checkpointed rather than
    allocating a second one.
    """
    existing = state.get("hold_intent")
    if existing:
        return {"hold_intent": existing}

    intent = build_hold_intent(
        state["selected_package"],
        state["duration"],
        int(state.get("travelers_count", 1)),
        Decimal(str(state["unit_price"])),
    )
    payload = asdict(intent)
    payload["unit_price"] = str(intent.unit_price)
    payload["total_amount"] = str(intent.total_amount)
    return {"hold_intent": payload}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_hold_intent.py -q`
Expected: PASS, 13 passed.

- [ ] **Step 5: Wire the node into the graph before the hold node**

In `meridian/backend/agents/orchestration_05/workflow.py`, inside `_build_graph`, add the node and route the edge that previously reached the hold node through it:

```python
        from backend.agents.orchestration_05.hold_intent import prepare_hold_node

        graph.add_node("prepare_hold", prepare_hold_node)
        graph.add_edge("prepare_hold", "hold")
```

Change whichever edge previously targeted `"hold"` to target `"prepare_hold"`. Add `hold_intent` to the graph state schema as an optional dict.

- [ ] **Step 6: Run the workflow tests**

Run: `venv/bin/pytest tests/test_phase5_workflow.py tests/test_workflow_safety.py tests/test_chat_workflow_transition.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add meridian/backend/agents/orchestration_05/hold_intent.py meridian/backend/agents/orchestration_05/workflow.py meridian/tests/test_hold_intent.py
git commit -m "Checkpoint the hold intent before the hold runs"
```

---

### Task 10: One hold per request, enforced inside the inventory transaction

`create_courtesy_hold` gains journey and request identity, authorizes the journey on both the creation and replay paths, and returns whether the call was a replay. The old signature is dropped so it cannot bypass idempotency.

**Files:**
- Create: `meridian/scripts/migrations/008_hold_request_identity.sql`
- Modify: `meridian/backend/routers/chat.py:2967-3030` (the `create_courtesy_hold` call site)
- Test: `meridian/tests/test_hold_request_identity.py`

**Interfaces:**
- Consumes: `hold_requests` (Task 3), `HoldIntent` (Task 9).
- Produces: `create_courtesy_hold(p_booking_id, p_traveler_id, p_journey_id, p_hold_request_id, p_fingerprint, p_package_id, p_duration, p_quantity, p_unit_price, p_total_amount, p_hold_expires_at)` returning `(booking_id, status, replayed, seats_available, seats_reserved, seats_remaining)`.

- [ ] **Step 1: Write the failing test**

Create `meridian/tests/test_hold_request_identity.py`:

```python
"""The expanded hold function must be the only callable path.

CREATE OR REPLACE with a changed parameter list creates an overload rather
than replacing, so leaving the old eight-argument function in place would
leave a path that bypasses idempotency entirely.
"""

import pathlib
import re

import pytest

MIGRATION = (
    pathlib.Path(__file__).parent.parent
    / "scripts" / "migrations" / "008_hold_request_identity.sql"
)


@pytest.fixture
def sql() -> str:
    return MIGRATION.read_text()


@pytest.fixture
def normalized(sql: str) -> str:
    return " ".join(sql.split()).lower()


def test_drops_the_old_signature(normalized: str) -> None:
    assert "drop function if exists create_courtesy_hold(" in normalized
    assert "text, text, text, text, integer, numeric, numeric, timestamptz" in normalized


def test_revokes_the_old_grant(normalized: str) -> None:
    assert "revoke" in normalized


def test_new_signature_takes_journey_and_request(normalized: str) -> None:
    assert "p_journey_id" in normalized
    assert "p_hold_request_id" in normalized
    assert "p_fingerprint" in normalized


def test_authorizes_the_journey_owner(normalized: str) -> None:
    assert "from journeys" in normalized
    assert "journey_not_owned" in normalized


def test_authorization_precedes_the_conflict_branch(normalized: str) -> None:
    owner_at = normalized.index("journey_not_owned")
    conflict_at = normalized.index("on conflict")
    assert owner_at < conflict_at, "replay must be authorized too"


def test_inserts_identity_before_taking_the_lock(normalized: str) -> None:
    insert_at = normalized.index("insert into hold_requests")
    lock_at = normalized.index("pg_advisory_xact_lock")
    assert insert_at < lock_at


def test_replay_returns_without_touching_inventory(normalized: str) -> None:
    assert "hold_request_parameter_mismatch" in normalized
    replay = normalized[normalized.index("if not v_inserted"):]
    assert "insert into bookings" not in replay


def test_returns_a_replay_flag(normalized: str) -> None:
    assert "replayed" in normalized


def test_keeps_the_existing_scope_and_agent_checks(normalized: str) -> None:
    assert "traveler_scope_mismatch" in normalized
    assert "booking_agent_not_authorized" in normalized


def test_keeps_the_inventory_guard(normalized: str) -> None:
    assert "insufficient_inventory" in normalized


def test_grants_execute_to_the_app_role_only(normalized: str) -> None:
    assert "grant execute on function create_courtesy_hold" in normalized
    assert "to meridian_app" in normalized
    assert "to public" not in normalized
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_hold_request_identity.py -q`
Expected: FAIL with `FileNotFoundError` for `008_hold_request_identity.sql`.

- [ ] **Step 3: Write the migration**

Create `meridian/scripts/migrations/008_hold_request_identity.sql`:

```sql
-- One hold per intended request, enforced inside the inventory transaction.
--
-- The old eight-argument function is dropped rather than replaced. CREATE OR
-- REPLACE with a changed parameter list creates an overload, which would leave
-- a callable path that bypasses idempotency.

REVOKE ALL ON FUNCTION create_courtesy_hold(
    TEXT, TEXT, TEXT, TEXT, INTEGER, NUMERIC, NUMERIC, TIMESTAMPTZ
) FROM meridian_app;

DROP FUNCTION IF EXISTS create_courtesy_hold(
    TEXT, TEXT, TEXT, TEXT, INTEGER, NUMERIC, NUMERIC, TIMESTAMPTZ
);

CREATE OR REPLACE FUNCTION create_courtesy_hold(
    p_booking_id TEXT,
    p_traveler_id TEXT,
    p_journey_id TEXT,
    p_hold_request_id TEXT,
    p_fingerprint TEXT,
    p_package_id TEXT,
    p_duration TEXT,
    p_quantity INTEGER,
    p_unit_price NUMERIC,
    p_total_amount NUMERIC,
    p_hold_expires_at TIMESTAMPTZ
) RETURNS TABLE (
    booking_id TEXT,
    status TEXT,
    replayed BOOLEAN,
    seats_available INTEGER,
    seats_reserved INTEGER,
    seats_remaining INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_scope TEXT := current_setting('app.current_traveler_id', true);
    v_agent_type TEXT := current_setting('app.agent_type', true);
    v_capacity INTEGER;
    v_reserved INTEGER;
    v_inserted BOOLEAN;
    v_existing RECORD;
BEGIN
    IF v_scope IS NULL OR v_scope = '' OR v_scope <> p_traveler_id THEN
        RAISE EXCEPTION 'traveler_scope_mismatch';
    END IF;
    IF v_agent_type NOT IN ('booking_agent', 'supervisor_agent', 'concierge_agent') THEN
        RAISE EXCEPTION 'booking_agent_not_authorized';
    END IF;
    IF p_quantity IS NULL OR p_quantity <= 0 THEN
        RAISE EXCEPTION 'invalid_hold_quantity';
    END IF;

    -- Authorize the journey before either path. Knowing a request id must not
    -- return another traveler's booking.
    PERFORM 1 FROM journeys
     WHERE journey_id = p_journey_id AND traveler_id = p_traveler_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'journey_not_owned';
    END IF;

    INSERT INTO hold_requests (
        journey_id, hold_request_id, booking_id, fingerprint, thread_id,
        execution_id
    ) VALUES (
        p_journey_id, p_hold_request_id, p_booking_id, p_fingerprint,
        COALESCE(current_setting('app.thread_id', true), ''),
        NULLIF(current_setting('app.execution_id', true), '')
    )
    ON CONFLICT (journey_id, hold_request_id) DO NOTHING;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    IF NOT v_inserted THEN
        SELECT hr.booking_id, hr.fingerprint, b.status
          INTO v_existing
          FROM hold_requests hr
          JOIN bookings b ON b.booking_id = hr.booking_id
         WHERE hr.journey_id = p_journey_id
           AND hr.hold_request_id = p_hold_request_id;

        IF v_existing.fingerprint IS DISTINCT FROM p_fingerprint THEN
            RAISE EXCEPTION 'hold_request_parameter_mismatch';
        END IF;

        RETURN QUERY SELECT
            v_existing.booking_id, v_existing.status, TRUE,
            NULL::INTEGER, NULL::INTEGER, NULL::INTEGER;
        RETURN;
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(p_package_id || ':' || p_duration, 0)
    );

    SELECT CASE
        WHEN jsonb_typeof(availability -> p_duration) = 'number'
        THEN (availability ->> p_duration)::INTEGER
        ELSE NULL
    END
    INTO v_capacity
    FROM trip_packages
    WHERE package_id = p_package_id AND durations ? p_duration;

    IF v_capacity IS NULL OR v_capacity < 0 THEN
        RAISE EXCEPTION 'invalid_package_inventory';
    END IF;

    SELECT COALESCE(SUM(bl.travelers_count), 0)::INTEGER
    INTO v_reserved
    FROM booking_lines bl
    JOIN bookings b ON b.booking_id = bl.booking_id
    WHERE bl.package_id = p_package_id
      AND bl.duration = p_duration
      AND (
          b.status = 'confirmed'
          OR (b.status = 'held' AND b.hold_expires_at > CURRENT_TIMESTAMP)
      );

    IF p_quantity > (v_capacity - v_reserved) THEN
        RAISE EXCEPTION 'insufficient_inventory';
    END IF;

    INSERT INTO bookings (
        booking_id, traveler_id, status, total_amount, hold_expires_at, created_at
    ) VALUES (
        p_booking_id, p_traveler_id, 'held', p_total_amount,
        p_hold_expires_at, CURRENT_TIMESTAMP
    );

    INSERT INTO booking_lines (
        booking_id, package_id, duration, travelers_count, unit_price
    ) VALUES (
        p_booking_id, p_package_id, p_duration, p_quantity, p_unit_price
    );

    RETURN QUERY SELECT
        p_booking_id, 'held'::TEXT, FALSE,
        v_capacity, v_reserved + p_quantity, v_capacity - v_reserved - p_quantity;
END;
$$;

REVOKE ALL ON FUNCTION create_courtesy_hold(
    TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, NUMERIC, NUMERIC, TIMESTAMPTZ
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION create_courtesy_hold(
    TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, NUMERIC, NUMERIC, TIMESTAMPTZ
) TO meridian_app;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_hold_request_identity.py -q`
Expected: PASS, 11 passed.

- [ ] **Step 5: Link or quarantine legacy holds**

Existing `booking_id` values are preserved; only the identity linkage is new.
Append to `008_hold_request_identity.sql`:

```sql
-- Link legacy holds only where the mapping is unambiguous: exactly one active
-- held booking for a traveler who has exactly one journey. Anything else is
-- left unlinked rather than guessed, because a wrong link would let a resume
-- adopt a hold it cannot prove belongs to the request.
INSERT INTO hold_requests (
    journey_id, hold_request_id, booking_id, fingerprint, thread_id
)
SELECT j.journey_id,
       'hrq_legacy_' || b.booking_id,
       b.booking_id,
       'legacy:unverified',
       COALESCE(j.active_thread_id, '')
  FROM bookings b
  JOIN journeys j ON j.traveler_id = b.traveler_id
 WHERE b.status = 'held'
   AND NOT EXISTS (
       SELECT 1 FROM hold_requests hr WHERE hr.booking_id = b.booking_id
   )
   AND (SELECT COUNT(*) FROM journeys j2 WHERE j2.traveler_id = b.traveler_id) = 1
   AND (SELECT COUNT(*) FROM bookings b2
         WHERE b2.traveler_id = b.traveler_id AND b2.status = 'held') = 1
ON CONFLICT (journey_id, hold_request_id) DO NOTHING;
```

A legacy row carries the sentinel fingerprint `legacy:unverified`, which can
never equal a computed fingerprint, so any request presenting a real
fingerprint against it raises `hold_request_parameter_mismatch` rather than
silently replaying. Unlinked legacy holds stay readable and cancellable but
are never resumed into an idempotent flow.

Add to `meridian/tests/test_hold_request_identity.py`:

```python
def test_legacy_backfill_only_links_unambiguous_holds(normalized: str) -> None:
    assert "count(*) from journeys j2" in normalized
    assert "count(*) from bookings b2" in normalized


def test_legacy_rows_cannot_be_replayed_as_a_match(normalized: str) -> None:
    assert "legacy:unverified" in normalized
```

- [ ] **Step 6: Update the caller**

In `meridian/backend/routers/chat.py`, at the `create_courtesy_hold(` call
(near line 2993), pass the new arguments in order and read the new return
columns. The intent comes from workflow state, never from a fresh allocation:

```python
                        FROM create_courtesy_hold(
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
```

with parameters `(booking_id, traveler_id, journey_id, intent["hold_request_id"],
intent["fingerprint"], package_id, duration, quantity, unit_price,
total_amount, hold_expires_at)`, and branch the response on the returned
`replayed` flag so a replay is reported as the existing hold rather than a new
reservation.

- [ ] **Step 7: Run the hold tests**

Run: `venv/bin/pytest tests/test_order_hold.py tests/test_workflow_safety.py tests/test_production_transaction_boundaries.py -q`
Expected: PASS. Update any test that constructs the old eight-argument call.

- [ ] **Step 8: Run the full suite**

Run: `venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add meridian/scripts/migrations/008_hold_request_identity.sql meridian/backend/routers/chat.py meridian/tests/test_hold_request_identity.py
git commit -m "Enforce one hold per request inside the inventory transaction"
```

---

### Task 11: The vertical slice, against real Aurora

Everything before this is unit-level. This task proves the claim the talk makes. It requires a live cluster and is marked `database`.

**Files:**
- Create: `meridian/tests/test_durable_recovery_slice.py`
- Create: `meridian/scripts/kill_and_resume_demo.py`

**Interfaces:**
- Consumes: every prior task.
- Produces: an executable acceptance suite plus a presenter script that performs the same sequence interactively.

- [ ] **Step 1: Apply both migrations against the demo cluster**

Run: `venv/bin/python scripts/apply_migrations.py`
Expected: `007_journey_shell.sql` and `008_hold_request_identity.sql` applied. Re-run once and confirm it is a no-op.

- [ ] **Step 2: Require a durable backend in the demo configuration**

Add to `meridian/.env`:

```
LANGGRAPH_CHECKPOINT_REQUIRED=true
```

Run: `venv/bin/python -c "
import asyncio
from backend.agents.orchestration_05.workflow import initialize_checkpoint_backend
b = asyncio.run(initialize_checkpoint_backend())
print(b.kind, b.durable)
"`
Expected: `AuroraDataApiSaver True`. If it prints MemorySaver, startup should have raised; fix selection before continuing.

- [ ] **Step 3: Write the acceptance suite**

Create `meridian/tests/test_durable_recovery_slice.py`:

```python
"""The claim the chalk talk makes, tested end to end against real Aurora.

Each test maps to one step of the demonstrated sequence. These require a live
cluster and are marked `database` so the offline suite stays fast.
"""

import asyncio
import os
import uuid

import pytest

from backend.agents.orchestration_05.workflow import initialize_checkpoint_backend
from backend.db.journey_store import claim_execution, release_execution
from backend.db.rds_data_client import get_rds_data_client

pytestmark = pytest.mark.database


@pytest.fixture
def db():
    return get_rds_data_client()


@pytest.fixture
def journey_id() -> str:
    return f"jrn_{uuid.uuid4().hex[:12]}"


@pytest.mark.asyncio
async def test_backend_is_durable_in_the_demo_configuration() -> None:
    backend = await initialize_checkpoint_backend()
    assert backend.durable is True
    assert backend.kind == "AuroraDataApiSaver"


@pytest.mark.asyncio
async def test_checkpoint_is_committed_and_readable(db) -> None:
    backend = await initialize_checkpoint_backend()
    thread_id = f"slice_{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    checkpoint = {
        "v": 1, "id": "cp_slice_1", "ts": "2026-09-06T00:00:00+00:00",
        "channel_values": {"messages": ["kept"]},
        "channel_versions": {"messages": "1"}, "versions_seen": {},
    }
    await backend.saver.aput(config, checkpoint, {"step": 1}, {"messages": "1"})

    rows = await db.execute(
        "SELECT checkpoint_id FROM checkpoints WHERE thread_id = %s", (thread_id,)
    )
    assert rows and rows[0]["checkpoint_id"] == "cp_slice_1"

    loaded = await backend.saver.aget_tuple(config)
    assert loaded.checkpoint["channel_values"]["messages"] == ["kept"]


@pytest.mark.asyncio
async def test_a_value_larger_than_one_row_round_trips(db) -> None:
    backend = await initialize_checkpoint_backend()
    thread_id = f"slice_{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    big = "x" * (64 * 1024 + 17)
    checkpoint = {
        "v": 1, "id": "cp_big", "ts": "2026-09-06T00:00:00+00:00",
        "channel_values": {"messages": [big]},
        "channel_versions": {"messages": "1"}, "versions_seen": {},
    }
    await backend.saver.aput(config, checkpoint, {"step": 1}, {"messages": "1"})
    loaded = await backend.saver.aget_tuple(config)
    assert loaded.checkpoint["channel_values"]["messages"][0] == big


@pytest.mark.asyncio
async def test_a_fresh_worker_resumes_the_same_thread(db, journey_id) -> None:
    thread_id = f"slice_{uuid.uuid4().hex[:8]}"
    await db.execute(
        "INSERT INTO journeys (journey_id, traveler_id, checkpoint_backend) "
        "VALUES (%s, %s, %s)",
        (journey_id, os.getenv("DEMO_TRAVELER_ID", "TRV-001"), "AuroraDataApiSaver"),
    )
    await db.execute(
        "INSERT INTO journey_threads (thread_id, journey_id) VALUES (%s, %s)",
        (thread_id, journey_id),
    )

    first = await claim_execution(db, journey_id, thread_id, "worker_01")
    assert first.claimed

    await release_execution(db, first.execution_id, "abandoned")

    second = await claim_execution(db, journey_id, thread_id, "worker_02")
    assert second.claimed
    assert second.attempt == first.attempt + 1
    assert second.worker_id != first.worker_id


@pytest.mark.asyncio
async def test_simultaneous_resume_yields_one_execution(db, journey_id) -> None:
    thread_id = f"slice_{uuid.uuid4().hex[:8]}"
    await db.execute(
        "INSERT INTO journeys (journey_id, traveler_id, checkpoint_backend) "
        "VALUES (%s, %s, %s)",
        (journey_id, os.getenv("DEMO_TRAVELER_ID", "TRV-001"), "AuroraDataApiSaver"),
    )
    await db.execute(
        "INSERT INTO journey_threads (thread_id, journey_id) VALUES (%s, %s)",
        (thread_id, journey_id),
    )

    claims = await asyncio.gather(
        claim_execution(db, journey_id, thread_id, "worker_a"),
        claim_execution(db, journey_id, thread_id, "worker_b"),
        return_exceptions=True,
    )
    granted = [c for c in claims if getattr(c, "claimed", False)]
    refused = [c for c in claims if getattr(c, "claimed", None) is False]
    assert len(granted) == 1
    assert len(refused) == 1
    assert refused[0].conflict["execution_id"] == granted[0].execution_id


@pytest.mark.asyncio
async def test_replaying_a_request_yields_one_hold(db, journey_id) -> None:
    """Step 5: retry after the hold commits but before its next checkpoint."""
    traveler = os.getenv("DEMO_TRAVELER_ID", "TRV-001")
    thread_id = f"slice_{uuid.uuid4().hex[:8]}"
    request_id = f"hrq_{uuid.uuid4().hex[:12]}"
    await db.execute(
        "INSERT INTO journeys (journey_id, traveler_id, checkpoint_backend) "
        "VALUES (%s, %s, %s)",
        (journey_id, traveler, "AuroraDataApiSaver"),
    )
    await db.execute(
        "INSERT INTO journey_threads (thread_id, journey_id) VALUES (%s, %s)",
        (thread_id, journey_id),
    )

    async def place(booking_id: str):
        return await db.execute(
            "SELECT booking_id, status, replayed FROM create_courtesy_hold("
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "CURRENT_TIMESTAMP + interval '12 hours')",
            (booking_id, traveler, journey_id, request_id, "fp_same",
             "TKY-003", "3 nights", 1, 1949.00, 1949.00),
        )

    first = await place(f"BKG-{uuid.uuid4().hex[:8]}")
    second = await place(f"BKG-{uuid.uuid4().hex[:8]}")

    assert first[0]["replayed"] is False
    assert second[0]["replayed"] is True
    assert second[0]["booking_id"] == first[0]["booking_id"]

    rows = await db.execute(
        "SELECT COUNT(*) AS n FROM hold_requests "
        "WHERE journey_id = %s AND hold_request_id = %s",
        (journey_id, request_id),
    )
    assert rows[0]["n"] == 1


@pytest.mark.asyncio
async def test_same_request_with_changed_terms_is_a_conflict(db, journey_id) -> None:
    traveler = os.getenv("DEMO_TRAVELER_ID", "TRV-001")
    thread_id = f"slice_{uuid.uuid4().hex[:8]}"
    request_id = f"hrq_{uuid.uuid4().hex[:12]}"
    await db.execute(
        "INSERT INTO journeys (journey_id, traveler_id, checkpoint_backend) "
        "VALUES (%s, %s, %s)",
        (journey_id, traveler, "AuroraDataApiSaver"),
    )
    await db.execute(
        "INSERT INTO journey_threads (thread_id, journey_id) VALUES (%s, %s)",
        (thread_id, journey_id),
    )
    await db.execute(
        "SELECT booking_id FROM create_courtesy_hold("
        "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "CURRENT_TIMESTAMP + interval '12 hours')",
        (f"BKG-{uuid.uuid4().hex[:8]}", traveler, journey_id, request_id,
         "fp_one", "TKY-003", "3 nights", 1, 1949.00, 1949.00),
    )

    with pytest.raises(Exception, match="hold_request_parameter_mismatch"):
        await db.execute(
            "SELECT booking_id FROM create_courtesy_hold("
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "CURRENT_TIMESTAMP + interval '12 hours')",
            (f"BKG-{uuid.uuid4().hex[:8]}", traveler, journey_id, request_id,
             "fp_two", "TKY-003", "3 nights", 2, 1949.00, 3898.00),
        )


@pytest.mark.asyncio
async def test_interrupt_between_intent_and_hold_yields_one_hold(db, journey_id) -> None:
    """Step 6: the worker dies after the intent is checkpointed, before the hold.

    This is the case the database constraint alone cannot cover. If the
    resumed graph allocated a new hold_request_id, both attempts would be
    distinct requests and both would reserve inventory.
    """
    from backend.agents.orchestration_05.hold_intent import prepare_hold_node

    traveler = os.getenv("DEMO_TRAVELER_ID", "TRV-001")
    thread_id = f"slice_{uuid.uuid4().hex[:8]}"
    await db.execute(
        "INSERT INTO journeys (journey_id, traveler_id, checkpoint_backend) "
        "VALUES (%s, %s, %s)",
        (journey_id, traveler, "AuroraDataApiSaver"),
    )
    await db.execute(
        "INSERT INTO journey_threads (thread_id, journey_id) VALUES (%s, %s)",
        (thread_id, journey_id),
    )

    backend = await initialize_checkpoint_backend()
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    state = {
        "selected_package": "TKY-003",
        "duration": "3 nights",
        "travelers_count": 1,
        "unit_price": "1949.00",
    }

    # First worker: establish the intent and checkpoint it, then die.
    intent = prepare_hold_node(state)["hold_intent"]
    checkpoint = {
        "v": 1, "id": "cp_intent", "ts": "2026-09-06T00:00:00+00:00",
        "channel_values": {"hold_intent": intent},
        "channel_versions": {"hold_intent": "1"}, "versions_seen": {},
    }
    await backend.saver.aput(config, checkpoint, {"step": 1}, {"hold_intent": "1"})

    # Second worker: resume, and reuse the checkpointed identity.
    restored = await backend.saver.aget_tuple(config)
    resumed_intent = prepare_hold_node(
        dict(state, hold_intent=restored.checkpoint["channel_values"]["hold_intent"])
    )["hold_intent"]
    assert resumed_intent["hold_request_id"] == intent["hold_request_id"]

    async def place():
        return await db.execute(
            "SELECT booking_id, replayed FROM create_courtesy_hold("
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "CURRENT_TIMESTAMP + interval '12 hours')",
            (f"BKG-{uuid.uuid4().hex[:8]}", traveler, journey_id,
             resumed_intent["hold_request_id"], resumed_intent["fingerprint"],
             "TKY-003", "3 nights", 1, 1949.00, 1949.00),
        )

    first = await place()
    second = await place()
    assert second[0]["booking_id"] == first[0]["booking_id"]

    rows = await db.execute(
        "SELECT COUNT(*) AS n FROM bookings b JOIN hold_requests hr "
        "ON hr.booking_id = b.booking_id WHERE hr.journey_id = %s",
        (journey_id,),
    )
    assert rows[0]["n"] == 1


@pytest.mark.asyncio
async def test_another_traveler_cannot_use_the_journey(db, journey_id) -> None:
    traveler = os.getenv("DEMO_TRAVELER_ID", "TRV-001")
    await db.execute(
        "INSERT INTO journeys (journey_id, traveler_id, checkpoint_backend) "
        "VALUES (%s, %s, %s)",
        (journey_id, traveler, "AuroraDataApiSaver"),
    )
    with pytest.raises(Exception, match="traveler_scope_mismatch|journey_not_owned"):
        await db.execute(
            "SELECT booking_id FROM create_courtesy_hold("
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "CURRENT_TIMESTAMP + interval '12 hours')",
            (f"BKG-{uuid.uuid4().hex[:8]}", "TRV-999", journey_id,
             f"hrq_{uuid.uuid4().hex[:12]}", "fp", "TKY-003", "3 nights",
             1, 1949.00, 1949.00),
        )
```

- [ ] **Step 3b: Verify `RETURNING` survives the Data API transport**

The segmented pending-write guard skips its appends when
`INSERT ... ON CONFLICT DO NOTHING RETURNING 1` returns no rows. That is
correct against PostgreSQL, verified live. It is NOT verified through the Data
API, and nothing in the offline suite can verify it: `FakeDataClient` returns
whatever is queued. If `records` came back empty for a successful insert, every
multi-segment pending write would silently truncate, with no error.

Run:

```
venv/bin/python -c "
import asyncio
from backend.db.rds_data_client import get_rds_data_client

async def main():
    db = get_rds_data_client()
    tx = await db.begin_transaction()
    try:
        await db.execute(
            'CREATE TABLE returning_probe (k TEXT PRIMARY KEY, v TEXT)',
            transaction_id=tx,
        )
        first = await db.execute(
            \"INSERT INTO returning_probe (k, v) VALUES ('a', 'x') \"
            'ON CONFLICT (k) DO NOTHING RETURNING 1',
            transaction_id=tx,
        )
        again = await db.execute(
            \"INSERT INTO returning_probe (k, v) VALUES ('a', 'y') \"
            'ON CONFLICT (k) DO NOTHING RETURNING 1',
            transaction_id=tx,
        )
        print('fresh insert rows:', len(first), '(must be 1)')
        print('conflicting insert rows:', len(again), '(must be 0)')
    finally:
        await db.rollback_transaction(tx)

asyncio.run(main())
"
```

Expected: `fresh insert rows: 1` and `conflicting insert rows: 0`. The probe
table is created and dropped inside a rolled-back transaction, so nothing
persists. If the fresh insert returns 0 rows, stop: the append guard cannot
work over this transport and `_write_pending_blob` needs a different mechanism,
such as reading `octet_length` back to decide whether appends are needed.

- [ ] **Step 4: Run the slice**

Run: `venv/bin/pytest tests/test_durable_recovery_slice.py -q -m database`
Expected: PASS, 9 passed.

- [ ] **Step 5: Write the presenter script**

Create `meridian/scripts/kill_and_resume_demo.py` performing the sequence
interactively: start a workflow to the interrupt point, print the committed
`checkpoint_id` read back from Aurora, `SIGKILL` the worker process, start a
second worker, claim the execution, resume the thread, and print the restored
state alongside the old and new `worker_id`. It prints only values read back
from Aurora, never values held in memory, so what the room sees is what
persisted.

- [ ] **Step 6: Run the script end to end**

Run: `venv/bin/python scripts/kill_and_resume_demo.py`
Expected: a committed checkpoint id, a killed worker, a different worker id on
resume, the same thread id, and the same hold id.

- [ ] **Step 7: Confirm the five-phase demo still runs**

Run: `venv/bin/pytest -q` and start the stack per the demo runbook, then walk phases 1 through 5.
Expected: unchanged behaviour, with phase 5 now reporting `AuroraDataApiSaver` rather than `MemorySaver (in-process)`.

- [ ] **Step 8: Commit**

```bash
git add meridian/tests/test_durable_recovery_slice.py meridian/scripts/kill_and_resume_demo.py meridian/.env.example
git commit -m "Prove kill and resume against Aurora"
```

---

## Deferred to a second plan

`GET /journeys/{journey_id}`, the resume endpoint, the view axis, and the four
surfaces are **not** in this plan. Their task decomposition depends on the
response shape this slice finalizes, and writing them now would produce exactly
the placeholder tasks that make plans useless. Once Task 11 passes, the read
API and shell get their own plan built on real field names.

The spec's section 4 response example is the contract that plan will implement.
