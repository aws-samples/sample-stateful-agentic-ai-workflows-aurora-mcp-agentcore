"""The guide says what the warm-up writes and how to authenticate each host and mode."""

import re
from pathlib import Path

MERIDIAN = Path(__file__).resolve().parents[1]
OPERATIONS = (MERIDIAN / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
EXPRESS = OPERATIONS.split("## Host the backend on Amazon ECS Express Mode")[1].split(
    "### Tear down the Express backend")[0]
PUBLISH = OPERATIONS.split("## Publish the web app")[1].split("## Bring the App Runner")[0]
VIEWER = (MERIDIAN / "infra" / "functions" / "viewer-request.js").read_text(encoding="utf-8")
WEB_STACK = (MERIDIAN / "infra" / "lib" / "meridian-web-stack.ts").read_text(encoding="utf-8")


def test_the_guide_no_longer_says_the_warm_up_writes_nothing():
    assert "This writes nothing and changes no data" not in EXPRESS
    assert "warm-up writes\nnothing" not in PUBLISH
    assert "one read-only turn for each of phases 1 to 4" not in PUBLISH


def test_the_guide_says_what_the_chat_turns_write_and_how_a_health_probe_differs():
    assert "**What the warm-up writes.** It is not a health probe." in EXPRESS
    for table in ("conversation and message rows", "trip interactions", "`traveler_access_audit`"):
        assert table in EXPRESS
    assert "never writes" in EXPRESS and "`/health`" in EXPRESS
    assert "`--conversation-ids-file" in EXPRESS


def test_the_iam_direct_url_example_sends_the_shared_bearer():
    block = EXPRESS.split("# iam release:")[1].split("```")[0]

    assert "MERIDIAN_API_TOKEN=" in block and "meridian/web/api-token" in block
    assert "warm_demo.py --base-url <ServiceEndpoint>" in block
    assert "MERIDIAN_AGENTCORE_AUTH=jwt python scripts/warm_demo.py --base-url" in EXPRESS


def test_the_edge_credential_keys_the_guide_names_are_the_ones_the_viewer_function_reads():
    keys = set(re.findall(r"kvs\.get\('(\w+)'\)", VIEWER))

    assert keys == {"basic", "token"}
    assert "`basic`" in EXPRESS and "`token`" in EXPRESS


def test_the_outputs_the_guide_tells_the_reader_to_use_exist_in_the_site_stack():
    for output in ("SiteUrl", "AccessStoreArn"):
        assert f"'{output}'" in WEB_STACK and f"`{output}`" in EXPRESS


def test_the_guide_places_callback_registration_after_the_cutover_for_the_jwt_release():
    assert EXPRESS.index("**Cut CloudFront over.**") < EXPRESS.index(
        "**First-time hosting: the callback and the edge credentials.**")
    assert "-c callbackUrls=http://localhost:5173/showcase,<SiteUrl>/showcase" in EXPRESS
    assert "fresh browser session" in EXPRESS
