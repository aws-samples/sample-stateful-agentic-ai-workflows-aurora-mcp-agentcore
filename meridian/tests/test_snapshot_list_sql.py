"""The snapshot listing statement binds every placeholder and escapes LIKE the default way."""

import re

import pytest

from backend.agents.phase_05_workflow.snapshot_storage import LIST_SQL, _like_prefix
from backend.db.rds_data_client import RDSDataClient
from tests.data_api_support import names_the_data_api_binds


def like_matches(pattern: str, text: str) -> bool:
    """PostgreSQL LIKE with its default escape: a backslash makes the next character literal."""
    regex, i = "", 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\" and i + 1 < len(pattern):
            i += 1
            regex += re.escape(pattern[i])
        elif char == "%":
            regex += ".*"
        elif char == "_":
            regex += "."
        else:
            regex += re.escape(char)
        i += 1
    return re.fullmatch(regex, text, re.DOTALL) is not None


def test_the_listing_statement_has_no_escape_clause():
    assert "ESCAPE" not in LIST_SQL


def test_every_placeholder_survives_the_data_api_scan_as_a_bound_parameter():
    count = LIST_SQL.count("%s")
    converted = RDSDataClient(region="us-east-1")._convert_sql_placeholders(LIST_SQL, count)
    assert count == 2
    assert names_the_data_api_binds(converted) == [f"p{i}" for i in range(count)]


def test_a_placeholder_after_an_escape_literal_would_be_lost():
    # The scan is the guard: the same statement with the old clause and one more predicate
    # loses a placeholder.
    old = LIST_SQL.replace("LIKE %s", "LIKE %s ESCAPE '\\' AND true = %s")
    converted = RDSDataClient(region="us-east-1")._convert_sql_placeholders(old, old.count("%s"))
    assert names_the_data_api_binds(converted) != [f"p{i}" for i in range(old.count("%s"))]


@pytest.mark.parametrize(
    ("prefix", "lookalike"),
    [
        ("session/a%b/", "session/aXXb/"),
        ("session/a_b/", "session/aXb/"),
        ("session/a\\b/", "session/ab/"),
    ],
)
def test_a_prefix_matches_itself_and_not_its_wildcard_lookalike(prefix, lookalike):
    pattern = _like_prefix(prefix)
    assert like_matches(pattern, prefix + "tail")
    assert not like_matches(pattern, lookalike + "tail")
