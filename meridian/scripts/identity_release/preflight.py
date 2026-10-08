"""Read-back checks: does every hop report the configuration the release mode needs?

Each check returns one line per problem, never raises, and names the hop. ``publish.py`` refuses
to run unless the lines are empty, and ``scripts/release_identity.py check`` prints them, so the
operator and the publisher read the same answer. The release changes the Gateway, both Runtimes,
the Cedar rules, the App Runner service and the identity stack together; a later
``agentcore deploy`` can reset the interceptor, so this is also the drift check.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.agentcore.auth_mode import IAM, JWT
from scripts.identity_release import settings

BASE_POLICIES = ("meridian_read_tools", "meridian_hold_governance", "meridian_booking_governance")
BINDING_POLICY = "meridian_traveler_binding"
RUNTIME_ENV_KEYS = ("AGENTCORE_RUNTIME_ARN", "AGENTCORE_WORKFLOW_RUNTIME_ARN")
RUNTIME_NAMES = {"AGENTCORE_RUNTIME_ARN": "MeridianConcierge",
                 "AGENTCORE_WORKFLOW_RUNTIME_ARN": "MeridianWorkflow"}
PROOF_MAX_AGE = timedelta(days=7)
PROOF_COMMAND = "python scripts/prove_backend_login.py --apply"
SIGN_IN_VARIABLES = (
    "MERIDIAN_COGNITO_REGION", "MERIDIAN_COGNITO_USER_POOL_ID", "MERIDIAN_COGNITO_APP_CLIENT_ID",
)
JWT_FORBIDDEN_VARIABLES = ("MERIDIAN_API_TOKEN", "MERIDIAN_ALLOW_INSECURE_LOCALHOST")


@dataclass(frozen=True)
class Target:
    """What every hop is expected to report.

    Attributes:
        mode: ``iam`` or ``jwt``.
        design: The Gateway enforcement design (``both``, ``cedar`` or ``interceptor``).
        cognito: The pool, in ``jwt`` mode.
        interceptor_arn: The interceptor function's ARN when the design uses one.
    """

    mode: str
    design: str
    cognito: settings.CognitoSettings | None = None
    interceptor_arn: str | None = None


def target_for(mode: str, env: Mapping[str, str | None], account: str, region: str) -> Target:
    """What every hop must report in ``mode``, from the settings in ``env``.

    Raises:
        ReleaseConfigError: When the enforcement design or the pool settings are invalid.
    """
    if mode != JWT:
        return Target(mode=mode, design=settings.BOTH)
    design = settings.enforcement(env)
    arn = settings.interceptor_arn(account, region) if settings.uses_interceptor(design) else None
    return Target(mode=mode, design=design, cognito=settings.cognito_settings(env),
                  interceptor_arn=arn)


@dataclass
class HopState:
    """The Gateway, both Runtimes and the active policy names, as the control plane sees them."""

    gateway: dict[str, Any]
    runtimes: dict[str, dict[str, Any]]
    policy_names: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ the Gateway


def _jwt_authorizer(block: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict((block or {}).get("customJWTAuthorizer") or {})


def _authorizer_findings(subject: str, authorizer: Mapping[str, Any], target: Target) -> list[str]:
    pool = target.cognito
    found = []
    if authorizer.get("discoveryUrl") != pool.discovery_url:
        found.append(f"{subject}: discoveryUrl is not this pool's OpenID configuration")
    if list(authorizer.get("allowedClients") or []) != [pool.client_id]:
        found.append(f"{subject}: allowedClients is not exactly the web app client")
    if authorizer.get("allowedAudience"):
        found.append(f"{subject}: allowedAudience is set, but an access token has no aud claim")
    return found


def _interceptor_findings(gateway: Mapping[str, Any], target: Target) -> list[str]:
    attached = gateway.get("interceptorConfigurations") or []
    if not (target.mode == JWT and settings.uses_interceptor(target.design)):
        if attached:
            return ["Gateway: has an interceptor attached, but this design uses none"]
        return []
    if not attached:
        return ["Gateway: no request interceptor is attached, so the traveler is not pinned"]
    if len(attached) != 1:
        return [f"Gateway: has {len(attached)} interceptors, expected exactly one"]
    entry = attached[0]
    found = []
    function = ((entry.get("interceptor") or {}).get("lambda") or {}).get("arn")
    if function != target.interceptor_arn:
        found.append("Gateway: the interceptor is not the Meridian traveler-pin function")
    if list(entry.get("interceptionPoints") or []) != ["REQUEST"]:
        found.append("Gateway: the interceptor must run at REQUEST only")
    if (entry.get("inputConfiguration") or {}).get("passRequestHeaders") is not True:
        found.append("Gateway: the interceptor does not get the request headers "
                     "(passRequestHeaders is not true), so it cannot read the token")
    return found


def check_gateway(gateway: Mapping[str, Any], target: Target) -> list[str]:
    """Findings for the Gateway's status, authorizer, interceptor and policy engine mode."""
    found = []
    if gateway.get("status") != "READY":
        found.append(f"Gateway: status is {gateway.get('status')}, not READY")
    wanted = "CUSTOM_JWT" if target.mode == JWT else "AWS_IAM"
    if gateway.get("authorizerType") != wanted:
        found.append(f"Gateway: authorizer is {gateway.get('authorizerType')}, expected {wanted}")
    elif target.mode == JWT:
        found += _authorizer_findings(
            "Gateway", _jwt_authorizer(gateway.get("authorizerConfiguration")), target)
    found += _interceptor_findings(gateway, target)
    if (gateway.get("policyEngineConfiguration") or {}).get("mode") != "ENFORCE":
        found.append("Gateway: the policy engine is not attached in ENFORCE mode")
    return found


