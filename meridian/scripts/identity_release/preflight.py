"""Read-back checks: does every hop report the configuration the release mode needs?

Each check returns one line per problem, never raises on what a hop returns (a value of the
wrong type is itself a finding), and names the hop. ``publish.py`` refuses to run unless the
lines are empty, and ``scripts/release_identity.py check`` prints them, so the operator and the
publisher read the same answer. The release changes the Gateway, both Runtimes, the Cedar rules,
the App Runner service and the identity stack together; a later ``agentcore deploy`` can reset
the interceptor, so this is also the drift check.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from backend.agentcore.auth_mode import IAM, JWT, MODES
from scripts.identity_release import settings

BASE_POLICIES = ("meridian_read_tools", "meridian_hold_governance", "meridian_booking_governance")
BINDING_POLICY = "meridian_traveler_binding"
ENFORCING = "ACTIVE"
RUNTIME_ENV_KEYS = ("AGENTCORE_RUNTIME_ARN", "AGENTCORE_WORKFLOW_RUNTIME_ARN")
RUNTIME_NAMES = {"AGENTCORE_RUNTIME_ARN": "MeridianConcierge",
                 "AGENTCORE_WORKFLOW_RUNTIME_ARN": "MeridianWorkflow"}
PROOF_MAX_AGE = timedelta(days=7)
PROOF_FUTURE_SKEW = timedelta(minutes=5)
PROOF_LOGIN = "meridian_backend"
PROOF_COMMAND = f"python scripts/prove_backend_login.py --apply {settings.CONFIRM_FLAG}"
SIGN_IN_VARIABLES = (
    "MERIDIAN_COGNITO_REGION", "MERIDIAN_COGNITO_USER_POOL_ID", "MERIDIAN_COGNITO_APP_CLIENT_ID",
)
JWT_FORBIDDEN_VARIABLES = ("MERIDIAN_API_TOKEN", "MERIDIAN_ALLOW_INSECURE_LOCALHOST")
GATEWAY_URL = re.compile(
    r"^https://[^./]+\.gateway\.bedrock-agentcore\.(?P<region>[a-z0-9-]+)\.amazonaws\.com(/|$)")


def _as_dict(value: Any) -> dict[str, Any]:
    """``value`` as a dict, or an empty one when a hop returned some other type."""
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    """``value`` as a list, or an empty one when a hop returned some other type."""
    return list(value) if isinstance(value, (list, tuple)) else []


def _unreadable(subject: str, value: Any) -> list[str]:
    return [f"{subject}: the description is unreadable (got {type(value).__name__})"]


@dataclass(frozen=True)
class Target:
    """What every hop is expected to report.

    Attributes:
        mode: ``iam`` or ``jwt``.
        design: The Gateway enforcement design (``both``, ``cedar`` or ``interceptor``).
        cognito: The pool; required in ``jwt`` mode.
        interceptor_arn: The interceptor function's ARN when the design uses one.
        account: The deployment's account; hop ARNs and the proof receipt must match it.
        region: The deployment's Region.
        master_secret_arn: The master login's secret (``AURORA_SECRET_ARN`` in ``.env``), which
            the hosted backend must not run as.
        backend_secret_arn: The ``meridian_backend`` login's secret, when known.

    Raises:
        ReleaseConfigError: When ``mode`` is unknown or ``jwt`` has no pool.
    """

    mode: str
    design: str
    cognito: settings.CognitoSettings | None = None
    interceptor_arn: str | None = None
    account: str = ""
    region: str = ""
    master_secret_arn: str | None = None
    backend_secret_arn: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise settings.ReleaseConfigError(
                f"unknown release mode {self.mode!r}; expected 'iam' or 'jwt'")
        if self.mode == JWT and self.cognito is None:
            raise settings.ReleaseConfigError(
                "the jwt release needs the Cognito pool settings; none were given")


def target_for(mode: str, env: Mapping[str, str | None], account: str, region: str) -> Target:
    """What every hop must report in ``mode``, from the settings in ``env``.

    Raises:
        ReleaseConfigError: When the enforcement design or the pool settings are invalid.
    """
    where = {"account": account, "region": region}
    if mode != JWT:
        return Target(mode=mode, design=settings.BOTH, **where)
    design = settings.enforcement(env)
    arn = settings.interceptor_arn(account, region) if settings.uses_interceptor(design) else None
    return Target(
        mode=mode, design=design, cognito=settings.cognito_settings(env), interceptor_arn=arn,
        master_secret_arn=(env.get("AURORA_SECRET_ARN") or "").strip() or None,
        backend_secret_arn=(env.get("AURORA_BACKEND_SECRET_ARN") or "").strip() or None,
        **where)


@dataclass
class HopState:
    """The Gateway, both Runtimes and the active rules, as the control plane describes them.

    ``policies`` maps each ACTIVE rule's name to its ``enforcementMode`` (``None`` when the
    summary carried none).
    """

    gateway: dict[str, Any]
    runtimes: dict[str, dict[str, Any]]
    policies: dict[str, str | None] = field(default_factory=dict)


# ------------------------------------------------------------------ the Gateway


def _jwt_authorizer(block: Any) -> dict[str, Any]:
    return _as_dict(_as_dict(block).get("customJWTAuthorizer"))


def _authorizer_findings(subject: str, authorizer: Mapping[str, Any], target: Target) -> list[str]:
    pool = target.cognito
    found = []
    if authorizer.get("discoveryUrl") != pool.discovery_url:
        found.append(f"{subject}: discoveryUrl is not this pool's OpenID configuration")
    if _as_list(authorizer.get("allowedClients")) != [pool.client_id]:
        found.append(f"{subject}: allowedClients is not exactly the web app client")
    if authorizer.get("allowedAudience"):
        found.append(f"{subject}: allowedAudience is set, but an access token has no aud claim")
    found += [f"{subject}: {member} is set, but the release expects none, so tokens the pool "
              "issues could be refused" for member in ("allowedScopes", "customClaims")
              if authorizer.get(member)]
    return found


def _interceptor_findings(gateway: Mapping[str, Any], target: Target) -> list[str]:
    raw = gateway.get("interceptorConfigurations")
    if raw is not None and not isinstance(raw, list):
        return ["Gateway: interceptorConfigurations is not a list"]
    attached = raw or []
    if not (target.mode == JWT and settings.uses_interceptor(target.design)):
        if attached:
            return ["Gateway: has an interceptor attached, but this design uses none"]
        return []
    if not attached:
        return ["Gateway: no request interceptor is attached, so the traveler is not pinned"]
    if len(attached) != 1:
        return [f"Gateway: has {len(attached)} interceptors, expected exactly one"]
    if not isinstance(attached[0], Mapping):
        return ["Gateway: the interceptor entry is unreadable"]
    return _interceptor_entry_findings(attached[0], target)


def _interceptor_entry_findings(entry: Mapping[str, Any], target: Target) -> list[str]:
    found = []
    function = _as_dict(_as_dict(entry.get("interceptor")).get("lambda")).get("arn")
    if function != target.interceptor_arn:
        found.append("Gateway: the interceptor is not the Meridian traveler-pin function")
    if _as_list(entry.get("interceptionPoints")) != ["REQUEST"]:
        found.append("Gateway: the interceptor must run at REQUEST only")
    if _as_dict(entry.get("inputConfiguration")).get("passRequestHeaders") is not True:
        found.append("Gateway: the interceptor does not get the request headers "
                     "(passRequestHeaders is not true), so it cannot read the token")
    return found


def check_gateway(gateway: Any, target: Target) -> list[str]:
    """Findings for the Gateway's status, authorizer, interceptor and policy engine mode."""
    if not isinstance(gateway, Mapping):
        return _unreadable("Gateway", gateway)
    found = []
    if gateway.get("status") != "READY":
        found.append(f"Gateway: status is {gateway.get('status')}, not READY")
    wanted = "CUSTOM_JWT" if target.mode == JWT else "AWS_IAM"
    if gateway.get("authorizerType") != wanted:
        found.append(f"Gateway: authorizer is {gateway.get('authorizerType')}, expected {wanted}")
    elif target.mode == JWT:
        found += _authorizer_findings(
            "Gateway", _jwt_authorizer(gateway.get("authorizerConfiguration")), target)
    elif gateway.get("authorizerConfiguration"):
        found.append("Gateway: iam mode still has a leftover authorizerConfiguration "
                     "(a JWT authorizer block)")
    found += _interceptor_findings(gateway, target)
    if _as_dict(gateway.get("policyEngineConfiguration")).get("mode") != "ENFORCE":
        found.append("Gateway: the policy engine is not attached in ENFORCE mode")
    return found


