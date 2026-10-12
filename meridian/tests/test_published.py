"""The release helper reads the publisher's actual record without exposing secrets."""
from unittest.mock import MagicMock
from urllib.error import HTTPError

import pytest

from scripts import published


def test_current_release_record_supplies_url():
    assert published.release_url({"site": {"SiteUrl": "https://example.com/"}}) == "https://example.com/"


@pytest.mark.parametrize("url", ["", "http://example.com", "https://user:secret@example.com"])
def test_unsafe_or_missing_release_url_is_rejected(url):
    with pytest.raises(ValueError, match="credential-free HTTPS"):
        published.release_url({"site": {"SiteUrl": url}})


def test_anonymous_protection_is_not_authenticated_success(monkeypatch):
    monkeypatch.delenv("MERIDIAN_HOSTED_AUTH", raising=False)
    monkeypatch.setattr(published.urllib.request, "urlopen", MagicMock(
        side_effect=HTTPError("https://example.com", 401, "Unauthorized", {}, None)))
    assert "authenticated validation still required" in published.reachability("https://example.com")


def test_credentials_are_sent_only_as_a_header(monkeypatch):
    monkeypatch.setenv("MERIDIAN_HOSTED_AUTH", '{"username":"demo","password":"test-only"}')
    response = MagicMock()
    response.__enter__.return_value.status = 200
    fetch = MagicMock(return_value=response)
    monkeypatch.setattr(published.urllib.request, "urlopen", fetch)
    assert published.reachability("https://example.com") == "reachable (HTTP 200)"
    request = fetch.call_args.args[0]
    assert request.full_url == "https://example.com"
    assert request.get_header("Authorization").startswith("Basic ")
