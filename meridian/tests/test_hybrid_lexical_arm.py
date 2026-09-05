"""Guard the lexical arm of Phase 3 hybrid retrieval.

The demo claims "pgvector + PostgreSQL full-text search + Cohere rerank" and
the trace panel prints the per-arm candidate counts on screen. A bare
``websearch_to_tsquery`` joins terms with AND, so a conversational prompt
requires every stemmed term in one row and the lexical arm silently returns
zero candidates for every prompt in the demo script.

These tests pin the two properties that keep the claim true, without needing a
database: the query must OR its operators, and it must still be parameterized.
"""

import re
from pathlib import Path

import pytest

SEARCH_AGENT = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "agents"
    / "retrieval_03"
    / "search_agent.py"
)


@pytest.fixture(scope="module")
def lexical_sql() -> str:
    source = SEARCH_AGENT.read_text()
    match = re.search(r'lexical_sql = """(.*?)"""', source, re.S)
    assert match, "lexical_sql block not found in search_agent.py"
    return match.group(1)


def test_lexical_arm_rewrites_and_operators_to_or(lexical_sql: str) -> None:
    """Conversational prompts must not require every term to co-occur."""
    normalized = " ".join(lexical_sql.split())
    assert "replace(" in normalized and "'&', '|'" in normalized, (
        "lexical arm must rewrite websearch_to_tsquery's AND operators to OR; "
        "without it every multi-word demo prompt yields lexical=0"
    )


def test_lexical_arm_still_ranks_by_ts_rank(lexical_sql: str) -> None:
    """OR recall is only acceptable because ts_rank orders the candidates."""
    normalized = " ".join(lexical_sql.split())
    assert "ts_rank(" in normalized
    assert "ORDER BY lexical_score DESC" in normalized


def test_lexical_arm_is_parameterized(lexical_sql: str) -> None:
    """The traveler-supplied query must stay a bound parameter."""
    assert "%s" in lexical_sql
    assert "format(" not in lexical_sql
    assert "||" not in lexical_sql, "no string concatenation into the tsquery"