# ----------------------------------------------------------------- the Runtimes


def check_runtime(name: str, runtime: Any, target: Target) -> list[str]:
    """Findings for one Runtime's status, authorizer, header allowlist and mode variable."""
    subject = f"Runtime {name}"
    if not isinstance(runtime, Mapping):
        return _unreadable(subject, runtime)
    found = []
    if runtime.get("status") != "READY":
        found.append(f"{subject}: status is {runtime.get('status')}, not READY")
    allowlist = _as_list(
        _as_dict(runtime.get("requestHeaderConfiguration")).get("requestHeaderAllowlist"))
    declared = _as_dict(runtime.get("environmentVariables")).get("MERIDIAN_AGENTCORE_AUTH")
    if target.mode == JWT:
        authorizer = _jwt_authorizer(runtime.get("authorizerConfiguration"))
        found += _jwt_runtime_findings(subject, authorizer, allowlist, declared, target)
    else:
        leftover = bool(runtime.get("authorizerConfiguration"))
        found += _iam_runtime_findings(subject, leftover, allowlist, declared)
    return found


def _jwt_runtime_findings(subject: str, authorizer: Mapping[str, Any], allowlist: list[Any],
                          declared: Any, target: Target) -> list[str]:
    found = []
    if not authorizer:
        found.append(f"{subject}: has no JWT authorizer, so it still accepts only IAM callers")
    else:
        found += _authorizer_findings(subject, authorizer, target)
    if allowlist != ["Authorization"]:
        found.append(f"{subject}: requestHeaderAllowlist is not exactly Authorization, so the "
                     "code cannot read the caller's token")
    if declared != JWT:
        found.append(f"{subject}: MERIDIAN_AGENTCORE_AUTH is {declared!r}, expected 'jwt'")
    return found


