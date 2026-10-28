"""No SQL in the application carries an ESCAPE clause (the Data API misreads its quote)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLAUSE = re.compile(r"escape\s*e?'\\+'", re.IGNORECASE)
SOURCES = tuple(ROOT / name for name in ("backend", "scripts", "infra", "meridian_agentcore"))
SUFFIXES = {".py", ".sql", ".ts", ".js", ".mjs", ".sh"}
SKIPPED = ("MeridianWorkflow/backend", "node_modules", "cdk.out", "__pycache__")


def clause_lines(text: str) -> list[int]:
    """Line numbers where an ESCAPE clause with a backslash literal starts."""
    return [text.count("\n", 0, match.start()) + 1 for match in CLAUSE.finditer(text)]


def _files():
    for base in SOURCES:
        for path in sorted(base.rglob("*")):
            posix = path.as_posix()
            if path.suffix in SUFFIXES and not any(part in posix for part in SKIPPED):
                yield path


@pytest.mark.parametrize(
    "spelling",
    [
        "ESCAPE '\\'",
        "ESCAPE '\\\\'",
        "escape '\\'",
        "Escape '\\\\'",
        "ESCAPE E'\\\\'",
        "escape e'\\\\'",
        "ESCAPE   '\\'",
        "ESCAPE\n  E'\\\\'",
        "ESCAPE'\\'",
    ],
)
def test_the_scan_catches_every_spelling_of_the_clause(spelling):
    assert clause_lines(f"WHERE a LIKE %s\n{spelling}\n") != []


def test_the_scan_ignores_statements_without_the_clause():
    assert clause_lines("WHERE a LIKE %s AND b = %s\nescape_hatch = 'x'") == []


def test_the_scan_covers_every_directory_that_sends_sql_over_the_data_api():
    assert {path.name for path in SOURCES} == {"backend", "scripts", "infra", "meridian_agentcore"}
    assert all(path.is_dir() for path in SOURCES)
    assert any(path.parts[-2] == "scripts" for path in _files())


def test_no_statement_in_the_application_or_its_tools_has_an_escape_clause():
    offenders = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in _files()
        for number in clause_lines(path.read_text())
    ]
    assert offenders == [], (
        "the RDS Data API reads a backslash before a quote as an escape and loses the next "
        "placeholder; the backslash is PostgreSQL's default LIKE escape, so drop the clause"
    )
