"""Tokens for the seeded users come from Cognito with the password read from Secrets Manager."""

import json

import pytest
from botocore.exceptions import ClientError

from scripts import cognito_tokens


class Recorder:
    def __init__(self, answers):
        self.calls, self.answers = [], answers

    def __getattr__(self, name):
        def record(**kwargs):
            self.calls.append((name, kwargs))
            return self.answers[name]
        return record


def test_tokens_come_from_the_admin_password_flow():
    sm = Recorder({"get_secret_value": {"SecretString": json.dumps(
        {"username": "jordan.lee@example.com", "password": "pw-from-secrets-manager"})}})
    idp = Recorder({"admin_initiate_auth": {"AuthenticationResult": {
        "AccessToken": "access.jwt", "IdToken": "id.jwt"}}})
    clients = {"sm": sm, "idp": idp, "pool_id": "us-east-1_Pool", "client_id": "client-web"}
    minted = cognito_tokens.mint_tokens("decoy", **clients)
    assert minted == {"access": "access.jwt", "id": "id.jwt"}
    assert cognito_tokens.mint_access_token("decoy", **clients) == "access.jwt"
    assert sm.calls == 2 * [("get_secret_value", {"SecretId": "meridian/cognito/decoy"})]
    (_, call), *_ = idp.calls
    assert call == {
        "UserPoolId": "us-east-1_Pool", "ClientId": "client-web",
        "AuthFlow": "ADMIN_USER_PASSWORD_AUTH",
        "AuthParameters": {"USERNAME": "jordan.lee@example.com",
                           "PASSWORD": "pw-from-secrets-manager"},
    }


PASSWORD = "pw-from-secrets-manager"
SECRET = {"SecretString": json.dumps({"username": "jordan.lee@example.com", "password": PASSWORD})}
CLIENTS = {"pool_id": "us-east-1_Pool", "client_id": "client-web"}
ARN = "arn:aws:secretsmanager:us-east-1:111122223333:secret:meridian/cognito/decoy-AbCdEf"


class Raiser:
    def __init__(self, error):
        self.error = error

    def __getattr__(self, name):
        def fail(**kwargs):
            raise self.error
        return fail


def test_a_challenge_is_reported_without_the_password_or_the_session():
    idp = Recorder({"admin_initiate_auth": {
        "ChallengeName": "NEW_PASSWORD_REQUIRED", "Session": "session-sentinel"}})
    with pytest.raises(RuntimeError) as exit_info:
        cognito_tokens.mint_tokens("decoy", sm=Recorder({"get_secret_value": SECRET}), idp=idp,
                                   **CLIENTS)
    message = str(exit_info.value)
    assert message == ("sign-in for decoy needs a NEW_PASSWORD_REQUIRED challenge; re-run "
                       "scripts/seed_cognito_users.py --user decoy --apply")
    assert PASSWORD not in message and "session-sentinel" not in message
    assert [n for n, _ in idp.calls] == ["admin_initiate_auth"]


def test_a_missing_secret_is_reported_with_the_fix_and_no_account_id():
    missing = ClientError({"Error": {"Code": "ResourceNotFoundException",
                                     "Message": f"Secrets Manager can't find {ARN}"}},
                          "GetSecretValue")
    with pytest.raises(RuntimeError) as exit_info:
        cognito_tokens.mint_tokens("decoy", sm=Raiser(missing), idp=Recorder({}), **CLIENTS)
    message = str(exit_info.value)
    assert "meridian/cognito/decoy" in message and "seed_cognito_users.py" in message
    assert "111122223333" not in message and ARN not in message


def test_a_failed_sign_in_is_redacted_and_leaks_no_password():
    denied = ClientError({"Error": {"Code": "NotAuthorizedException", "Message":
                                    "Incorrect username or password in 111122223333"}},
                         "AdminInitiateAuth")
    with pytest.raises(RuntimeError) as exit_info:
        cognito_tokens.mint_tokens("decoy", sm=Recorder({"get_secret_value": SECRET}),
                                   idp=Raiser(denied), **CLIENTS)
    message = str(exit_info.value)
    assert "AdminInitiateAuth" in message and "NotAuthorizedException" in message
    assert "111122223333" not in message and PASSWORD not in message


def test_minting_prints_nothing(capsys):
    idp = Recorder({"admin_initiate_auth": {"AuthenticationResult": {
        "AccessToken": "access-sentinel.jwt", "IdToken": "id-sentinel.jwt"}}})
    cognito_tokens.mint_tokens("decoy", sm=Recorder({"get_secret_value": SECRET}), idp=idp,
                               **CLIENTS)
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
