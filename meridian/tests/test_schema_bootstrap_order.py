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
from pathlib import Path

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
