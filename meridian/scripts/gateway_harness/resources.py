"""Create, switch and delete the throwaway Gateway and everything it needs.

Every create call records what it made in a ledger before the next call, so a failure half way
still tears down exactly what exists. All AWS clients are passed in; this module never builds one.

Ownership: each run has an id. Every resource that takes tags is created with the tag
``meridian-harness-run=<run id>``, and teardown reads that tag back and refuses to delete anything
whose tag is missing or differs. Teardown deletes exact names and ids from the ledger; it never
lists resources and never matches by prefix.
"""

from __future__ import annotations

import functools
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple

import botocore.session
from botocore import xform_name
from botocore.exceptions import BotoCoreError, ClientError
from botocore.validate import ParamValidator

from scripts.gateway_harness import sources
from scripts.gateway_harness.guards import HarnessRefusal, check_name
from scripts.gateway_harness.verdicts import scrub

RUN_TAG = "meridian-harness-run"
TARGET_NAME = "EchoTarget"
TOOL_NAME = "echo"
ACTION = f"{TARGET_NAME}___{TOOL_NAME}"
BINDING_POLICY = "meridian_traveler_binding"
TEMPLATE_ACTIONS = (
    'AgentCore::Action::"MeridianHolds___create_courtesy_hold", '
    'AgentCore::Action::"MeridianHolds___confirm_booking"'
)
INTERCEPTOR_MODES = ("pin", "bad_type", "drop_required", "refuse")
LOG_ACTIONS = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
TEARDOWN_KINDS = ("target", "gateway", "policy", "policy-engine", "lambda", "iam-role")
GONE = {"ResourceNotFoundException", "NoSuchEntity", "ResourceNotFound"}
CONTROL_SERVICE = "bedrock-agentcore-control"
GATEWAY_DESCRIPTION = "Throwaway Meridian identity harness"
PREFLIGHT_PLACEHOLDER_GATEWAY = "preflight-gateway-0000000000"
PREFLIGHT_PLACEHOLDER_ENGINE = "preflight-engine-0000000000"
# The installed botocore tool-schema model has only type, properties, required, items and
# description, so the schema declares nothing else.
ECHO_SCHEMA = [{
    "name": TOOL_NAME,
    "description": "Return the arguments exactly as the Lambda received them.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "travelerId": {"type": "string", "description": "Pinned by the interceptor."},
            "note": {"type": "string", "description": "Free text."},
        },
        "required": ["travelerId"],
    },
}]


class HarnessFailure(RuntimeError):
    """A create or wait step failed; the message names the step and the service's reason."""


class TeardownIncomplete(RuntimeError):
    """Some resources could not be deleted; the message lists each with its identifier."""


DELETE_ERRORS = (ClientError, BotoCoreError, HarnessFailure, HarnessRefusal, ValueError)


@dataclass(frozen=True)
class HarnessConfig:
    """What the harness needs to know about the account and the identity provider."""

    name: str
    account: str
    region: str
    discovery_url: str
    allowed_client_id: str
    binding_template: str
    run_id: str = field(default_factory=lambda: secrets.token_hex(8))

    @property
    def engine_name(self) -> str:
        return self.name.replace("-", "_")


@dataclass
class Clients:
    """The four boto3 clients the harness uses."""

    iam: Any
    lambda_: Any
    control: Any
    logs: Any


@dataclass
class Ledger:
    """Created resources in creation order, saved after every addition.

    ``save`` receives ``{"run_id": ..., "entries": [[kind, identifier], ...]}``. ``run_id`` is the
    tag value the resources carry; it is persisted with the entries because teardown in a later
    process refuses without a matching tag.
    """

    entries: List[Tuple[str, str]] = field(default_factory=list)
    save: Callable[[Dict[str, Any]], None] = lambda payload: None
    run_id: str = ""

    def payload(self) -> Dict[str, Any]:
        """The run id and the entries in the shape ``save`` receives."""
        return {"run_id": self.run_id, "entries": [[kind, ident] for kind, ident in self.entries]}

    def persist(self) -> None:
        """Save the current state."""
        self.save(self.payload())

    def add(self, kind: str, identifier: str) -> None:
        self.entries.append((kind, identifier))
        self.persist()


@dataclass
class Live:
    """Identifiers of the running throwaway Gateway."""

    gateway_id: str = ""
    gateway_arn: str = ""
    gateway_url: str = ""
    engine_id: str = ""
    engine_arn: str = ""
    interceptor_name: str = ""
    echo_name: str = ""
    binding_policy_accepted: bool = False
    binding_policy_reason: str = ""


