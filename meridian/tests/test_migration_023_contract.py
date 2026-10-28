"""Migration 023 drops the legacy semantic_product_search function and nothing else."""

import pathlib
import subprocess

import pytest

from scripts.init_aurora_schema import split_sql

REPO = pathlib.Path(__file__).resolve().parents[2]
MIGRATION = REPO / "meridian" / "scripts" / "migrations" / "023_drop_legacy_product_search.sql"
CONTRACT_TEST = pathlib.Path(__file__).resolve()
LEGACY_NAME = b"semantic_product_search"


@pytest.fixture
def statements():
    code = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.lstrip().startswith("--")
    )
    return [" ".join(s.split()).lower().rstrip(";") for s in split_sql(code)]


def test_the_migration_drops_the_legacy_function_idempotently_and_nothing_else(statements):
    assert statements == ["drop function if exists semantic_product_search(vector, integer)"]


def test_the_migration_has_no_cascade_and_no_other_object(statements):
    assert not any("cascade" in s or "table" in s or "schema" in s for s in statements)


def _tracked_or_untracked_paths() -> list[pathlib.Path]:
    """Every file in the repository git tracks or could add, ignored files excluded."""
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO,
        capture_output=True,
        check=True,
    )
    return [REPO / name for name in listing.stdout.decode().split("\0") if name]


def test_nothing_in_the_repository_calls_the_legacy_function():
    readers = []
    for path in _tracked_or_untracked_paths():
        if path in (MIGRATION, CONTRACT_TEST) or not path.is_file():
            continue
        if LEGACY_NAME in path.read_bytes():
            readers.append(path.relative_to(REPO).as_posix())
    assert readers == [], (
        "semantic_product_search is referenced outside migration 023 and its contract test; "
        "remove the reference before dropping the function"
    )
