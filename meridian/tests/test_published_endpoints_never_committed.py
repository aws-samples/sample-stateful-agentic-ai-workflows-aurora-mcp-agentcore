"""No account-tied endpoint or AWS account ID may be committed to this public repository.

The published demo lives in one AWS account behind basic auth. Committing its
address would hand anyone reading the sample an endpoint to scan, and it would
rot into a dead link the moment the stack comes down. `publish.py` records the
address in the gitignored `.local/published.json` and `scripts/published.py`
reads it back locally, so nothing needs it in tree.

The same holds for account IDs. The AgentCore configuration is committed as
templates and rendered per account by `scripts/render_agentcore_config.py`,
so only documented placeholder accounts belong in tracked files.

This scans the files git actually tracks, so an ignored local record cannot trip
it and a new doc that pastes a URL in cannot slip past review.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# Endpoints that name one account's deployment. A bare mention of "CloudFront"
# or "App Runner" is fine; a resolvable host is not.
ENDPOINTS = {
    "CloudFront distribution": re.compile(r"\b[a-z0-9]{10,}\.cloudfront\.net\b"),
    "App Runner service": re.compile(r"\b[a-z0-9]{8,}\.[a-z0-9-]+\.awsapprunner\.com\b"),
}
# An account ID inside an ARN, a CDK environment URI, a JSON "account" field, or
# written next to the word "account".
ACCOUNT_ID = re.compile(
    r"arn:aws[a-z-]*:[a-z0-9-]+:[a-z0-9-]*:(\d{12}):"
    r"|aws://(\d{12})/"
    r"|\"account\"\s*:\s*\"(\d{12})\""
    r"|\baccount[^0-9\n]{0,24}?(?<![0-9A-Za-z])(\d{12})(?![0-9A-Za-z])",
    re.IGNORECASE,
)
# AWS documents 123456789012 and 111122223333 as example accounts; the tests use
# 000000000000 and 999999999999 as obviously fake ones.
PLACEHOLDER_ACCOUNTS = {"123456789012", "111122223333", "000000000000", "999999999999"}
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


def tracked_lines() -> Iterator[tuple[str, str]]:
    """Yield ``path:line`` and the line text for every tracked UTF-8 text file."""
    for path in tracked_files():
        if path.name in EXEMPT or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            yield f"{path.relative_to(REPO)}:{line_number}", line


@pytest.mark.parametrize("label,pattern", sorted(ENDPOINTS.items()))
def test_no_tracked_file_names_a_deployed_endpoint(label: str, pattern: re.Pattern) -> None:
    hits = [location for location, line in tracked_lines() if pattern.search(line)]
    assert not hits, (
        f"{len(hits)} tracked line(s) name a live {label}: {hits}. The address "
        f"belongs in .local/published.json, which is gitignored; read it back "
        f"with `python scripts/published.py`."
    )


def test_no_tracked_file_names_a_real_aws_account() -> None:
    hits = [
        f"{location} ({account})"
        for location, line in tracked_lines()
        for match in ACCOUNT_ID.finditer(line)
        for account in [next(group for group in match.groups() if group)]
        if account not in PLACEHOLDER_ACCOUNTS
    ]
    assert not hits, (
        f"{len(hits)} tracked line(s) name an AWS account: {hits}. Use a placeholder "
        f"such as 123456789012, or a {{{{AWS_ACCOUNT_ID}}}} template value rendered "
        f"by scripts/render_agentcore_config.py."
    )


@pytest.mark.parametrize(
    "line",
    [
        '"Resource": "arn:aws:rds:us-east-1:210987654321:cluster:meridian"',
        '"account": "210987654321"',
        "npx cdk bootstrap aws://210987654321/us-east-1",
        "Deployed to account `210987654321`.",
    ],
)
def test_account_pattern_finds_real_accounts(line: str) -> None:
    match = ACCOUNT_ID.search(line)
    assert match and "210987654321" in match.groups()


def test_account_pattern_ignores_hashes_and_placeholders() -> None:
    assert not ACCOUNT_ID.search("sha256:0c1f00ff210987654321ab")
    match = ACCOUNT_ID.search('"account": "123456789012"')
    assert match and "123456789012" in PLACEHOLDER_ACCOUNTS