def _iam_runtime_findings(subject: str, has_authorizer: bool, allowlist: list[Any],
                          declared: Any) -> list[str]:
    found = []
    if has_authorizer:
        found.append(f"{subject}: still has a JWT authorizer, so IAM callers are refused")
    if "Authorization" in allowlist:
        found.append(f"{subject}: still allowlists the Authorization header")
    if declared not in (None, IAM):
        found.append(f"{subject}: MERIDIAN_AGENTCORE_AUTH is {declared!r}, expected 'iam'")
    return found


# ------------------------------------------------------------------- the rules


def check_policies(policies: Any, target: Target) -> list[str]:
    """Findings for the Cedar rules the policy engine holds.

    ``policies`` maps each ACTIVE rule's name to its ``enforcementMode``. A rule that is present
    but not in ``ACTIVE`` mode (``LOG_ONLY`` records a denial and lets the call through) is a
    finding, as is a rule that is missing.
    """
    if not isinstance(policies, Mapping):
        return ["Policy engine: the rules could not be read"]
    wanted = target.mode == JWT and settings.uses_cedar_binding(target.design)
    required = [*BASE_POLICIES, *([BINDING_POLICY] if wanted else [])]
    found = []
    for name in required:
        if name not in policies:
            found.append(f"Policy engine: the {name} rule is missing")
        elif policies[name] != ENFORCING:
            found.append(f"Policy engine: the {name} rule is in mode {policies[name]!r}, "
                         f"not {ENFORCING}, so it does not block")
    if not wanted and BINDING_POLICY in policies:
        found.append(f"Policy engine: the {BINDING_POLICY} rule is present, but this design "
                     "does not use it")
    return found


# ------------------------------------------------------------ the App Runner service


def image_environment(service: Any) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The plain and secret environment of an image-based service, or ``None`` for any other."""
    source = _as_dict(_as_dict(service).get("SourceConfiguration"))
    image = source.get("ImageRepository")
    if not isinstance(image, Mapping):
        return None
    config = _as_dict(image.get("ImageConfiguration"))
    return (_as_dict(config.get("RuntimeEnvironmentVariables")),
            _as_dict(config.get("RuntimeEnvironmentSecrets")))


def check_service_environment(variables: Any, secrets: Any, target: Target) -> list[str]:
    """Findings for the hosted backend's environment, planned or live."""
    if not isinstance(variables, Mapping) or not isinstance(secrets, Mapping):
        return ["Service: the environment is unreadable (expected two mappings)"]
    if target.mode != JWT:
        return _iam_service_findings(variables)
    found = []
    if variables.get("MERIDIAN_AGENTCORE_AUTH") != JWT:
        found.append("Service: MERIDIAN_AGENTCORE_AUTH is not 'jwt', so the backend would "
                     "sign AgentCore calls with IAM")
    found += _pool_variable_findings(variables, target.cognito)
    if variables.get("ENVIRONMENT") != "production":
        found.append("Service: ENVIRONMENT is not 'production', so the loopback path could open")
    found += [f"Service: {key} is still set; the jwt release has no shared token or loopback"
              for key in JWT_FORBIDDEN_VARIABLES if key in variables]
    if "MERIDIAN_API_TOKEN" in secrets:
        found.append("Service: MERIDIAN_API_TOKEN is still a secret reference; the jwt release "
                     "has no shared token")
    return found + _backend_login_findings(variables, target)


