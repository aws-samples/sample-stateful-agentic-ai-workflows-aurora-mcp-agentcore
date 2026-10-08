"""A stateful fake of every service the snapshot and the rollback touch (no AWS, no network).

``SnapWorld`` starts in the IAM configuration. ``release()`` moves every hop to the Cognito
configuration the way the window does. The fakes keep what the real services keep. By default the
write operations replace what the real ones may replace (``update_gateway`` and
``update_agent_runtime`` drop any field left out), so a restore that forgets a field shows;
``keeps_omitted = True`` models the other possibility, that the service keeps a field the update
leaves out. Every update moves the hop to UPDATING (Lambda: InProgress) for ``settle_reads`` reads
before it is READY again. Real behavior is only proven by the Task 22 rehearsal.
"""

from __future__ import annotations

import io
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from botocore.exceptions import WaiterError

from scripts.identity_release import lambda_release, settings, snapshot
from tests import release_support as rs
from tests.aws_recorders import Waiters, client_error, violations
from tests.gateway_release_support import current as gateway_current

STAMPED = datetime(2026, 10, 8, 12, 30, 5, tzinfo=timezone.utc)
CLUSTER = f"arn:aws:rds:{rs.REGION}:{rs.ACCOUNT}:cluster:meridian"
SERVICE_ARN = rs.SERVICE_ARN
API_SECRET = f"arn:aws:secretsmanager:{rs.REGION}:{rs.ACCOUNT}:secret:meridian/web/api-token-AbC123"
GATEWAY_SECRET = f"arn:aws:secretsmanager:{rs.REGION}:{rs.ACCOUNT}:secret:gateway-QrS456"
HOLDS_ARN = (f"arn:aws:lambda:{rs.REGION}:{rs.ACCOUNT}:function:"
             "AgentCore-meridianv2-MeridianHoldsAbc12")
SEMANTIC_ARN = (f"arn:aws:lambda:{rs.REGION}:{rs.ACCOUNT}:function:"
                f"{lambda_release.SEMANTIC_FUNCTION}")
DISTRIBUTION = "E2EXAMPLE12345"
VIEWER_ARN = f"arn:aws:cloudfront::{rs.ACCOUNT}:function/{snapshot.EDGE_FUNCTION}"
STORE_ARN = f"arn:aws:cloudfront::{rs.ACCOUNT}:key-value-store/11111111-2222-3333-4444-555555555555"
ENGINE_ID = rs.ENGINE_ARN.rsplit("/", 1)[-1]
NEW_GATEWAY_ID = "meridianv2-meridian-aurora-jwt-zyxwv98765"
NEW_ENGINE_ID = "meridianv2_MeridianGovernance-zyxwv98765"
IAM_CODE = "async function handler(event) { /* basic auth at the edge */ return event.request; }"
JWT_CODE = "async function handler(event) { /* token passes through */ return event.request; }"
PLANTED = ("hunter2-plain-secret", "AKIAABCDEFGHIJKLMNOP", "e" + "yJhbGciOiJSUzI1NiJ9.e"
           + "yJzdWIiOiJ4In0.signature_-9")


class Fake:
    """Records calls like ``Recorder`` and lets the world fail one operation."""

    def __init__(self, world: "SnapWorld", service: str):
        self.world, self.service, self.calls = world, service, []

    def _enter(self, operation: str, kwargs: dict, write: bool = False) -> None:
        self.calls.append((operation, deepcopy(kwargs)))
        self.world.log.append(("write" if write else "read", self.service, operation))
        failure = self.world.failures.pop(operation, None)
        if failure is not None:
            raise failure

    def get_waiter(self, name):
        return Waiters().get_waiter(name)


