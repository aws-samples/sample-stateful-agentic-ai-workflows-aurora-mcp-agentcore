"""
Custom MCP server: Meridian travel concierge tools.

Why this exists
===============

`awslabs.postgres-mcp-server` (used by Phase 2 alongside this file) is
a *generic* SQL transport - any agent can use it to talk to any
PostgreSQL database. It exposes `run_query`, `connect_to_database`, and
schema introspection. That's powerful, but it's also *only* SQL.

This server demonstrates the second half of the MCP story: custom,
domain-shaped tools that no public MCP server could provide because
they encode travel-specific business logic on top of Aurora.

Tools exposed (none of these are runnable as a single SQL query - they
require domain rules, secondary lookups, or external context):

    - compare_packages(package_ids[])     → side-by-side comparison block
    - seasonal_price_band(destination, m) → low/avg/high price for a month
    - region_inventory(region, trip_type) → operational availability roll-up
    - currency_convert(amount, from, to)  → indicative FX (deterministic table)
    - loyalty_balance(traveler_id, program) → points + tier readout

Phase 2 (`backend/routers/chat.py:mcp_search`) attaches BOTH this server
and `awslabs.postgres-mcp-server` so the trace visibly shows two MCP
servers feeding one agent turn. That answers the workshop question:
"what does a custom MCP get you that the public one can't?".

Memory tools intentionally do NOT live here. Traveler memory is
Phase 4's story (Aurora RLS + AgentCore Memory) - putting it here
would muddy the Phase 2 narrative.

Run stand-alone (e.g. for Claude Desktop):

    AURORA_CLUSTER_ARN=... AURORA_SECRET_ARN=... \
        python -m backend.mcp.concierge_server
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from backend.db.rds_data_client import get_rds_data_client

logger = logging.getLogger(__name__)

mcp = FastMCP("meridian-concierge")

# Static FX table - in a real deployment this would call a treasury API
# or pull from a daily-refreshed Aurora table. The point of the demo is
# the *shape* of the tool, not the rate accuracy.
_FX_PER_USD: Dict[str, float] = {
    "USD": 1.0,
    "EUR": 0.92,
    "GBP": 0.79,
    "JPY": 156.4,
    "AUD": 1.52,
    "CAD": 1.37,
    "INR": 83.5,
    "BRL": 5.45,
    "CHF": 0.88,
    "MXN": 17.1,
}

# Loyalty tier thresholds (points → tier label). Real systems plug into
# Marriott Bonvoy / United MileagePlus APIs; the demo returns the same
# shape so the agent can reason about tier without us shipping creds.
_LOYALTY_TIERS = [
    (0, "Member"),
    (10_000, "Silver"),
    (50_000, "Gold"),
    (75_000, "Platinum"),
    (125_000, "Titanium"),
]


def _db():
    return get_rds_data_client()


# --------------------------------------------------------------------------- #
# compare_packages: side-by-side comparison rows (price, durations, highlights)
# --------------------------------------------------------------------------- #


@mcp.tool()
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
    rows = await _db().execute(sql, tuple(package_ids))
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


# --------------------------------------------------------------------------- #
# seasonal_price_band: low/avg/high for a destination + month
# --------------------------------------------------------------------------- #


@mcp.tool()
async def seasonal_price_band(destination: str, month: int) -> Dict[str, Any]:
    """Return seasonal price band (low / median / high) for a destination.

    Args:
        destination: city or region string (matched ILIKE).
        month: 1-12 calendar month.

    The band is computed from current trip_packages prices for the
    destination, modulated by a deterministic seasonal multiplier so the
    same query returns the same answer across demo runs.
    """
    if not (1 <= month <= 12):
        return {"error": "month must be between 1 and 12"}
    sql = """
        SELECT MIN(price_per_person)::float AS min_price,
               AVG(price_per_person)::float AS avg_price,
               MAX(price_per_person)::float AS max_price,
               COUNT(*) AS sample_size
        FROM trip_packages
        WHERE destination ILIKE %s OR region ILIKE %s
    """
    pat = f"%{destination}%"
    row = await _db().execute_one(sql, (pat, pat))
    if not row or not row.get("sample_size"):
        return {"destination": destination, "month": month, "sample_size": 0}

    # Northern-hemisphere bias for the demo: summer (Jun-Aug) +18%,
    # shoulder (Apr-May, Sep-Oct) +0%, winter (Nov-Mar) -8%.
    if month in (6, 7, 8):
        seasonal = 1.18
        season_label = "peak"
    elif month in (4, 5, 9, 10):
        seasonal = 1.0
        season_label = "shoulder"
    else:
        seasonal = 0.92
        season_label = "off-season"

    return {
        "destination": destination,
        "month": month,
        "season": season_label,
        "low": round(row["min_price"] * seasonal, 2),
        "median": round(row["avg_price"] * seasonal, 2),
        "high": round(row["max_price"] * seasonal, 2),
        "sample_size": int(row["sample_size"]),
    }


# --------------------------------------------------------------------------- #
# region_inventory: operational availability roll-up by region + trip type
# --------------------------------------------------------------------------- #


@mcp.tool()
async def region_inventory(
    region: str,
    trip_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Return how many distinct trip packages we sell in a region, with
    aggregate availability across all duration options.

    Args:
        region: region name (e.g. 'Europe', 'Asia', 'South America').
        trip_type: optional category filter ('City Breaks', 'Wellness & Luxury' ...).
    """
    sql = """
        SELECT package_id, name, trip_type, availability
        FROM trip_packages
        WHERE region ILIKE %s
    """
    params: List[Any] = [f"%{region}%"]
    if trip_type:
        sql += " AND trip_type = %s"
        params.append(trip_type)
    rows = await _db().execute(sql, tuple(params))

    total_slots = 0
    by_trip_type: Dict[str, int] = {}
    for r in rows:
        tt = r.get("trip_type") or "Other"
        by_trip_type[tt] = by_trip_type.get(tt, 0) + 1
        avail = r.get("availability") or {}
        if isinstance(avail, dict):
            total_slots += sum(int(v or 0) for v in avail.values())

    return {
        "region": region,
        "trip_type": trip_type,
        "package_count": len(rows),
        "total_departure_slots": total_slots,
        "package_count_by_trip_type": by_trip_type,
    }


