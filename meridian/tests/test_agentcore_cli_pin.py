"""Every mention of the AgentCore CLI names the one pinned release.

The release tools run ``npx -y @aws/agentcore@<AGENTCORE_CLI_VERSION>``; the docs and messages that
tell a person how to install it must name the same release, never ``@latest``, a range or a bare
``npm install -g @aws/agentcore``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from scripts.identity_release import settings

REPOSITORY = Path(__file__).resolve().parents[2]
# The CLI's own recorded output under tests/fixtures quotes its upgrade hint verbatim.
SKIPPED_PARTS = {"fixtures"}
SUFFIXES = {".md", ".py", ".ts", ".sh", ".json", ".toml", ".yml", ".yaml"}
PINNED = re.compile(r"@aws/agentcore@([0-9A-Za-z.{}<>_-]+)")
UNPINNED = re.compile(r"npm install -g @aws/agentcore(?![@\w-])")


def scanned_files() -> list[Path]:
    """Tracked text files, so a generated or staged copy never decides the result."""
    listed = subprocess.run(["git", "ls-files"], cwd=REPOSITORY, capture_output=True,
                            text=True, check=True).stdout.splitlines()
    return [
        REPOSITORY / name for name in listed
        if (Path(name).suffix in SUFFIXES or Path(name).name == ".env.example")
        and not SKIPPED_PARTS & set(Path(name).parts)
        and Path(name).name != "package-lock.json"
        and REPOSITORY / name != Path(__file__).resolve()
    ]


def mentions() -> list[tuple[str, str]]:
    found = []
    for path in scanned_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(REPOSITORY))
        found += [(rel, match.group(1)) for match in PINNED.finditer(text)]
        found += [(rel, "<unpinned>") for _ in UNPINNED.finditer(text)]
        if "/opt/homebrew/bin/agentcore" in text:
            found.append((rel, "/opt/homebrew/bin/agentcore"))
    return found


def test_the_pin_is_one_exact_stable_release():
    assert re.fullmatch(r"\d+\.\d+\.\d+", settings.AGENTCORE_CLI_VERSION)


def test_every_cli_mention_names_the_pinned_release():
    allowed = {settings.AGENTCORE_CLI_VERSION, "{AGENTCORE_CLI_VERSION}",
               "{settings.AGENTCORE_CLI_VERSION}", "<settings.AGENTCORE_CLI_VERSION>"}
    wrong = [(path, version) for path, version in mentions() if version not in allowed]

    assert wrong == []


def test_the_release_tools_and_the_docs_mention_the_pin():
    paths = {path for path, version in mentions() if version == settings.AGENTCORE_CLI_VERSION}

    assert "meridian/docs/AGENTCORE_DEPLOY_RUNBOOK.md" in paths
    assert "meridian/.env.example" in paths
    assert "meridian/backend/agentcore/errors.py" in paths
