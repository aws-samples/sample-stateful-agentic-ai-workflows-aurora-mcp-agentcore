"""Recording fakes for the holds Lambda, the catalog Lambda, their roles and the SSM parameter."""

from __future__ import annotations

from scripts.identity_release import lambda_release as lambdas
from tests import release_support as rs
from tests.aws_recorders import Recorder, Waiters

MASTER = f"arn:aws:secretsmanager:{rs.REGION}:{rs.ACCOUNT}:secret:meridian-AbC123"
GATEWAY = f"arn:aws:secretsmanager:{rs.REGION}:{rs.ACCOUNT}:secret:meridian/aurora/gateway-GwY456"
CATALOG = f"arn:aws:secretsmanager:{rs.REGION}:{rs.ACCOUNT}:secret:meridian/aurora/catalog-CtL789"
HOLDS_NAME = "AgentCore-meridianv2-MeridianHolds1"
HOLDS_ARN = f"arn:aws:lambda:{rs.REGION}:{rs.ACCOUNT}:function:{HOLDS_NAME}"
HOLDS_ROLE = "AgentCore-meridianv2-MeridianHoldsRole"
CATALOG_ROLE = "MeridianCatalogToolsRole"
SECRETS = lambdas.Secrets(master=MASTER, gateway=GATEWAY, catalog=CATALOG)


def policy(*resources, action="secretsmanager:GetSecretValue", effect="Allow"):
    return {"Version": "2012-10-17", "Statement": [
        {"Effect": effect, "Action": [action], "Resource": list(resources)},
        {"Effect": "Allow", "Action": ["ssm:GetParameters"], "Resource": "*"}]}


class LambdaClient(Recorder, Waiters):
    def __init__(self, **kwargs):
        Recorder.__init__(self, **kwargs)
        Waiters.__init__(self)


class World:
    """The holds Lambda, the catalog Lambda, their roles, the SSM parameter and the target list."""

    def __init__(self, *, ssm=GATEWAY, holds_grants=(GATEWAY,), catalog_env=CATALOG,
                 catalog_grants=(CATALOG,), holds_attached=None, managed=None,
                 catalog_attached=None):
        self.control = Recorder({
            "list_gateway_targets": {"items": [
                {"name": "MeridianCatalog", "targetId": "T1"},
                {"name": "MeridianHolds", "targetId": "T2"}]},
            "get_gateway_target": {"targetConfiguration": {"mcp": {"lambda": {
                "lambdaArn": HOLDS_ARN}}}}})
        self.ssm = Recorder({"get_parameter": {"Parameter": {"Value": ssm}}})
        self.configs = {
            HOLDS_ARN: {"FunctionName": HOLDS_NAME,
                        "Role": f"arn:aws:iam::{rs.ACCOUNT}:role/{HOLDS_ROLE}",
                        "Environment": {"Variables": {"EXISTING": "1"}}},
            lambdas.CATALOG_FUNCTION: {
                "FunctionName": lambdas.CATALOG_FUNCTION,
                "Role": f"arn:aws:iam::{rs.ACCOUNT}:role/{CATALOG_ROLE}",
                "Environment": {"Variables": {"AURORA_SECRET_ARN": catalog_env}}}}
        self.lam = LambdaClient(answers={
            "get_function_configuration": lambda FunctionName: self.configs[FunctionName],
            "update_function_configuration": self.update})
        self.roles = {HOLDS_ROLE: policy(*holds_grants), CATALOG_ROLE: policy(*catalog_grants)}
        attached = {HOLDS_ROLE: holds_attached or [], CATALOG_ROLE: catalog_attached or []}
        self.iam = Recorder({
            "list_role_policies": lambda RoleName: {"PolicyNames": ["inline"]},
            "get_role_policy": lambda RoleName, PolicyName: {
                "PolicyDocument": self.roles[RoleName]},
            "list_attached_role_policies": lambda RoleName: {
                "AttachedPolicies": attached[RoleName]},
            "get_policy": lambda PolicyArn: {"Policy": {"DefaultVersionId": "v1"}},
            "get_policy_version": lambda PolicyArn, VersionId: {
                "PolicyVersion": {"Document": (managed or {}).get(PolicyArn, policy(MASTER))}}})
        self.sts = Recorder({"get_caller_identity": {"Account": rs.ACCOUNT}})

    def update(self, FunctionName, **changes):
        self.configs[FunctionName]["Environment"] = changes["Environment"]
        return self.configs[FunctionName]

    def check(self, stage):
        return lambdas.check(self.ssm, self.lam, self.iam, self.control, rs.GATEWAY_ID,
                             SECRETS, stage)

    def session(self, region):
        clients = {"sts": self.sts, "bedrock-agentcore-control": self.control, "ssm": self.ssm,
                   "iam": self.iam, "lambda": self.lam}
        return type("Session", (), {"client": lambda _, name, **kw: clients[name]})()