# --------------------------------------------------------------------------- #
# currency_convert: indicative FX (deterministic table)
# --------------------------------------------------------------------------- #


@mcp.tool()
async def currency_convert(amount: float, from_ccy: str, to_ccy: str) -> Dict[str, Any]:
    """Convert an amount between supported currencies using indicative rates.

    Args:
        amount: numeric amount in `from_ccy`.
        from_ccy: ISO 4217 code (e.g. 'USD', 'EUR').
        to_ccy: ISO 4217 code.

    Returns the converted amount + the rate used. Indicative only -
    not for settlement.
    """
    src = (from_ccy or "").upper()
    dst = (to_ccy or "").upper()
    if src not in _FX_PER_USD or dst not in _FX_PER_USD:
        return {
            "error": "unsupported currency",
            "supported": sorted(_FX_PER_USD.keys()),
        }
    # Quote everything via USD so we don't store an N*N rate matrix.
    usd = amount / _FX_PER_USD[src]
    converted = usd * _FX_PER_USD[dst]
    rate = _FX_PER_USD[dst] / _FX_PER_USD[src]
    return {
        "from": src,
        "to": dst,
        "amount": amount,
        "converted": round(converted, 2),
        "rate": round(rate, 6),
        "note": "indicative rate, not for settlement",
    }


# --------------------------------------------------------------------------- #
# loyalty_balance: points + tier for a traveler / program pair
# --------------------------------------------------------------------------- #


@mcp.tool()
async def loyalty_balance(traveler_id: str, program: str) -> Dict[str, Any]:
    """Return loyalty points balance + tier for a traveler.

    Args:
        traveler_id: e.g. 'trv_meridian_demo'.
        program: e.g. 'Marriott Bonvoy', 'United MileagePlus'.

    The demo traveler is read from Aurora ``traveler_profiles.loyalty_programs``.
    Unknown travelers use a stable SHA-256-derived fallback so repeat runs never
    change with Python's process-level hash seed.
    """
    row = await _db().execute_one(
        """
        SELECT loyalty_programs
        FROM traveler_profiles
        WHERE traveler_id = %s
        """,
        (traveler_id,),
    )
    programs = (row or {}).get("loyalty_programs") or {}
    requested = (program or "").lower()
    if isinstance(programs, dict):
        for value in programs.values():
            if not isinstance(value, dict):
                continue
            configured_program = str(value.get("program") or "")
            if (
                requested in configured_program.lower()
                or configured_program.lower() in requested
            ):
                return {
                    "traveler_id": traveler_id,
                    "program": configured_program,
                    "member_id": value.get("member_id"),
                    "points_balance": int(value.get("points_balance") or 0),
                    "tier": value.get("tier") or "Member",
                    "next_tier_threshold": None,
                    "points_to_next_tier": 0,
                    "source": "traveler_profiles.loyalty_programs",
                }

    digest = hashlib.sha256(f"{traveler_id}:{program}".encode("utf-8")).digest()
    base = int.from_bytes(digest[:8], "big") % 200_000
    tier_label = _LOYALTY_TIERS[0][1]
    next_tier_at: Optional[int] = None
    for i, (threshold, label) in enumerate(_LOYALTY_TIERS):
        if base >= threshold:
            tier_label = label
            if i + 1 < len(_LOYALTY_TIERS):
                next_tier_at = _LOYALTY_TIERS[i + 1][0]
    return {
        "traveler_id": traveler_id,
        "program": program,
        "points_balance": base,
        "tier": tier_label,
        "next_tier_threshold": next_tier_at,
        "points_to_next_tier": (next_tier_at - base) if next_tier_at else 0,
        "source": "stable indicative fallback",
    }


# --------------------------------------------------------------------------- #
# Stand-alone entry point so Claude Desktop / external clients can attach.
# --------------------------------------------------------------------------- #


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("MCP_CONCIERGE_LOG_LEVEL", "WARNING"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger.info("starting meridian-concierge MCP server (stdio)")
    mcp.run()
