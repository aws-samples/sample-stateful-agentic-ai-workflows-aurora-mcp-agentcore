"""Cognito pre-token-generation trigger (event version 2): put the traveler in the access token.

A signed-in person is bound to exactly one traveler by an active row in
``traveler_identity_bindings`` (``identity_provider='cognito'``, ``subject_id`` = the user's
``sub``). This trigger looks that row up through the Data API as the ``meridian_identity``
login, which can read that table and nothing else, and adds ``traveler_id`` to the access token.
The API, both AgentCore Runtimes and the Gateway take the traveler from that verified claim.

It fails closed. A user with no active binding, or with more than one, cannot sign in.

Amazon Cognito gives a pre token generation trigger 5 seconds and cannot be changed, so the
function times out at 4. A dropped connection is retried once, and only while the second
attempt can still finish before the function does.
"""

import json
import logging
import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
)

CLUSTER_ARN = os.environ["AURORA_CLUSTER_ARN"]
SECRET_ARN = os.environ["AURORA_SECRET_ARN"]
DATABASE = os.environ["AURORA_DATABASE"]
PROVIDER = "cognito"
TRAVELER_CLAIM = "traveler_id"

BINDING_SQL = (
    "SELECT traveler_id FROM traveler_identity_bindings "
    "WHERE identity_provider = :provider AND subject_id = :subject "
    "AND status = 'active' AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)"
)

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

FUNCTION_TIMEOUT_S = 4.0
RETRY_MARGIN_S = 0.3
RETRY_CONNECT_TIMEOUT_S = 1.0
RETRY_MIN_READ_TIMEOUT_S = 0.5
CONNECTION_ERRORS = (ConnectionClosedError, EndpointConnectionError, ConnectTimeoutError)


def _data_api(connect_timeout: float, read_timeout: float):
    return boto3.client(
        "rds-data",
        config=Config(
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            retries={"total_max_attempts": 1},
        ),
    )


RDS = _data_api(connect_timeout=1, read_timeout=3)


def _retry_client(read_timeout: float):
    return _data_api(connect_timeout=RETRY_CONNECT_TIMEOUT_S, read_timeout=read_timeout)


def _bound_travelers(client, subject: str) -> list:
    response = client.execute_statement(
        resourceArn=CLUSTER_ARN,
        secretArn=SECRET_ARN,
        database=DATABASE,
        sql=BINDING_SQL,
        formatRecordsAs="JSON",
        parameters=[
            {"name": "provider", "value": {"stringValue": PROVIDER}},
            {"name": "subject", "value": {"stringValue": subject}},
        ],
    )
    return [row["traveler_id"] for row in json.loads(response.get("formattedRecords") or "[]")]


def _lookup(subject: str, started: float) -> list:
    """Look the subject up, retrying once after a dropped connection if the budget allows."""
    try:
        return _bound_travelers(RDS, subject)
    except CONNECTION_ERRORS as error:
        remaining = FUNCTION_TIMEOUT_S - (time.monotonic() - started)
        read_timeout = remaining - RETRY_MARGIN_S - RETRY_CONNECT_TIMEOUT_S
        if read_timeout < RETRY_MIN_READ_TIMEOUT_S:
            raise
        LOGGER.warning("Traveler lookup retrying after: %s", _error_code(error))
    return _bound_travelers(_retry_client(read_timeout), subject)


def _error_code(error: Exception) -> str:
    if isinstance(error, ClientError):
        return error.response.get("Error", {}).get("Code") or "ClientError"
    return type(error).__name__


def lambda_handler(event: dict, _context) -> dict:
    """Add the user's bound traveler to the access token, or refuse the sign-in."""
    started = time.monotonic()
    if str(event.get("version")) != "2":
        raise RuntimeError("This trigger needs pre token generation event version 2.")
    subject = (event.get("request", {}).get("userAttributes", {}) or {}).get("sub")
    if not subject:
        raise RuntimeError("The sign-in event carries no user sub.")
    try:
        travelers = _lookup(subject, started)
    except (ClientError, BotoCoreError) as error:
        LOGGER.error("Traveler lookup failed: %s", _error_code(error))
        raise RuntimeError("Sign-in refused: the traveler lookup failed.") from None
    if len(travelers) != 1:
        raise RuntimeError(
            "Sign-in refused: this user needs exactly one active traveler binding, "
            f"found {len(travelers)}."
        )
    event["response"] = {
        "claimsAndScopeOverrideDetails": {
            "accessTokenGeneration": {
                "claimsToAddOrOverride": {TRAVELER_CLAIM: travelers[0]},
            },
        },
    }
    return event
