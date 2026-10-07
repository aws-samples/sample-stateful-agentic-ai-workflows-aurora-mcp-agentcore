"""The published Markdown describes the Strands Graph Phase 5 and uses the project's copy rules."""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKIPPED_DIRECTORIES = (".superpowers", ".kiro", ".cache", "examples/langgraph")
PRIVATE_FILES = ("chalk_talk.md", "STAGE_CHECKLIST.md")
STALE_PHRASES = (
    "LangGraph in FastAPI",
    "AuroraDataApiSaver",
    "LANGGRAPH_CHECKPOINT",
    "kill_and_resume_demo",
    "lost_response_demo",
    "Amazon Aurora",
)
MIDDLE_DOT = "·"
FENCE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE = re.compile(r"`[^`\n]*`")


def public_markdown() -> list[Path]:
    """The tracked Markdown files that are published."""
    listed = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    files = []
    for name in listed:
        parts = name.split("/")
        if any(directory in name for directory in SKIPPED_DIRECTORIES) or ".cache" in parts:
            continue
        if parts[-1] in PRIVATE_FILES:
            continue
        files.append(REPO / name)
    return files


def prose_lines(text: str) -> list[tuple[int, str]]:
    """Numbered lines outside fenced code blocks, with inline code removed."""
    lines = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append((number, INLINE_CODE.sub("", line)))
    return lines


def test_the_scan_covers_the_public_documents():
    names = {path.relative_to(REPO).as_posix() for path in public_markdown()}

    assert {"README.md", "meridian/README.md", "meridian/docs/STATEFUL_ARCHITECTURE.md"} <= names
    assert not any("examples/langgraph" in name or ".superpowers" in name for name in names)


@pytest.mark.parametrize("phrase", STALE_PHRASES)
def test_no_public_document_uses_the_stale_phrase(phrase):
    found = [
        f"{path.relative_to(REPO)}:{number}"
        for path in public_markdown()
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if phrase in line
    ]

    assert not found, f"{phrase!r} appears in: {', '.join(found)}"


def test_no_public_document_has_a_middle_dot_in_prose():
    found = [
        f"{path.relative_to(REPO)}:{number}"
        for path in public_markdown()
        for number, line in prose_lines(path.read_text())
        if MIDDLE_DOT in line
    ]

    assert not found, f"middle dot in prose at: {', '.join(found)}"


def test_the_dot_scan_ignores_code():
    text = "fine `a · b`\n```\nc · d\n```\nbad · here\n"

    assert [number for number, line in prose_lines(text) if MIDDLE_DOT in line] == [5]
