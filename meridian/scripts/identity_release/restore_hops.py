"""Put each hop back to what a snapshot saved, and read it back.

Every hop has a ``check`` (the lines in which the live hop differs from the saved copy, empty when
it is as saved, including the findings of ``preflight`` that the saved state did not already
have) and, where the API can do it, a ``restore`` that sends the saved configuration in full. The
Gateway, the Runtimes and the service are replaced whole, so every field the API takes is sent.
The roles stack and the Cedar rules belong to CloudFormation and ``agentcore deploy``; they have a
``check`` and a printed remedy, and nothing here deletes them.

Nothing here deletes anything. A hop whose saved copy was redacted anywhere, or holds a
placeholder inside any string, is refused rather than restored with the placeholder.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import botocore.session
from botocore.exceptions import ClientError, WaiterError
from botocore.validate import ParamValidator

from scripts.identity_release import lambda_release, preflight, settings, snapshot

WAIT_ATTEMPTS = 60
POLL_SECONDS = 5
CONTROL = "bedrock-agentcore-control"
STATUS_PREFIX = "status "
NOT_RESTORABLE = "not restorable here: "
PENDING_RESTART = "ssm"


class RestoreRefused(RuntimeError):
    """A hop is not safe to restore from this snapshot; nothing was changed for it."""


@dataclass
class Context:
    """What a rollback works with: the snapshot, the clients, the deployment and the mode."""

    saved: Mapping[str, Any]
    clients: snapshot.Clients
    where: snapshot.Where
    apply: bool
    sleep: Callable[[float], None]
    stamp: str
    pending: set[str] = field(default_factory=set)
    journal: Callable[[], None] = lambda: None

    @property
    def target(self) -> preflight.Target:
        """What the hops reported when the snapshot was taken."""
        return preflight.target_for(
            self.saved["mode"], self.where.env, self.where.account, self.where.region)


@dataclass(frozen=True)
class Step:
    """One hop: how to compare it, how to restore it (``None``: not by this tool), the remedy.

    ``needs`` names the steps that must not have failed for this one to run.
    """

    name: str
    check: Callable[[Context], list[str]]
    restore: Callable[[Context], None] | None = None
    remedy: Callable[[Context], list[str]] | None = None
    needs: tuple[str, ...] = ()


# ----------------------------------------------------------------------- helpers


def show(value: Any) -> str:
    """A value as one line, in full; absent as ``(absent)``."""
    if value is None:
        return "(absent)"
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def differences(current: Any, saved: Any, path: str = "") -> list[str]:
    """Lines for each place ``current`` differs from ``saved``, with both values in full."""
    if isinstance(current, dict) and isinstance(saved, dict):
        lines: list[str] = []
        for key in sorted(set(current) | set(saved)):
            lines += differences(current.get(key), saved.get(key), f"{path}.{key}" if path else key)
        return lines
    if current == saved:
        return []
    return [f"{path}: {show(current)} -> {show(saved)}"]


def compare(label: str, current: Mapping[str, Any], saved: Mapping[str, Any]) -> list[str]:
    """Every difference between redacted views, labelled with the hop."""
    cleaned, _ = snapshot.redact(dict(current))
    return [f"{label} {line}" for line in differences(cleaned, dict(saved))]


def refuse_placeholders(ctx: Context, label: str, saved_view: Any, prefix: str) -> None:
    """Refuse a hop whose saved copy was redacted or holds a placeholder in any string.

    Raises:
        RestoreRefused: Naming the paths (never values).
    """
    paths = snapshot.redacted_under(ctx.saved, prefix) + snapshot.placeholders(saved_view)
    if paths:
        raise RestoreRefused(
            f"{label}: the saved copy holds redacted value(s) at {', '.join(paths[:5])}; a "
            "placeholder cannot be written back, so restore this hop by hand")


def new_findings(ctx: Context, lines: list[str]) -> list[str]:
    """Findings the saved state did not already have."""
    known = set(ctx.saved["baselineFindings"])
    return [f"new finding: {line}" for line in lines if line not in known]


@lru_cache(maxsize=None)
def input_shape(service: str, operation: str) -> Any:
    """The installed botocore model's input shape for ``operation``."""
    model = botocore.session.get_session().get_service_model(service)
    return model.operation_model(operation).input_shape


def request_problems(service: str, operation: str, arguments: Mapping[str, Any]) -> list[str]:
    """Where ``arguments`` is not a valid call for the installed service model."""
    shape = input_shape(service, operation)
    report = ParamValidator().validate(dict(arguments), shape)
    return [report.generate_report()] if report.has_errors() else []


