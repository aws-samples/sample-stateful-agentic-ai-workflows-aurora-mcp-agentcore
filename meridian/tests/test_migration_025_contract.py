"""Migration 025 retires journeys.checkpoint_backend once every writer uses snapshot_store."""

import pathlib

from scripts.init_aurora_schema import split_sql

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "migrations"
MIGRATION = MIGRATIONS / "025_drop_journeys_checkpoint_backend.sql"


def _statements() -> list[str]:
    code = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.lstrip().startswith("--")
    )
    return [" ".join(s.split()).lower().rstrip(";") for s in split_sql(code)]


def test_the_trigger_and_its_function_go_before_the_old_column():
    assert _statements() == [
        "drop trigger if exists journeys_sync_snapshot_store on journeys",
        "drop function if exists journeys_sync_snapshot_store()",
        "alter table journeys drop column if exists checkpoint_backend",
    ]


def test_snapshot_store_is_left_alone():
    assert not any("snapshot_store" in s for s in _statements() if "drop column" in s)


def test_the_header_names_the_deploy_it_waits_for():
    header = MIGRATION.read_text().split("DROP", 1)[0]
    for writer in ("backend", "MeridianHolds Lambda", "MeridianWorkflow"):
        assert writer in header
