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
    "kill_and_resume_" + "demo",
    "lost_response_" + "demo",
    "Amazon Aurora",
)
MIDDLE_DOT = "·"
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`).+?(?<!`)\1(?!`)")
EM_DASH = "\u2014"
GENERATED_DOCS = ("agentcore/cdk/README.md",)


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
    opener = ""
    for number, line in enumerate(text.splitlines(), start=1):
        fence = FENCE.match(line)
        if opener:
            closing = fence and fence.group(1)[0] == opener[0] and len(fence.group(1)) >= len(opener)
            if closing and line.strip() == fence.group(1):
                opener = ""
            continue
        if fence:
            opener = fence.group(1)
            continue
        lines.append((number, INLINE_CODE.sub("", line)))
    return lines


def test_the_scan_covers_the_public_documents():
    names = {path.relative_to(REPO).as_posix() for path in public_markdown()}

    assert {
        "README.md",
        "meridian/README.md",
        "meridian/AGENTS.md",
        "meridian/docs/STATEFUL_ARCHITECTURE.md",
    } <= names
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


def test_the_dot_scan_handles_nested_and_long_fences():
    tilde = "~~~\n```\nin \u00b7 tilde\n```\nstill \u00b7 tilde\n~~~\nbad \u00b7 out\n"
    long = "````\n```\ninside \u00b7\n```\n````\nbad \u00b7 out\n"
    double = "``a \u00b7 `b` c`` bad \u00b7 out\n"

    assert [n for n, line in prose_lines(tilde) if MIDDLE_DOT in line] == [7]
    assert [n for n, line in prose_lines(long) if MIDDLE_DOT in line] == [6]
    assert [n for n, line in prose_lines(double) if MIDDLE_DOT in line] == [1]


def test_no_public_document_has_an_em_dash():
    found = [
        f"{path.relative_to(REPO)}:{number}"
        for path in public_markdown()
        if not path.as_posix().endswith(GENERATED_DOCS)
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if EM_DASH in line
    ]

    assert not found, f"em dash at: {', '.join(found)}"
