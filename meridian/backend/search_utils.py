"""
Shared keyword search utilities for trip_packages (Phases 1 & 2).
"""

import re
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

from backend.config import config

PACKAGE_COLUMNS = """package_id, name, trip_type, destination, region,
                     price_per_person, operator, description, image_url,
                     durations, availability, highlights"""


@dataclass
class SearchParams:
    query: str
    query_lower: str
    price_filter: Optional[float]
    matched_trip_type: Optional[str]
    search_pattern: str


def parse_search_query(query: str) -> SearchParams:
    query_lower = query.lower()
    price_filter = None
    price_match = re.search(
        r"(?:under|below|less than|<)\s*\$?(\d[\d,]*(?:\.\d{2})?)",
        query_lower,
    )
    if price_match:
        price_filter = float(price_match.group(1).replace(",", ""))

    matched_trip_type = None
    for keyword, trip_type in config.search.category_keywords.items():
        if keyword in query_lower and trip_type:
            matched_trip_type = trip_type
            break

    return SearchParams(
        query=query,
        query_lower=query_lower,
        price_filter=price_filter,
        matched_trip_type=matched_trip_type,
        search_pattern=f"%{query}%",
    )


async def execute_keyword_search(
    db: Any,
    params: SearchParams,
    limit: int = 5,
) -> Tuple[List[Dict], str, str]:
    results: List[Dict] = []
    display_sql = ""
    search_title = ""

    if params.matched_trip_type:
        if params.price_filter:
            sql = f"""
                SELECT {PACKAGE_COLUMNS}
                FROM trip_packages
                WHERE trip_type = %s AND price_per_person <= %s
                ORDER BY price_per_person ASC
                LIMIT %s
            """
            display_sql = (
                f"SELECT … FROM trip_packages WHERE trip_type = '{params.matched_trip_type}' "
                f"AND price_per_person <= {params.price_filter} LIMIT {limit}"
            )
            results = await db.execute(sql, (params.matched_trip_type, params.price_filter, limit))
        else:
            sql = f"""
                SELECT {PACKAGE_COLUMNS}
                FROM trip_packages
                WHERE trip_type = %s
                ORDER BY price_per_person ASC
                LIMIT %s
            """
            display_sql = f"SELECT … FROM trip_packages WHERE trip_type = '{params.matched_trip_type}' LIMIT {limit}"
            results = await db.execute(sql, (params.matched_trip_type, limit))
        search_title = f"Trip type filter: {params.matched_trip_type}"

    else:
        if params.price_filter:
            sql = f"""
                SELECT {PACKAGE_COLUMNS}
                FROM trip_packages
                WHERE (name ILIKE %s OR description ILIKE %s OR operator ILIKE %s
                       OR destination ILIKE %s OR trip_type ILIKE %s)
                  AND price_per_person <= %s
                ORDER BY price_per_person ASC
                LIMIT %s
            """
            display_sql = f"SELECT … FROM trip_packages WHERE text ILIKE '%{params.query}%' AND price <= {params.price_filter}"
            results = await db.execute(
                sql,
                (
                    params.search_pattern,
                    params.search_pattern,
                    params.search_pattern,
                    params.search_pattern,
                    params.search_pattern,
                    params.price_filter,
                    limit,
                ),
            )
        else:
            sql = f"""
                SELECT {PACKAGE_COLUMNS}
                FROM trip_packages
                WHERE name ILIKE %s OR description ILIKE %s OR operator ILIKE %s
                   OR destination ILIKE %s OR trip_type ILIKE %s
                ORDER BY price_per_person ASC
                LIMIT %s
            """
            display_sql = f"SELECT … FROM trip_packages WHERE text ILIKE '%{params.query}%'"
            results = await db.execute(
                sql,
                (
                    params.search_pattern,
                    params.search_pattern,
                    params.search_pattern,
                    params.search_pattern,
                    params.search_pattern,
                    limit,
                ),
            )
        search_title = f"Filter search: {params.query}"

    return results, display_sql, search_title


def build_search_sql(params: SearchParams, limit: int = 5) -> Tuple[str, str, str]:
    safe = params.query.replace("'", "''")
    pattern = f"%{safe}%"

    if params.matched_trip_type:
        if params.price_filter:
            sql = f"""
                SELECT {PACKAGE_COLUMNS}
                FROM trip_packages
                WHERE trip_type = '{params.matched_trip_type}' AND price_per_person <= {params.price_filter}
                ORDER BY price_per_person ASC
                LIMIT {limit}
            """
            display_sql = f"trip_type = '{params.matched_trip_type}' AND price <= {params.price_filter}"
        else:
            sql = f"""
                SELECT {PACKAGE_COLUMNS}
                FROM trip_packages
                WHERE trip_type = '{params.matched_trip_type}'
                ORDER BY price_per_person ASC
                LIMIT {limit}
            """
            display_sql = f"trip_type = '{params.matched_trip_type}'"
        title = f"Trip type filter: {params.matched_trip_type}"
    elif params.price_filter:
        sql = f"""
            SELECT {PACKAGE_COLUMNS}
            FROM trip_packages
            WHERE (name ILIKE '{pattern}' OR description ILIKE '{pattern}'
                   OR operator ILIKE '{pattern}' OR destination ILIKE '{pattern}')
              AND price_per_person <= {params.price_filter}
            ORDER BY price_per_person ASC
            LIMIT {limit}
        """
        display_sql = f"ILIKE '{pattern}' AND price <= {params.price_filter}"
        title = f"Filter search: {params.query}"
    else:
        sql = f"""
            SELECT {PACKAGE_COLUMNS}
            FROM trip_packages
            WHERE name ILIKE '{pattern}' OR description ILIKE '{pattern}'
               OR operator ILIKE '{pattern}' OR destination ILIKE '{pattern}'
            ORDER BY price_per_person ASC
            LIMIT {limit}
        """
        display_sql = f"ILIKE '{pattern}'"
        title = f"Filter search: {params.query}"

    return sql.strip(), display_sql, title


def results_to_packages(results: List[Dict]) -> List[Dict]:
    return [
        {
            "package_id": row["package_id"],
            "name": row["name"],
            "trip_type": row["trip_type"],
            "destination": row.get("destination") or "",
            "region": row.get("region") or "",
            "price_per_person": float(row["price_per_person"]),
            "operator": row.get("operator") or "",
            "description": row.get("description") or "",
            "image_url": row.get("image_url") or "",
            "durations": row.get("durations"),
            "availability": row.get("availability"),
            "highlights": row.get("highlights"),
        }
        for row in results
    ]


# Back-compat alias used during migration
results_to_products = results_to_packages
