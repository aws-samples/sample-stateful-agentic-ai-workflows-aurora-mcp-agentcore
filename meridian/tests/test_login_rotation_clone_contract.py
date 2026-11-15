"""The Lambda's idea of a consistent login and clone matches what migration 034 creates."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "infra" / "functions" / "login_rotation" / "login_rotation.py"
MIGRATION = (ROOT / "scripts" / "migrations" / "034_login_rotation_clones.sql").read_text().lower()


def load():
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("login_rotation_contract", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_attribute_the_lambda_forbids_the_migration_also_refuses():
    for column in load().FORBIDDEN_ATTRIBUTES:
        assert column in MIGRATION, column


def test_the_attributes_the_lambda_compares_are_the_ones_the_clone_copies():
    lr = load()
    assert set(lr.SAME_ATTRIBUTES) == {"rolinherit", "rolconnlimit", "config"}
    assert "noinherit" in MIGRATION
    assert "alter role %i set %i = %l" in " ".join(MIGRATION.split())


def test_the_role_query_selects_every_column_the_lambda_reads():
    lr = load()
    selected = " ".join(lr.ROLE_QUERY.split())
    for column in lr.ROLE_COLUMNS:
        assert column in selected, column


def test_the_lambda_alters_only_a_role_it_checked():
    source = SOURCE.read_text()
    assert source.count('sql=f"ALTER ROLE') == 1
    assert 'ROLE_NAME.fullmatch(pending["username"])' in source
