"""
Custom MCP server: Meridian travel concierge tools.

`awslabs.postgres-mcp-server` is a generic SQL transport. This server shows the
second half of the MCP story: custom, domain-shaped tools that encode travel
business logic on top of Amazon Aurora.

Tools exposed:

    - search_trips(trip_type, max_price_per_person, limit, text)  parameterized filter
    - compare_packages(package_ids[])                       side-by-side comparison block
    - price_range(destination)                              low/avg/high price for a destination
    - region_inventory(region, trip_type)                   availability roll-up
    - currency_convert(amount, from_ccy, to_ccy)            indicative FX (deterministic table)
    - loyalty_balance(travelerId, program)                  points + tier readout

The catalog tools live in `backend/tools/catalog_tools.py`; the same functions are
registered here for Phases 2 and 3, and the MeridianCatalog Gateway Lambda carries a port
of them (tests/test_catalog_lambda_parity.py keeps the two in step). `loyalty_balance`
stays here for Phases 2 and 3; Phase 4 reaches it through the MeridianHolds Gateway target.

Memory tools intentionally do NOT live here. Traveler memory is Phase 4's story.

Run stand-alone (e.g. for Claude Desktop):

    AURORA_CLUSTER_ARN=... AURORA_SECRET_ARN=... \
        python -m backend.mcp.concierge_server
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from mcp.server.fastmcp import FastMCP

from backend.agentcore.identity import get_agentcore_identity
from backend.authorization import TravelerAuthorizationError
from backend.db.rds_data_client import get_rds_data_client
from backend.security.redaction import install_masking, install_record_factory

install_record_factory()
from backend.tools import catalog_tools

logger = logging.getLogger(__name__)

mcp = FastMCP("meridian-concierge")

REFUSAL_MESSAGE = (
    "This traveler is not authorized for this request. Asking again will not change the answer."
)

# The shared functions are registered as they are, so the schema FastMCP derives and the
# schema the Gateway target publishes come from one signature.
search_trips = mcp.tool()(catalog_tools.search_trips)
compare_packages = mcp.tool()(catalog_tools.compare_packages)
price_range = mcp.tool()(catalog_tools.price_range)
region_inventory = mcp.tool()(catalog_tools.region_inventory)
currency_convert = mcp.tool()(catalog_tools.currency_convert)


def _db():
    return get_rds_data_client()


def _authorization():
    return get_agentcore_identity().authorization_context()


def _unavailable(traveler_id: str, program: str) -> Dict[str, Any]:
    return {
        "traveler_id": traveler_id,
        "program": program,
        "error": "loyalty_balance_unavailable",
        "reason": "No recorded balance for this program in the traveler's profile.",
        "source": "traveler_profiles.loyalty_programs",
    }


def loyalty_from_programs(traveler_id: str, program: str, programs: Any) -> Dict[str, Any]:
    """Pick the requested program from a traveler's saved programs.

    A missing program or balance is unavailable; it is never replaced by a generated total.
    The MeridianHolds Lambda carries a port of this function; tests/test_holds_loyalty.py
    keeps the two in step.
    """
    requested = (program or "").strip().lower()
    if not isinstance(programs, dict):
        return _unavailable(traveler_id, program)
    for value in programs.values():
        if not isinstance(value, dict):
            continue
        configured = str(value.get("program") or "")
        if not (requested and configured and value.get("points_balance") is not None):
            continue
        if requested in configured.lower() or configured.lower() in requested:
            return {
                "traveler_id": traveler_id,
                "program": configured,
                "member_id": value.get("member_id"),
                "points_balance": int(value.get("points_balance") or 0),
                "tier": value.get("tier") or "Member",
                "next_tier_threshold": None,
                "points_to_next_tier": 0,
                "source": "traveler_profiles.loyalty_programs",
            }
    return _unavailable(traveler_id, program)


@mcp.tool()
async def loyalty_balance(travelerId: str, program: str) -> Dict[str, Any]:
    """Return loyalty points balance + tier for a traveler.

    Args:
        travelerId: the traveler id. The platform sets it; do not ask the traveler for it.
        program: e.g. 'Marriott Bonvoy', 'United MileagePlus'.

    The profile is read inside ``scoped_session`` so the workload must hold an
    active grant for the traveler before any row is touched; a traveler the
    workload is not bound to is refused with a structured error rather than a balance.
    """
    db = _db()
    try:
        async with db.scoped_session(
            traveler_id=travelerId,
            agent_type="concierge_agent",
            authorization=_authorization(),
        ) as tx:
            row = await db.execute_one(
                "SELECT loyalty_programs FROM traveler_profiles WHERE traveler_id = %s",
                (travelerId,),
                transaction_id=tx,
            )
    except TravelerAuthorizationError as exc:
        return {
            "traveler_id": travelerId,
            "program": program,
            "error": "traveler_not_authorized",
            "final": True,
            "message": REFUSAL_MESSAGE,
            "reason": str(exc),
            "source": "traveler_identity_bindings",
        }
    return loyalty_from_programs(travelerId, program, (row or {}).get("loyalty_programs"))


def configure_logging() -> None:
    """Configure the root logger and mask every record each of its handlers writes."""
    logging.basicConfig(
        level=os.getenv("MCP_CONCIERGE_LOG_LEVEL", "WARNING"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    install_masking()


if __name__ == "__main__":
    configure_logging()
    logger.info("starting meridian-concierge MCP server (stdio)")
    mcp.run()
