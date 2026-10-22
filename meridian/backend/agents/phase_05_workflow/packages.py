"""Reading catalog packages out of workflow state.

Both the hold node and the intent that precedes it have to agree on which
package and which duration a hold is for. They read it through here so the
fingerprinted terms and the executed terms cannot drift apart.
"""

from typing import Any, Dict, List, Optional

# The recovery offers this many alternatives; the availability step checks the same ones.
REVIEW_SHORTLIST = 3


def package_to_dict(package: Any) -> Dict[str, Any]:
    """Normalize API/domain models to plain dicts before a Strands snapshot saves them."""
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


def _counted_places(availability: Any) -> Dict[str, int]:
    """The durations whose places the catalog counts, with the count."""
    counted: Dict[str, int] = {}
    if isinstance(availability, dict):
        for duration, places in availability.items():
            try:
                counted[str(duration)] = int(places)
            except (TypeError, ValueError):
                continue
    return counted


def duration_for_party(package: Dict[str, Any], quantity: int) -> Optional[str]:
    """Pick the first duration with enough places for the whole party.

    Args:
        package: A ranked package, with ``availability`` as read before the hold.
        quantity: The party size the hold is for.

    Returns:
        The first counted duration with at least ``quantity`` places. None when the
        package counts places and no duration seats the party, so no hold is
        prepared that the write would refuse. When nothing is counted, the first
        listed size: the write still checks the places.
    """
    counted = _counted_places(package.get("availability"))
    if counted:
        return next((d for d, places in counted.items() if places >= quantity), None)
    sizes = package.get("available_sizes") or package.get("durations") or []
    if isinstance(sizes, list) and sizes:
        return str(sizes[0])
    return "7 nights"


def ranked_package_id(package: Dict[str, Any]) -> str:
    """The catalog id of a ranked package, in either the API or the catalog shape."""
    return str(package.get("product_id") or package.get("package_id") or "")


def ranked_packages(packages: Any) -> List[Dict[str, Any]]:
    """The ranked packages that carry an identifier, in rank order, as dicts.

    Args:
        packages: The ranked list from workflow state, in either dict or
            domain-model form.
    """
    candidates = (package_to_dict(package) for package in packages or [])
    return [candidate for candidate in candidates if ranked_package_id(candidate)]


def top_ranked_package(packages: Any) -> Dict[str, Any] | None:
    """Return the first ranked package that carries an identifier, or None."""
    ranked = ranked_packages(packages)
    return ranked[0] if ranked else None


def review_shortlist(packages: Any) -> List[str]:
    """The ids the traveler reviews and may choose from, best first."""
    return [ranked_package_id(package) for package in ranked_packages(packages)][:REVIEW_SHORTLIST]


def find_ranked_package(packages: Any, package_id: str) -> Dict[str, Any] | None:
    """The ranked package with this id, or None."""
    matches = (p for p in ranked_packages(packages) if ranked_package_id(p) == package_id)
    return next(matches, None)