# ----------------------------------------------------------------- the Runtimes


def check_runtime(name: str, runtime: Mapping[str, Any], target: Target) -> list[str]:
    """Findings for one Runtime's status, authorizer, header allowlist and mode variable."""
    subject = f"Runtime {name}"
    found = []
    if runtime.get("status") != "READY":
        found.append(f"{subject}: status is {runtime.get('status')}, not READY")
    authorizer = _jwt_authorizer(runtime.get("authorizerConfiguration"))
    allowlist = list(
        (runtime.get("requestHeaderConfiguration") or {}).get("requestHeaderAllowlist") or [])
    declared = (runtime.get("environmentVariables") or {}).get("MERIDIAN_AGENTCORE_AUTH")
    if target.mode == JWT:
        found += _jwt_runtime_findings(subject, authorizer, allowlist, declared, target)
    else:
        found += _iam_runtime_findings(subject, authorizer, allowlist, declared)
    return found


def _jwt_runtime_findings(subject: str, authorizer: Mapping[str, Any], allowlist: list[str],
                          declared: str | None, target: Target) -> list[str]:
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


def _iam_runtime_findings(subject: str, authorizer: Mapping[str, Any], allowlist: list[str],
                          declared: str | None) -> list[str]:
    found = []
    if authorizer:
        found.append(f"{subject}: still has a JWT authorizer, so IAM callers are refused")
    if "Authorization" in allowlist:
        found.append(f"{subject}: still allowlists the Authorization header")
    if declared not in (None, IAM):
        found.append(f"{subject}: MERIDIAN_AGENTCORE_AUTH is {declared!r}, expected 'iam'")
    return found


# ------------------------------------------------------------------- the rules


def check_policies(names: list[str], target: Target) -> list[str]:
    """Findings for the Cedar rules the policy engine holds."""
    found = [f"Policy engine: the {name} rule is missing" for name in BASE_POLICIES
             if name not in names]
    wanted = target.mode == JWT and settings.uses_cedar_binding(target.design)
    if wanted and BINDING_POLICY not in names:
        found.append(f"Policy engine: the {BINDING_POLICY} rule is missing")
    if not wanted and BINDING_POLICY in names:
        found.append(f"Policy engine: the {BINDING_POLICY} rule is present, but this design "
                     "does not use it")
    return found


# ------------------------------------------------------------ the App Runner service


def check_service_environment(
    variables: Mapping[str, str], secrets: Mapping[str, str], target: Target
) -> list[str]:
    """Findings for the hosted backend's environment, planned or live."""
    if target.mode != JWT:
        return [f"Service: {key} is set, but the iam release has no sign-in"
                for key in SIGN_IN_VARIABLES if key in variables]
    found = []
    if variables.get("MERIDIAN_AGENTCORE_AUTH") != JWT:
        found.append("Service: MERIDIAN_AGENTCORE_AUTH is not 'jwt', so the backend would "
                     "sign AgentCore calls with IAM")
    expected = {
        "MERIDIAN_COGNITO_REGION": target.cognito.region,
        "MERIDIAN_COGNITO_USER_POOL_ID": target.cognito.pool_id,
        "MERIDIAN_COGNITO_APP_CLIENT_ID": target.cognito.client_id,
    }
    found += [f"Service: {key} is not the configured pool setting"
              for key, value in expected.items() if variables.get(key) != value]
    if variables.get("ENVIRONMENT") != "production":
        found.append("Service: ENVIRONMENT is not 'production', so the loopback path could open")
    found += [f"Service: {key} is still set; the jwt release has no shared token or loopback"
              for key in JWT_FORBIDDEN_VARIABLES if key in variables]
    if "MERIDIAN_API_TOKEN" in secrets:
        found.append("Service: MERIDIAN_API_TOKEN is still a secret reference; the jwt release "
                     "has no shared token")
    login, master = variables.get("AURORA_BACKEND_SECRET_ARN"), variables.get("AURORA_SECRET_ARN")
    if not login or login != master:
        found.append("Service: AURORA_SECRET_ARN is not the AURORA_BACKEND_SECRET_ARN, so the "
                     "backend would not run as the meridian_backend login")
    return found


