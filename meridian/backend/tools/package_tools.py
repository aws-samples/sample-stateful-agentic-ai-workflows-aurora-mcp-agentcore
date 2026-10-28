"""get_package_details for Phase 3, in process.

The MeridianHolds Lambda serves the same tool to Phase 4 through the Gateway. The SQL and the
result shape, the ``unknown_package`` answer included, are pinned to it by
tests/test_package_tools.py.
"""

from typing import Any, Dict

from backend.db.rds_data_client import get_rds_data_client

PACKAGE_SQL = (
    "SELECT package_id, name, operator, destination, region, price_per_person, "
    "durations, package_places_left(package_id) AS availability, highlights FROM trip_packages "
    "WHERE package_id = %s"
)
# The answer for an id the catalog does not hold. It is a fact, not a failure: the Runtime and
# the Phase 4 audit record a call that gets it as allowed (owner decision 14).
UNKNOWN_PACKAGE = "unknown_package"


async def get_package_details(packageId: str) -> Dict[str, Any]:
    """Read one Meridian trip package with its live durations, places left and highlights.

    Args:
        packageId: Catalog package id, for example CTY-002.
    """
    package_id = str(packageId).strip()
    if not package_id:
        return {"error": UNKNOWN_PACKAGE, "package_id": package_id}
    row = await get_rds_data_client().execute_one(PACKAGE_SQL, (package_id,))
    if not row:
        return {"error": UNKNOWN_PACKAGE, "package_id": package_id}
    availability = row.get("availability") or {}
    open_slots = ", ".join(f"{k}: {v} places left" for k, v in availability.items())
    return {
        "package": {**row, "price_per_person": float(row["price_per_person"])},
        "summary": f"{row.get('name')}, {open_slots or 'no published durations'}",
    }