class Control(Fake):
    def _require_live(self, gateway_id):
        if gateway_id != self.world.gateway["gatewayId"]:
            raise client_error("ResourceNotFoundException", "no such gateway")

    def list_gateways(self, **kw):
        self._enter("list_gateways", kw)
        gateway = self.world.gateway
        return {"items": [{"gatewayId": gateway["gatewayId"], "name": gateway["name"]}]
                + list(self.world.other_gateways)}

    def get_gateway(self, **kw):
        self._enter("get_gateway", kw)
        self._require_live(kw["gatewayIdentifier"])
        described = deepcopy(self.world.gateway)
        if self.world.updating.get("gateway", 0) > 0:
            self.world.updating["gateway"] -= 1
            described["status"] = "UPDATING"
        return described

    def update_gateway(self, **kw):
        self._enter("update_gateway", kw, write=True)
        sent = deepcopy({k: v for k, v in kw.items() if k != "gatewayIdentifier"})
        base = (deepcopy(self.world.gateway) if self.world.keeps_omitted else
                {k: v for k, v in self.world.gateway.items() if k in gateway_readonly()})
        self.world.gateway = {**base, **sent, "status": "READY"}
        self.world.updating["gateway"] = self.world.settle_reads
        return {}

    def get_agent_runtime(self, **kw):
        self._enter("get_agent_runtime", kw)
        key = kw["agentRuntimeId"]
        described = deepcopy(self.world.runtimes[key])
        if self.world.updating.get(key, 0) > 0:
            self.world.updating[key] -= 1
            described["status"] = "UPDATING"
        return described

    def update_agent_runtime(self, **kw):
        self._enter("update_agent_runtime", kw, write=True)
        old = self.world.runtimes[kw["agentRuntimeId"]]
        sent = {k: v for k, v in kw.items() if k not in ("agentRuntimeId", "clientToken")}
        base = (deepcopy(old) if self.world.keeps_omitted else
                {k: v for k, v in old.items() if k in runtime_readonly()})
        self.world.runtimes[kw["agentRuntimeId"]] = {**base, **deepcopy(sent), "status": "READY"}
        self.world.updating[kw["agentRuntimeId"]] = self.world.settle_reads
        return {}

    def list_policies(self, **kw):
        self._enter("list_policies", kw)
        return {"policies": [{"name": n, "status": "ACTIVE", "enforcementMode": m,
                              "policyId": f"{n}-x"} for n, m in self.world.policies.items()]}

    def list_gateway_targets(self, **kw):
        self._enter("list_gateway_targets", kw)
        self._require_live(kw["gatewayIdentifier"])
        return {"items": [{"name": "MeridianHolds", "targetId": "t1"}]}

    def get_gateway_target(self, **kw):
        self._enter("get_gateway_target", kw)
        return {"targetConfiguration": {"mcp": {"lambda": {"lambdaArn": HOLDS_ARN}}}}


def gateway_readonly():
    return ("gatewayId", "gatewayArn", "gatewayUrl", "createdAt", "updatedAt",
            "workloadIdentityDetails")


def runtime_readonly():
    return ("agentRuntimeArn", "agentRuntimeName", "agentRuntimeId", "agentRuntimeVersion",
            "createdAt", "lastUpdatedAt", "workloadIdentityDetails")


class AppRunner(Fake):
    def describe_service(self, **kw):
        self._enter("describe_service", kw)
        service = deepcopy(self.world.service)
        if self.world.busy > 0:
            self.world.busy -= 1
            service["Status"] = "OPERATION_IN_PROGRESS"
        return {"Service": service}

    def update_service(self, **kw):
        self._enter("update_service", kw, write=True)
        for key, value in kw.items():
            if key == "ServiceArn":
                continue
            if key == "AutoScalingConfigurationArn":
                self.world.service["AutoScalingConfigurationSummary"][key] = value
            else:
                self.world.service[key] = deepcopy(value)
        self.world.busy = self.world.update_takes
        return {"Service": {}, "OperationId": "op-1"}


