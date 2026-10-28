"""The documented bootstrap has to work on an empty database.

The failure this guards was invisible in development: rls_app_role.sql grants
on agent_audit_log, rls_for_agents.sql creates it, and the role file used to be
applied first. Any database that had been seeded once already had the table, so
the grant succeeded and the ordering defect stayed hidden until someone ran the
documented sequence against a fresh cluster, where it aborts.

These tests reconstruct the order init_aurora_schema plans and assert the
dependency invariant over it: nothing is granted before it exists, and nothing
is granted to a role before that role exists. That is a structural check, not a
substitute for running the DDL - a real empty-PostgreSQL bootstrap test is the
only thing that proves the SQL executes.
"""

from __future__ import annotations

import re

import pytest

from scripts.init_aurora_schema import (
    RLS_APP_ROLE_PATH,
    RLS_PATH,
    SCHEMA_PATH,
    _grants_on_objects,
    split_sql,
)


def planned_statements() -> list[str]:
    """Mirror the phase order initialize_database applies."""
    role_setup, object_grants = [], []
    for sql in split_sql(RLS_APP_ROLE_PATH.read_text()):
        (object_grants if _grants_on_objects(sql) else role_setup).append(sql)

    return [
        *split_sql(SCHEMA_PATH.read_text()),
        *role_setup,
        *split_sql(RLS_PATH.read_text()),
        *object_grants,
    ]


def created_tables(sql: str) -> set[str]:
    return set(
        re.findall(
            r"CREATE TABLE (?:IF NOT EXISTS )?([A-Za-z_][A-Za-z0-9_]*)", sql, re.IGNORECASE
        )
    )


def granted_tables(sql: str) -> set[str]:
    """Table names named by a GRANT ... ON <objects> TO <role>."""
    match = re.search(r"\bON\b(.*?)\bTO\b", sql, re.IGNORECASE | re.DOTALL)
    if not match:
        return set()
    body = match.group(1)
    if re.search(r"\b(ALL |FUNCTION|SCHEMA|SEQUENCE)", body, re.IGNORECASE):
        return set()
    return {
        token.strip()
        for token in body.split(",")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token.strip())
    }


def test_every_granted_table_is_created_first():
    """The regression itself: agent_audit_log was granted before it existed."""
    existing: set[str] = set()
    for sql in planned_statements():
        missing = granted_tables(sql) - existing
        assert not missing, (
            f"GRANT references {sorted(missing)} before any CREATE TABLE for it. "
            "On an empty database this aborts the documented bootstrap."
        )
        existing |= created_tables(sql)


def test_app_role_exists_before_anything_grants_to_it():
    """The other direction: rls_for_agents.sql grants EXECUTE to meridian_app."""
    role_created = False
    for sql in planned_statements():
        if re.search(r"CREATE ROLE meridian_app", sql, re.IGNORECASE):
            role_created = True
        if re.search(r"\bTO meridian_app\b", sql, re.IGNORECASE):
            assert role_created, (
                "A statement grants to meridian_app before the role is created."
            )


def test_agent_audit_log_orders_correctly():
    """Name the specific pair, so a reorder that reintroduces it fails loudly."""
    order = planned_statements()
    creates = next(
        i for i, sql in enumerate(order) if "agent_audit_log" in creates_target(sql)
    )
    grants = next(
        i for i, sql in enumerate(order) if "agent_audit_log" in granted_tables(sql)
    )
    assert creates < grants, "agent_audit_log must be created before it is granted on"


def creates_target(sql: str) -> set[str]:
    return created_tables(sql)


@pytest.mark.parametrize(
    "statement,expected",
    [
        ("GRANT meridian_app TO meridian_admin;", False),
        ("GRANT SELECT ON travelers TO meridian_app;", True),
        ("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO meridian_app;", True),
        ("CREATE ROLE meridian_app NOLOGIN;", False),
    ],
)
def test_grant_classification(statement: str, expected: bool):
    """Role grants and object grants land in different phases."""
    assert _grants_on_objects(statement) is expected