def _iam_service_findings(variables: Mapping[str, Any]) -> list[str]:
    found = [f"Service: {key} is set, but the iam release has no sign-in"
             for key in SIGN_IN_VARIABLES if key in variables]
    declared = variables.get("MERIDIAN_AGENTCORE_AUTH")
    if declared not in (None, IAM):
        found.append(f"Service: MERIDIAN_AGENTCORE_AUTH is {declared!r}, expected unset or 'iam'")
    return found


def _pool_variable_findings(variables: Mapping[str, Any],
                            pool: settings.CognitoSettings) -> list[str]:
    expected = {
        "MERIDIAN_COGNITO_REGION": pool.region,
        "MERIDIAN_COGNITO_USER_POOL_ID": pool.pool_id,
        "MERIDIAN_COGNITO_APP_CLIENT_ID": pool.client_id,
    }
    return [f"Service: {key} is not the configured pool setting"
            for key, value in expected.items() if variables.get(key) != value]


def _backend_login_findings(variables: Mapping[str, Any], target: Target) -> list[str]:
    login, backend = variables.get("AURORA_SECRET_ARN"), variables.get("AURORA_BACKEND_SECRET_ARN")
    if not login or login != backend:
        return ["Service: AURORA_SECRET_ARN is not the AURORA_BACKEND_SECRET_ARN, so the "
                "backend would not run as the meridian_backend login"]
    if target.master_secret_arn and login == target.master_secret_arn:
        return ["Service: AURORA_SECRET_ARN is the master login's secret, so the backend would "
                "not run as the meridian_backend login"]
    if target.backend_secret_arn and login != target.backend_secret_arn:
        return ["Service: AURORA_SECRET_ARN is not the meridian_backend secret expected from "
                "AURORA_BACKEND_SECRET_ARN in meridian/.env"]
    return []


# ----------------------------------------------------- identity stack and the proof


def identity_findings(outputs: Any, cognito: settings.CognitoSettings | None,
                      hosted_ui_domain: str | None = None) -> list[str]:
    """Findings for the identity stack's outputs against the configured pool and client.

    ``hosted_ui_domain`` is the sign-in domain the site is configured with
    (``VITE_COGNITO_DOMAIN``); when it is given the stack's ``HostedUiDomain`` must equal it.
    """
    if not isinstance(outputs, Mapping):
        return ["Identity stack: its outputs are unreadable"]
    if cognito is None:
        return ["Identity stack: no pool settings to compare its outputs with"]
    wanted = ("UserPoolId", "AppClientId", "HostedUiDomain", "Issuer")
    missing = [key for key in wanted if key not in outputs]
    found = []
    if missing:
        found.append(f"Identity stack: has no output {', '.join(missing)}; deploy it first")
    compared = [("UserPoolId", cognito.pool_id), ("AppClientId", cognito.client_id),
                ("Issuer", cognito.issuer)]
    if hosted_ui_domain is not None:
        compared.append(("HostedUiDomain", hosted_ui_domain))
    for key, expected in compared:
        if key in outputs and outputs[key] != expected:
            found.append(f"Identity stack: output {key} differs from meridian/.env or "
                         "VITE_COGNITO_DOMAIN; run scripts/sync_cognito_env.py --write so the "
                         "site and the backend trust one client")
    return found


def check_backend_login_proof(path: Path, target: Target, git_sha: str,
                              now: datetime | None = None) -> list[str]:
    """Findings for the receipt ``scripts/prove_backend_login.py`` writes after a passing run.

    The receipt must be this release's: ``ok`` is the boolean true, ``at`` is a time-zone-aware
    moment no more than 5 minutes ahead and no more than 7 days back, ``account``, ``region``,
    ``user_pool_id`` and ``git_sha`` equal the target's (``git_sha`` the repository HEAD the
    caller passes), the login is ``meridian_backend`` and every entry of ``checks`` is true.
    ``settings.PROOF_FIELDS`` documents the schema.
    """
    now = now or datetime.now(timezone.utc)
    suffix = f"; run `{PROOF_COMMAND}` before the release"
    if target.cognito is None:
        return [f"Backend login proof: no pool settings to bind it to{suffix}"]
    try:
        proof = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"Backend login proof: none recorded{suffix}"]
    except (OSError, ValueError, RecursionError):
        return [f"Backend login proof: unreadable{suffix}"]
    return [f"Backend login proof: {problem}{suffix}"
            for problem in _receipt_problems(proof, target, git_sha, now)]


