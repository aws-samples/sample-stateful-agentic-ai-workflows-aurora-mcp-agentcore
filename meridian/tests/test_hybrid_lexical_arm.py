"""Guard the lexical arm of Phase 3 hybrid retrieval.

The demo claims "pgvector + PostgreSQL full-text search + Cohere rerank" and
the trace panel prints the per-arm candidate counts on screen. A bare
``websearch_to_tsquery`` joins terms with AND, so a conversational prompt
requires every stemmed term in one row and the lexical arm silently returns
zero candidates for every prompt in the demo script.

Broadening cannot be blanket, though: rewriting every operator turns
"tokyo -luxury" into "tokyo OR NOT luxury", and ``!luxury`` then matches every
non-luxury row. Against the seeded catalog that returned 30 of 35 packages
instead of 5.

These tests pin the properties that keep the claim true, without needing a
database: positive terms OR, negations stay ANDed, and the traveler's text
stays a bound parameter.
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


def test_lexical_arm_broadens_positive_terms_to_or(lexical_sql: str) -> None:
    """Conversational prompts must not require every term to co-occur."""
    normalized = " ".join(lexical_sql.split())
    assert "string_agg(part, ' | ')" in normalized, (
        "positive terms must be ORed; without it every multi-word demo "
        "prompt yields lexical=0"
    )


def test_lexical_arm_keeps_negations_anded(lexical_sql: str) -> None:
    """An exclusion must stay an exclusion.

    "tokyo -luxury" has to mean Tokyo AND NOT luxury. Folding the negation
    into the OR makes it "tokyo OR NOT luxury", which matches every row that
    simply is not luxury and silently discards the exclusion.
    """
    normalized = " ".join(lexical_sql.split())
    assert "left(part, 1) = '!'" in normalized, "negated lexemes must be split out"
    assert "string_agg(part, ' & ')" in normalized, "negations must stay ANDed"
    assert "') & ' || negatives" in normalized, (
        "negations must be ANDed onto the OR group, not folded into it"
    )


def test_lexical_arm_still_ranks_by_ts_rank(lexical_sql: str) -> None:
    """OR recall is only acceptable because ts_rank orders the candidates."""
    normalized = " ".join(lexical_sql.split())
    assert "ts_rank(" in normalized
    assert "ORDER BY lexical_score DESC" in normalized


def test_lexical_arm_is_parameterized(lexical_sql: str) -> None:
    """The traveler-supplied query must stay a bound parameter.

    The query concatenates while reassembling the tsquery, but only ever onto
    PostgreSQL's own canonical ``websearch_to_tsquery(...)::text`` output.
    Traveler text must never be an operand.
    """
    normalized = " ".join(lexical_sql.split())
    assert "format(" not in lexical_sql
    # Exactly two bound parameters: the traveler query, and the row limit.
    assert normalized.count("%s") == 2
    assert "websearch_to_tsquery('english', %s)" in normalized
    assert "LIMIT %s" in normalized
    # Nothing is ever concatenated onto a bound parameter.
    assert not re.search(r"%s\s*\|\|", normalized), "no concat onto a parameter"
    assert not re.search(r"\|\|\s*%s", normalized), "no concat onto a parameter"
