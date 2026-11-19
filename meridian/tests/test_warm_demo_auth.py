"""The warm-up authenticates each host and mode, and says what its chat turns write."""

import pytest

from scripts import warm_demo

ENDPOINT = "https://web.example.net"


@pytest.fixture
def iam(monkeypatch):
    monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH", raising=False)
    monkeypatch.delenv("MERIDIAN_HOSTED_AUTH", raising=False)
    monkeypatch.delenv("MERIDIAN_API_TOKEN", raising=False)


@pytest.fixture
def sent(monkeypatch):
    """Every request the warm-up sends: (path, headers)."""
    seen = []

    def fake_call(base, headers, path, body=None):
        seen.append((base + path, dict(headers)))
        return {"status": "healthy", "aurora_reachable": True, "products": [{}], "journeys": [],
                "traveler_id": "trv_meridian_demo", "facts": [], "message": "ok",
                "activities": []}

    monkeypatch.setattr(warm_demo, "call", fake_call)
    return seen


def test_a_direct_url_in_the_iam_release_sends_the_shared_token_as_a_bearer(
        iam, monkeypatch, sent):
    monkeypatch.setenv("MERIDIAN_API_TOKEN", "shared-secret")

    assert warm_demo.main(["--base-url", ENDPOINT]) == 0

    assert sent and all(h == {"Authorization": "Bearer shared-secret"} for _p, h in sent)


def test_without_the_token_the_direct_url_example_sends_no_credential(iam, sent):
    assert warm_demo.main(["--base-url", ENDPOINT]) == 0

    assert all(h == {} for _p, h in sent)


def test_a_placeholder_token_is_never_sent(iam, monkeypatch, sent):
    monkeypatch.setenv("MERIDIAN_API_TOKEN", "{{resolve:secretsmanager:x}}")

    warm_demo.main(["--base-url", ENDPOINT])

    assert all(h == {} for _p, h in sent)


def test_the_shared_token_never_goes_to_a_plain_http_host_that_is_not_local(iam, monkeypatch, sent):
    monkeypatch.setenv("MERIDIAN_API_TOKEN", "shared-secret")

    with pytest.raises(SystemExit, match="Refusing to send a credential"):
        warm_demo.main(["--base-url", "http://web.example.net"])

    assert sent == []


def test_a_loopback_backend_still_accepts_the_token_over_http(iam, monkeypatch, sent):
    monkeypatch.setenv("MERIDIAN_API_TOKEN", "shared-secret")

    assert warm_demo.main([]) == 0
    assert sent[0][1] == {"Authorization": "Bearer shared-secret"}


def test_the_hosted_site_uses_the_edge_credential_not_the_shared_token(iam, monkeypatch):
    monkeypatch.setenv("MERIDIAN_API_TOKEN", "shared-secret")
    monkeypatch.setenv("MERIDIAN_HOSTED_AUTH", '{"username": "u", "password": "p"}')

    headers = warm_demo.request_headers(True, "jordan")

    assert headers["Authorization"].startswith("Basic ")


def test_the_warm_up_says_its_chat_turns_write_and_that_a_health_probe_does_not():
    doc = warm_demo.__doc__

    assert "NOT read-only" in doc and "not a health probe" in doc
    assert "audit" in doc and "--conversation-ids-file" in doc
    assert "one\nread-only turn" not in doc
