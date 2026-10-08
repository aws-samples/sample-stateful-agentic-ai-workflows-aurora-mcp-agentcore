"""Hop descriptions as the AgentCore control plane returns them, for the release tools' tests."""

from __future__ import annotations

from copy import deepcopy

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
ENGINE_ARN = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:policy-engine/meridianv2_Engine-abc"
INTERCEPTOR_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{settings.INTERCEPTOR_FUNCTION}"
GATEWAY_ROLE = f"arn:aws:iam::{ACCOUNT}:role/AgentCore-meridianv2-gateway-role"
RUNTIME_IDS = {"MeridianConcierge": "meridianv2_MeridianConcierge-LpDBbFBjsc",
               "MeridianWorkflow": "meridianv2_MeridianWorkflow-cTi3MLBsPW"}
BINDING_POLICY = "meridian_traveler_binding"
BASE_POLICIES = ["meridian_read_tools", "meridian_hold_governance", "meridian_booking_governance"]


def target(mode: str = "jwt", design: str = settings.BOTH):
    """What the release expects every hop to report."""
    from scripts.identity_release.preflight import Target

    pool = settings.cognito_settings(COGNITO_ENV)
    return Target(
        mode=mode, design=design, cognito=pool if mode == "jwt" else None,
        interceptor_arn=INTERCEPTOR_ARN if settings.uses_interceptor(design) else None)


def jwt_authorizer() -> dict:
    pool = settings.cognito_settings(COGNITO_ENV)
    return {"customJWTAuthorizer": {"discoveryUrl": pool.discovery_url, "allowedClients": [CLIENT]}}


def gateway(mode: str = "jwt", design: str = settings.BOTH) -> dict:
    """``get_gateway`` for a Gateway in the given mode."""
    described = {
        "gatewayId": GATEWAY_ID, "status": "READY", "roleArn": GATEWAY_ROLE,
        "name": "meridian-aurora", "protocolType": "MCP",
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
        "roleArn": f"arn:aws:iam::{ACCOUNT}:role/{name}", "environmentVariables": {}}
    if mode == "jwt":
        described["authorizerConfiguration"] = jwt_authorizer()
        described["requestHeaderConfiguration"] = {"requestHeaderAllowlist": ["Authorization"]}
        described["environmentVariables"] = {"MERIDIAN_AGENTCORE_AUTH": "jwt"}
    elif iam_env:
        described["environmentVariables"] = {"MERIDIAN_AGENTCORE_AUTH": "iam"}
    return described


BACKEND_SECRET = f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:backend-AbC123"


def jwt_service_variables() -> dict:
    return {
        "MERIDIAN_AGENTCORE_AUTH": "jwt", **COGNITO_ENV, "ENVIRONMENT": "production",
        "AURORA_SECRET_ARN": BACKEND_SECRET,
        "AURORA_BACKEND_SECRET_ARN": BACKEND_SECRET,
        "EXISTING_SETTING": "preserve",
    }


def policies(names: list[str]) -> list[dict]:
    return [{"name": name, "status": "ACTIVE", "policyId": f"{name}-x"} for name in names]


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