def harness_binding_statement(template_statement: str, config: HarnessConfig,
                              gateway_id: str) -> str:
    """The production deny rule with the throwaway gateway and its echo tool substituted.

    Raises:
        HarnessRefusal: The template rule no longer names the two production actions, so the
            substitution would silently test a different rule.
    """
    if TEMPLATE_ACTIONS not in template_statement:
        raise HarnessRefusal(
            "the template's meridian_traveler_binding rule no longer lists the two production "
            "actions; update scripts/gateway_harness/resources.py to match before running."
        )
    statement = template_statement.replace(TEMPLATE_ACTIONS, f'AgentCore::Action::"{ACTION}"')
    values = {
        "AWS_REGION": config.region, "AWS_ACCOUNT_ID": config.account, "GATEWAY_ID": gateway_id,
    }
    for key, value in values.items():
        statement = statement.replace("{{" + key + "}}", value)
    return statement


def wait_for(read: Callable[[], Tuple[str, str]], ready: str, *, what: str, timeout: float = 300,
             interval: float = 5, sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.monotonic) -> None:
    """Poll ``read() -> (status, reason)`` until ``ready``.

    Raises:
        HarnessFailure: A failed status appears or the timeout passes.
    """
    deadline = clock() + timeout
    while True:
        status, reason = read()
        if status == ready:
            return
        if "FAIL" in status or "UNSUCCESSFUL" in status:
            raise HarnessFailure(f"{what} ended in {status}: {scrub(reason)}")
        if clock() >= deadline:
            raise HarnessFailure(f"{what} still {status} after {timeout:.0f} s")
        sleep(interval)


def tag_list(run_id: str) -> List[Dict[str, str]]:
    """The run tag in IAM's list-of-pairs shape."""
    return [{"Key": RUN_TAG, "Value": run_id}]


@functools.lru_cache(maxsize=None)
def _installed_model(service: str) -> Any:
    """The service model botocore ships, read from disk; no client, no network."""
    return botocore.session.get_session().get_service_model(service)


def _status(response: Dict[str, Any]) -> Tuple[str, str]:
    return response["status"], "; ".join(str(r) for r in response.get("statusReasons") or [])


def _trust(service: str, account: str, source_arn: str = "") -> str:
    condition: Dict[str, Any] = {"StringEquals": {"aws:SourceAccount": account}}
    if source_arn:
        condition["ArnLike"] = {"aws:SourceArn": source_arn}
    return json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole",
            "Condition": condition,
        }],
    })


