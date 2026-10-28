"""Source files that must not hold dashes, middle dots or raw line separators."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN = {
    "em dash": chr(0x2014),
    "en dash": chr(0x2013),
    "middle dot": chr(0x00B7),
    "line separator": chr(0x2028),
    "paragraph separator": chr(0x2029),
}

CHECKED_FILES = [
    "backend/agentcore/__init__.py",
    "backend/search_utils.py",
    "tests/test_trace_sql_display.py",
]


@pytest.mark.parametrize("relative_path", CHECKED_FILES)
def test_source_holds_no_forbidden_characters(relative_path: str) -> None:
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    found = [
        f"{relative_path}:{number}: {name}"
        for number, line in enumerate(text.split("\n"), start=1)
        for name, character in FORBIDDEN.items()
        if character in line
    ]
    assert not found, "\n".join(found)
