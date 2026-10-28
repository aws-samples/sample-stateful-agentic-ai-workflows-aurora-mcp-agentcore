"""Catalog tools that run in the backend and, over MCP, on meridian-concierge.

``search_trips`` is the one catalog search for Phases 1 and 2. Every value the
traveler's words produce is a bound RDS Data API parameter; the statement text is
fixed by the code in this file, so no value can change what the statement means.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.activity import ActivityEntry
from backend.db.rds_data_client import get_rds_data_client
from backend.retrieval import hybrid
from backend.search_utils import PACKAGE_COLUMNS, numbered_sql, results_to_packages

MAX_LIMIT = 20
MAX_QUERY_CHARS = 500
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


def _bounded(limit: int) -> int:
    """``limit`` clamped to 1 through ``MAX_LIMIT``, as every catalog tool reads it."""
    return max(1, min(int(limit), MAX_LIMIT))


async def search_trips(
    trip_type: Optional[str] = None,
    max_price_per_person: Optional[float] = None,
    limit: int = 5,
    text: Optional[str] = None,
) -> Dict[str, Any]:
    """Search the trip catalog by type, price ceiling and text, cheapest first, ties by package id.

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
    params.append(_bounded(limit))
    statement = " ".join(
        f"SELECT {PACKAGE_COLUMNS} FROM trip_packages{where} "
        "ORDER BY price_per_person ASC, package_id ASC LIMIT %s".split()
    )
    rows = await get_rds_data_client().execute(statement, tuple(params))
    return {
        "packages": results_to_packages(rows),
        "sql": numbered_sql(statement),
        "parameters": params,
    }


# Bounds the Gateway tool schema declares for these arguments; the code clamps to the same range.
ARGUMENT_BOUNDS: Dict[tuple, tuple] = {
    ("search_trips", "limit"): (1, MAX_LIMIT),
    ("hybrid_trip_search", "limit"): (1, MAX_LIMIT),
}
# Longest text the schema declares for a string argument; the code refuses anything longer.
ARGUMENT_MAX_LENGTHS: Dict[tuple, int] = {
    ("hybrid_trip_search", "query"): MAX_QUERY_CHARS,
}


def _refusal(message: str) -> Dict[str, Any]:
    """The answer for unusable arguments, as the MeridianCatalog Lambda gives it."""
    return {"error": message}

# Static FX table: the point of the tool is its shape, not the rate accuracy.
FX_PER_USD: Dict[str, float] = {
    "USD": 1.0, "EUR": 0.92, "GBP": 0.79, "JPY": 156.4, "AUD": 1.52,
    "CAD": 1.37, "INR": 83.5, "BRL": 5.45, "CHF": 0.88, "MXN": 17.1,
}
# The answer when a currency is not in the table. It is a fact, not a failure: the Runtime and
# the Phase 4 audit record a call that gets it as allowed (owner decision 14).
UNSUPPORTED_CURRENCY = "unsupported_currency"


async def compare_packages(package_ids: List[str]) -> List[Dict[str, Any]]:
    """Return aligned comparison rows for up to 4 trip packages.

    Args:
        package_ids: list of package_id strings (e.g. ['CTY-002', 'CTY-019']).

    Each row includes pricing, duration list, and the top 3 highlights so an
    agent can answer "compare these" without writing comparison SQL itself.
    """
    if not package_ids:
        return []
    package_ids = package_ids[:4]
    placeholders = ",".join(["%s"] * len(package_ids))
    sql = f"""
        SELECT package_id, name, destination, region, operator,
               trip_type, price_per_person, durations, highlights
        FROM trip_packages
        WHERE package_id IN ({placeholders})
    """
    rows = await get_rds_data_client().execute(sql, tuple(package_ids))
    return [
        {
            "package_id": r["package_id"],
            "name": r["name"],
            "destination": r.get("destination"),
            "region": r.get("region"),
            "operator": r.get("operator"),
            "trip_type": r.get("trip_type"),
            "price_per_person": float(r["price_per_person"]),
            "durations": r.get("durations") or [],
            "top_highlights": (r.get("highlights") or [])[:3],
        }
        for r in rows
    ]


async def price_range(destination: str) -> Dict[str, Any]:
    """Return the price range (low / average / high) for a destination.

    Args:
        destination: city or region string (matched ILIKE against the
            destination or region columns).

    Computed directly from the current `trip_packages` prices for the
    destination. `trip_packages` stores one price per package, not a
    price per departure date or per month, so there is no seasonal
    price to compute. This is the real, whole-catalog range, and the
    payload says so explicitly rather than implying a seasonal figure.
    """
    sql = """
        SELECT MIN(price_per_person)::float AS min_price,
               AVG(price_per_person)::float AS avg_price,
               MAX(price_per_person)::float AS max_price,
               COUNT(*) AS sample_size
        FROM trip_packages
        WHERE destination ILIKE %s OR region ILIKE %s
    """
    destination = (destination or "").strip()
    if not destination:
        return _refusal("destination is required")
    pattern = f"%{destination}%"
    row = await get_rds_data_client().execute_one(sql, (pattern, pattern))
    if not row or not row.get("sample_size"):
        return {"destination": destination, "sample_size": 0}
    return {
        "destination": destination,
        "low": round(row["min_price"], 2),
        "average": round(row["avg_price"], 2),
        "high": round(row["max_price"], 2),
        "sample_size": int(row["sample_size"]),
        "note": (
            "Not seasonal: the catalog holds one price per package, not "
            "per-month or per-season prices."
        ),
    }


