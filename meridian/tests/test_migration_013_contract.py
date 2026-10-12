"""Migration 013 must scope checkpoints and the audit log without breaking the saver.

The saver runs as the master role and the journey document is the only scoped
reader of checkpoints, so the application role keeps SELECT on one table behind
a policy and loses everything else. The audit log stays append-only (011).
"""

from __future__ import annotations

import pathlib

import pytest

from scripts.init_aurora_schema import split_sql

MIGRATION = (
    pathlib.Path(__file__).parent.parent
    / "scripts" / "migrations" / "013_scope_checkpoints_and_agent_audit.sql"
)
SCOPE = "traveler_id = current_setting('app.current_traveler_id', true)"


@pytest.fixture
def statements() -> list[str]:
    code = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.startswith("--")
    )
    return [" ".join(s.split()).lower().rstrip(";") for s in split_sql(code)]


@pytest.fixture
def sql(statements: list[str]) -> str:
    return " ".join(statements)


def test_checkpoints_loses_every_write_privilege(statements: list[str]) -> None:
    assert "revoke insert, update, delete on checkpoints from meridian_app".lower() in statements


def test_sibling_checkpoint_tables_lose_all_privileges(statements: list[str]) -> None:
    assert (
        "revoke all on checkpoint_blobs, checkpoint_writes, checkpoint_migrations "
        "from meridian_app".lower()
    ) in statements


def test_checkpoints_select_is_never_revoked(sql: str) -> None:
    assert "revoke select" not in sql
    assert "revoke all on checkpoints" not in sql


def test_checkpoint_policy_follows_visible_journey_threads(sql: str) -> None:
    assert "alter table checkpoints enable row level security" in sql
    assert (
        "create policy checkpoints_traveler_select on checkpoints "
        "for select to meridian_app"
    ) in sql
    assert (
        "exists ( select 1 from journey_threads jt "
        "where jt.thread_id = checkpoints.thread_id )"
    ) in sql


def test_checkpoints_is_not_forced(sql: str) -> None:
    """The saver owns these tables; FORCE could lock it out."""
    assert "alter table checkpoints force" not in sql
    assert sql.count("force row level security") == 1
    assert "alter table agent_audit_log force row level security" in sql


def test_audit_log_is_scoped_for_select_and_insert(sql: str) -> None:
    assert "alter table agent_audit_log enable row level security" in sql
    assert (
        "create policy agent_audit_traveler_select on agent_audit_log "
        f"for select to meridian_app using ({SCOPE})"
    ) in sql
    assert (
        "create policy agent_audit_traveler_insert on agent_audit_log "
        f"for insert to meridian_app with check ({SCOPE})"
    ) in sql


def test_audit_log_stays_append_only(sql: str) -> None:
    assert "grant" not in sql
    assert "for update" not in sql
    assert "for delete" not in sql
    assert "for all" not in sql


def test_every_policy_is_dropped_first_so_reruns_succeed(statements: list[str]) -> None:
    policies = [i for i, s in enumerate(statements) if s.startswith("create policy")]
    assert len(policies) == 3
    for index in policies:
        _, _, name, _, table, *_ = statements[index].split()
        assert statements[index - 1] == f"drop policy if exists {name} on {table}"


def test_no_policy_is_open_to_public(sql: str) -> None:
    assert "to public" not in sql
