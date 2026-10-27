"""Migration 021 adds the nullable tool_calls and turn_error columns, checks them, and appends
them to the view."""

import pathlib
import re

import pytest

from backend.audit_vocabulary import (
    CALL_RESULTS,
    DENYING_LAYERS,
    REFUSING_LAYERS,
    TURN_ERROR_LIMIT,
)
from scripts.init_aurora_schema import split_sql

MIGRATIONS = pathlib.Path(__file__).parent.parent / "scripts" / "migrations"
MIGRATION = MIGRATIONS / "021_audit_outcome.sql"


@pytest.fixture
def statements():
    code = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.lstrip().startswith("--")
    )
    return [" ".join(s.split()).lower().rstrip(";") for s in split_sql(code)]


def test_it_is_the_next_migration_and_the_only_021():
    names = sorted(p.name for p in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    assert [n for n in names if n.startswith("021_")] == ["021_audit_outcome.sql"]
    assert names[names.index("021_audit_outcome.sql") - 1].startswith("020_")


def test_two_columns_are_added_idempotently_and_stay_nullable(statements):
    alter = statements[0]
    assert alter.startswith("alter table agent_audit_log add column if not exists tool_calls jsonb")
    assert re.findall(r"add column if not exists (\w+)", alter) == ["tool_calls", "turn_error"]
    assert "add column if not exists tool_calls jsonb constraint" in alter
    assert ", add column if not exists turn_error text constraint" in alter
    assert "not null" not in alter.replace("is not null", "") and "default" not in alter


def test_the_column_must_hold_an_array(statements):
    check = "check (jsonb_typeof(tool_calls) = 'array')"
    assert f"constraint agent_audit_log_tool_calls_array {check}" in statements[0]


ELEMENT_CHECKS = ("access_audit_id", "object", "refusal_code", "refused_by", "result", "tool")


def check_path(name: str) -> str:
    """The jsonpath one element CHECK tests, its quoted pieces joined as PostgreSQL joins them."""
    found = re.search(rf"CONSTRAINT agent_audit_log_tool_calls_{name}\b(.*?)(?=CONSTRAINT|;)",
                      MIGRATION.read_text(), re.DOTALL)
    assert found, f"no CHECK agent_audit_log_tool_calls_{name}"
    return "".join(re.findall(r"'([^']*)'", found.group(1)))


def test_the_element_checks_use_the_immutable_path_function(statements):
    alter = statements[0]
    for name in ELEMENT_CHECKS:
        assert (f"constraint agent_audit_log_tool_calls_{name} check "
                "(not jsonb_path_exists(tool_calls,") in alter
    assert "_tz(" not in alter


def test_every_element_is_an_object_even_when_a_lax_path_would_unwrap_an_array():
    assert check_path("object") == '$[*].type() ? (@ != "object")'


def test_every_element_names_its_tool_with_a_non_empty_string():
    assert check_path("tool") == '$[*] ? (!(@.tool.type() == "string" && @.tool != ""))'


def test_a_code_is_absent_null_or_a_non_empty_string():
    assert check_path("refusal_code") == (
        '$[*] ? (exists(@.refusal_code) && !((@.refusal_code.type() == "string" && '
        '@.refusal_code != "") || @.refusal_code.type() == "null"))')


def test_an_access_row_id_is_a_non_empty_string_on_and_only_on_a_workload_grant_refusal():
    assert check_path("access_audit_id") == (
        '$[*] ? (!((@.refused_by == "workload_grant" && @.access_audit_id.type() == "string" && '
        '@.access_audit_id != "") || (!(@.refused_by == "workload_grant") && '
        '(!exists(@.access_audit_id) || @.access_audit_id.type() == "null"))))')


def column_check(name: str) -> str:
    """One named CHECK as written, up to the next constraint, column or the statement's end."""
    found = re.search(rf"CONSTRAINT {name}\b(.*?)(?=,\s*ADD COLUMN|CONSTRAINT|;)",
                      MIGRATION.read_text(), re.DOTALL)
    assert found, f"no CHECK {name}"
    return " ".join(found.group(1).split())


def decision_check() -> str:
    return column_check("agent_audit_log_tool_calls_decision")


def test_the_rows_decision_must_follow_from_its_calls_and_turn_error_as_write_audit_requires():
    check = decision_check()
    assert check.startswith(
        "CHECK (tool_calls IS NULL OR authorization_decision IS NOT DISTINCT FROM (CASE WHEN")
    deny = re.search(r"WHEN jsonb_path_exists\(tool_calls, (.*?)\) THEN 'deny'", check)
    assert deny, "no deny branch"
    path = "".join(re.findall(r"'([^']*)'", deny.group(1)))
    assert set(re.findall(r'@\.refused_by == "(\w+)"', path)) == DENYING_LAYERS
    assert check.endswith(
        """WHEN jsonb_path_exists(tool_calls, '$[*] ? (@.result == "error")') """
        "OR turn_error IS NOT NULL THEN 'error' ELSE 'allow' END))")
    assert "_tz(" not in check


def test_a_turn_error_is_null_or_a_non_empty_text_of_at_most_400_characters():
    assert TURN_ERROR_LIMIT == 400
    assert column_check("agent_audit_log_turn_error_text") == (
        "CHECK (turn_error IS NULL OR "
        f"(turn_error <> '' AND char_length(turn_error) <= {TURN_ERROR_LIMIT}))")


def test_a_row_with_a_turn_error_is_never_allowed():
    assert column_check("agent_audit_log_turn_error_decision") == (
        "CHECK (turn_error IS NULL OR authorization_decision IS DISTINCT FROM 'allow')")


def test_the_checks_hold_the_same_words_as_the_python_vocabulary():
    text = MIGRATION.read_text()
    assert set(re.findall(r'@\.result == "(\w+)"', text)) == set(CALL_RESULTS)
    assert set(re.findall(r'@\.refused_by == "(\w+)"', text)) == set(REFUSING_LAYERS)


def test_the_header_states_the_authorization_decision_rule():
    header = " ".join(
        line.lstrip("- ") for line in MIGRATION.read_text().splitlines() if line.startswith("--"))
    assert "authorization_decision" in header
    for layer in ("identity_check, policy, workload_grant or database_guard",
                  "business_rule and runtime_guard"):
        assert layer in header, layer
    assert "or turn_error is set" in header


def test_the_view_gains_the_columns_at_the_end_so_it_can_be_replaced_in_place(statements):
    view = statements[1]
    assert view.startswith("create or replace view agent_iam_audit as select")
    assert view.endswith(
        "rls_traveler, rls_agent_type, iam_identity, rows_returned, "
        "tool_calls, turn_error from agent_audit_log order by ran_at desc")


def test_nothing_else_changes(statements):
    assert len(statements) == 2
    for statement in statements:
        for word in ("drop ", "delete from", "update ", "insert into", "create policy",
                     "grant ", "revoke "):
            assert word not in statement, word