def interceptor_environment_findings(configuration: Any, target: Target) -> list[str]:
    """Findings for the deployed interceptor's environment, from ``GetFunctionConfiguration``.

    ``PINNED_TOOLS`` must be unset (an override replaces the default tool list) and the expected
    client and issuer must be the pool's. Values are never printed.
    """
    environment = configuration.get("Environment") if isinstance(configuration, dict) else None
    variables = environment.get("Variables") if isinstance(environment, dict) else None
    if not isinstance(variables, dict):
        variables = {}
    found = []
    if "PINNED_TOOLS" in variables:
        found.append("Interceptor Lambda: PINNED_TOOLS is set; unset it, an override replaces "
                     "the default tool list")
    if target.cognito is None:
        return found
    expected = (("EXPECTED_CLIENT_ID", target.cognito.client_id),
                ("EXPECTED_ISSUER", target.cognito.issuer))
    for name, value in expected:
        if not variables.get(name):
            found.append(f"Interceptor Lambda: {name} is not set")
        elif variables[name] != value:
            found.append(f"Interceptor Lambda: {name} is not the pool's; run "
                         "scripts/release_identity.py interceptor to redeploy it")
    return found


def interceptor_lambda_findings(lambda_client: Any, target: Target) -> list[str]:
    """Findings for the deployed interceptor, read with ``GetFunctionConfiguration``.

    Empty when the design uses no interceptor. A function that does not exist is a finding;
    any other AWS error propagates.
    """
    if not target.interceptor_arn:
        return []
    try:
        configuration = lambda_client.get_function_configuration(
            FunctionName=target.interceptor_arn)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise
        return ["Interceptor Lambda: not deployed; run scripts/release_identity.py interceptor "
                f"--apply {settings.CONFIRM_FLAG}"]
    return interceptor_environment_findings(configuration, target)


def _receipt_problems(proof: Any, target: Target, git_sha: str, now: datetime) -> list[str]:
    if not isinstance(proof, dict) or proof.get("ok") is not True:
        return ["the last run did not pass"]
    problems = []
    if proof.get("login") != PROOF_LOGIN:
        problems.append(f"it ran as {proof.get('login')!r}, not {PROOF_LOGIN}")
    problems += _timestamp_problems(proof.get("at"), now)
    bound = (("account", target.account), ("region", target.region),
             ("user_pool_id", target.cognito.pool_id), ("git_sha", git_sha))
    for name, expected in bound:
        if proof.get(name) is None:
            problems.append(f"it records no {name}")
        elif proof[name] != expected:
            problems.append(f"its {name} is not this release's {name}")
    return problems + _checks_problems(proof.get("checks"))


def _timestamp_problems(at: Any, now: datetime) -> list[str]:
    try:
        taken = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return ["its timestamp is unreadable"]
    if taken.utcoffset() is None:
        return ["its timestamp has no time zone"]
    if taken - now > PROOF_FUTURE_SKEW:
        return ["its timestamp is in the future"]
    if now - taken > PROOF_MAX_AGE:
        return [f"it is older than {PROOF_MAX_AGE.days} days"]
    return []


def _checks_problems(checks: Any) -> list[str]:
    if not isinstance(checks, Mapping) or not checks:
        return ["it records no checks"]
    failed = sorted(str(name) for name, passed in checks.items() if passed is not True)
    return [f"checks did not all pass ({', '.join(failed)})"] if failed else []


# ------------------------------------------------- where the settings say the hops are


def _arn_findings(key: str, value: str, service: str, target: Target) -> list[str]:
    parts = value.split(":")
    if len(parts) < 6 or parts[0] != "arn" or parts[2] != service:
        return [f"Settings: {key} is not a {service} ARN"]
    found = []
    if parts[3] != target.region:
        found.append(f"Settings: {key} names another Region than the cluster")
    if parts[4] != target.account:
        found.append(f"Settings: {key} names another account than the cluster")
    return found


