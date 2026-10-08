"""The release tools read the mode, the enforcement design and the pool from one place."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from scripts.identity_release import settings

COGNITO = {
    "MERIDIAN_COGNITO_REGION": "us-east-1",
    "MERIDIAN_COGNITO_USER_POOL_ID": "us-east-1_AbCdEfGhI",
    "MERIDIAN_COGNITO_APP_CLIENT_ID": "exampleclientid123",
}


@pytest.mark.parametrize(("env", "expected"), [
    ({}, "iam"),
    ({"MERIDIAN_AGENTCORE_AUTH": ""}, "iam"),
    ({"MERIDIAN_AGENTCORE_AUTH": "  "}, "iam"),
    ({"MERIDIAN_AGENTCORE_AUTH": "IAM"}, "iam"),
    ({"MERIDIAN_AGENTCORE_AUTH": " jwt "}, "jwt"),
    ({"MERIDIAN_AGENTCORE_AUTH": None}, "iam"),
])
def test_the_mode_defaults_to_iam_and_ignores_case_and_space(env, expected):
    assert settings.release_mode(env) == expected


@pytest.mark.parametrize("value", ["true", "cognito", "1", "none"])
def test_any_other_mode_is_refused_by_name(value):
    with pytest.raises(settings.ReleaseConfigError, match="MERIDIAN_AGENTCORE_AUTH.*iam.*jwt"):
        settings.release_mode({"MERIDIAN_AGENTCORE_AUTH": value})


@pytest.mark.parametrize(("env", "expected"), [
    ({}, "both"),
    ({"MERIDIAN_GATEWAY_ENFORCEMENT": ""}, "both"),
    ({"MERIDIAN_GATEWAY_ENFORCEMENT": "BOTH"}, "both"),
    ({"MERIDIAN_GATEWAY_ENFORCEMENT": " cedar"}, "cedar"),
    ({"MERIDIAN_GATEWAY_ENFORCEMENT": "interceptor"}, "interceptor"),
])
def test_the_enforcement_design_defaults_to_both(env, expected):
    assert settings.enforcement(env) == expected


def test_an_unknown_enforcement_design_is_refused_with_the_choices():
    with pytest.raises(settings.ReleaseConfigError) as refused:
        settings.enforcement({"MERIDIAN_GATEWAY_ENFORCEMENT": "neither"})
    message = str(refused.value)
    assert "MERIDIAN_GATEWAY_ENFORCEMENT" in message
    for choice in ("both", "cedar", "interceptor"):
        assert choice in message


def test_each_design_says_which_layers_it_uses():
    assert (settings.uses_interceptor("both"), settings.uses_cedar_binding("both")) == (True, True)
    assert (settings.uses_interceptor("cedar"), settings.uses_cedar_binding("cedar")) == (
        False, True)
    assert (settings.uses_interceptor("interceptor"),
            settings.uses_cedar_binding("interceptor")) == (True, False)


def test_the_pool_settings_give_the_issuer_and_the_discovery_url():
    pool = settings.cognito_settings(COGNITO)
    assert pool.issuer == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_AbCdEfGhI"
    assert pool.discovery_url == pool.issuer + "/.well-known/openid-configuration"
    assert pool.client_id == "exampleclientid123"


def test_missing_pool_settings_are_all_named_in_one_message():
    with pytest.raises(settings.ReleaseConfigError) as refused:
        settings.cognito_settings({"MERIDIAN_COGNITO_REGION": "us-east-1"})
    assert "MERIDIAN_COGNITO_USER_POOL_ID" in str(refused.value)
    assert "MERIDIAN_COGNITO_APP_CLIENT_ID" in str(refused.value)
    assert "sync_cognito_env.py --write" in str(refused.value)


def test_pool_id_region_must_match_cognito_region():
    with pytest.raises(settings.ReleaseConfigError, match="MERIDIAN_COGNITO_USER_POOL_ID"):
        settings.cognito_settings(
            {**COGNITO, "MERIDIAN_COGNITO_REGION": "eu-west-1",
             "MERIDIAN_COGNITO_USER_POOL_ID": "us-west-2_AbC"}
        )


@pytest.mark.parametrize(("key", "value"), [
    ("MERIDIAN_COGNITO_REGION", "us east 1"),
    ("MERIDIAN_COGNITO_USER_POOL_ID", "pool/with/slashes"),
    ("MERIDIAN_COGNITO_APP_CLIENT_ID", "client id"),
    ("MERIDIAN_COGNITO_APP_CLIENT_ID", "x" * 200),
])
def test_a_malformed_pool_setting_is_refused_without_echoing_other_values(key, value):
    with pytest.raises(settings.ReleaseConfigError, match=key):
        settings.cognito_settings({**COGNITO, key: value})


def test_the_git_head_is_a_full_sha_for_a_repository(tmp_path):
    assert re.fullmatch(r"[0-9a-f]{40}", settings.git_head())
    with pytest.raises(settings.ReleaseConfigError, match="git HEAD"):
        settings.git_head(tmp_path)


def test_a_git_head_that_is_not_a_full_lowercase_sha_is_refused(monkeypatch):
    for output in ("HEAD\n", "abc123\n", "A" * 40 + "\n", ""):
        monkeypatch.setattr(subprocess, "run", lambda *a, _o=output, **k: (
            subprocess.CompletedProcess(a, 0, stdout=_o, stderr="")))
        with pytest.raises(settings.ReleaseConfigError, match="git HEAD"):
            settings.git_head()


def git(repo: Path, *argv: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    *argv], check=True, capture_output=True)


@pytest.fixture
def checkout(tmp_path):
    """A repository whose ``meridian/`` folder holds one tracked file, ``.local`` ignored."""
    git(tmp_path, "init", "-q")
    folder = tmp_path / "meridian"
    (folder / ".local").mkdir(parents=True)
    (folder / "tracked.py").write_text("x = 1\n")
    (folder / ".gitignore").write_text("ignored.txt\n")
    (tmp_path / "outside.txt").write_text("a\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "first")
    return folder


def test_a_clean_checkout_has_no_working_tree_changes(checkout):
    (checkout / "ignored.txt").write_text("x")
    (checkout / ".local" / "release.json").write_text("{}")
    (checkout.parent / "outside.txt").write_text("changed outside meridian\n")

    assert settings.working_tree_changes(checkout) == []


@pytest.mark.parametrize("change", ["modify", "untracked", "staged", "deleted"])
def test_any_tracked_or_untracked_change_under_the_folder_is_listed(checkout, change):
    if change == "modify":
        (checkout / "tracked.py").write_text("x = 2\n")
    elif change == "untracked":
        (checkout / "new.py").write_text("y = 1\n")
    elif change == "staged":
        (checkout / "tracked.py").write_text("x = 3\n")
        git(checkout.parent, "add", "meridian/tracked.py")
    else:
        (checkout / "tracked.py").unlink()

    found = settings.working_tree_changes(checkout)

    assert len(found) == 1 and ("tracked.py" in found[0] or "new.py" in found[0])


def test_working_tree_changes_outside_a_repository_are_refused(tmp_path):
    with pytest.raises(settings.ReleaseConfigError, match="git status"):
        settings.working_tree_changes(tmp_path)