class CloudFront(Fake):
    def describe_function(self, **kw):
        self._enter("describe_function", kw)
        entry = self.world.fn[kw.get("Stage", "DEVELOPMENT").lower()]
        return {"FunctionSummary": {"Name": kw["Name"], "Status": "UNPUBLISHED",
                                    "FunctionConfig": deepcopy(entry["config"])},
                "ETag": entry["etag"]}

    def get_function(self, **kw):
        self._enter("get_function", kw)
        entry = self.world.fn[kw.get("Stage", "DEVELOPMENT").lower()]
        return {"FunctionCode": io.BytesIO(entry["code"].encode()), "ETag": entry["etag"]}

    def update_function(self, **kw):
        self._enter("update_function", kw, write=True)
        dev = self.world.fn["development"]
        if kw["IfMatch"] != dev["etag"]:
            raise client_error("PreconditionFailed")
        code = kw["FunctionCode"]
        self.world.fn["development"] = {
            "code": (code.decode() if isinstance(code, bytes) else code),
            "config": deepcopy(kw["FunctionConfig"]), "etag": dev["etag"] + "n"}
        return {"ETag": self.world.fn["development"]["etag"]}

    def publish_function(self, **kw):
        self._enter("publish_function", kw, write=True)
        if kw["IfMatch"] != self.world.fn["development"]["etag"]:
            raise client_error("PreconditionFailed")
        self.world.fn["live"] = deepcopy(self.world.fn["development"])
        return {}

    def get_distribution_config(self, **kw):
        self._enter("get_distribution_config", kw)
        return {"DistributionConfig": deepcopy(self.world.distribution), "ETag": "D1"}

    def get_response_headers_policy(self, **kw):
        self._enter("get_response_headers_policy", kw)
        return {"ResponseHeadersPolicy": {
            "Id": kw["Id"], "LastModifiedTime": STAMPED,
            "ResponseHeadersPolicyConfig": deepcopy(self.world.rhp["config"])},
            "ETag": self.world.rhp["etag"]}

    def update_response_headers_policy(self, **kw):
        self._enter("update_response_headers_policy", kw, write=True)
        if kw.get("IfMatch") != self.world.rhp["etag"]:
            raise client_error("PreconditionFailed")
        self.world.rhp = {"config": deepcopy(kw["ResponseHeadersPolicyConfig"]),
                          "etag": self.world.rhp["etag"] + "n"}
        return {}


class Cfn(Fake):
    def describe_stacks(self, **kw):
        self._enter("describe_stacks", kw)
        return {"Stacks": [deepcopy(self.world.stack)]}

    def get_template(self, **kw):
        self._enter("get_template", kw)
        return {"TemplateBody": deepcopy(self.world.template)}


class Ssm(Fake):
    def get_parameter(self, **kw):
        self._enter("get_parameter", kw)
        entry = self.world.parameters.get(kw["Name"])
        if entry is None:
            raise client_error("ParameterNotFound")
        return {"Parameter": {"Name": kw["Name"], **deepcopy(entry)}}

    def put_parameter(self, **kw):
        self._enter("put_parameter", kw, write=True)
        self.world.parameters[kw["Name"]] = {"Type": kw["Type"], "Value": kw["Value"]}
        return {"Version": 2}


class Iam(Fake):
    """The Runtime roles' inline policies: one policy each, holding the InvokeGateway grant."""

    def list_role_policies(self, **kw):
        self._enter("list_role_policies", kw)
        document = self.world.role_policies.get(kw["RoleName"])
        return {"PolicyNames": [] if document is None else ["runtime-policy"], "IsTruncated": False}

    def get_role_policy(self, **kw):
        self._enter("get_role_policy", kw)
        return {"PolicyDocument": deepcopy(self.world.role_policies[kw["RoleName"]])}

    def list_attached_role_policies(self, **kw):
        self._enter("list_attached_role_policies", kw)
        return {"AttachedPolicies": [], "IsTruncated": False}


class LambdaWaiter:
    """Waits by letting the pending update finish; ``lambda_stuck`` never lets it."""

    def __init__(self, world: "SnapWorld"):
        self.world = world

    def wait(self, **kwargs):
        if self.world.lambda_stuck:
            raise WaiterError("FunctionUpdated", "Max attempts exceeded", {})
        self.world.lambda_updating.clear()


