"""Migration 031 bounds every workload login's statements, lock waits and idle transactions."""

import pathlib
import re

from scripts.init_aurora_schema import split_sql

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts" / "migrations" / "031_workload_login_timeouts.sql"
)
LOGINS = (
    "meridian_backend", "meridian_gateway", "meridian_identity", "meridian_catalog",
    "meridian_workflow",
)
EXPECTED = {
    "statement_timeout": "30s",
    "lock_timeout": "10s",
    "idle_in_transaction_session_timeout": "120s",
}
SETTING = re.compile(r"alter role (\w+) set (\w+) = '([^']+)'")


def _statements() -> list[str]:
    code = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.lstrip().startswith("--")
    )
    return [" ".join(s.split()).lower().rstrip(";") for s in split_sql(code)]


def _settings() -> dict[tuple[str, str], str]:
    found = {}
    for statement in _statements():
        match = SETTING.fullmatch(statement)
        if match:
            found[(match.group(1), match.group(2))] = match.group(3)
    return found


def test_every_workload_login_gets_all_three_limits():
    expected = {(login, name): value for login in LOGINS for name, value in EXPECTED.items()}
    assert _settings() == expected


def test_the_master_login_and_meridian_app_are_left_alone():
    touched = {login for login, _ in _settings()}
    assert "meridian_admin" not in touched
    assert "meridian_app" not in touched


def test_it_refuses_to_run_when_a_login_is_missing():
    text = MIGRATION.read_text()
    assert "RAISE EXCEPTION" in text
    for login in LOGINS:
        assert f"'{login}'" in text


def test_the_statement_limit_is_below_the_data_api_call_timeout():
    seconds = int(EXPECTED["statement_timeout"].rstrip("s"))
    assert seconds < 45
    idle = int(EXPECTED["idle_in_transaction_session_timeout"].rstrip("s"))
    assert idle < 180