async def region_inventory(region: str, trip_type: Optional[str] = None) -> Dict[str, Any]:
    """Return how many distinct trip packages we sell in a region, with
    aggregate availability across all duration options.

    Args:
        region: region name (e.g. 'Europe', 'Asia', 'South America').
        trip_type: optional category filter ('City Breaks', 'Wellness & Luxury' ...).
    """
    region = (region or "").strip()
    if not region:
        return _refusal("region is required")
    sql = """
        SELECT package_id, name, trip_type, package_places_left(package_id) AS availability
        FROM trip_packages
        WHERE region ILIKE %s
    """
    params: List[Any] = [f"%{region}%"]
    if trip_type:
        sql += " AND trip_type = %s"
        params.append(trip_type)
    rows = await get_rds_data_client().execute(sql, tuple(params))

    total_slots = 0
    by_trip_type: Dict[str, int] = {}
    for row in rows:
        kind = row.get("trip_type") or "Other"
        by_trip_type[kind] = by_trip_type.get(kind, 0) + 1
        availability = row.get("availability") or {}
        if isinstance(availability, dict):
            total_slots += sum(int(v or 0) for v in availability.values())
    return {
        "region": region,
        "trip_type": trip_type,
        "package_count": len(rows),
        "total_departure_slots": total_slots,
        "package_count_by_trip_type": by_trip_type,
    }


async def currency_convert(amount: float, from_ccy: str, to_ccy: str) -> Dict[str, Any]:
    """Convert an amount between supported currencies using indicative rates.

    Args:
        amount: numeric amount in `from_ccy`.
        from_ccy: ISO 4217 code (e.g. 'USD', 'EUR').
        to_ccy: ISO 4217 code.

    Returns the converted amount + the rate used. Indicative only,
    not for settlement. A currency outside the table is answered with
    ``{"error": "unsupported_currency", "supported": [...]}``.
    """
    source, target = (from_ccy or "").upper(), (to_ccy or "").upper()
    if source not in FX_PER_USD or target not in FX_PER_USD:
        return {"error": UNSUPPORTED_CURRENCY, "supported": sorted(FX_PER_USD)}
    usd = amount / FX_PER_USD[source]
    return {
        "from": source,
        "to": target,
        "amount": amount,
        "converted": round(usd * FX_PER_USD[target], 2),
        "rate": round(FX_PER_USD[target] / FX_PER_USD[source], 6),
        "note": "indicative rate, not for settlement",
    }


def _query_refusal(query: Any) -> Optional[Dict[str, Any]]:
    """The refusal for a blank or over-long query, or None; the length is that of the raw text."""
    if not isinstance(query, str) or not query.strip():
        return _refusal("query is required")
    if len(query) > MAX_QUERY_CHARS:
        return _refusal(f"query is longer than {MAX_QUERY_CHARS} characters")
    return None


async def hybrid_trip_search_with_spans(
    query: str, limit: int = 5
) -> tuple[Dict[str, Any], List[ActivityEntry]]:
    """Run hybrid search once and return the tool result with the spans that describe it.

    A blank query or one over ``MAX_QUERY_CHARS`` is refused with ``{"error": <message>}`` and
    no spans, as the MeridianCatalog Lambda refuses it. The counts come from
    ``hybrid.retrieval_rows`` itself (Reconciliation 1), never from span text, and each package
    is ``hybrid.package_payload``'s shape.
    """
    refusal = _query_refusal(query)
    if refusal:
        return refusal, []
    rows, spans, counts = await hybrid.retrieval_rows(query.strip(), _bounded(limit))
    result = {
        "packages": [hybrid.package_payload(row) for row in rows],
        "candidates": {key: counts[key] for key in ("semantic", "lexical", "merged")},
        "reranked": counts["reranked"],
    }
    return result, spans


async def hybrid_trip_search(query: str, limit: int = 5) -> Dict[str, Any]:
    """Search trip packages by meaning and by words, then rerank them.

    Args:
        query: natural-language trip request. Name the destination and the qualities
            wanted. A price ceiling in the query ("under $2,500") is applied, and
            sold-out packages are left out. At most 500 characters.
        limit: maximum packages to return, 1 to 20.

    Embeds the query with Cohere Embed v4, takes pgvector and full-text candidates
    from Amazon Aurora, merges them by package, then reranks with Cohere Rerank 3.5.
    `reranked` is false when the reranker was unavailable and candidate order is shown.
    """
    result, _ = await hybrid_trip_search_with_spans(query, limit)
    return result


CATALOG_FUNCTIONS: Dict[str, Callable] = {
    "search_trips": search_trips,
    "compare_packages": compare_packages,
    "price_range": price_range,
    "region_inventory": region_inventory,
    "currency_convert": currency_convert,
    "hybrid_trip_search": hybrid_trip_search,
}
