"""Every relative link and image in the tracked Markdown resolves, heading anchors included."""

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
SKIPPED_DIRS = {"node_modules", "venv", ".git", ".local", ".superpowers", "dist", "build"}
LINK = re.compile(
    r"!?\[[^\]\n]*\]\((?:<([^>\n]+)>|([^)\s]+))(?:\s+\"[^\"]*\")?\)")
HTML = re.compile(r"""(?:href|src)="([^"]+)\"""")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
LINE_ANCHOR = re.compile(r"L\d+(-L\d+)?")
EXTERNAL = ("http://", "https://", "mailto:", "tel:")


def markdown_files() -> list[Path]:
    return sorted(
        path for path in ROOT.rglob("*.md")
        if not SKIPPED_DIRS & set(path.relative_to(ROOT).parts))


def visible_lines(text: str) -> list[str]:
    """The lines outside fenced code blocks."""
    lines, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            lines.append(line)
    return lines


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"`([^`]*)`", r"\1", heading)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text).lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    found: dict[str, int] = {}
    names = set()
    for line in visible_lines(path.read_text(encoding="utf-8")):
        match = HEADING.match(line)
        if match:
            base = slug(match.group(1))
            count = found.get(base, 0)
            names.add(base if count == 0 else f"{base}-{count}")
            found[base] = count + 1
    return names


def targets(path: Path) -> list[str]:
    text = "\n".join(visible_lines(path.read_text(encoding="utf-8")))
    links = [angled or plain for angled, plain in LINK.findall(text)]
    return [*links, *HTML.findall(text)]


def broken_links() -> list[str]:
    problems = []
    for path in markdown_files():
        for target in targets(path):
            if target.startswith(EXTERNAL):
                continue
            name, _, fragment = target.partition("#")
            destination = path if not name else (path.parent / unquote(name)).resolve()
            where = f"{path.relative_to(ROOT)} -> {target}"
            if not destination.exists():
                problems.append(f"{where}: no such file")
            elif fragment and not LINE_ANCHOR.fullmatch(fragment) and destination.suffix == ".md":
                if unquote(fragment).lower() not in anchors(destination):
                    problems.append(f"{where}: no such heading")
    return problems


def test_every_relative_link_and_image_in_the_markdown_resolves():
    assert broken_links() == []


def test_the_scanner_sees_links_headings_and_skips_code_fences(tmp_path):
    doc = tmp_path / "a.md"
    doc.write_text("# Title `x`\n[a](b.md#title-x) ![i](i.png)\n```\n[no](missing.md)\n```\n"
                   "## Same\n## Same\n", encoding="utf-8")

    assert targets(doc) == ["b.md#title-x", "i.png"]
    assert anchors(doc) == {"title-x", "same", "same-1"}


def test_the_root_readme_leads_with_the_no_account_entry_point_beside_run_locally():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    start = readme.split("**Start here:**")[1].split("\n\n")[0]
    label = "Explore the five phases without an AWS account"

    assert f"[**{label}**](meridian/docs/CODE_WALKTHROUGH.md)" in start
    assert start.index(label) < start.index("[Run locally](#quick-start)")
    between = start[start.index(label):start.index("[Run locally](#quick-start)")]
    assert between.count("](") == 1
    for pointer in ("meridian/backend/agents/phase_05_workflow/snapshot_storage.py",
                    "meridian/backend/agents/phase_05_workflow/hold_intent.py",
                    "meridian/tests/test_workflow_graph.py"):
        assert f"]({pointer})" in readme
        assert (ROOT / pointer).is_file()
    assert "Setting up AWS is a separate step and it costs money" in readme