class Lam(Fake):
    def _find(self, name):
        key = HOLDS_ARN if name == HOLDS_ARN or "Holds" in name else name
        return self.world.lambdas[key]

    def get_function_configuration(self, **kw):
        self._enter("get_function_configuration", kw)
        config = deepcopy(self._find(kw["FunctionName"]))
        name = config.get("FunctionName")
        if self.world.lambda_updating.get(name, 0) > 0:
            self.world.lambda_updating[name] -= 1
            config["LastUpdateStatus"] = "InProgress"
        return config

    def update_function_configuration(self, **kw):
        self._enter("update_function_configuration", kw, write=True)
        config = self._find(kw["FunctionName"])
        variables = {k: v for k, v in kw["Environment"]["Variables"].items()
                     if k not in self.world.lambda_drops}
        config["Environment"] = {"Variables": variables}
        config["RevisionId"] = config["RevisionId"] + "n"
        self.world.lambda_updating[config["FunctionName"]] = self.world.settle_reads
        return {}

    def get_waiter(self, name):
        self.world.log.append(("read", "lambda", "wait"))
        return LambdaWaiter(self.world)


class SnapWorld:
    """The deployment, in the IAM configuration until ``release()`` is called."""

    def __init__(self, tmp_path: Path, account: str = rs.ACCOUNT, mode: str = "iam"):
        self.tmp_path = tmp_path
        self.log: list[tuple[str, str, str]] = []
        self.failures: dict = {}
        self.busy, self.update_takes = 0, 1
        self.settle_reads, self.keeps_omitted = 2, False
        self.updating: dict[str, int] = {}
        self.lambda_updating: dict[str, int] = {}
        self.lambda_stuck, self.lambda_drops = False, set()
        self.other_gateways: list[dict] = []
        self.gateway = runtime_gateway(mode)
        self.runtimes = {rs.RUNTIME_IDS[name]: runtime_state(name, mode) for name in rs.RUNTIME_IDS}
        self.service = service_state(mode)
        self.fn = {"development": function_entry(mode), "live": function_entry(mode)}
        self.distribution = distribution_state()
        self.rhp = {"config": rhp_config("default-src 'self'"), "etag": "R1"}
        self.stack = {"StackName": "MeridianWebRoles", "StackStatus": "UPDATE_COMPLETE",
                      "Parameters": [], "Outputs": [{"OutputKey": "InstanceRoleArn",
                      "OutputValue": f"arn:aws:iam::{rs.ACCOUNT}:role/meridian-web-instance"}]}
        self.template = {"Resources": {"Role": {"Type": "AWS::IAM::Role", "Tight": False}}}
        self.parameters = {lambda_release.SSM_SECRET_PARAMETER: {
            "Type": "String", "Value": rs.MASTER_SECRET}}
        self.lambdas = {
            HOLDS_ARN: lambda_config(HOLDS_ARN, {"AURORA_SECRET_ARN": rs.MASTER_SECRET,
                                                 "POOL_SIZE": "4"}),
            lambda_release.SEMANTIC_FUNCTION: lambda_config(
                lambda_release.SEMANTIC_FUNCTION, {"AURORA_SECRET_ARN": rs.MASTER_SECRET})}
        self.policies = {name: "ACTIVE" for name in rs.BASE_POLICIES}
        if mode == "jwt":
            self.policies[rs.BINDING_POLICY] = "ACTIVE"
        self.hosted_release = tmp_path / "hosted-release.json"
        self.hosted_release.write_text(json.dumps({
            "status": "verified", "identityMode": mode, "previousImage": "ecr/meridian:older",
            "image": "ecr/meridian:old", "serviceArn": SERVICE_ARN,
            "site": {"DistributionId": DISTRIBUTION, "SiteUrl": "https://site.example.test",
                     "ApiTokenSecretName": "meridian/web/api-token"}}))
        self.sts = Mock()
        self.sts.get_caller_identity.return_value = {"Account": account}
        self.built: list[str] = []
        self.clients = {
            "bedrock-agentcore-control": Control(self, "bedrock-agentcore-control"),
            "apprunner": AppRunner(self, "apprunner"), "cloudfront": CloudFront(self, "cloudfront"),
            "cloudformation": Cfn(self, "cloudformation"), "ssm": Ssm(self, "ssm"),
            "lambda": Lam(self, "lambda"), "iam": Iam(self, "iam")}
        self.role_policies = {name: invoke_gateway_policy() if mode == "iam" else None
                              for name in rs.RUNTIME_IDS}

    def session(self, region):
        def client(name, **kwargs):
            self.built.append(name)
            return self.sts if name == "sts" else self.clients[name]
        return Mock(client=client)

    def writes(self) -> list[str]:
        return [operation for kind, _, operation in self.log if kind == "write"]

    def violations(self) -> list[str]:
        found = []
        for name, client in self.clients.items():
            found += violations(name, client.calls)
        return found

    def state(self) -> dict:
        """Everything a rollback restores, normalized for equality (holds marker removed)."""
        lambdas = deepcopy(self.lambdas)
        for config in lambdas.values():
            config.pop("RevisionId", None)
            config["Environment"]["Variables"].pop(lambda_release.MARKER, None)
        runtimes = deepcopy(self.runtimes)
        for described in runtimes.values():
            described.pop("agentRuntimeVersion")
        function = {k: v for k, v in self.fn["live"].items() if k != "etag"}
        return deepcopy({"gateway": self.gateway, "runtimes": runtimes,
                         "service": self.service, "fn": function, "rhp": self.rhp["config"],
                         "parameters": self.parameters, "lambdas": lambdas})

    def release(self, manual: bool = True) -> None:
        """Move every hop the way an in-place window would: the Gateway keeps its id and name.

        A real release replaces the Gateway (see ``replace_gateway``); this keeps the one the
        rollback can still restore through the API, which is the unreplaced case.
        """
        self.gateway = {**runtime_gateway("jwt"), "name": self.gateway["name"]}
        for name, runtime_id in rs.RUNTIME_IDS.items():
            self.runtimes[runtime_id] = runtime_state(name, "jwt")
        self.service = service_state("jwt")
        self.fn = {"development": function_entry("jwt"), "live": function_entry("jwt")}
        self.rhp = {"config": rhp_config("default-src 'self' https://auth.example.test"),
                    "etag": "R2"}
        self.parameters[lambda_release.SSM_SECRET_PARAMETER]["Value"] = GATEWAY_SECRET
        for config in self.lambdas.values():
            config["Environment"]["Variables"]["AURORA_SECRET_ARN"] = GATEWAY_SECRET
        if manual:
            self.template = {"Resources": {"Role": {"Type": "AWS::IAM::Role", "Tight": True}}}
            self.policies[rs.BINDING_POLICY] = "ACTIVE"
            self.role_policies = {name: None for name in rs.RUNTIME_IDS}

    def replace_gateway(self, mode: str, gateway_id: str = NEW_GATEWAY_ID) -> None:
        """What a stack deploy does when the mode changes: a new Gateway with a new id, URL, role
        and engine replaces the old one, and the CDK rewires both Runtimes to it."""
        url = gateway_url(gateway_id)
        engine = f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:policy-engine/{NEW_ENGINE_ID}"
        self.gateway = {**runtime_gateway(mode), "gatewayId": gateway_id, "gatewayUrl": url,
                        "gatewayArn": f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:"
                                      f"gateway/{gateway_id}",
                        "roleArn": f"arn:aws:iam::{rs.ACCOUNT}:role/AgentCore-new-gateway-role",
                        "policyEngineConfiguration": {"arn": engine, "mode": "ENFORCE"}}
        for described in self.runtimes.values():
            variables = described["environmentVariables"]
            for other in ("iam", "jwt"):
                variables.pop(settings.gateway_url_variable(other), None)
            variables.update({settings.gateway_url_variable(mode): url,
                              "AGENTCORE_GATEWAY_URL": url, "MERIDIAN_GATEWAY_ID": gateway_id,
                              "MERIDIAN_POLICY_ENGINE_ID": NEW_ENGINE_ID})
        config = self.service["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"]
        config["RuntimeEnvironmentVariables"]["AGENTCORE_GATEWAY_URL"] = url
        if mode == "iam":
            policy = invoke_gateway_policy(gateway_id)
            self.role_policies = {name: deepcopy(policy) for name in rs.RUNTIME_IDS}

    def deploy_iam_render(self) -> None:
        """What `agentcore deploy -y` of the IAM render does to the parts the API cannot reach."""
        self.template = {"Resources": {"Role": {"Type": "AWS::IAM::Role", "Tight": False}}}
        self.policies.pop(rs.BINDING_POLICY, None)
        self.role_policies = {name: invoke_gateway_policy() for name in rs.RUNTIME_IDS}