def require_valid(service: str, operation: str, arguments: Mapping[str, Any]) -> None:
    """Refuse to send an invalid request.

    Raises:
        RestoreRefused: When the model rejects ``arguments``.
    """
    problems = request_problems(service, operation, arguments)
    if problems:
        raise RestoreRefused(f"the {operation} request would be invalid: {problems[0]}")


def settle(ctx: Context, probe: Callable[[], list[str]], what: str) -> None:
    """Poll ``probe`` until it returns no lines, a bounded number of times.

    Raises:
        RestoreRefused: When it is still not empty after ``WAIT_ATTEMPTS`` checks.
    """
    lines: list[str] = []
    for attempt in range(WAIT_ATTEMPTS):
        lines = probe()
        if not lines:
            return
        if attempt < WAIT_ATTEMPTS - 1:
            ctx.sleep(POLL_SECONDS)
    raise RestoreRefused(f"{what} is still {lines[0]} after {WAIT_ATTEMPTS} checks")


def code_of(error: ClientError) -> str:
    """The AWS error code."""
    return error.response.get("Error", {}).get("Code", "")


# ----------------------------------------------------------------------- Gateway


def gateway_arguments(ctx: Context) -> dict[str, Any]:
    """The complete, model-valid ``update_gateway`` request for the saved Gateway."""
    saved = ctx.saved["gateway"]
    if saved.get("gatewayId") != ctx.where.gateway_id:
        raise RestoreRefused("Gateway: the saved copy is for another Gateway than the configured "
                             "one")
    view = snapshot.gateway_view(saved)
    refuse_placeholders(ctx, "Gateway", view, "gateway")
    arguments = {"gatewayIdentifier": ctx.where.gateway_id, **view}
    require_valid(CONTROL, "UpdateGateway", arguments)
    return arguments


def gateway_check(ctx: Context) -> list[str]:
    arguments = gateway_arguments(ctx)
    view = {k: v for k, v in arguments.items() if k != "gatewayIdentifier"}
    current = ctx.clients.control.get_gateway(gatewayIdentifier=ctx.where.gateway_id)
    lines = compare("Gateway", snapshot.gateway_view(current), view)
    if current.get("status") != "READY":
        lines.append(f"{STATUS_PREFIX}{current.get('status')}, not READY")
    return lines + new_findings(ctx, preflight.check_gateway(current, ctx.target))


def gateway_restore(ctx: Context) -> None:
    arguments = gateway_arguments(ctx)
    settle(ctx, lambda: gateway_busy(ctx), "the Gateway")
    ctx.clients.control.update_gateway(**arguments)


def gateway_busy(ctx: Context) -> list[str]:
    status = ctx.clients.control.get_gateway(gatewayIdentifier=ctx.where.gateway_id).get("status")
    return [] if status == "READY" else [f"{status}, not READY"]


# ---------------------------------------------------------------------- Runtimes


def runtime_arguments(ctx: Context, name: str) -> dict[str, Any]:
    """The complete, model-valid ``update_agent_runtime`` request for the saved Runtime."""
    saved = ctx.saved["runtimes"][name]
    runtime_id = ctx.where.runtime_ids[name]
    if saved.get("agentRuntimeId") != runtime_id:
        raise RestoreRefused(f"Runtime {name}: the saved copy is for another Runtime than the "
                             "configured one")
    view = snapshot.runtime_view(saved)
    refuse_placeholders(ctx, f"Runtime {name}", view, f"runtimes.{name}")
    arguments = {"agentRuntimeId": runtime_id, **view}
    require_valid(CONTROL, "UpdateAgentRuntime", arguments)
    return arguments


def runtime_check(ctx: Context, name: str) -> list[str]:
    arguments = runtime_arguments(ctx, name)
    view = {k: v for k, v in arguments.items() if k != "agentRuntimeId"}
    current = ctx.clients.control.get_agent_runtime(agentRuntimeId=ctx.where.runtime_ids[name])
    lines = compare(f"Runtime {name}", snapshot.runtime_view(current), view)
    if current.get("status") != "READY":
        lines.append(f"{STATUS_PREFIX}{current.get('status')}, not READY")
    return lines + new_findings(ctx, preflight.check_runtime(name, current, ctx.target))


def runtime_restore(ctx: Context, name: str) -> None:
    arguments = runtime_arguments(ctx, name)
    runtime_id = ctx.where.runtime_ids[name]

    def busy() -> list[str]:
        status = ctx.clients.control.get_agent_runtime(agentRuntimeId=runtime_id).get("status")
        return [] if status == "READY" else [f"{status}, not READY"]

    settle(ctx, busy, f"Runtime {name}")
    ctx.clients.control.update_agent_runtime(**arguments)