# ----------------------------------------------------- identity stack and the proof


def identity_findings(outputs: Mapping[str, str], cognito: settings.CognitoSettings) -> list[str]:
    """Findings for the identity stack's outputs against the configured pool and client."""
    wanted = ("UserPoolId", "AppClientId", "HostedUiDomain", "Issuer")
    missing = [key for key in wanted if key not in outputs]
    found = []
    if missing:
        found.append(f"Identity stack: has no output {', '.join(missing)}; deploy it first")
    for key, expected in (("UserPoolId", cognito.pool_id), ("AppClientId", cognito.client_id)):
        if key in outputs and outputs[key] != expected:
            found.append(f"Identity stack: output {key} differs from meridian/.env; run "
                         "scripts/sync_cognito_env.py --write so the site and the backend "
                         "trust one client")
    return found


def check_backend_login_proof(path: Path, now: datetime | None = None) -> list[str]:
    """Findings for the receipt ``scripts/prove_backend_login.py`` writes after a passing run."""
    now = now or datetime.now(timezone.utc)
    command = f"run `{PROOF_COMMAND}` before the release"
    try:
        proof = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"Backend login proof: none recorded; {command}"]
    except (OSError, ValueError):
        return [f"Backend login proof: unreadable; {command}"]
    if not isinstance(proof, dict) or not proof.get("ok"):
        return [f"Backend login proof: the last run did not pass; {command}"]
    if proof.get("login") != "meridian_backend":
        return [f"Backend login proof: it ran as {proof.get('login')!r}, not meridian_backend; "
                f"{command}"]
    try:
        taken = datetime.fromisoformat(str(proof["at"]))
    except (KeyError, ValueError):
        return [f"Backend login proof: its timestamp is unreadable; {command}"]
    if now - taken > PROOF_MAX_AGE:
        return [f"Backend login proof: older than {PROOF_MAX_AGE.days} days; {command}"]
    return []


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


def _active_policy_names(control: Any, engine_id: str) -> list[str]:
    names: list[str] = []
    token = None
    while True:
        page = control.list_policies(
            policyEngineId=engine_id, **({"nextToken": token} if token else {}))
        names += [p["name"] for p in page.get("policies", []) if p.get("status") == "ACTIVE"]
        token = page.get("nextToken")
        if not token:
            return names


def read_state(control: Any, gateway_id: str, runtime_ids: Mapping[str, str]) -> HopState:
    """Describe the Gateway, both Runtimes and the policy engine's active rules."""
    gateway = control.get_gateway(gatewayIdentifier=gateway_id)
    runtimes = {name: control.get_agent_runtime(agentRuntimeId=runtime_id)
                for name, runtime_id in runtime_ids.items()}
    engine_arn = (gateway.get("policyEngineConfiguration") or {}).get("arn")
    names = _active_policy_names(control, engine_arn.rsplit("/", 1)[-1]) if engine_arn else []
    return HopState(gateway=gateway, runtimes=runtimes, policy_names=names)


def hop_findings(state: HopState, target: Target) -> list[str]:
    """Every hop's findings, Gateway first."""
    found = check_gateway(state.gateway, target)
    for name, runtime in state.runtimes.items():
        found += check_runtime(name, runtime, target)
    return found + check_policies(state.policy_names, target)


def refuse_if_any(findings: list[str], mode: str) -> None:
    """Stop with one line per finding.

    Raises:
        SystemExit: When ``findings`` is not empty.
    """
    if findings:
        lines = "\n".join(f"  {line}" for line in findings)
        raise SystemExit(f"refusing: {len(findings)} hops do not report {mode}:\n{lines}")
