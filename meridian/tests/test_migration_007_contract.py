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
