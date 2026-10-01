"""No account-tied endpoint or AWS account ID may be committed to this public repository.

The published demo lives in one AWS account behind basic auth. Committing its
address would hand anyone reading the sample an endpoint to scan, and it would
rot into a dead link the moment the stack comes down. `publish.py` records the
address in the gitignored `.local/hosted-release.json` and `scripts/published.py`
reads it back locally, so nothing needs it in tree. The AgentCore gateway URL
likewise lives in the gitignored `meridian/.env`.

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
requires_git_checkout = pytest.mark.skipif(
    not (REPO / ".git").exists(),
    reason="scans the files git tracks; this copy has no .git (for example, a ZIP download)",
)

PUBLISHED_ADDRESS = (
    "The address belongs in .local/published.json, which is gitignored; read it back "
    "with `python scripts/published.py`."
)
# Endpoints that name one account's deployment, with where each belongs instead.
# A bare mention of "CloudFront" or a placeholder host is fine; a resolvable host
# is not. A gateway ID always ends in a hyphen and ten lowercase letters or
# digits, so test hosts such as meridian-test.gateway... and {id}.gateway... pass.
ENDPOINTS = {
    "CloudFront distribution": (
        re.compile(r"\b[a-z0-9]{10,}\.cloudfront\.net\b"),
        PUBLISHED_ADDRESS,
    ),
    "App Runner service": (
        re.compile(r"\b[a-z0-9]{8,}\.[a-z0-9-]+\.awsapprunner\.com\b"),
        PUBLISHED_ADDRESS,
    ),
    "AgentCore gateway": (
        re.compile(r"-[0-9a-z]{10}\.gateway\.bedrock-agentcore\."),
        "Set AGENTCORE_GATEWAY_URL in meridian/.env, which is gitignored, or let "
        "`python scripts/sync_agentcore_env.py --write` read it from the deployment state.",
    ),
}
# An account ID inside an ARN, a CDK environment URI, a JSON "account" field, an
# ECR registry host, or a CDK bootstrap bucket or role name (...-<account>-<region>,
# optionally with an availability-zone letter such as "-us-east-1a").
# Each alternative binds tightly to one specific 12-digit number, so a single
# left-to-right scan of the line never needs to consider the same digits twice.
ACCOUNT_ID = re.compile(
    r"arn:aws[a-z-]*:[a-z0-9-]+:[a-z0-9-]*:(\d{12}):"
    r"|aws://(\d{12})/"
    r"|\"account\"\s*:\s*\"(\d{12})\""
    r"|(?<!\d)(\d{12})\.dkr\.ecr\."
    r"|-(\d{12})-[a-z]{2}(?:-[a-z]+)+-\d[a-z]?\b",
    re.IGNORECASE,
)
# A bare 12-digit number, isolated from surrounding digits or letters so a hash
# or a longer digit run never qualifies as a candidate account ID.
DIGIT_RUN = re.compile(r"(?<![0-9A-Za-z])(\d{12})(?![0-9A-Za-z])")
# The word "account" (AWS_ACCOUNT_ID=, CDK_DEFAULT_ACCOUNT=, awsAccountId:,
# "Deployed to account ..."). Unlike the alternatives above, one mention of this
# word can name several account IDs, as in "accounts 123456789012, 210987654321".
# So it is matched on its own, and every digit run on the line is then judged by
# its distance from the word, instead of one combined match consuming the word
# and leaving later digit runs on the same line with no keyword to bind to.
ACCOUNT_KEYWORD = re.compile(r"account", re.IGNORECASE)
# How many characters may separate the end of "account" from a digit run and
# still count as that word naming it. Wide enough to span a short list such as
# "accounts 123456789012, 210987654321", where the gap includes the first number.
ACCOUNT_KEYWORD_REACH = 24
# AWS documents 123456789012 and 111122223333 as example accounts; the tests use
# 000000000000 and 999999999999 as obviously fake ones.
PLACEHOLDER_ACCOUNTS = {"123456789012", "111122223333", "000000000000", "999999999999"}
# This file states the patterns it forbids, so it would always match itself.
EXEMPT = {Path(__file__).name}


def _digit_runs_named_by_keyword(line: str) -> list[re.Match[str]]:
    """Return every digit run on the line close enough to "account" to name it."""
    keyword_ends = [match.end() for match in ACCOUNT_KEYWORD.finditer(line)]
    if not keyword_ends:
        return []
    return [
        digit_run
        for digit_run in DIGIT_RUN.finditer(line)
        if any(0 <= digit_run.start() - end <= ACCOUNT_KEYWORD_REACH for end in keyword_ends)
    ]


def _is_within(inner: tuple[int, int], outer: tuple[int, int]) -> bool:
    """Return whether the ``inner`` span falls entirely inside the ``outer`` span."""
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def real_accounts(line: str) -> list[str]:
    """Return the account IDs a line names, other than the documented placeholders."""
    tight_matches = list(ACCOUNT_ID.finditer(line))
    accounts = [next(group for group in match.groups() if group) for match in tight_matches]
    tight_spans = [match.span() for match in tight_matches]
    for digit_run in _digit_runs_named_by_keyword(line):
        if not any(_is_within(digit_run.span(), span) for span in tight_spans):
            accounts.append(digit_run.group(1))
    return [account for account in accounts if account not in PLACEHOLDER_ACCOUNTS]


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


@requires_git_checkout
@pytest.mark.parametrize("label", sorted(ENDPOINTS))
def test_no_tracked_file_names_a_deployed_endpoint(label: str) -> None:
    pattern, remedy = ENDPOINTS[label]
    hits = [location for location, line in tracked_lines() if pattern.search(line)]
    assert not hits, f"{len(hits)} tracked line(s) name a live {label}: {hits}. {remedy}"


@requires_git_checkout
def test_no_tracked_file_names_a_real_aws_account() -> None:
    hits = [
        f"{location} ({account})"
        for location, line in tracked_lines()
        for account in real_accounts(line)
    ]
    assert not hits, (
        f"{len(hits)} tracked line(s) name an AWS account: {hits}. Use a placeholder "
        f"such as 123456789012, or a {{{{AWS_ACCOUNT_ID}}}} template value rendered "
        f"by scripts/render_agentcore_config.py."
    )


@pytest.mark.parametrize(
    "real,placeholder",
    [
        (
            '"Resource": "arn:aws:rds:us-east-1:210987654321:cluster:meridian"',
            '"Resource": "arn:aws:rds:us-east-1:123456789012:cluster:meridian"',
        ),
        ('"account": "210987654321"', '"account": "123456789012"'),
        (
            "npx cdk bootstrap aws://210987654321/us-east-1",
            "npx cdk bootstrap aws://111122223333/us-east-1",
        ),
        ("Deployed to account `210987654321`.", "Deployed to account `123456789012`."),
        ("export AWS_ACCOUNT_ID=210987654321", "export AWS_ACCOUNT_ID=123456789012"),
        ("CDK_DEFAULT_ACCOUNT=210987654321", "CDK_DEFAULT_ACCOUNT=<account-id>"),
        ("awsAccountId: '210987654321',", "awsAccountId: '000000000000',"),
        (
            "docker push 210987654321.dkr.ecr.us-east-1.amazonaws.com/meridian:latest",
            "docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/meridian:latest",
        ),
        (
            "docker push 210987654321.dkr.ecr.eu-west-1.amazonaws.com/meridian:latest",
            "docker push 123456789012.dkr.ecr.eu-west-1.amazonaws.com/meridian:latest",
        ),
        (
            "s3://cdk-hnb659fds-assets-210987654321-us-east-1/asset.zip",
            "s3://cdk-hnb659fds-assets-123456789012-us-east-1/asset.zip",
        ),
        (
            "role/cdk-hnb659fds-cfn-exec-role-210987654321-ap-southeast-2",
            "role/cdk-hnb659fds-cfn-exec-role-<account-id>-ap-southeast-2",
        ),
        (
            "cdk-hnb659fds-assets-210987654321-us-east-1a",
            "cdk-hnb659fds-assets-123456789012-us-east-1a",
        ),
    ],
)
def test_account_pattern_finds_real_accounts_and_passes_placeholders(
    real: str, placeholder: str
) -> None:
    assert real_accounts(real) == ["210987654321"]
    assert real_accounts(placeholder) == []


def test_account_pattern_ignores_hashes_and_unrelated_numbers() -> None:
    assert real_accounts("sha256:0c1f00ff210987654321ab") == []
    assert real_accounts("--hash=sha256:210987654321210987654321") == []
    assert real_accounts("order 210987654321 shipped") == []


@pytest.mark.parametrize(
    "line",
    [
        "accounts 123456789012, 210987654321",
        "accounts 210987654321, 123456789012",
    ],
)
def test_account_pattern_judges_each_number_on_a_line_independently(line: str) -> None:
    # A placeholder and a real ID can share one line, in either order, under a
    # single "accounts" mention. Each 12-digit number must be judged on its own
    # instead of the scan stopping once the first number consumes the keyword.
    assert real_accounts(line) == ["210987654321"]


@pytest.mark.parametrize(
    "host,is_deployed",
    [
        (
            "https://meridianv2-meridian-aurora-k3v9q2x7ma.gateway.bedrock-agentcore"
            ".us-east-1.amazonaws.com/mcp",
            True,
        ),
        ("https://mygw-0123456789.gateway.bedrock-agentcore.eu-west-1.amazonaws.com/mcp", True),
        ("https://meridian-test.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp", False),
        ("https://x.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp", False),
        ("https://*.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp", False),
        ("https://{id}.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp", False),
        ("https://<id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp", False),
    ],
)
def test_gateway_pattern_finds_deployed_hosts_and_passes_test_hosts(
    host: str, is_deployed: bool
) -> None:
    pattern, _ = ENDPOINTS["AgentCore gateway"]
    assert bool(pattern.search(host)) is is_deployed
