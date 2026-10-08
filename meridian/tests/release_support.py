"""Hop descriptions as the AgentCore control plane returns them, for the release tools' tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from scripts.identity_release import settings

ACCOUNT = "123456789012"
REGION = "us-east-1"
POOL = "us-east-1_AbCdEfGhI"
CLIENT = "exampleclientid123"
COGNITO_ENV = {
    "MERIDIAN_COGNITO_REGION": REGION,
    "MERIDIAN_COGNITO_USER_POOL_ID": POOL,
    "MERIDIAN_COGNITO_APP_CLIENT_ID": CLIENT,
}
GATEWAY_ID = "meridianv2-meridian-aurora-abcde12345"
ENGINE_ID = "meridianv2_Engine-abc"
ENGINE_ARN = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:policy-engine/{ENGINE_ID}"
GATEWAY_URL = f"https://{GATEWAY_ID}.gateway.bedrock-agentcore.{REGION}.amazonaws.com/mcp"
INTERCEPTOR_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{settings.INTERCEPTOR_FUNCTION}"
GATEWAY_ROLE = f"arn:aws:iam::{ACCOUNT}:role/AgentCore-meridianv2-gateway-role"
RUNTIME_IDS = {"MeridianConcierge": "meridianv2_MeridianConcierge-LpDBbFBjsc",
               "MeridianWorkflow": "meridianv2_MeridianWorkflow-cTi3MLBsPW"}
BINDING_POLICY = "meridian_traveler_binding"
BASE_POLICIES = ["meridian_read_tools", "meridian_hold_governance", "meridian_booking_governance"]
MASTER_SECRET = f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:master-XyZ987"
BACKEND_SECRET = f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:backend-AbC123"
SHA = "0123456789abcdef0123456789abcdef01234567"
HOSTED_UI_DOMAIN = "d.auth.us-east-1.amazoncognito.com"
SERVICE_ARN = f"arn:aws:apprunner:{REGION}:{ACCOUNT}:service/meridian-web/abc123"


def target(mode: str = "jwt", design: str = settings.BOTH):
    """What the release expects every hop to report."""
    from scripts.identity_release.preflight import Target

    pool = settings.cognito_settings(COGNITO_ENV)
    return Target(
        mode=mode, design=design, cognito=pool if mode == "jwt" else None,
        interceptor_arn=INTERCEPTOR_ARN if settings.uses_interceptor(design) else None,
        account=ACCOUNT, region=REGION, master_secret_arn=MASTER_SECRET,
        backend_secret_arn=BACKEND_SECRET if mode == "jwt" else None)


def jwt_authorizer() -> dict:
    pool = settings.cognito_settings(COGNITO_ENV)
    return {"customJWTAuthorizer": {"discoveryUrl": pool.discovery_url, "allowedClients": [CLIENT]}}


def gateway(mode: str = "jwt", design: str = settings.BOTH) -> dict:
    """``get_gateway`` for a Gateway in the given mode."""
    described = {
        "gatewayId": GATEWAY_ID, "status": "READY", "roleArn": GATEWAY_ROLE,
        "gatewayUrl": GATEWAY_URL, "name": settings.gateway_physical_name(mode),
        "protocolType": "MCP",
        "policyEngineConfiguration": {"arn": ENGINE_ARN, "mode": "ENFORCE"},
        "authorizerType": "AWS_IAM",
    }
    if mode == "jwt":
        described["authorizerType"] = "CUSTOM_JWT"
        described["authorizerConfiguration"] = jwt_authorizer()
        if settings.uses_interceptor(design):
            described["interceptorConfigurations"] = [{
                "interceptor": {"lambda": {"arn": INTERCEPTOR_ARN}},
                "interceptionPoints": ["REQUEST"],
                "inputConfiguration": {"passRequestHeaders": True},
            }]
    return described


def runtime(name: str, mode: str = "jwt", *, iam_env: bool = False) -> dict:
    """``get_agent_runtime`` for a Runtime in the given mode."""
    described = {
        "agentRuntimeId": RUNTIME_IDS[name], "status": "READY",
        "roleArn": f"arn:aws:iam::{ACCOUNT}:role/{name}",
        "environmentVariables": {
            settings.gateway_url_variable(mode): GATEWAY_URL}}
    if name == "MeridianConcierge":
        described["environmentVariables"].update(
            {"MERIDIAN_GATEWAY_ID": GATEWAY_ID, "MERIDIAN_POLICY_ENGINE_ID": ENGINE_ID})
    if mode == "jwt":
        described["authorizerConfiguration"] = jwt_authorizer()
        described["requestHeaderConfiguration"] = {"requestHeaderAllowlist": ["Authorization"]}
        described["environmentVariables"]["MERIDIAN_AGENTCORE_AUTH"] = "jwt"
    elif iam_env:
        described["environmentVariables"]["MERIDIAN_AGENTCORE_AUTH"] = "iam"
    return described



def jwt_service_variables() -> dict:
    return {
        "MERIDIAN_AGENTCORE_AUTH": "jwt", **COGNITO_ENV, "ENVIRONMENT": "production",
        "AURORA_SECRET_ARN": BACKEND_SECRET,
        "AURORA_BACKEND_SECRET_ARN": BACKEND_SECRET,
        "EXISTING_SETTING": "preserve",
    }


def policies(names: list[str], mode: str = "ACTIVE") -> list[dict]:
    """``list_policies`` summaries; every one is ACTIVE and in ``mode``."""
    return [{"name": name, "status": "ACTIVE", "enforcementMode": mode, "policyId": f"{name}-x"}
            for name in names]


def policy_modes(names: list[str], mode: str = "ACTIVE") -> dict:
    """What ``read_state`` carries: policy name to enforcement mode."""
    return {name: mode for name in names}


def identity_outputs() -> list[dict]:
    """``describe_stacks`` outputs of the identity stack."""
    pool = settings.cognito_settings(COGNITO_ENV)
    return [{"OutputKey": "UserPoolId", "OutputValue": POOL},
            {"OutputKey": "AppClientId", "OutputValue": CLIENT},
            {"OutputKey": "HostedUiDomain", "OutputValue": HOSTED_UI_DOMAIN},
            {"OutputKey": "Issuer", "OutputValue": pool.issuer}]


def app_runner_service(variables: dict, secrets: dict | None = None) -> dict:
    """``describe_service``'s Service for an image-based service with this environment."""
    return {"ServiceArn": SERVICE_ARN, "Status": "RUNNING", "SourceConfiguration": {
        "ImageRepository": {"ImageIdentifier": "x", "ImageRepositoryType": "ECR",
                            "ImageConfiguration": {"RuntimeEnvironmentVariables": variables,
                                                   "RuntimeEnvironmentSecrets": secrets or {}}}}}


def receipt(taken: datetime, **fields) -> dict:
    """A backend-login receipt with every field of ``settings.PROOF_FIELDS``."""
    pool = settings.cognito_settings(COGNITO_ENV)
    return {"ok": True, "at": taken.isoformat(), "account": ACCOUNT, "region": REGION,
            "user_pool_id": pool.pool_id, "git_sha": SHA, "login": "meridian_backend",
            "checks": {"warm": True, "recovery": True}, **fields}


def mutated(described: dict, path: str, value) -> dict:
    """A copy of ``described`` with the dotted ``path`` set to ``value`` (``None`` deletes it)."""
    copy = deepcopy(described)
    node = copy
    *parents, leaf = path.split(".")
    for key in parents:
        node = node[key]
    if value is None:
        node.pop(leaf, None)
    else:
        node[leaf] = value
    return copy
