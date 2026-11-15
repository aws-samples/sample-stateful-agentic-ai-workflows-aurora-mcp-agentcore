"""Migration 034 creates one clone per workload login, and the role that sets their passwords.

The statements are read from the file. What Aurora does with them is checked live by
tests/test_migration_034_live.py (marked ``database``).
"""

import pathlib
import re

import pytest

from scripts.init_aurora_schema import split_sql
from scripts.provision_service_logins import LOGINS as SERVICE_LOGINS
from scripts.provision_workflow_login import ROLE as WORKFLOW_ROLE
from tests.test_migration_031_contract import LOGINS as TIMEOUT_LOGINS

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "migrations"
MIGRATION = MIGRATIONS / "034_login_rotation_clones.sql"
LOGIN_ROLES = (*TIMEOUT_LOGINS, "meridian_audit_export")
CLONE_ATTRIBUTES = "nologin nobypassrls noinherit nocreatedb nocreaterole"


@pytest.fixture(scope="module")
def code():
    text = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.lstrip().startswith("--")
    )
    return " ".join(text.lower().split())


def array_of_logins(code, start):
    """The quoted role names of the first ARRAY[...] at or after ``start``."""
    begin = code.index("array[", start)
    block = code[begin: code.index("]", begin)]
    return tuple(re.findall(r"'(\w+)'", block))


def test_the_logins_are_every_provisioned_login(code):
    provisioned = {spec.role for spec in SERVICE_LOGINS.values()} | {WORKFLOW_ROLE}
    first = array_of_logins(code, 0)
    assert set(first) == provisioned == set(LOGIN_ROLES)
    second_loop = code.index("meridian_rotator nologin")
    assert array_of_logins(code, second_loop) == first


def test_the_clone_is_created_with_the_attributes_the_logins_have(code):
    assert f"create role %i {CLONE_ATTRIBUTES}" in code
    for number in ("015", "018", "022", "033"):
        original = " ".join(next(MIGRATIONS.glob(f"{number}_*.sql")).read_text().lower().split())
        assert "nologin nobypassrls noinherit nocreatedb nocreaterole" in original


def test_the_clone_holds_no_grant_of_its_own_but_membership_of_its_login(code):
    assert "grant %i to %i with inherit true, set true', login_name, clone_name" in code
    assert not re.search(r"grant (select|insert|update|delete|execute|usage|all)\b", code)
    assert "create policy" not in code
    assert "alter policy" not in code


def test_an_existing_clone_with_other_attributes_or_roles_stops_the_migration(code):
    attributes = (
        "rolsuper or rolbypassrls or rolcreatedb or rolcreaterole or rolreplication "
        "or rolinherit"
    )
    assert attributes in code
    assert "is a member of a role other than % that 034 does not allow" in code


def test_the_clone_copies_the_logins_role_settings_and_resets_stale_ones(code):
    assert "alter role %i reset all" in code
    assert "from pg_db_role_setting s join pg_roles r on r.oid = s.setrole" in code
    assert "where r.rolname = login_name and s.setdatabase = 0" in code
    assert "alter role %i set %i = %l" in code
    assert "alter role meridian_" not in code


def test_the_rotator_can_create_roles_and_nothing_else_elevated(code):
    rotator = "create role meridian_rotator nologin nobypassrls noinherit nocreatedb createrole"
    assert rotator in code
    assert "or not rolcreaterole" in code
    assert "meridian_rotator holds table grants 034 does not allow" in code
    assert "bypassrls" in code


def test_the_rotator_gets_admin_without_inherit_or_set_on_each_login_and_clone(code):
    grants = re.findall(r"grant %i to meridian_rotator with ([a-z ,]+)'", code)
    assert grants == ["admin true, inherit false, set false"] * 2
    assert "login_name || '_clone'" in code


def test_the_migration_never_gives_a_role_a_password_or_login(code):
    assert "password" not in code
    assert " login " not in code.replace("nologin", "")


def test_the_migration_number_is_unique_and_follows_033():
    numbers = sorted(int(path.name[:3]) for path in MIGRATIONS.glob("*.sql"))
    assert 34 in numbers
    assert len(numbers) == len(set(numbers))


def test_the_file_is_one_statement_for_the_runner():
    assert len(split_sql(MIGRATION.read_text())) == 1
