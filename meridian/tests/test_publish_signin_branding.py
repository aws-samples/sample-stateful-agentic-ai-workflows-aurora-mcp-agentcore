"""Branding cannot write to a different identity stack or bypass its apply guard."""

from unittest.mock import Mock

import pytest

from scripts import publish_signin_branding as branding


@pytest.fixture
def release(monkeypatch, tmp_path):
    assets = tmp_path / "infra/branding"
    assets.mkdir(parents=True)
    (assets / "cognito.css").write_text(".background-customizable { background-color: black; }")
    (assets / "meridian-login.png").write_bytes(b"logo")
    monkeypatch.setattr(branding, "ROOT", tmp_path)
    outputs = {"UserPoolId": "test-pool", "AppClientId": "test-client"}
    monkeypatch.setattr(branding, "stack_outputs", lambda client: outputs)
    monkeypatch.setattr(branding, "require_account", lambda client, arn: None)
    cognito = Mock(spec=["get_ui_customization", "set_ui_customization"])
    cognito.get_ui_customization.return_value = {"UICustomization": {"CSS": "old"}}
    session = Mock()
    session.client.side_effect = (
        lambda service, **kwargs: cognito if service == "cognito-idp" else Mock()
    )
    env = {
        "AURORA_CLUSTER_ARN": "cluster",
        "MERIDIAN_COGNITO_USER_POOL_ID": "test-pool",
        "MERIDIAN_COGNITO_APP_CLIENT_ID": "test-client",
    }
    return session, env, cognito


def test_branding_dry_run_never_writes(release, tmp_path):
    session, env, cognito = release
    branding.publish(session, env, False)
    cognito.set_ui_customization.assert_not_called()
    assert not (tmp_path / ".local").exists()


@pytest.mark.parametrize("key", ["USER_POOL_ID", "APP_CLIENT_ID"])
def test_branding_refuses_another_identity_target(release, key):
    session, env, cognito = release
    env[f"MERIDIAN_COGNITO_{key}"] = "different"
    with pytest.raises(ValueError, match="differ"):
        branding.publish(session, env, True)
    cognito.get_ui_customization.assert_not_called()
    cognito.set_ui_customization.assert_not_called()


def test_branding_requires_explicit_confirmation_before_opening_a_session(monkeypatch):
    monkeypatch.setattr(branding.sys, "argv", ["branding", "--apply"])
    session = Mock(side_effect=AssertionError("AWS must not be called"))
    monkeypatch.setattr(branding.boto3, "Session", session)
    assert branding.main() == 3
    session.assert_not_called()


def test_branding_requires_matching_readback_and_preserves_original(release, tmp_path):
    session, env, cognito = release
    with pytest.raises(ValueError, match="readback"):
        branding.publish(session, env, True)
    call = cognito.set_ui_customization.call_args.kwargs
    assert call["UserPoolId"] == "test-pool"
    assert call["ClientId"] == "test-client"
    assert set(call) == {"UserPoolId", "ClientId", "CSS", "ImageFile"}
    backup = tmp_path / ".local/signin-branding/before.json"
    original = backup.read_bytes()
    assert backup.stat().st_mode & 0o777 == 0o600
    cognito.get_ui_customization.return_value = {
        "UICustomization": {"CSS": call["CSS"], "ImageUrl": "https://example.com/logo.png"},
    }
    branding.publish(session, env, True)
    assert backup.read_bytes() == original


CLUSTER = "arn:aws:rds:us-east-1:123456789012:cluster:meridian"


@pytest.fixture
def command(monkeypatch, tmp_path):
    monkeypatch.setattr(branding, "ROOT", tmp_path)
    monkeypatch.setattr(branding.sys, "argv", ["branding"])
    monkeypatch.setenv("AURORA_CLUSTER_ARN", CLUSTER)
    monkeypatch.setattr(branding.boto3, "Session", Mock())


def test_a_failed_run_says_what_to_fix_and_masks_account_ids(command, monkeypatch, capsys):
    def refuse(*_args):
        raise ValueError(f"Identity stack differs from {CLUSTER}.")

    monkeypatch.setattr(branding, "publish", refuse)
    assert branding.main() == 2
    printed = capsys.readouterr().err
    assert "Identity stack differs from" in printed
    assert "123456789012" not in printed and "<acct>" in printed


def test_a_missing_setting_is_named(command, monkeypatch, capsys):
    monkeypatch.delenv("AURORA_CLUSTER_ARN")
    assert branding.main() == 2
    assert "AURORA_CLUSTER_ARN" in capsys.readouterr().err
