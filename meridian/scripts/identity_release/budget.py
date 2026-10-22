"""Refuse a hosted release when its hops disagree on the unsaved-trip budget."""

from collections.abc import Mapping

from backend.agents.budget import DEFAULT_ENV, default_budget_cents
from scripts.identity_release import lambda_release


def budget_findings(environments: Mapping[str, object]) -> list[str]:
    """Compare effective defaults, including the built-in default when unset.

    The backend, both Runtimes and the holds Lambda must agree. Invalid values
    produce findings without echoing environment contents.
    """
    found = []
    ceilings = {}
    for name, env in environments.items():
        if not isinstance(env, Mapping):
            found.append(f"{name}: cannot read the fallback budget environment")
            continue
        try:
            ceilings[name] = default_budget_cents(env)
        except ValueError:
            found.append(f"{name}: {DEFAULT_ENV} is not a valid fallback budget")
    if len(set(ceilings.values())) > 1:
        found.append(
            "Fallback budget mismatch between the hosted backend, Runtimes and holds Lambda; "
            "render and deploy AgentCore with the same " + DEFAULT_ENV + " before publishing"
        )
    return found


def release_findings(control, lambda_client, gateway_id, runtimes, backend) -> list[str]:
    """Read the holds target's actual configuration alongside the read-back Runtimes."""
    arn = lambda_release.holds_function_arn(control, gateway_id)
    configuration = lambda_client.get_function_configuration(FunctionName=arn)
    environment = configuration.get("Environment", {})
    holds = None if environment.get("Error") else environment.get("Variables", {})
    return budget_findings({
        "Hosted backend": backend,
        **{name: runtime.get("environmentVariables", {}) for name, runtime in runtimes.items()},
        "Holds Lambda": holds,
    })
