"""Every `cdk destroy` in the Markdown names --exclusively, so it never takes dependents with it."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = [*sorted((ROOT / "docs").rglob("*.md")), ROOT.parent / "README.md", ROOT / "README.md"]
INLINE = re.compile(r"`([^`\n]*cdk destroy[^`\n]*)`")


def destroy_commands(text: str) -> list[str]:
    """Return each `cdk destroy` command in the text, with line continuations joined."""
    joined = re.sub(r"\s*\\\n\s*", " ", text)
    commands: list[str] = []
    for line in joined.splitlines():
        inline = INLINE.findall(line)
        if inline:
            commands.extend(inline)
        elif "cdk destroy" in line:
            commands.append(line[line.index("cdk destroy"):])
    return commands


def test_the_scanner_finds_commands_in_code_blocks_and_inline_spans():
    text = "run `npx cdk destroy A` now\n```bash\nnpx cdk destroy B \\\n  --force\n```\n"

    assert destroy_commands(text) == ["npx cdk destroy A", "cdk destroy B --force"]


def test_every_cdk_destroy_in_the_docs_carries_exclusively():
    found = 0
    missing: list[str] = []
    for path in MARKDOWN:
        if not path.exists():
            continue
        for command in destroy_commands(path.read_text(encoding="utf-8")):
            found += 1
            if "--exclusively" not in command:
                missing.append(f"{path.name}: {command}")

    assert found >= 4
    assert missing == []
