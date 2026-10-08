"""Fakes and descriptions for the Gateway release tests (no AWS, no network)."""

from __future__ import annotations

import json
from copy import deepcopy

from scripts.identity_release import gateway_release as gw
from scripts.identity_release import interceptor_lambda, settings
from tests import release_support as rs
from tests.aws_recorders import Recorder, client_error

GATEWAY_ARN = f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:gateway/{rs.GATEWAY_ID}"
ROLE_NAME = "AgentCore-meridianv2-gateway-role"


def current(mode="iam", design=settings.BOTH, **extra):
    """A Gateway as get_gateway returns it, with the fields update_gateway does not accept."""
    described = rs.gateway(mode, design)
    described.update({
        "gatewayArn": GATEWAY_ARN,
        "gatewayUrl": f"https://{rs.GATEWAY_ID}.gateway.bedrock-agentcore.{rs.REGION}"
                      ".amazonaws.com/mcp",
        "createdAt": "2026-10-02T09:00:00Z", "updatedAt": "2026-10-07T09:00:00Z",
        "description": f"Gateway for {rs.gateway(mode)['name']}", "exceptionLevel": "DEBUG",
        "protocolConfiguration": {"mcp": {"supportedVersions": ["2025-03-26"],
                                          "searchType": "SEMANTIC"}},
        "workloadIdentityDetails": {"workloadIdentityArn": "arn:aws:bedrock-agentcore:x"},
    })
    described.update(extra)
    return described


class Control(Recorder):
    """get_gateway answers ``before`` until update_gateway, then each ``after`` in turn."""

    def __init__(self, before, *after, events=None):
        super().__init__()
        self.before, self.after = before, list(after)
        self.updated = False
        self.events = events if events is not None else []

    def list_gateways(self, **kwargs):
        self.calls.append(("list_gateways", kwargs))
        gateway = self.before
        return listing((gateway["name"], gateway["gatewayId"]))

    def get_gateway(self, **kwargs):
        self.calls.append(("get_gateway", kwargs))
        if not self.updated:
            return deepcopy(self.before)
        return deepcopy(self.after.pop(0) if len(self.after) > 1 else self.after[0])

    def update_gateway(self, **kwargs):
        self.calls.append(("update_gateway", kwargs))
        self.events.append("update")
        self.updated = True
        return {}


class FakeIam(Recorder):
    """One inline policy on the Gateway's role, written, read and deleted like IAM does."""

    def __init__(self, document=None, events=None):
        super().__init__()
        self.document = document
        self.events = events if events is not None else []

    def get_role_policy(self, **kwargs):
        self.calls.append(("get_role_policy", kwargs))
        if self.document is None:
            raise client_error("NoSuchEntity")
        return {"PolicyDocument": deepcopy(self.document)}

    def put_role_policy(self, **kwargs):
        self.calls.append(("put_role_policy", kwargs))
        self.events.append("grant")
        self.document = json.loads(kwargs["PolicyDocument"])
        return {}

    def delete_role_policy(self, **kwargs):
        self.calls.append(("delete_role_policy", kwargs))
        self.events.append("revoke")
        if self.document is None:
            raise client_error("NoSuchEntity")
        self.document = None
        return {}


def iam_client(installed=True, events=None):
    document = gw.invoke_policy(rs.INTERCEPTOR_ARN) if installed else None
    return FakeIam(document, events)


class FakeLambda(Recorder):
    """get_function for the interceptor; it has no resource-policy calls to record."""

    def __init__(self, tags=None, **variables):
        super().__init__()
        wanted = interceptor_lambda.desired(
            rs.ACCOUNT, rs.REGION, settings.cognito_settings(rs.COGNITO_ENV))
        self.function = {
            "Configuration": {"FunctionName": wanted.function_name,
                              "Environment": {"Variables": {**wanted.environment, **variables}}},
            "Tags": dict(interceptor_lambda.TAGS if tags is None else tags)}

    def get_function(self, **kwargs):
        self.calls.append(("get_function", kwargs))
        return deepcopy(self.function)


def lambda_client(**keywords):
    return FakeLambda(**keywords)


def bare(mode="jwt", design=settings.BOTH, **extra):
    """A Gateway as the deploy leaves it: no interceptor attached yet."""
    return current(mode, design, interceptorConfigurations=None, **extra)


def listing(*named):
    """``list_gateways`` pages for (name, id) pairs."""
    return {"items": [{"gatewayId": gid, "name": name} for name, gid in named]}


def clients(control, iam=None, lam=None, cfn=None):
    return gw.Clients(control, iam or iam_client(), lam or lambda_client(), cfn)


def fast(**keywords):
    """The sleep and clock keywords that make ``apply`` instant."""
    return {"sleep": lambda seconds: None, "clock": iter(range(0, 100_000)).__next__, **keywords}
