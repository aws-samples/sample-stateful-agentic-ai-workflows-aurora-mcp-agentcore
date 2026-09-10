"""No account-tied endpoint may be committed to this public repository.

The published demo lives in one AWS account behind basic auth. Committing its
address would hand anyone reading the sample an endpoint to scan, and it would
rot into a dead link the moment the stack comes down. `publish.py` records the
address in the gitignored `.local/published.json` and `scripts/published.py`
reads it back locally, so nothing needs it in tree.

This scans the files git actually tracks, so an ignored local record cannot trip
it and a new doc that pastes a URL in cannot slip past review.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# Endpoints that name one account's deployment. A bare mention of "CloudFront"
# or "App Runner" is fine; a resolvable host is not.
ENDPOINTS = {
    "CloudFront distribution": re.compile(r"\b[a-z0-9]{10,}\.cloudfront\.net\b"),
    "App Runner service": re.compile(r"\b[a-z0-9]{8,}\.[a-z0-9-]+\.awsapprunner\.com\b"),
}
# This file states the patterns it forbids, so it would always match itself.
EXEMPT = {Path(__file__).name}


def tracked_files() -> list[Path]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO,
        capture_output=True,
        check=True,
    )
    return [REPO / name for name in listing.stdout.decode().split("\0") if name]


@pytest.mark.parametrize("label,pattern", sorted(ENDPOINTS.items()))
def test_no_tracked_file_names_a_deployed_endpoint(label: str, pattern: re.Pattern) -> None:
    hits: list[str] = []
    for path in tracked_files():
        if path.name in EXEMPT or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(REPO)}:{line_number}")
    assert not hits, (
        f"{len(hits)} tracked line(s) name a live {label}: {hits}. The address "
        f"belongs in .local/published.json, which is gitignored; read it back "
        f"with `python scripts/published.py`."
    )
