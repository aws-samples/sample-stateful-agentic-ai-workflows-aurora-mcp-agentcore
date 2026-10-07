"""Tokens for the seeded users come from Cognito with the password read from Secrets Manager."""

import json

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
