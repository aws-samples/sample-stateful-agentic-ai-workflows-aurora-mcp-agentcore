"""The public repository holds the sample only: no event session material, no local servers."""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SESSION_CODE = re.compile(r"\bDAT\d{3}(-R\d*)?\b")
EVENT_PREP = re.compile(r"speaker notes|run of show|presentation machine", re.IGNORECASE)
LOCAL_SERVER = re.compile(r"\b(initdb|pg_ctl)\b")
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".woff", ".woff2", ".zip"}


def tracked(*patterns: str) -> list[Path]:
    """Tracked files matching the git pathspecs, excluding binary assets."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", *patterns],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split("\0")
    return [REPO / name for name in listed if name and Path(name).suffix not in BINARY_SUFFIXES]


def offenders(files: list[Path], pattern: re.Pattern[str]) -> list[str]:
    """Each file:line whose text matches the pattern."""
    found = []
    for path in files:
        for number, line in enumerate(path.read_text(errors="ignore").splitlines(), start=1):
            if pattern.search(line):
                found.append(f"{path.relative_to(REPO)}:{number}")
    return found


def test_no_event_session_code_is_committed():
    assert offenders(tracked("*.md", "*.py", "*.ts", "*.tsx", "*.json"), SESSION_CODE) == []


def test_no_event_preparation_notes_are_committed():
    assert offenders(tracked("*.md"), EVENT_PREP) == []


def test_tests_never_start_a_local_postgresql_server():
    others = [path for path in tracked("meridian/tests/*.py") if path != Path(__file__).resolve()]
    assert offenders(others, LOCAL_SERVER) == []
