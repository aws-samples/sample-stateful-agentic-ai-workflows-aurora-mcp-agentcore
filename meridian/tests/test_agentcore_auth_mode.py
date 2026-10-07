"""One environment variable chooses between the IAM path and the Cognito bearer path."""

import pytest

from backend.agentcore.auth_mode import (
    AUTH_MODE_ENV,
    AuthModeError,
    agentcore_auth_mode,
    jwt_mode,
)


def test_unset_means_iam_so_the_current_release_is_unchanged(monkeypatch):
    monkeypatch.delenv(AUTH_MODE_ENV, raising=False)
    assert agentcore_auth_mode() == "iam"
    assert jwt_mode() is False


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_value_means_iam(monkeypatch, value):
    monkeypatch.setenv(AUTH_MODE_ENV, value)
    assert agentcore_auth_mode() == "iam"


@pytest.mark.parametrize(("value", "mode"), [("iam", "iam"), ("JWT", "jwt"), (" jwt ", "jwt")])
def test_the_two_modes_are_case_and_space_insensitive(monkeypatch, value, mode):
    monkeypatch.setenv(AUTH_MODE_ENV, value)
    assert agentcore_auth_mode() == mode
    assert jwt_mode() is (mode == "jwt")


@pytest.mark.parametrize("value", ["true", "cognito", "none", "1"])
def test_anything_else_is_refused_by_name(monkeypatch, value):
    monkeypatch.setenv(AUTH_MODE_ENV, value)
    with pytest.raises(AuthModeError, match=AUTH_MODE_ENV):
        agentcore_auth_mode()
