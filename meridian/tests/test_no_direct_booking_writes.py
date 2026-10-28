"""Application code never writes booking rows itself: every change goes through the Gateway."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JOIN = r"""(?:[\s"'+()]|\\n)*"""
WRITE = re.compile(
    rf"\b(INSERT{JOIN}INTO|UPDATE|DELETE{JOIN}FROM){JOIN}(bookings|booking_lines|hold_requests)\b",
    re.IGNORECASE,
)
SOURCES = (ROOT / "backend", ROOT / "meridian_agentcore" / "app")


def _python_files():
    for base in SOURCES:
        for path in base.rglob("*.py"):
            posix = path.as_posix()
            if "MeridianWorkflow/backend" in posix or "__pycache__" in path.parts:
                continue
            yield path


def _hits(text: str):
    """Line numbers of writes, found across line breaks and adjoining string literals."""
    return [text.count("\n", 0, match.start()) + 1 for match in WRITE.finditer(text)]


@pytest.mark.parametrize("source", [
    'sql = "UPDATE bookings SET status = 1"',
    'sql = ("UPDATE "\n       "bookings SET status = 1")',
    "sql = (\n  'INSERT INTO'\n  ' hold_requests (a)')",
    'sql = "DELETE\\n FROM booking_lines"',
])
def test_the_scan_sees_a_write_split_across_lines(source):
    assert _hits(source)


def test_the_scan_ignores_reads():
    assert not _hits('sql = "SELECT * FROM bookings WHERE updated_at > 1"')


def test_no_application_module_writes_booking_rows():
    offenders = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in _python_files()
        for number in _hits(path.read_text())
    ]
    assert offenders == [], (
        "booking rows change only in the holds Lambda and its SQL functions; "
        "call a Gateway tool instead"
    )