# ------------------------------------------------------------------------ service


def service_arn(ctx: Context) -> str:
    arn = ctx.saved["service"].get("ServiceArn")
    problems = preflight.check_service_arn(arn, ctx.target)
    if problems or arn != ctx.where.service_arn:
        raise RestoreRefused("Service: the saved service is not the meridian-web service of "
                             "this account and Region that was asked for")
    return arn


def service_arguments(ctx: Context) -> dict[str, Any]:
    """The complete, model-valid ``update_service`` request for the saved service."""
    arn = service_arn(ctx)
    view = snapshot.service_view(ctx.saved["service"])
    refuse_placeholders(ctx, "Service", view, "service")
    arguments = {"ServiceArn": arn, **view}
    require_valid("apprunner", "UpdateService", arguments)
    return arguments


def service_check(ctx: Context) -> list[str]:
    arguments = service_arguments(ctx)
    view = {k: v for k, v in arguments.items() if k != "ServiceArn"}
    current = ctx.clients.apprunner.describe_service(ServiceArn=arguments["ServiceArn"])["Service"]
    lines = compare("Service", snapshot.service_view(current), view)
    if current.get("Status") != "RUNNING":
        lines.append(f"{STATUS_PREFIX}{current.get('Status')}, not RUNNING")
    environment = preflight.image_environment(current) or ({}, {})
    found = preflight.check_service_environment(*environment, ctx.target)
    return lines + new_findings(ctx, found)


def service_restore(ctx: Context) -> None:
    arguments = service_arguments(ctx)
    arn = arguments["ServiceArn"]

    def busy() -> list[str]:
        status = ctx.clients.apprunner.describe_service(ServiceArn=arn)["Service"].get("Status")
        return [] if status == "RUNNING" else [f"{status}, not RUNNING"]

    settle(ctx, busy, "the service")
    ctx.clients.apprunner.update_service(**arguments)


# --------------------------------------------------------------------------- site


def site_check(ctx: Context) -> list[str]:
    saved = ctx.saved["site"]
    refuse_placeholders(ctx, "Site", [saved["viewerFunction"], saved["responseHeadersPolicy"]],
                        "site")
    cloudfront = ctx.clients.cloudfront
    live = snapshot.read_viewer(cloudfront, "LIVE")
    lines = []
    if live["code"] != saved["viewerFunction"]["code"]:
        lines.append("Site viewer function: the code differs from the saved code")
    lines += compare("Site viewer function", {"config": live["config"]},
                     {"config": saved["viewerFunction"]["config"]})
    policy = saved["responseHeadersPolicy"]
    current = cloudfront.get_response_headers_policy(Id=policy["id"])
    lines += compare("Site response headers policy",
                     current["ResponseHeadersPolicy"]["ResponseHeadersPolicyConfig"],
                     policy["config"])
    config = cloudfront.get_distribution_config(Id=saved["distributionId"])["DistributionConfig"]
    if snapshot.behaviors_of(config) != saved["behaviors"]:
        lines.append(f"{NOT_RESTORABLE}Site distribution: the behaviors' function associations "
                     "or response headers policy differ; this command does not rewrite the "
                     "distribution")
    return lines


def site_restore(ctx: Context) -> None:
    saved = ctx.saved["site"]
    cloudfront = ctx.clients.cloudfront
    live = snapshot.read_viewer(cloudfront, "LIVE")
    wanted = saved["viewerFunction"]
    if live["code"] != wanted["code"] or live["config"] != wanted["config"]:
        development = cloudfront.describe_function(Name=snapshot.EDGE_FUNCTION,
                                                   Stage="DEVELOPMENT")
        updated = cloudfront.update_function(
            Name=snapshot.EDGE_FUNCTION, IfMatch=development["ETag"],
            FunctionConfig=wanted["config"], FunctionCode=wanted["code"].encode("utf-8"))
        cloudfront.publish_function(Name=snapshot.EDGE_FUNCTION, IfMatch=updated["ETag"])
    policy = saved["responseHeadersPolicy"]
    current = cloudfront.get_response_headers_policy(Id=policy["id"])
    if current["ResponseHeadersPolicy"]["ResponseHeadersPolicyConfig"] != policy["config"]:
        cloudfront.update_response_headers_policy(
            Id=policy["id"], IfMatch=current["ETag"], ResponseHeadersPolicyConfig=policy["config"])


# --------------------------------------------------------- roles stack and rules