class ThrowawayGateway:
    """The harness's Gateway, its interceptor, echo target, policy engine and roles."""

    def __init__(self, config: HarnessConfig, clients: Clients, ledger: Ledger, *,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self.aws = clients
        self.ledger = ledger
        self.live = Live()
        self._sleep = sleep
        self._clock = clock
        self._role_arn = ""
        self._owned: Dict[Tuple[str, str], bool] = {}
        self._log_groups: List[str] = []
        check_name(config.name)
        self._adopt_run_id()

    def _adopt_run_id(self) -> None:
        run_id = self.config.run_id
        if not run_id.strip():
            raise HarnessRefusal("the harness needs a run id to tag what it creates.")
        if self.ledger.run_id and self.ledger.run_id != run_id:
            raise HarnessRefusal(
                "refusing: the ledger belongs to a different run than this configuration.")
        self.ledger.run_id = run_id

    # ---------------------------------------------------------------- planning

    def plan(self) -> List[str]:
        """What a live run would create, in order. Makes no AWS call."""
        name = self.config.name
        return [
            f"IAM role {name}-lambda (Lambda execution, logs of this throwaway only)",
            f"IAM role {name}-gateway (invoke the two functions, evaluate the policy engine)",
            f"Lambda {name}-echo (target: returns its event)",
            f"Lambda {name}-interceptor (production interceptor plus harness switches)",
            f"Gateway {name} (CUSTOM_JWT, Cognito client allowed, REQUEST interceptor, "
            "passRequestHeaders)",
            f"Gateway target {TARGET_NAME} (tool {TOOL_NAME}, schema with type, properties and "
            "required only)",
            f"Policy engine {self.config.engine_name} (ENFORCE)",
            f"Policy permit_all and {BINDING_POLICY} (the template rule, FAIL_ON_ANY_FINDINGS)",
            "Probes with the real jordan and decoy tokens, then delete all of the above",
        ]

    # --------------------------------------------------------------- requests

    def _name(self, suffix: str) -> str:
        return f"{self.config.name}-{suffix}"

    def _arn(self, resource: str, identifier: str) -> str:
        return (f"arn:aws:bedrock-agentcore:{self.config.region}:{self.config.account}:"
                f"{resource}/{identifier}")

    def _function_arn(self, name: str) -> str:
        return f"arn:aws:lambda:{self.config.region}:{self.config.account}:function:{name}"

    def _role_request(self, suffix: str) -> Dict[str, Any]:
        if suffix == "lambda":
            trust = _trust("lambda.amazonaws.com", self.config.account)
            what = "Lambda execution"
        else:
            trust = _trust("bedrock-agentcore.amazonaws.com", self.config.account,
                           self._arn("gateway", "*"))
            what = "service role"
        return {
            "RoleName": self._name(suffix), "AssumeRolePolicyDocument": trust,
            "Description": f"Throwaway Meridian Gateway harness: {what}",
            "Tags": tag_list(self.config.run_id),
        }

    def _role_policy_request(self, suffix: str) -> Dict[str, Any]:
        document = self._lambda_role_policy() if suffix == "lambda" else self._gateway_role_policy()
        return {"RoleName": self._name(suffix), "PolicyName": f"throwaway-{suffix}",
                "PolicyDocument": json.dumps(document)}

    def _lambda_role_policy(self) -> Dict[str, Any]:
        group = (f"arn:aws:logs:{self.config.region}:{self.config.account}:log-group:"
                 f"/aws/lambda/{self.config.name}-*")
        return {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": LOG_ACTIONS, "Resource": [group, group + ":*"]}]}

    def _gateway_role_policy(self) -> Dict[str, Any]:
        base = f"arn:aws:bedrock-agentcore:{self.config.region}:{self.config.account}"
        functions = [self._function_arn(self._name(suffix)) for suffix in ("echo", "interceptor")]
        return {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": functions},
            {"Effect": "Allow", "Action": "bedrock-agentcore:GetPolicyEngine",
             "Resource": f"{base}:policy-engine/*"},
            {"Effect": "Allow", "Action": [
                "bedrock-agentcore:AuthorizeAction", "bedrock-agentcore:PartiallyAuthorizeActions"],
             "Resource": [f"{base}:policy-engine/*", f"{base}:gateway/*"]},
        ]}

    def _function_specs(self) -> List[Tuple[str, str, bytes, Dict[str, str]]]:
        return [
            ("echo", sources.ECHO_HANDLER, sources.echo_package(), {}),
            ("interceptor", sources.INTERCEPTOR_HANDLER, sources.interceptor_package(),
             self._interceptor_variables("pin")),
        ]

    def _function_request(self, spec: Tuple[str, str, bytes, Dict[str, str]],
                          role_arn: str) -> Dict[str, Any]:
        suffix, handler, package, variables = spec
        request: Dict[str, Any] = {
            "FunctionName": self._name(suffix), "Runtime": "python3.13", "Role": role_arn,
            "Handler": handler, "Code": {"ZipFile": package}, "Timeout": 10, "MemorySize": 128,
            "Description": "Throwaway Meridian Gateway harness",
            "Tags": {RUN_TAG: self.config.run_id},
        }
        if variables:
            request["Environment"] = {"Variables": variables}
        return request

    @staticmethod
    def _interceptor_variables(mode: str) -> Dict[str, str]:
        return {"PINNED_TOOLS": ACTION, "HARNESS_MODE": mode, "HARNESS_RECORD": "1"}

    def _gateway_arguments(self, role_arn: str) -> Dict[str, Any]:
        return {
            "name": self.config.name, "description": GATEWAY_DESCRIPTION, "roleArn": role_arn,
            "protocolType": "MCP", "authorizerType": "CUSTOM_JWT",
            "authorizerConfiguration": {"customJWTAuthorizer": {
                "discoveryUrl": self.config.discovery_url,
                "allowedClients": [self.config.allowed_client_id]}},
            "interceptorConfigurations": [{
                "interceptor": {"lambda": {
                    "arn": self._function_arn(self._name("interceptor"))}},
                "interceptionPoints": ["REQUEST"],
                "inputConfiguration": {"passRequestHeaders": True}}],
        }

    def _gateway_request(self, role_arn: str) -> Dict[str, Any]:
        return {"tags": {RUN_TAG: self.config.run_id}, **self._gateway_arguments(role_arn)}

    def _update_request(self, gateway_id: str, role_arn: str, engine_arn: str) -> Dict[str, Any]:
        return {"gatewayIdentifier": gateway_id, **self._gateway_arguments(role_arn),
                "policyEngineConfiguration": {"arn": engine_arn, "mode": "ENFORCE"}}

    def _target_request(self, gateway_id: str) -> Dict[str, Any]:
        return {
            "gatewayIdentifier": gateway_id, "name": TARGET_NAME,
            "targetConfiguration": {"mcp": {"lambda": {
                "lambdaArn": self._function_arn(self._name("echo")),
                "toolSchema": {"inlinePayload": ECHO_SCHEMA}}}},
            "credentialProviderConfigurations": [{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        }

    def _engine_request(self) -> Dict[str, Any]:
        return {"name": self.config.engine_name, "description": GATEWAY_DESCRIPTION,
                "tags": {RUN_TAG: self.config.run_id}}

    @staticmethod
    def _policy_request(engine_id: str, name: str, statement: str,
                        validation: str) -> Dict[str, Any]:
        return {"policyEngineId": engine_id, "name": name, "validationMode": validation,
                "definition": {"cedar": {"statement": statement}}}

    @staticmethod
    def _permit_all(gateway_arn: str) -> str:
        return f'permit(principal, action, resource == AgentCore::Gateway::"{gateway_arn}");'

    # -------------------------------------------------------------- preflight

    @staticmethod
    def _model(service: str, client: Any) -> Any:
        model = getattr(getattr(client, "meta", None), "service_model", None)
        return model if model is not None else _installed_model(service)

    def _preflight_requests(self) -> List[Tuple[str, Any, str, Dict[str, Any]]]:
        iam, lam, control = self.aws.iam, self.aws.lambda_, self.aws.control
        gateway_id, engine_id = PREFLIGHT_PLACEHOLDER_GATEWAY, PREFLIGHT_PLACEHOLDER_ENGINE
        role_arn = f"arn:aws:iam::{self.config.account}:role/{self._name('lambda')}"
        statement = harness_binding_statement(
            self.config.binding_template, self.config, gateway_id)
        requests = [("iam", iam, "CreateRole", self._role_request(suffix))
                    for suffix in ("lambda", "gateway")]
        requests += [("iam", iam, "PutRolePolicy", self._role_policy_request(suffix))
                     for suffix in ("lambda", "gateway")]
        requests += [("lambda", lam, "CreateFunction", self._function_request(spec, role_arn))
                     for spec in self._function_specs()]
        gateway_arn = self._arn("gateway", gateway_id)
        policies = [
            ("CreatePolicy", self._policy_request(
                engine_id, "permit_all", self._permit_all(gateway_arn), "IGNORE_ALL_FINDINGS")),
            ("CreatePolicy", self._policy_request(
                engine_id, BINDING_POLICY, statement, "FAIL_ON_ANY_FINDINGS")),
        ]
        controls = [
            ("CreateGateway", self._gateway_request(role_arn)),
            ("CreateGatewayTarget", self._target_request(gateway_id)),
            ("CreatePolicyEngine", self._engine_request()),
            *policies,
            ("UpdateGateway", self._update_request(
                gateway_id, role_arn, self._arn("policy-engine", engine_id))),
        ]
        requests += [(CONTROL_SERVICE, control, op, request) for op, request in controls]
        return requests

    def _preflight(self) -> None:
        """Validate every create request against the botocore models before any AWS call.

        Raises:
            HarnessRefusal: A request does not match its operation's input model, or the template
                rule no longer fits; nothing has been created.
        """
        models: Dict[str, Any] = {}
        for service, client, operation, request in self._preflight_requests():
            model = models.setdefault(service, self._model(service, client))
            shape = model.operation_model(operation).input_shape
            report = ParamValidator().validate(request, shape)
            if report.has_errors():
                raise HarnessRefusal(
                    f"preflight rejected {service}.{xform_name(operation)}; nothing was created: "
                    + scrub(report.generate_report()))

    # ----------------------------------------------------------------- create

    def create(self) -> Live:
        """Create everything. On failure the ledger still lists what exists.

        Every request is validated against the service models first, and the ledger (with the run
        id) is saved before the first call. A name-keyed resource (role, function) is added to the
        ledger before its create call, so a crash cannot leave one untracked.

        Raises:
            HarnessRefusal: A request failed the preflight; nothing was created.
            HarnessFailure: A step failed or timed out.
        """
        self._preflight()
        self.ledger.persist()
        roles = {suffix: self._create_role(suffix) for suffix in ("lambda", "gateway")}
        self._sleep(10)
        self._create_functions(roles["lambda"])
        self._create_gateway(roles["gateway"])
        self._create_target()
        self._create_policies()
        self._update_gateway()
        return self.live

    def _create_role(self, suffix: str) -> str:
        request = self._role_request(suffix)
        self.ledger.add("iam-role", request["RoleName"])
        arn = self.aws.iam.create_role(**request)["Role"]["Arn"]
        self.aws.iam.put_role_policy(**self._role_policy_request(suffix))
        return arn

    def _create_function(self, spec: Tuple[str, str, bytes, Dict[str, str]],
                         role_arn: str) -> str:
        request = self._function_request(spec, role_arn)
        name = request["FunctionName"]
        self.ledger.add("lambda", name)
        for attempt in range(6):
            try:
                self.aws.lambda_.create_function(**request)
                break
            except ClientError as exc:
                text = exc.response["Error"]["Message"]
                if "cannot be assumed" not in text or attempt == 5:
                    raise
                self._sleep(5)
        self.aws.lambda_.get_waiter("function_active_v2").wait(FunctionName=name)
        return name

    def _create_functions(self, role_arn: str) -> None:
        names = [self._create_function(spec, role_arn) for spec in self._function_specs()]
        self.live.echo_name, self.live.interceptor_name = names

    def _create_gateway(self, role_arn: str) -> None:
        self._role_arn = role_arn
        control = self.aws.control
        created = control.create_gateway(**self._gateway_request(role_arn))
        self.live.gateway_id = created["gatewayId"]
        self.live.gateway_arn = created["gatewayArn"]
        self.live.gateway_url = created["gatewayUrl"]
        self.ledger.add("gateway", created["gatewayId"])
        wait_for(lambda: _status(control.get_gateway(gatewayIdentifier=created["gatewayId"])),
                 "READY", what="gateway", sleep=self._sleep, clock=self._clock)

    def _create_target(self) -> None:
        control, gateway_id = self.aws.control, self.live.gateway_id
        created = control.create_gateway_target(**self._target_request(gateway_id))
        self.ledger.add("target", f"{gateway_id}/{created['targetId']}")
        wait_for(lambda: _status(control.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=created["targetId"])),
            "READY", what="gateway target", sleep=self._sleep, clock=self._clock)

    def _create_policy(self, name: str, statement: str, validation: str) -> str:
        control, engine_id = self.aws.control, self.live.engine_id
        created = control.create_policy(
            **self._policy_request(engine_id, name, statement, validation))
        self.ledger.add("policy", f"{engine_id}/{created['policyId']}")
        wait_for(lambda: _status(control.get_policy(
            policyEngineId=engine_id, policyId=created["policyId"])),
            "ACTIVE", what=f"policy {name}", sleep=self._sleep, clock=self._clock)
        return created["policyId"]

    def _create_policies(self) -> None:
        control = self.aws.control
        engine = control.create_policy_engine(**self._engine_request())
        self.live.engine_id, self.live.engine_arn = engine["policyEngineId"], engine[
            "policyEngineArn"]
        self.ledger.add("policy-engine", engine["policyEngineId"])
        wait_for(lambda: _status(control.get_policy_engine(
            policyEngineId=engine["policyEngineId"])),
            "ACTIVE", what="policy engine", sleep=self._sleep, clock=self._clock)
        self._create_policy(
            "permit_all", self._permit_all(self.live.gateway_arn), "IGNORE_ALL_FINDINGS")
        statement = harness_binding_statement(
            self.config.binding_template, self.config, self.live.gateway_id)
        try:
            self._create_policy(BINDING_POLICY, statement, "FAIL_ON_ANY_FINDINGS")
            self.live.binding_policy_accepted = True
        except (HarnessFailure, ClientError) as exc:
            self.live.binding_policy_reason = scrub(str(exc))[:400]

    def _update_gateway(self) -> None:
        control = self.aws.control
        control.update_gateway(**self._update_request(
            self.live.gateway_id, self._role_arn, self.live.engine_arn))
        wait_for(lambda: _status(control.get_gateway(gatewayIdentifier=self.live.gateway_id)),
                 "READY", what="gateway update", sleep=self._sleep, clock=self._clock)

    # ----------------------------------------------------------------- switch

    def set_interceptor_mode(self, mode: str) -> None:
        """Switch the interceptor wrapper between ``pin``, ``bad_type``, ``drop_required`` and
        ``refuse``.

        Raises:
            HarnessRefusal: ``mode`` is none of those; nothing is called.
        """
        if mode not in INTERCEPTOR_MODES:
            raise HarnessRefusal(
                f"unknown interceptor mode '{mode}'; use one of {', '.join(INTERCEPTOR_MODES)}")
        name = self.live.interceptor_name
        self.aws.lambda_.update_function_configuration(
            FunctionName=name, Environment={"Variables": self._interceptor_variables(mode)})
        self.aws.lambda_.get_waiter("function_updated_v2").wait(FunctionName=name)

    # --------------------------------------------------------------- teardown

    def teardown(self, *, from_file: bool = False) -> None:
        """Delete what this run created, newest first within each kind.

        Every resource is deleted by the exact name or id in the ledger, and only after its run tag
        is read back and matches. A resource that is already gone is skipped. Every failure,
        whatever its type, is collected and the remaining entries are still deleted.

        Args:
            from_file: The ledger was loaded from a file. An empty one is refused, because an
                empty ledger there means the file was lost or truncated, not that nothing exists.

        Raises:
            HarnessRefusal: ``from_file`` is true and the ledger has no entries; nothing is called.
            TeardownIncomplete: Something could not be deleted or was not tagged for this run.
        """
        if from_file and not self.ledger.entries:
            raise HarnessRefusal(
                "refusing: the ledger has no entries, so the file was lost or truncated; "
                "nothing was deleted. Look for the tag "
                f"{RUN_TAG}={self.config.run_id} by hand.")
        leftovers: List[str] = []
        for kind, identifier in self._teardown_order():
            try:
                self._delete_if_ours(kind, identifier)
            except DELETE_ERRORS as exc:
                reason = self._leftover_reason(exc)
                if reason:
                    leftovers.append(f"{kind} {identifier}: {reason}")
        self._delete_log_groups(leftovers)
        if leftovers:
            raise TeardownIncomplete(
                "not deleted; remove by hand or re-run with --teardown <ledger>: "
                + "; ".join(leftovers))

    @staticmethod
    def _leftover_reason(exc: Exception) -> str:
        """Why a deletion failed, or an empty string when the resource was already gone."""
        if isinstance(exc, ClientError):
            code = exc.response["Error"]["Code"]
            return "" if code in GONE else code
        if isinstance(exc, (HarnessFailure, HarnessRefusal)):
            return scrub(str(exc))
        return f"{type(exc).__name__}: {scrub(str(exc))}"

    def _teardown_order(self) -> List[Tuple[str, str]]:
        """Targets, then the gateway, then its policies and engine, then functions and roles."""
        rank = {kind: index for index, kind in enumerate(TEARDOWN_KINDS)}
        newest_first = list(reversed(self.ledger.entries))
        return sorted(newest_first, key=lambda entry: rank.get(entry[0], len(rank)))

    def _delete_if_ours(self, kind: str, identifier: str) -> None:
        if kind not in TEARDOWN_KINDS:
            raise HarnessRefusal(f"unknown ledger kind '{kind}'; not deleted")
        if self._is_ours(kind, identifier):
            self._delete(kind, identifier)
        elif kind == "lambda":
            self._log_groups.append(identifier)

    def _is_ours(self, kind: str, identifier: str) -> bool:
        """True when the resource exists and carries this run's tag; False when already gone.

        Targets are checked through their gateway and policies through their engine, because only
        the gateway and the engine take tags. The tag of a gateway or engine is read again for
        its own deletion, not taken from the check made for its children.

        Raises:
            HarnessRefusal: The name is not one this run creates, or the tag is missing or differs.
        """
        anchor = {"target": "gateway", "policy": "policy-engine"}.get(kind, kind)
        if anchor != kind and identifier.count("/") != 1:
            raise ValueError(f"malformed ledger identifier '{identifier}': expected <parent>/<id>")
        anchor_id = identifier.split("/")[0] if anchor != kind else identifier
        key = (anchor, anchor_id)
        if anchor == kind or key not in self._owned:
            self._owned[key] = self._tag_matches(anchor, anchor_id)
        return self._owned[key]

    def _tag_matches(self, kind: str, identifier: str) -> bool:
        if kind in ("iam-role", "lambda") and identifier not in self._own_names(kind):
            raise HarnessRefusal(
                f"'{identifier}' does not belong to {self.config.name}; not deleted")
        try:
            tags = self._read_tags(kind, identifier)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in GONE:
                return False
            raise
        if tags.get(RUN_TAG) != self.config.run_id:
            raise HarnessRefusal(
                f"not tagged for this run (tag {RUN_TAG} is missing or differs); not deleted")
        return True

    def _own_names(self, kind: str) -> Tuple[str, ...]:
        suffixes = ("lambda", "gateway") if kind == "iam-role" else ("echo", "interceptor")
        return tuple(f"{self.config.name}-{suffix}" for suffix in suffixes)

    def _read_tags(self, kind: str, identifier: str) -> Dict[str, str]:
        aws = self.aws
        if kind == "iam-role":
            pairs = aws.iam.list_role_tags(RoleName=identifier)["Tags"]
            return {pair["Key"]: pair["Value"] for pair in pairs}
        if kind == "lambda":
            return aws.lambda_.list_tags(Resource=self._function_arn(identifier))["Tags"]
        resource = "gateway" if kind == "gateway" else "policy-engine"
        return aws.control.list_tags_for_resource(
            resourceArn=self._arn(resource, identifier))["tags"]

    def _delete(self, kind: str, identifier: str) -> None:
        deleters = {
            "policy": self._delete_policy, "target": self._delete_target,
            "gateway": self._delete_gateway, "policy-engine": self._delete_engine,
            "lambda": self._delete_function, "iam-role": self._delete_role,
        }
        deleters[kind](identifier)

    def _delete_policy(self, identifier: str) -> None:
        control = self.aws.control
        engine_id, policy_id = identifier.split("/")
        control.delete_policy(policyEngineId=engine_id, policyId=policy_id)
        self._wait_gone(lambda: control.get_policy(policyEngineId=engine_id, policyId=policy_id))

    def _delete_target(self, identifier: str) -> None:
        control = self.aws.control
        gateway_id, target_id = identifier.split("/")
        control.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        self._wait_gone(lambda: control.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id))

    def _delete_gateway(self, identifier: str) -> None:
        control = self.aws.control
        control.delete_gateway(gatewayIdentifier=identifier)
        self._wait_gone(lambda: control.get_gateway(gatewayIdentifier=identifier))

    def _delete_engine(self, identifier: str) -> None:
        control = self.aws.control
        control.delete_policy_engine(policyEngineId=identifier)
        self._wait_gone(lambda: control.get_policy_engine(policyEngineId=identifier))

    def _delete_function(self, identifier: str) -> None:
        try:
            self.aws.lambda_.delete_function(FunctionName=identifier)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in GONE:
                raise
        self._log_groups.append(identifier)

    def _wait_gone(self, read: Callable[[], Dict[str, Any]], timeout: float = 180) -> None:
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            try:
                state = read()
            except ClientError as exc:
                if exc.response["Error"]["Code"] in GONE:
                    return
                raise
            status, reason = _status(state)
            if "FAIL" in status:
                raise HarnessFailure(f"{status}: {scrub(reason)}")
            self._sleep(5)
        raise HarnessFailure("still deleting after the timeout")

    def _delete_role(self, role: str) -> None:
        iam = self.aws.iam
        for attached in iam.list_attached_role_policies(RoleName=role)["AttachedPolicies"]:
            iam.detach_role_policy(RoleName=role, PolicyArn=attached["PolicyArn"])
        for inline in iam.list_role_policies(RoleName=role)["PolicyNames"]:
            iam.delete_role_policy(RoleName=role, PolicyName=inline)
        iam.delete_role(RoleName=role)

    def _delete_log_groups(self, leftovers: List[str]) -> None:
        """Delete the log groups of the functions that are this run's (verified, or gone)."""
        for identifier in dict.fromkeys(self._log_groups):
            try:
                self.aws.logs.delete_log_group(logGroupName=f"/aws/lambda/{identifier}")
            except DELETE_ERRORS as exc:
                reason = self._leftover_reason(exc)
                if reason:
                    leftovers.append(f"log group {identifier}: {reason}")
