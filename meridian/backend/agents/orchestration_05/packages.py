"""Reading catalog packages out of workflow state.

Both the hold node and the intent that precedes it have to agree on which
package and which duration a hold is for. They read it through here so the
fingerprinted terms and the executed terms cannot drift apart.
"""

from typing import Any, Dict


def package_to_dict(package: Any) -> Dict[str, Any]:
    """Normalize API/domain models before LangGraph checkpoints serialize them."""
    if isinstance(package, dict):
        return dict(package)
    if hasattr(package, "model_dump"):
        return dict(package.model_dump(mode="json"))
    if hasattr(package, "__dict__"):
        return {
            key: value
            for key, value in vars(package).items()
            if not key.startswith("_")
        }
    raise TypeError(f"Unsupported workflow package type: {type(package).__name__}")


def first_available_duration(package: Dict[str, Any]) -> str:
    """Pick a duration that still has inventory, falling back to the first."""
    availability = package.get("availability") or {}
    if isinstance(availability, dict):
        for duration, seats in availability.items():
            try:
                if int(seats) > 0:
                    return str(duration)
            except (TypeError, ValueError):
                continue
    sizes = package.get("available_sizes") or package.get("durations") or []
    if isinstance(sizes, list) and sizes:
        return str(sizes[0])
    return "7 nights"


def top_ranked_package(packages: Any) -> Dict[str, Any] | None:
    """Return the first ranked package that carries an identifier.

    Args:
        packages: The ranked list from workflow state, in either dict or
            domain-model form.

    Returns:
        The package as a dict, or None when nothing is holdable.
    """
    for package in packages or []:
        candidate = package_to_dict(package)
        if candidate.get("product_id") or candidate.get("package_id"):
            return candidate
    return None
