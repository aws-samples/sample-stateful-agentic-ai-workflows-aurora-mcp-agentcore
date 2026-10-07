"""The identity stack's outputs reach the backend and the development frontend only."""

import pytest
from botocore.exceptions import ClientError

from scripts import sync_cognito_env as sync

OUTPUTS = {
    "UserPoolId": "us-east-1_AbCdEfGhI",
    "AppClientId": "client-web",
    "HostedUiDomain": "meridian-travelers-x.auth.us-east-1.amazoncognito.com",
    "Issuer": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_AbCdEfGhI",
}


def test_the_backend_gets_three_settings_and_the_frontend_two():
    settings = sync.settings_from(OUTPUTS, "us-east-1")
    assert settings[sync.ENV_FILE] == {
        "MERIDIAN_COGNITO_REGION": "us-east-1",
        "MERIDIAN_COGNITO_USER_POOL_ID": "us-east-1_AbCdEfGhI",
        "MERIDIAN_COGNITO_APP_CLIENT_ID": "client-web",
    }
    assert settings[sync.FRONTEND_ENV_FILE] == {
        "VITE_COGNITO_DOMAIN": "meridian-travelers-x.auth.us-east-1.amazoncognito.com",
        "VITE_COGNITO_CLIENT_ID": "client-web",
    }


def test_the_frontend_file_is_the_development_one_so_a_hosted_build_never_reads_it():
    assert sync.FRONTEND_ENV_FILE.name == ".env.development.local"
    assert sync.FRONTEND_ENV_FILE.parent.name == "frontend"


def test_a_missing_output_names_the_gap():
    with pytest.raises(SystemExit, match="AppClientId"):
        sync.settings_from({"UserPoolId": "p", "HostedUiDomain": "d"}, "us-east-1")


def test_outputs_are_read_by_key():
    class Cfn:
        def describe_stacks(self, StackName):
            assert StackName == "MeridianIdentity"
            return {"Stacks": [{"Outputs": [
                {"OutputKey": "UserPoolId", "OutputValue": "p"}]}]}

    assert sync.stack_outputs(Cfn()) == {"UserPoolId": "p"}


def test_nothing_is_written_without_the_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sync, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(sync, "FRONTEND_ENV_FILE", tmp_path / ".env.development.local")
    monkeypatch.setenv("AURORA_CLUSTER_ARN", "arn:aws:rds:us-east-1:111122223333:cluster:c")
    monkeypatch.setattr(sync, "stack_outputs", lambda cfn: OUTPUTS)
    monkeypatch.setattr(sync.boto3, "client", lambda *a, **k: object())
    sync.main([])
    assert not (tmp_path / ".env").exists()
    assert "would write MERIDIAN_COGNITO_USER_POOL_ID" in capsys.readouterr().out


def test_write_replaces_each_line_in_its_own_file(tmp_path, monkeypatch):
    backend, frontend = tmp_path / ".env", tmp_path / ".env.development.local"
    backend.write_text("A=1\nMERIDIAN_COGNITO_USER_POOL_ID=old\n")
    monkeypatch.setattr(sync, "ENV_FILE", backend)
    monkeypatch.setattr(sync, "FRONTEND_ENV_FILE", frontend)
    monkeypatch.setenv("AURORA_CLUSTER_ARN", "arn:aws:rds:us-east-1:111122223333:cluster:c")
    monkeypatch.setattr(sync, "stack_outputs", lambda cfn: OUTPUTS)
    monkeypatch.setattr(sync.boto3, "client", lambda *a, **k: object())
    sync.main(["--write"])
    assert backend.read_text().splitlines() == [
        "A=1", "MERIDIAN_COGNITO_USER_POOL_ID=us-east-1_AbCdEfGhI",
        "MERIDIAN_COGNITO_REGION=us-east-1", "MERIDIAN_COGNITO_APP_CLIENT_ID=client-web"]
    assert frontend.read_text().splitlines() == [
        "VITE_COGNITO_DOMAIN=meridian-travelers-x.auth.us-east-1.amazoncognito.com",
        "VITE_COGNITO_CLIENT_ID=client-web"]


def test_a_stack_that_is_not_deployed_stops_with_an_actionable_message(tmp_path, monkeypatch):
    class Cfn:
        def describe_stacks(self, StackName):
            raise ClientError({"Error": {"Code": "ValidationError", "Message":
                                         "Stack does not exist in 111122223333"}},
                              "DescribeStacks")

    monkeypatch.setattr(sync, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("AURORA_CLUSTER_ARN", "arn:aws:rds:us-east-1:111122223333:cluster:c")
    monkeypatch.setattr(sync.boto3, "client", lambda *a, **k: Cfn())
    with pytest.raises(SystemExit) as exit_info:
        sync.main(["--write"])
    message = str(exit_info.value)
    assert "DescribeStacks" in message and "AWS_PROFILE" in message
    assert "111122223333" not in message