MIGRATION_021 = RLS_PATH.parents[1] / "scripts" / "migrations" / "021_audit_outcome.sql"


def _columns_added_by_migration_021() -> set[str]:
    return set(re.findall(r"ADD COLUMN IF NOT EXISTS (\w+)", MIGRATION_021.read_text()))


def _audit_log_columns(sql: str) -> set[str]:
    """Columns from the CREATE TABLE body plus every ALTER ... ADD COLUMN IF NOT EXISTS."""
    body = re.search(r"CREATE TABLE IF NOT EXISTS agent_audit_log \((.*?)\n\);", sql, re.DOTALL)
    assert body, "examples/rls_for_agents.sql has no CREATE TABLE agent_audit_log"
    declared = set(re.findall(r"^\s*([a-z_]\w*)\s+[A-Z]", body.group(1), re.MULTILINE))
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    alters = re.findall(r"ALTER TABLE agent_audit_log\s(.*?);", code, re.DOTALL)
    added = {
        name for alter in alters for name in re.findall(r"ADD COLUMN IF NOT EXISTS (\w+)", alter)
    }
    return declared | added


def _split_top_level_commas(text: str) -> list[str]:
    """Split on commas outside parentheses and single-quoted text."""
    parts, depth, quoted, start = [], 0, False, 0
    for index, character in enumerate(text):
        if character == "'":
            quoted = not quoted
        elif quoted:
            continue
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _audit_view_columns(sql: str) -> set[str]:
    view = re.search(
        r"CREATE VIEW agent_iam_audit AS\s+SELECT(.*?)FROM agent_audit_log", sql, re.DOTALL
    )
    assert view, "examples/rls_for_agents.sql has no CREATE VIEW agent_iam_audit"
    return {column.strip() for column in _split_top_level_commas(view.group(1)) if column.strip()}


def test_the_audit_log_column_collector_ignores_other_tables():
    sql = (
        "CREATE TABLE IF NOT EXISTS agent_audit_log (\n    id BIGINT\n);\n"
        "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS agent_access TEXT;\n"
        "ALTER TABLE agent_audit_log\n    ADD COLUMN IF NOT EXISTS turn_error TEXT;\n"
    )
    assert _audit_log_columns(sql) == {"id", "turn_error"}


def test_the_view_column_parser_keeps_commas_inside_parentheses_and_quotes_together():
    sql = (
        "CREATE VIEW agent_iam_audit AS\n  SELECT id, COALESCE(tool_calls, '[]') AS calls,\n"
        "         'a, b' AS label, turn_error\n  FROM agent_audit_log"
    )
    assert _audit_view_columns(sql) == {
        "id", "COALESCE(tool_calls, '[]') AS calls", "'a, b' AS label", "turn_error"}


def test_base_bootstrap_mirrors_every_column_migration_021_adds():
    """A fresh bootstrap skips migrations 001-021, so the base file must carry their columns."""
    base = RLS_PATH.read_text()
    added = _columns_added_by_migration_021()
    assert added == {"tool_calls", "turn_error"}, "migration 021 changed shape"
    assert not added - _audit_log_columns(base), "agent_audit_log is missing migration 021 columns"
    assert not added - _audit_view_columns(base), "agent_iam_audit is missing migration 021 columns"


def _audit_columns_definition(sql: str) -> str:
    """The tool_calls and turn_error columns with their CHECKs, whitespace collapsed."""
    found = re.search(r"ADD COLUMN IF NOT EXISTS (tool_calls .*?);", sql, re.DOTALL)
    assert found, "no ADD COLUMN IF NOT EXISTS tool_calls"
    code = "\n".join(line for line in found.group(1).splitlines()
                     if not line.lstrip().startswith("--"))
    return " ".join(code.split())


def test_base_bootstrap_checks_the_audit_columns_exactly_as_migration_021_does():
    base = _audit_columns_definition(RLS_PATH.read_text())
    assert "ADD COLUMN IF NOT EXISTS turn_error TEXT" in base
    assert base == _audit_columns_definition(MIGRATION_021.read_text())