def gateway_url(gateway_id: str) -> str:
    return f"https://{gateway_id}.gateway.bedrock-agentcore.{rs.REGION}.amazonaws.com/mcp"


def invoke_gateway_policy(gateway_id: str = rs.GATEWAY_ID) -> dict:
    return {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Action": "bedrock-agentcore:InvokeGateway",
        "Resource": f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:gateway/{gateway_id}"}]}


def runtime_gateway(mode: str) -> dict:
    return gateway_current(mode)


def runtime_state(name: str, mode: str) -> dict:
    described = rs.runtime(name, mode, iam_env=True)
    described.update({
        "agentRuntimeArn": f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:runtime/"
                           + rs.RUNTIME_IDS[name],
        "agentRuntimeName": name, "agentRuntimeVersion": "3" if mode == "jwt" else "2",
        "createdAt": "2026-10-02T09:00:00Z", "lastUpdatedAt": "2026-10-07T09:00:00Z",
        "agentRuntimeArtifact": {"containerConfiguration": {
            "containerUri": f"ecr/{name.lower()}:{'new' if mode == 'jwt' else 'old'}"}},
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "description": f"{name} runtime"})
    described["environmentVariables"] = {**described["environmentVariables"],
                                         "AGENTCORE_GATEWAY_URL": rs.GATEWAY_URL}
    return described


def service_state(mode: str) -> dict:
    variables = {"AWS_REGION": rs.REGION, "ENVIRONMENT": "production",
                 "AGENTCORE_GATEWAY_URL": rs.GATEWAY_URL}
    secrets = {"MERIDIAN_API_TOKEN": API_SECRET}
    image = "ecr/meridian:old"
    if mode == "jwt":
        variables = {**variables, "MERIDIAN_AGENTCORE_AUTH": "jwt", **rs.COGNITO_ENV,
                     "AURORA_SECRET_ARN": rs.BACKEND_SECRET,
                     "AURORA_BACKEND_SECRET_ARN": rs.BACKEND_SECRET}
        secrets, image = {}, "ecr/meridian:new"
    return {
        "ServiceName": "meridian-web", "ServiceId": "abc123", "ServiceArn": SERVICE_ARN,
        "ServiceUrl": "abc.awsapprunner.example.test", "Status": "RUNNING",
        "CreatedAt": "2026-09-01T00:00:00Z", "UpdatedAt": "2026-10-07T00:00:00Z",
        "SourceConfiguration": {
            "AuthenticationConfiguration": {
                "AccessRoleArn": f"arn:aws:iam::{rs.ACCOUNT}:role/meridian-web-access"},
            "AutoDeploymentsEnabled": False,
            "ImageRepository": {"ImageIdentifier": image, "ImageRepositoryType": "ECR",
                                "ImageConfiguration": {
                                    "Port": "8000", "RuntimeEnvironmentVariables": variables,
                                    "RuntimeEnvironmentSecrets": secrets}}},
        "InstanceConfiguration": {
            "Cpu": "1024", "Memory": "2048",
            "InstanceRoleArn": f"arn:aws:iam::{rs.ACCOUNT}:role/meridian-web-instance"},
        "HealthCheckConfiguration": {"Protocol": "HTTP", "Path": "/health", "Interval": 10,
                                     "Timeout": 5, "HealthyThreshold": 1, "UnhealthyThreshold": 5},
        "NetworkConfiguration": {"EgressConfiguration": {"EgressType": "DEFAULT"},
                                 "IngressConfiguration": {"IsPubliclyAccessible": True},
                                 "IpAddressType": "IPV4"},
        "ObservabilityConfiguration": {"ObservabilityEnabled": False},
        "AutoScalingConfigurationSummary": {
            "AutoScalingConfigurationArn": f"arn:aws:apprunner:{rs.REGION}:{rs.ACCOUNT}:"
                                           "autoscalingconfiguration/default/1/abc",
            "AutoScalingConfigurationName": "default", "AutoScalingConfigurationRevision": 1}}


def function_entry(mode: str) -> dict:
    stores = {"Quantity": 1, "Items": [{"KeyValueStoreARN": STORE_ARN}]}
    return {"code": JWT_CODE if mode == "jwt" else IAM_CODE,
            "etag": "F1" if mode == "iam" else "F2",
            "config": {"Comment": f"Meridian edge {mode}", "Runtime": "cloudfront-js-2.0",
                       "KeyValueStoreAssociations": stores}}


def distribution_state() -> dict:
    association = {"Quantity": 1, "Items": [{"EventType": "viewer-request",
                                             "FunctionARN": VIEWER_ARN}]}
    return {"DefaultCacheBehavior": {"TargetOriginId": "site", "ResponseHeadersPolicyId": "rhp-1",
                                     "FunctionAssociations": association},
            "CacheBehaviors": {"Quantity": 1, "Items": [{
                "PathPattern": "/api/*", "TargetOriginId": "api",
                "ResponseHeadersPolicyId": "rhp-1", "FunctionAssociations": association}]},
            "Origins": {"Quantity": 1, "Items": [{"Id": "api", "DomainName": "x.example.test",
                        "CustomHeaders": {"Quantity": 1, "Items": [
                            {"HeaderName": "X-Origin-Token", "HeaderValue": PLANTED[0]}]}}]}}


def rhp_config(csp: str) -> dict:
    return {"Name": "MeridianWebResponseHeaders", "SecurityHeadersConfig": {
        "ContentSecurityPolicy": {"Override": True, "ContentSecurityPolicy": csp}}}


def lambda_config(arn: str, variables: dict) -> dict:
    return {"FunctionName": arn.rsplit(":", 1)[-1], "FunctionArn": arn, "RevisionId": "r1",
            "Role": f"arn:aws:iam::{rs.ACCOUNT}:role/{arn.rsplit(':', 1)[-1]}-role",
            "Environment": {"Variables": dict(variables)}}


def clients_of(world: SnapWorld) -> snapshot.Clients:
    return snapshot.Clients(
        control=world.clients["bedrock-agentcore-control"], apprunner=world.clients["apprunner"],
        cloudfront=world.clients["cloudfront"], cfn=world.clients["cloudformation"],
        ssm=world.clients["ssm"], lam=world.clients["lambda"], iam=world.clients["iam"])


ENV = {
    "AURORA_CLUSTER_ARN": CLUSTER, **rs.COGNITO_ENV, "AURORA_SECRET_ARN": rs.MASTER_SECRET,
    "AURORA_BACKEND_SECRET_ARN": rs.BACKEND_SECRET,
}


def where_of(gateway_id: str = rs.GATEWAY_ID) -> snapshot.Where:
    return snapshot.Where(account=rs.ACCOUNT, region=rs.REGION, gateway_id=gateway_id,
                          runtime_ids=dict(rs.RUNTIME_IDS), service_arn=SERVICE_ARN, env=ENV)


def taken(world: SnapWorld) -> dict:
    """A snapshot of the world as it is now."""
    return snapshot.take(clients_of(world), where_of(), now=STAMPED, commit="abc1234",
                         hosted_release_path=world.hosted_release)