def check_hop_locations(env: Mapping[str, str | None], target: Target) -> list[str]:
    """Findings for hop settings that name another account or Region than the deployment.

    A setting that is not set is left to ``hop_ids``, which names it.
    """
    found = []
    for key in RUNTIME_ENV_KEYS:
        value = (env.get(key) or "").strip()
        if value:
            found += _arn_findings(key, value, "bedrock-agentcore", target)
    url = (env.get("AGENTCORE_GATEWAY_URL") or "").strip()
    if url:
        match = GATEWAY_URL.match(url)
        if not match:
            found.append("Settings: AGENTCORE_GATEWAY_URL is not a Gateway URL")
        elif match["region"] != target.region:
            found.append("Settings: AGENTCORE_GATEWAY_URL names another Region than the cluster")
    return found


def check_service_arn(service_arn: Any, target: Target) -> list[str]:
    """Findings when ``--service-arn`` is not the meridian-web service of this deployment."""
    pattern = (rf"arn:aws:apprunner:{re.escape(target.region)}:{re.escape(target.account)}:"
               "service/meridian-web/[a-f0-9]+")
    if isinstance(service_arn, str) and re.fullmatch(pattern, service_arn):
        return []
    return ["Service: --service-arn is not a meridian-web service in this account and Region"]


# ----------------------------------------------------------------- reading the hops


def hop_ids(env: Mapping[str, str | None]) -> tuple[str, dict[str, str]]:
    """The Gateway id and the Runtime ids, from the backend's AGENTCORE_* settings.

    Raises:
        ReleaseConfigError: When a setting is missing.
    """
    values = {key: (env.get(key) or "").strip()
              for key in ("AGENTCORE_GATEWAY_URL", *RUNTIME_ENV_KEYS)}
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise settings.ReleaseConfigError(
            f"{', '.join(missing)} not set; run scripts/sync_agentcore_env.py --write")
    host = values["AGENTCORE_GATEWAY_URL"].split("//", 1)[-1].split("/", 1)[0]
    runtimes = {RUNTIME_NAMES[key]: values[key].rsplit("/", 1)[-1] for key in RUNTIME_ENV_KEYS}
    return host.split(".")[0], runtimes


def _policy_modes(control: Any, engine_id: str) -> dict[str, str | None]:
    modes: dict[str, str | None] = {}
    seen: set[str] = set()
    token = None
    while True:
        page = _as_dict(control.list_policies(
            policyEngineId=engine_id, **({"nextToken": token} if token else {})))
        for entry in _as_list(page.get("policies")):
            if isinstance(entry, Mapping) and isinstance(entry.get("name"), str) \
                    and entry.get("status") == "ACTIVE":
                modes[entry["name"]] = entry.get("enforcementMode")
        token = page.get("nextToken")
        if not token:
            return modes
        if not isinstance(token, str) or token in seen:
            raise settings.ReleaseConfigError(
                "list_policies returned a nextToken it had already returned; cannot read all "
                "of the policy engine's rules")
        seen.add(token)


def read_state(control: Any, gateway_id: str, runtime_ids: Mapping[str, str]) -> HopState:
    """Describe the Gateway, both Runtimes and the policy engine's active rules."""
    gateway = control.get_gateway(gatewayIdentifier=gateway_id)
    runtimes = {name: control.get_agent_runtime(agentRuntimeId=runtime_id)
                for name, runtime_id in runtime_ids.items()}
    engine_arn = _as_dict(_as_dict(gateway).get("policyEngineConfiguration")).get("arn")
    has_engine = isinstance(engine_arn, str) and bool(engine_arn)
    policies = _policy_modes(control, engine_arn.rsplit("/", 1)[-1]) if has_engine else {}
    return HopState(gateway=gateway, runtimes=runtimes, policies=policies)


def hop_findings(state: HopState, target: Target) -> list[str]:
    """Every hop's findings, Gateway first; a Runtime that was not read is a finding."""
    found = check_gateway(state.gateway, target)
    runtimes = _as_dict(state.runtimes)
    for name in RUNTIME_NAMES.values():
        if name in runtimes:
            found += check_runtime(name, runtimes[name], target)
        else:
            found.append(f"Runtime {name}: was not read")
    return found + check_policies(state.policies, target)


def refuse_if_any(findings: list[str], mode: str) -> None:
    """Stop with one line per finding.

    Raises:
        SystemExit: When ``findings`` is not empty.
    """
    if findings:
        count = len(findings)
        noun = "finding" if count == 1 else "findings"
        lines = "\n".join(f"  {line}" for line in findings)
        raise SystemExit(f"refusing: {count} {noun} against the {mode} release:\n{lines}")
