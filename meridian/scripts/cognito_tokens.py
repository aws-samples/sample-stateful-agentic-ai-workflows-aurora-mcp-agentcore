"""Mint real tokens for a seeded user, for tests and proof scripts.

The password comes from Secrets Manager (meridian/cognito/<key>) and goes straight to Cognito's
ADMIN_USER_PASSWORD_AUTH, which only a caller with IAM permission can use. The tokens are returned
to the caller in memory. Nothing here prints, logs or writes a token or a password.
"""

import json
import os
from typing import Dict, Optional

import boto3

SECRET_PREFIX = "meridian/cognito/"


def mint_tokens(
    user_key: str, *, sm=None, idp=None, pool_id: Optional[str] = None,
    client_id: Optional[str] = None,
) -> Dict[str, str]:
    """Sign the seeded user in and return ``{"access": ..., "id": ...}``.

    Args:
        user_key: ``jordan`` or ``decoy``.
        sm: A Secrets Manager client, or None for the default.
        idp: A Cognito identity provider client, or None for the default.
        pool_id: Defaults to MERIDIAN_COGNITO_USER_POOL_ID.
        client_id: Defaults to MERIDIAN_COGNITO_APP_CLIENT_ID.
    """
    region = os.environ.get("MERIDIAN_COGNITO_REGION", "us-east-1")
    sm = sm or boto3.client("secretsmanager", region_name=region)
    idp = idp or boto3.client("cognito-idp", region_name=region)
    pool_id = pool_id or os.environ["MERIDIAN_COGNITO_USER_POOL_ID"]
    client_id = client_id or os.environ["MERIDIAN_COGNITO_APP_CLIENT_ID"]
    login = json.loads(sm.get_secret_value(SecretId=SECRET_PREFIX + user_key)["SecretString"])
    result = idp.admin_initiate_auth(
        UserPoolId=pool_id, ClientId=client_id, AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": login["username"], "PASSWORD": login["password"]},
    )["AuthenticationResult"]
    return {"access": result["AccessToken"], "id": result["IdToken"]}


def mint_access_token(user_key: str, **clients) -> str:
    """Sign the seeded user in and return just the access token."""
    return mint_tokens(user_key, **clients)["access"]