def printable_commit(commit: str) -> str:
    """The commit as it can be printed whole: a 12-digit run would be masked as an account id,
    so such a commit is shortened to 11 characters."""
    return commit[:11] if re.search(r"\d{12}", commit) else commit


def checkout_lines(ctx: Context) -> list[str]:
    """How to get a checkout of the snapshot's commit, with the ids taken from ``.env``."""
    commit = printable_commit(str(ctx.saved["commit"]))
    folder = f".local/worktrees/rollback-{commit[:11]}"
    return [
        f"MANUAL (ASK FIRST): run the next commands from a checkout of the snapshot's commit "
        f"{commit}, not from the current one (the current code would deploy the new release "
        "again):",
        f"  from the repository root: git worktree add {folder} {commit}",
        f"  then: cd {folder}/meridian and copy your meridian/.env into it",
        "  set ACCOUNT, REGION and SERVICE_ARN from your own meridian/.env (AURORA_CLUSTER_ARN "
        "gives the account and Region); this output never prints them",
    ]


def finish_line() -> str:
    return "  then re-run rollback to re-attach the interceptor and verify"


def roles_check(ctx: Context) -> list[str]:
    saved = ctx.saved["roles"]
    stack = ctx.clients.cfn.describe_stacks(StackName=snapshot.ROLES_STACK)["Stacks"][0]
    lines = []
    if snapshot.template_hash(ctx.clients.cfn) != saved["templateSha256"]:
        lines.append("Roles stack: the template differs from the saved one")
    if (stack.get("Outputs") or []) != saved["outputs"]:
        lines.append("Roles stack: the outputs differ from the saved ones")
    if (stack.get("Parameters") or []) != saved["parameters"]:
        lines.append("Roles stack: the parameters differ from the saved ones")
    return lines


def publish_remedy(ctx: Context) -> list[str]:
    """Redeploy the roles stack, the service and the site in the saved mode, from that commit."""
    return [*checkout_lines(ctx),
            f"  MERIDIAN_AGENTCORE_AUTH={ctx.saved['mode']} python scripts/publish.py "
            '--account "$ACCOUNT" --region "$REGION" --service-arn "$SERVICE_ARN" '
            f"--apply {settings.CONFIRM_FLAG}",
            finish_line()]


def rules_check(ctx: Context) -> list[str]:
    state = preflight.read_state(ctx.clients.control, ctx.where.gateway_id, ctx.where.runtime_ids)
    return compare("Cedar rules", dict(state.policies), dict(ctx.saved["policies"]))


def rules_remedy(ctx: Context) -> list[str]:
    return [*checkout_lines(ctx),
            f"  MERIDIAN_AGENTCORE_AUTH={ctx.saved['mode']} "
            "python scripts/render_agentcore_config.py",
            "  cd ../meridian_agentcore && /opt/homebrew/bin/agentcore deploy -y   (the rules are "
            "deployed with the Runtimes; this command never deletes one)",
            finish_line()]


# ------------------------------------------------------------------------ Lambdas


def ssm_check(ctx: Context) -> list[str]:
    saved = ctx.saved["lambdas"]["ssmSecretArn"]
    if saved["name"] != lambda_release.SSM_SECRET_PARAMETER:
        raise RestoreRefused("SSM: the saved copy is for another parameter than the secret ARN")
    if not str(saved["value"]).startswith("arn:"):
        raise RestoreRefused("SSM: the saved value is not a secret ARN")
    try:
        found = ctx.clients.ssm.get_parameter(Name=saved["name"])["Parameter"]["Value"]
    except ClientError as error:
        if code_of(error) != "ParameterNotFound":
            raise
        found = None
    if found == saved["value"]:
        return []
    ctx.pending.add(PENDING_RESTART)
    return [f"SSM {saved['name']}: {show(found)} -> {show(saved['value'])}"]


def ssm_restore(ctx: Context) -> None:
    saved = ctx.saved["lambdas"]["ssmSecretArn"]
    ctx.pending.add(PENDING_RESTART)
    ctx.journal()
    ctx.clients.ssm.put_parameter(Name=saved["name"], Value=saved["value"], Type=saved["type"],
                                  Overwrite=True)


def settle_lambda(lam: Any, function: str) -> None:
    """Wait, bounded, for the function's last update to finish."""
    try:
        lam.get_waiter("function_updated_v2").wait(
            FunctionName=function, WaiterConfig=lambda_release.WAIT)
    except WaiterError as error:
        raise RestoreRefused(f"the Lambda {function.rsplit(':', 1)[-1]} did not settle "
                             f"({type(error).__name__}); read it before trying again") from error


