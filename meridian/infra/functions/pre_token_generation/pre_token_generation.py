"""Cognito pre-token-generation trigger (event version 2): put the traveler in the access token.

A signed-in person is bound to exactly one traveler by an active row in
``traveler_identity_bindings`` (``identity_provider='cognito'``, ``subject_id`` = the user's
``sub``). This trigger looks that row up through the Data API as the ``meridian_identity``
login, which can read that table and nothing else, and adds ``traveler_id`` to the access token.
The API, both AgentCore Runtimes and the Gateway take the traveler from that verified claim.

It fails closed. A user with no active binding, or with more than one, cannot sign in.
"""

import json
import os

import boto3

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

RDS = boto3.client("rds-data")


def _bound_travelers(subject: str) -> list:
    response = RDS.execute_statement(
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


def lambda_handler(event: dict, _context) -> dict:
    """Add the user's bound traveler to the access token, or refuse the sign-in."""
    if str(event.get("version")) != "2":
        raise RuntimeError("This trigger needs pre token generation event version 2.")
    subject = (event.get("request", {}).get("userAttributes", {}) or {}).get("sub")
    if not subject:
        raise RuntimeError("The sign-in event carries no user sub.")
    travelers = _bound_travelers(subject)
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
