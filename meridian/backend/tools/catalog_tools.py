"""Catalog tools that run in the backend and, over MCP, on meridian-concierge.

``search_trips`` is the one catalog search for Phases 1 and 2. Every value the
traveler's words produce is a bound RDS Data API parameter; the statement text is
fixed by the code in this file, so no value can change what the statement means.
"""

from typing import Any, Dict, List, Optional, Tuple

from backend.db.rds_data_client import get_rds_data_client
from backend.search_utils import PACKAGE_COLUMNS, numbered_sql, results_to_packages

MAX_LIMIT = 20
TEXT_COLUMNS = ("name", "description", "operator", "destination", "trip_type")


def _escape_like(text: str) -> str:
    """The text with LIKE's own wildcards made literal, so ``100%`` means 100 and a percent sign.

    The backslash is PostgreSQL's default LIKE escape, so the statement has no ESCAPE clause:
    the RDS Data API reads the literal ``'\\'`` as an escaped quote and binds no parameter
    after it.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _where(
    trip_type: Optional[str], max_price: Optional[float], text: Optional[str]
) -> Tuple[str, List[Any]]:
    """The WHERE clause and its parameters, in placeholder order."""
    clauses: List[str] = []
    params: List[Any] = []
    if trip_type:
        clauses.append("trip_type = %s")
        params.append(trip_type)
    if max_price is not None:
        clauses.append("price_per_person <= %s")
        params.append(max_price)
    if text:
        like = " OR ".join(f"{column} ILIKE %s" for column in TEXT_COLUMNS)
        clauses.append(f"({like})")
        params.extend([f"%{_escape_like(text)}%"] * len(TEXT_COLUMNS))
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


async def search_trips(
    trip_type: Optional[str] = None,
    max_price_per_person: Optional[float] = None,
    limit: int = 5,
    text: Optional[str] = None,
) -> Dict[str, Any]:
    """Search the trip catalog by type, price ceiling and text, cheapest first.

    Args:
        trip_type: Exact trip type, for example ``City Breaks``.
        max_price_per_person: Highest price per traveler to include.
        limit: Number of trips to return, clamped to 1 through 20.
        text: Words to look for in a trip's name, description, operator, destination
            or trip type. The Phase 1 and 2 fallback when no trip type matched.

    Returns:
        ``{"packages": [...], "sql": <statement with $1.. placeholders>,
        "parameters": [<the bound values, in order>]}``.
    """
    where, params = _where(trip_type, max_price_per_person, text)
    params.append(max(1, min(int(limit), MAX_LIMIT)))
    statement = " ".join(
        f"SELECT {PACKAGE_COLUMNS} FROM trip_packages{where} "
        "ORDER BY price_per_person ASC LIMIT %s".split()
    )
    rows = await get_rds_data_client().execute(statement, tuple(params))
    return {
        "packages": results_to_packages(rows),
        "sql": numbered_sql(statement),
        "parameters": params,
    }