def environment_lines(ctx: Context, label: str, function: str, saved: Mapping[str, str],
                      prefix: str, restart: bool = False) -> list[str]:
    refuse_placeholders(ctx, label, dict(saved), prefix)
    configuration = ctx.clients.lam.get_function_configuration(FunctionName=function)
    current = snapshot.read_environment(configuration, label)
    lines = compare(label, current, dict(saved))
    variables = (configuration.get("Environment") or {}).get("Variables") or {}
    if restart and PENDING_RESTART in ctx.pending \
            and variables.get(lambda_release.MARKER) != ctx.stamp:
        lines.append(f"{label}: restart needed so it reads the restored parameter")
    return lines


def holds_function(ctx: Context) -> str:
    saved = ctx.saved["lambdas"]["holds"]["arn"]
    live = lambda_release.holds_function_arn(ctx.clients.control, ctx.where.gateway_id)
    if saved != live:
        raise RestoreRefused("Lambda holds: the saved function is not the Gateway's MeridianHolds "
                             "target")
    lambda_release.require_holds(ctx.clients.lam, saved, ctx.where.account, ctx.where.region)
    return saved


def holds_check(ctx: Context) -> list[str]:
    function = holds_function(ctx)
    return environment_lines(ctx, "Lambda holds", function,
                             ctx.saved["lambdas"]["holds"]["environment"], "lambdas.holds",
                             restart=True)


def apply_environment(ctx: Context, function: str, saved: Mapping[str, str],
                      marker: bool) -> None:
    """Replace the function's environment with ``saved`` and verify the whole map afterwards.

    Raises:
        RestoreRefused: When the environment cannot be read in full before, a wait runs out, or
            the environment afterwards is not exactly the one sent.
    """
    lam = ctx.clients.lam
    label = function.rsplit(":", 1)[-1]
    settle_lambda(lam, function)
    configuration = lam.get_function_configuration(FunctionName=function)
    snapshot.read_environment(configuration, label)
    variables = dict(saved)
    if marker:
        variables[lambda_release.MARKER] = ctx.stamp
    revision = {"RevisionId": configuration["RevisionId"]} if "RevisionId" in configuration else {}
    lam.update_function_configuration(
        FunctionName=function, Environment={"Variables": variables}, **revision)
    settle_lambda(lam, function)
    after = lam.get_function_configuration(FunctionName=function)
    if (after.get("Environment") or {}).get("Variables") != variables:
        raise RestoreRefused(f"the Lambda {label}: its environment after the update is not the "
                             "one sent (a variable or the restart marker differs); read it with "
                             "get-function-configuration and repair it before going on")


def holds_restore(ctx: Context) -> None:
    apply_environment(ctx, holds_function(ctx), ctx.saved["lambdas"]["holds"]["environment"], True)
    ctx.pending.discard(PENDING_RESTART)


def semantic_check(ctx: Context) -> list[str]:
    saved = ctx.saved["lambdas"]["semantic"]
    if saved["name"] != lambda_release.SEMANTIC_FUNCTION:
        raise RestoreRefused("Lambda semantic: the saved copy is for another function")
    return environment_lines(ctx, "Lambda semantic", saved["name"], saved["environment"],
                             "lambdas.semantic")


def semantic_restore(ctx: Context) -> None:
    saved = ctx.saved["lambdas"]["semantic"]
    apply_environment(ctx, saved["name"], saved["environment"], False)


SECRET_STEP = "lambda secret parameter"


def steps(runtime_names: list[str]) -> list[Step]:
    """The hops in the reverse of the release order: site, service, roles, Gateway, Runtimes,
    rules, then the Lambdas. A Runtime needs the Gateway step; the Lambdas need the secret."""
    runtimes = [Step(f"runtime {name}", lambda ctx, n=name: runtime_check(ctx, n),
                     lambda ctx, n=name: runtime_restore(ctx, n), needs=("gateway",))
                for name in runtime_names]
    return [
        Step("site", site_check, site_restore, publish_remedy),
        Step("service", service_check, service_restore),
        Step("roles stack", roles_check, None, publish_remedy),
        Step("gateway", gateway_check, gateway_restore),
        *runtimes,
        Step("cedar rules", rules_check, None, rules_remedy),
        Step(SECRET_STEP, ssm_check, ssm_restore),
        Step("lambda holds", holds_check, holds_restore, needs=(SECRET_STEP,)),
        Step("lambda semantic", semantic_check, semantic_restore, needs=(SECRET_STEP,)),
    ]
