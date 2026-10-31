"""Migration 024 adds journeys.snapshot_store beside checkpoint_backend and keeps them equal."""

import pathlib
import re
import subprocess

import pytest

from scripts.init_aurora_schema import split_sql

REPO = pathlib.Path(__file__).resolve().parents[2]
MIGRATIONS = REPO / "meridian" / "scripts" / "migrations"
MIGRATION = MIGRATIONS / "024_journeys_snapshot_store.sql"
OLD_NAME = "checkpoint_backend"
ORIGINAL_TABLE = MIGRATIONS / "007_journey_shell.sql"
CONTRACT = MIGRATIONS / "025_drop_journeys_checkpoint_backend.sql"


def _code() -> str:
    return "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.lstrip().startswith("--")
    )


@pytest.fixture
def statements():
    return [" ".join(s.split()).lower().rstrip(";") for s in split_sql(_code())]


def test_the_column_has_the_old_columns_type_and_is_added_idempotently(statements):
    assert re.search(r"checkpoint_backend\s+VARCHAR\(32\) NOT NULL,", ORIGINAL_TABLE.read_text())
    assert statements[0] == (
        "alter table journeys add column if not exists snapshot_store varchar(32)"
    )


def test_the_trigger_function_pins_its_search_path_and_copies_both_ways(statements):
    function = statements[1]
    assert function.startswith("create or replace function journeys_sync_snapshot_store()")
    assert "set search_path = pg_catalog, pg_temp" in function
    assert "new.snapshot_store := new.checkpoint_backend" in function
    assert "new.checkpoint_backend := new.snapshot_store" in function


def test_the_trigger_runs_before_insert_or_update_and_is_recreated_idempotently(statements):
    assert statements[2] == "drop trigger if exists journeys_sync_snapshot_store on journeys"
    assert statements[3] == (
        "create trigger journeys_sync_snapshot_store before insert or update on journeys "
        "for each row execute function journeys_sync_snapshot_store()"
    )


def test_backfill_precedes_not_null_and_the_old_column_stays(statements):
    assert statements[4] == (
        "update journeys set snapshot_store = checkpoint_backend where snapshot_store is null"
    )
    assert statements[5] == "alter table journeys alter column snapshot_store set not null"
    assert len(statements) == 6
    assert not any("drop column" in s or "rename" in s for s in statements)


def test_only_025_names_the_old_column_after_this_migration_and_it_drops_it():
    later = [
        p.name
        for p in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
        if p.name not in (ORIGINAL_TABLE.name, MIGRATION.name) and OLD_NAME in p.read_text()
    ]
    assert later == [CONTRACT.name]
    assert "drop column if exists checkpoint_backend" in CONTRACT.read_text().lower()


def _tracked_or_untracked_paths() -> list[pathlib.Path]:
    """Every file in the repository git tracks or could add, ignored files excluded."""
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO,
        capture_output=True,
        check=True,
    )
    return [REPO / name for name in listing.stdout.decode().split("\0") if name]


def test_no_application_code_names_the_old_column_or_the_old_health_field():
    allowed = {
        ORIGINAL_TABLE,
        MIGRATION,
        pathlib.Path(__file__).resolve(),
        REPO / "meridian" / "tests" / "test_health_config.py",
        CONTRACT,
        REPO / "meridian" / "tests" / "test_migration_025_contract.py",
        REPO / "meridian" / "tests" / "test_migration_025_live.py",
    }
    retired = (OLD_NAME, "checkpoint_required")
    holders = []
    for path in _tracked_or_untracked_paths():
        if path in allowed or not path.is_file() or "langgraph" in path.parts:
            continue
        data = path.read_bytes()
        if any(name.encode() in data for name in retired):
            holders.append(path.relative_to(REPO).as_posix())
    assert holders == []
