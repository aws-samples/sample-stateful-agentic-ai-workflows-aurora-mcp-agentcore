"""
Chat API Router for Meridian.

Handles chat interactions with the AI travel concierge across five phases:
- Phase 1: Direct RDS Data API connection (SQL filters on trip_packages)
- Phase 2: Via MCP (awslabs.postgres-mcp-server) abstraction
- Phase 3: Hybrid retrieval (semantic + lexical) + Cohere rerank
- Phase 4: Production concierge with AgentCore Runtime, Gateway, Memory
- Phase 5: Strands Graph workflow orchestration

AWS docs (by phase):
  Phase 1/2/3/4 data plane — RDS Data API:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
  Phase 2 MCP transport — Aurora via postgres-mcp-server (awslabs):
    https://github.com/awslabs/mcp/tree/main/src/postgres-mcp-server
  Phase 3 embeddings — Cohere Embed v4 on Bedrock:
    https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html
  Phase 3 pgvector — Aurora PostgreSQL extension:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraPostgreSQL.Extensions.html#AuroraPostgreSQL.Extensions.pgvector
  Phase 4 AgentCore — Runtime, Gateway, Memory, Identity:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html
  Bedrock models (Phases 1–4 Strands agents):
    https://docs.aws.amazon.com/bedrock/latest/userguide/model-ids.html
"""

import json
import logging
import asyncio
import re
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional, List, Any, Dict
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse
from backend.activity import ActivityEntry, Product, TraceTelemetry, create_activity
from backend.chat_stream import chat_event_sink
from backend.retrieval.availability import retrieval_availability_search
from backend.agents.phase_05_workflow.governed_hold import HoldOutcomeUnknown

from backend.agentcore.identity import get_agentcore_identity
from backend.agents.phase_04_production.concierge import runtime_model_id
from backend.authorization import TravelerAuthorizationError
from backend.db.rds_data_client import get_rds_data_client
from backend.config import bedrock_model_label
from backend.demo_prompts import tee_up_prompt, working_prompts
from backend.timing import clock, elapsed_ms
from backend.logging_config import log_exception, log_search, log_order, log_error, log_turn_start, log_turn_complete, log_activity_entry
from backend.http_auth import (
    HttpPrincipal,
    authorize_traveler,
    require_http_principal,
)
from backend.search_utils import (
    PACKAGE_COLUMNS,
    parse_search_query,
    execute_keyword_search,
    build_search_sql,
    results_to_packages,
)
from backend.catalog_compat import row_to_api_product

# MCP clients — Phase 2 demos two distinct MCP servers side-by-side:
#   1. awslabs.postgres-mcp-server (generic SQL transport, public AWS server)
#   2. backend.mcp.concierge_server (custom domain tools - compare,
#      price range, region inventory, FX, loyalty)
#
# Memory tools live in Phase 4 by design (Aurora RLS + AgentCore) - the
# Phase 2 narrative is "what does a CUSTOM MCP get you that the public
# one can't?" so domain logic, not memory, is what we showcase here.
from backend.mcp.mcp_client import mcp_session
from backend.mcp.concierge_mcp_client import concierge_mcp_session
from backend.llm_polish import PolishResult, polish_concierge_reply


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    """Request model for chat endpoint."""
    message: str = Field(min_length=1, max_length=4000)
    phase: Literal[1, 2, 3, 4, 5]
    customer_id: Optional[str] = Field(default=None, min_length=1, max_length=50)
    conversation_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    resume: bool = False
    travelers_count: int = Field(default=1, ge=1, le=20, strict=True)
    memory_enabled: bool = True
    experience: Literal["capability", "concierge"] = "capability"
    review_only: bool = False


class OrderItem(BaseModel):
    """Model for order items."""
    product_id: str
    name: str
    size: Optional[str] = None
    quantity: int
    unit_price: float


class Order(BaseModel):
    """Model for order data."""
    order_id: str
    items: List[OrderItem]
    subtotal: float
    tax: float
    shipping: float
    total: float
    status: str
    estimated_delivery: Optional[str] = None
    hold_expires_at: Optional[str] = None
    hold_created_at: Optional[str] = None
    departure_date: Optional[str] = None
    payment_required: bool = False
    confirmed_at: Optional[str] = None
    seats_remaining: Optional[int] = None


class MemoryFact(BaseModel):
    """Long-term preference fact from Aurora."""
    key: str
    value: str
    source: Optional[str] = None
    confidence: Optional[float] = None


class ChatResponse(BaseModel):
    """Response model for chat endpoint."""
    message: str
    products: Optional[List[Product]] = None
    order: Optional[Order] = None
    activities: List[ActivityEntry]
    follow_ups: Optional[List[str]] = None
    conversation_id: Optional[str] = None
    memory_facts: Optional[List[MemoryFact]] = None
    workflow_status: Optional[str] = None
    workflow_resumed_after_restart: Optional[bool] = None
    recovery_request: Optional[str] = None
    # The Bedrock model that actually wrote `message` (may be a fallback,
    # not the configured primary). None when the reply is a pure tool
    # result and no model wrote any part of it - the frontend must show
    # no model badge in that case.
    model_label: Optional[str] = None


def _complete_chat_turn(
    response: ChatResponse,
    phase: int,
    started_at: float,
    *,
    error: Optional[str] = None,
) -> ChatResponse:
    from backend.concierge_voice import direct_reply
    response.message = direct_reply(response.message)
    log_turn_complete(
        phase,
        products_count=len(response.products) if response.products else 0,
        activities_count=len(response.activities),
        started_at=started_at,
        error=error,
    )
    return response


def _phase_suggestions(query: str, products: List[Product], phase: int) -> List[str]:
    """Pick contextual suggestions from the query and the returned trips.

    Several branches return early. ``generate_follow_ups`` wraps this so the
    phase's tee-up prompt is appended on every path.
    """
    follow_ups = []
    query_lower = query.lower()

    if products:
        result_context = " ".join(
            " ".join(
                filter(
                    None,
                    (
                        product.name,
                        product.destination,
                        product.region,
                        product.category,
                    ),
                )
            )
            for product in products
        ).lower()
        is_tokyo_result = "tokyo" in result_context

        if phase == 5 and is_tokyo_result and (
            _is_workflow_resume_query(query)
            or any(
                marker in query_lower
                for marker in ("cancel", "disrupt", "rebook", "stranded")
            )
        ):
            return [
                "Compare live duration inventory for the top three Tokyo options.",
                f"Show the best available duration for {products[0].name}.",
                "Show slower Tokyo options with live duration inventory.",
            ]

        if "tokyo" in query_lower or is_tokyo_result:
            if phase >= 4:
                return [
                    "Find a Tokyo culture trip under $3,200 per traveler.",
                    "Show me a slower boutique Tokyo option.",
                    "Rework the Tokyo trip and check duration availability.",
                ]
            return [
                "Which duration options are available for Tokyo Culture & Cuisine?",
                "Show Tokyo options under $3,200 per traveler.",
                "Find a slower Tokyo ryokan stay.",
            ]

        if any(marker in query_lower for marker in ("wine", "villa", "tuscany")):
            return [
                "Which duration options are available for Tuscany Wine & Wellness?",
                "Compare Tuscany Wine & Wellness with Amalfi Coast Villa Week.",
                "Show wine-country trips under $3,500 per traveler.",
            ]

        # Get categories and brands from results
        categories = list(set(p.category for p in products))
        brands = list(set(p.brand for p in products if p.brand))
        prices = [p.price for p in products]
        primary_category = categories[0] if categories else None

        category_keywords_map = {
            "City Breaks": "city trips",
            "Beach & Resort": "beach resort",
            "Adventure & Outdoors": "adventure travel",
            "Wellness & Luxury": "wellness travel",
            "Family Trips": "family trips",
            "Business Travel": "business travel",
        }
        category_keyword = (
            category_keywords_map.get(primary_category, "travel packages")
            if primary_category
            else "travel packages"
        )

        if phase in [1, 2]:
            if prices:
                avg_price = sum(prices) / len(prices)
                if avg_price > 2000:
                    follow_ups.append(f"Show me {category_keyword} under $2,000 per traveler.")

            if brands:
                for brand in brands:
                    if brand.lower() not in query_lower:
                        # Brands come from mixed results, so do not pair the
                        # brand with the primary category - an adventure
                        # operator was being offered as "wellness travel".
                        follow_ups.append(f"Show me more trips from {brand}.")
                        break

            if "City Breaks" in categories:
                follow_ups.append("Show me beach and resort trips.")
            elif "Beach & Resort" in categories:
                follow_ups.append("Show me adventure and outdoors trips.")
            elif "Adventure & Outdoors" in categories:
                follow_ups.append("Show me wellness and luxury trips.")
            else:
                follow_ups.append("Show me city trips under $2,000 per traveler.")

        else:
            semantic_suggestions = {
                "City Breaks": [
                    "Romantic weekend in Europe",
                    "Culture and food focused city trip",
                    "Walkable neighborhoods with great museums",
                ],
                "Beach & Resort": [
                    "All-inclusive beach escape",
                    "Snorkeling and calm waters",
                    "Luxury overwater villa",
                ],
                "Adventure & Outdoors": [
                    "Moderate hiking with guided tours",
                    "Northern lights season trip",
                    "Rainforest and wildlife experience",
                ],
                "Wellness & Luxury": [
                    "Spa retreat in the mountains",
                    "Fine dining and wine country",
                    "Traditional ryokan with onsen",
                ],
                "Family Trips": [
                    "Theme park vacation with kids",
                    "Beach resort with kids club",
                    "National park wildlife safari",
                ],
                "Business Travel": [
                    "Quick conference stopover",
                    "Hotel near airport with lounge",
                    "Flexible change policy",
                ],
            }

            if primary_category in semantic_suggestions:
                for suggestion in semantic_suggestions[primary_category]:
                    if suggestion.lower() not in query_lower:
                        follow_ups.append(suggestion)
                        if len(follow_ups) >= 2:
                            break

            if "City Breaks" not in categories:
                follow_ups.append("Show me city trips under $2,000 per traveler.")
            elif "Beach & Resort" not in categories:
                follow_ups.append("Show me beach trips under $2,500 per traveler.")

    else:
        # No results: offer this phase's documented known-good prompts rather
        # than generic fragments.
        follow_ups = working_prompts(phase)

    return follow_ups


def generate_follow_ups(query: str, products: List[Product], phase: int) -> List[str]:
    """Three follow-up chips, the last of which always advances the ladder.

    Every phase ends with the prompt that motivates the next rung, so the
    hand-off is on screen rather than left for the user to remember. The two
    preceding chips are contextual suggestions for the current results.
    """
    suggestions = _phase_suggestions(query, products, phase)
    tee_up = tee_up_prompt(phase)
    if tee_up and tee_up.lower() != query.lower():
        suggestions = suggestions[:2] + [tee_up]

    seen: set[str] = set()
    unique_follow_ups: List[str] = []
    for suggestion in suggestions:
        key = suggestion.lower()
        if key not in seen:
            seen.add(key)
            unique_follow_ups.append(suggestion)

    return unique_follow_ups[:3]


# =============================================================================
# PHASE 1: Direct RDS Data API Connection
# Simple SQL queries directly to Aurora PostgreSQL
# =============================================================================

async def sql_search(query: str, limit: int = 5) -> tuple[List[Product], List[ActivityEntry]]:
    """
    Phase 1: Direct database search using RDS Data API.
    Simple trip_type matching and LIKE queries.
    """
    activities = []
    start_time = datetime.now(timezone.utc)

    db = get_rds_data_client()

    activities.append(create_activity(
        activity_type="database",
        title="Direct RDS Data API connection",
        details="Executing SQL query via HTTP endpoint",
        agent_name="SQLAgent",
        agent_file="backend/routers/chat.py"
    ))

    # Use shared search utilities
    params = parse_search_query(query)
    query_started = clock()
    results, display_sql, search_title = await execute_keyword_search(db, params, limit)
    query_ms = elapsed_ms(query_started)

    activities.append(create_activity(
        activity_type="search",
        title=search_title,
        sql_query=display_sql,
        execution_time_ms=query_ms,
        agent_name="SQLAgent",
        agent_file="backend/routers/chat.py"
    ))

    execution_time = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

    # Log for monitoring
    log_search(phase=1, query=query, results_count=len(results),
               execution_time_ms=execution_time, search_type="keyword")

    activities.append(create_activity(
        activity_type="result",
        title=f"Found {len(results)} trips",
        execution_time_ms=execution_time,
        agent_name="SQLAgent",
        agent_file="backend/routers/chat.py"
    ))

    # Convert results to Product models
    product_dicts = results_to_packages(results)
    products = [Product(**row_to_api_product(p)) for p in product_dicts]

    return products, activities


# =============================================================================
# PHASE 2: MCP (Model Context Protocol) Abstraction
# Uses awslabs.postgres-mcp-server for database operations
#
# AWS docs:
#   RDS Data API: https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
#   postgres-mcp-server: https://github.com/awslabs/mcp/tree/main/src/postgres-mcp-server
#
# Phase 2 requires MCP — no RDS Data API substitute.
# =============================================================================

# Keywords that trigger the CUSTOM meridian-concierge MCP server.
# When the prompt asks for things you can't get from a generic SQL
# transport (compare these / what's the price range / how many
# trips do you sell in Europe / convert to EUR / loyalty status), we
# layer the custom server on top so the trace shows both in action.
_DOMAIN_INTENT_KEYWORDS = (
    "compare",
    "comparison",
    "side by side",
    "in eur",
    "in euro",
    "in euros",
    "in gbp",
    "in pounds",
    "convert",
    "loyalty",
    "bonvoy",
    "skymiles",
    "mileageplus",
    "united status",
    "price range",
    "how many",
    "inventory",
)


def _wants_domain_tool(query: str) -> bool:
    q = query.lower()
    return any(k in q for k in _DOMAIN_INTENT_KEYWORDS)


def _is_semantic_intent_query(query: str) -> bool:
    """Detect mood-led prompts that need semantic retrieval, not MCP tools."""
    q = (query or "").lower()
    intent_markers = (
        "quiet",
        "romantic",
        "wine country",
        "villa",
        "family-friendly",
        "snorkeling",
        "vibe",
        "mood",
    )
    return sum(marker in q for marker in intent_markers) >= 2


_CURRENCY_KEYWORDS = ("convert", "in eur", "in euro", "in gbp", "in pounds", "in jpy", "in yen")
_LOYALTY_KEYWORDS = ("loyalty", "bonvoy", "skymiles", "mileageplus", "united status")
_PRICE_RANGE_DESTINATIONS = (
    "Tokyo", "Paris", "Bali", "Lisbon", "Porto", "Iceland", "Rome", "Kyoto",
)
_INVENTORY_REGIONS = ("Asia", "Europe", "Americas", "Africa", "Oceania")


async def _timed_tool_call(cli: Any, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Call one custom-MCP tool, timing only that call."""
    started = clock()
    result = await cli.call(tool, args)
    return {"tool": tool, "args": args, "result": result, "elapsed_ms": elapsed_ms(started)}


async def _flagship_package_ids() -> List[str]:
    """One flagship per trip_type, so a comparison spans diverse experiences.

    Stratified pick: City Breaks vs Beach vs Wellness vs Adventure... instead
    of three of the same kind. Within each trip_type the highest-priced row is
    that type's "flagship" representative, capped at 3 total. Falls through to
    any 3 rows if the stratified query comes back empty (defensive).
    """
    ids: List[str] = []
    try:
        stratified_sql = (
            "SELECT package_id FROM ("
            "  SELECT package_id, trip_type, price_per_person, "
            "         ROW_NUMBER() OVER ("
            "           PARTITION BY trip_type "
            "           ORDER BY price_per_person DESC, package_id"
            "         ) AS rn "
            "  FROM trip_packages"
            ") flagship "
            "WHERE rn = 1 "
            "ORDER BY price_per_person DESC "
            "LIMIT 3"
        )
        rows = await get_rds_data_client().execute(stratified_sql)
        ids = [r["package_id"] for r in rows if r.get("package_id")]
        if not ids:
            rows = await get_rds_data_client().execute(
                "SELECT package_id FROM trip_packages LIMIT 3"
            )
            ids = [r["package_id"] for r in rows if r.get("package_id")]
    except Exception as exc:
        log_error("compare_packages_row_pull", error=str(exc))
    return ids


async def _currency_call(
    cli: Any, q: str, compared_packages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Convert the compared packages' prices, or a sample amount when none were compared.

    Timed around every currency_convert call it makes.
    """
    target = (
        "EUR" if "eur" in q
        else "GBP" if "gbp" in q or "pound" in q
        else "JPY" if "jpy" in q or "yen" in q
        else "EUR"
    )
    started = clock()
    if compared_packages:
        conversions: List[Dict[str, Any]] = []
        for package in compared_packages:
            amount = float(package.get("price_per_person") or 0)
            converted = await cli.call(
                "currency_convert",
                {"amount": amount, "from_ccy": "USD", "to_ccy": target},
            )
            if isinstance(converted, dict):
                conversions.append({
                    "package_id": package.get("package_id"),
                    "name": package.get("name"),
                    **converted,
                })
        result: Dict[str, Any] = {
            "to": target,
            "conversions": conversions,
            "note": "indicative rates, not for settlement",
        }
        args: Dict[str, Any] = {
            "amounts": [
                float(package.get("price_per_person") or 0)
                for package in compared_packages
            ],
            "to": target,
        }
    else:
        result = await cli.call(
            "currency_convert",
            {"amount": 2500.0, "from_ccy": "USD", "to_ccy": target},
        )
        args = {"amount": 2500.0, "to": target}
    return {
        "tool": "currency_convert",
        "args": args,
        "result": result,
        "elapsed_ms": elapsed_ms(started),
    }


def _loyalty_program(q: str) -> str:
    return (
        "Marriott Bonvoy"
        if "bonvoy" in q
        else "United MileagePlus"
        if "mileageplus" in q or "united" in q
        else "Delta SkyMiles"
        if "skymiles" in q
        else "Marriott Bonvoy"
    )


def _named_in(q: str, names: tuple, default: str) -> str:
    """The first of ``names`` the query mentions, or ``default``."""
    for name in names:
        if name.lower() in q:
            return name
    return default


async def _call_domain_tool(
    query: str,
    *,
    traveler_id: str,
) -> Optional[Dict[str, Any]]:
    """Pick the most relevant custom-MCP tool(s) for a domain-flavored
    prompt and call them. Returns a single dict (legacy single-call path)
    OR a dict with `tool='multi'` and a `calls` list when more than one
    tool intent was detected (e.g. "compare ... in EUR" fires BOTH
    compare_packages and currency_convert). Each call carries `elapsed_ms`,
    measured around the MCP tool call or calls it made."""
    q = query.lower()
    calls: List[Dict[str, Any]] = []
    compared_packages: List[Dict[str, Any]] = []

    async with concierge_mcp_session() as cli:
        if any(k in q for k in ("compare", "comparison", "side by side")):
            ids = await _flagship_package_ids()
            if ids:
                call = await _timed_tool_call(cli, "compare_packages", {"package_ids": ids})
                if isinstance(call["result"], list):
                    compared_packages = call["result"]
                calls.append(call)

        if any(k in q for k in _CURRENCY_KEYWORDS):
            calls.append(await _currency_call(cli, q, compared_packages))

        if any(k in q for k in _LOYALTY_KEYWORDS):
            calls.append(await _timed_tool_call(
                cli, "loyalty_balance",
                {"traveler_id": traveler_id, "program": _loyalty_program(q)},
            ))

        if "price range" in q:
            destination = _named_in(q, _PRICE_RANGE_DESTINATIONS, "Europe")
            calls.append(await _timed_tool_call(
                cli, "price_range", {"destination": destination},
            ))

        if any(k in q for k in ("how many", "inventory")):
            region = _named_in(q, _INVENTORY_REGIONS, "Europe")
            calls.append(await _timed_tool_call(cli, "region_inventory", {"region": region}))

    if not calls:
        return None
    if len(calls) == 1:
        return calls[0]
    return {"tool": "multi", "calls": calls}


async def _postgres_mcp_query(
    params: Any, limit: int, activities: List[ActivityEntry],
) -> List[Dict[str, Any]]:
    """Run the search through the generic postgres-mcp server and trace it."""
    sql, display_sql, search_title = build_search_sql(params, limit)
    session_started = clock()
    async with mcp_session() as client:
        # Opening the session starts the server, connects it and lists its tools.
        session_ms = elapsed_ms(session_started)
        activities.append(create_activity(
            activity_type="mcp",
            title="MCP server discovered: awslabs.postgres-mcp-server",
            details="Generic SQL transport · tools/list returned " + ", ".join(
                tool["name"] for tool in client.available_tools
            ),
            execution_time_ms=session_ms,
            agent_name="MCPAgent", agent_file="backend/routers/chat.py",
        ))
        activities.append(create_activity(
            activity_type="mcp", title="postgres-mcp · session connected",
            details="Aurora PostgreSQL via RDS Data API; connection configured at server startup",
            agent_name="MCPAgent", agent_file="backend/routers/chat.py",
        ))
        query_started = clock()
        results = await client.run_query(sql)
        query_ms = elapsed_ms(query_started)
    activities.append(create_activity(
        activity_type="mcp",
        title="postgres-mcp · run_query",
        details=f"Generic SQL tool: {search_title}",
        sql_query=display_sql,
        execution_time_ms=query_ms,
        agent_name="MCPAgent",
        agent_file="backend/routers/chat.py",
    ))
    return results


def _record_domain_calls(
    domain_call: Dict[str, Any], activities: List[ActivityEntry],
) -> tuple[List[str], List[str]]:
    """Trace each custom-MCP call; return the reply parts and any compared package ids."""
    activities.append(create_activity(
        activity_type="mcp",
        title="MCP server discovered: meridian-concierge (custom)",
        details="Custom domain call completed; the executed tools and results follow.",
        agent_name="MCPAgent",
        agent_file="backend/mcp/concierge_server.py",
    ))
    # Normalize single-call and multi-call shapes into a list
    # so we can log + format both uniformly.
    if domain_call.get("tool") == "multi":
        sub_calls = domain_call["calls"]
    else:
        sub_calls = [domain_call]

    reply_parts: List[str] = []
    # Package_ids surfaced by compare_packages get hydrated
    # back into full Product rows below so the recommendation
    # grid renders alongside the polished bubble.
    compared_ids: List[str] = []
    for sub in sub_calls:
        tool_name = sub["tool"]
        tool_args = sub["args"]
        tool_result = sub["result"]
        summary = _summarize_domain_result(tool_name, tool_result)
        activities.append(create_activity(
            activity_type="mcp",
            title=f"meridian-concierge · {tool_name}",
            details=f"args={tool_args} · {summary}",
            execution_time_ms=sub.get("elapsed_ms"),
            agent_name="MCPAgent",
            agent_file="backend/mcp/concierge_server.py",
        ))
        reply = _format_domain_reply(tool_name, tool_result)
        if reply:
            reply_parts.append(reply)
        if tool_name == "compare_packages":
            compared_ids = list(tool_args.get("package_ids") or [])
    return reply_parts, compared_ids


async def _hydrate_compared(
    compared_ids: List[str], activities: List[ActivityEntry],
) -> Optional[List[Dict[str, Any]]]:
    """Join compared package ids to full catalog rows, in the order compared.

    Returns None when the catalog read itself failed, so the caller keeps
    what it had.
    """
    placeholders = ",".join(["%s"] * len(compared_ids))
    hydrate_sql = f"""
        SELECT {PACKAGE_COLUMNS}
        FROM trip_packages
        WHERE package_id IN ({placeholders})
    """
    results: Optional[List[Dict[str, Any]]] = None
    try:
        hydrate_started = clock()
        results = await get_rds_data_client().execute(
            hydrate_sql, tuple(compared_ids)
        )
        hydrate_ms = elapsed_ms(hydrate_started)
        # Preserve the order returned by compare_packages.
        order = {pid: i for i, pid in enumerate(compared_ids)}
        results.sort(key=lambda r: order.get(r["package_id"], 99))
        activities.append(create_activity(
            activity_type="database",
            title="Hydrated compared packages into product cards",
            details=f"{len(results)} rows joined from trip_packages",
            sql_query=(
                f"SELECT … FROM trip_packages WHERE package_id IN "
                f"({', '.join(repr(p) for p in compared_ids)})"
            ),
            execution_time_ms=hydrate_ms,
            agent_name="MCPAgent",
            agent_file="backend/routers/chat.py",
        ))
    except Exception as exc:
        log_error("compare_hydrate", error=str(exc))
    return results


async def _concierge_mcp_turn(
    query: str,
    traveler_id: str,
    results: List[Dict[str, Any]],
    activities: List[ActivityEntry],
) -> tuple[List[Dict[str, Any]], Optional[str], bool]:
    """Answer a domain intent through meridian-concierge.

    Returns the catalog rows to show, the domain reply, and whether the
    server answered.
    """
    domain_text: Optional[str] = None
    custom_answered = False
    try:
        domain_call = await _call_domain_tool(
            query,
            traveler_id=traveler_id,
        )
        if domain_call:
            custom_answered = True
            reply_parts, compared_ids = _record_domain_calls(domain_call, activities)
            # Hydrate compare_packages IDs into full catalog rows so
            # the recommendation grid in the UI shows the trips the
            # tool just compared. Only do this when the SQL search
            # itself returned zero rows (a pure-domain query) so we
            # don't override a real keyword match.
            if compared_ids and not results:
                hydrated = await _hydrate_compared(compared_ids, activities)
                if hydrated is not None:
                    results = hydrated
            if reply_parts:
                # Phase 2 demonstrates deterministic, explicit MCP tools.
                # Keep the response in that contract rather than invoking
                # an LLM that the capability ladder does not advertise.
                domain_text = "\n\n".join(reply_parts)
        else:
            # The intent matched but no tool branch picked it up.
            domain_text = (
                "I recognized this as a domain-tool query but couldn't pick a "
                "matching meridian-concierge tool. Try keywords like 'compare', "
                "'in EUR', 'price range', 'inventory', or 'loyalty'."
            )
    except Exception:
        error_ref = log_exception("concierge_mcp_turn")
        activities.append(create_activity(
            activity_type="error",
            title="meridian-concierge MCP error",
            details=f"The domain tool failed. Reference {error_ref}.",
            agent_name="MCPAgent",
            agent_file="backend/mcp/concierge_server.py",
        ))
        # Surface the failure to the user instead of letting the
        # generic "Phase 1/2 keyword filters" message take over.
        domain_text = (
            "The meridian-concierge domain tool failed to run. "
            f"Reference {error_ref}; the backend log has the details."
        )
    return results, domain_text, custom_answered


async def mcp_search(
    query: str,
    *,
    traveler_id: str,
    limit: int = 5,
) -> tuple[List[Product], List[ActivityEntry], Optional[str]]:
    """
    Phase 2: Search via MCP abstraction layer.

    Routes through TWO MCP servers in one agent turn:
      - awslabs.postgres-mcp-server  → generic SQL transport
      - meridian-concierge (custom)  → travel-domain tools

    Returns (products, activities, domain_text). When the prompt is a
    pure domain query (compare/FX/price range/inventory/loyalty), the
    domain_text contains a markdown-style readout that the caller uses
    as the bot reply instead of the generic "I found N trips" message.
    """
    activities: List[ActivityEntry] = []
    start_time = datetime.now(timezone.utc)

    params = parse_search_query(query)
    use_custom_mcp = _wants_domain_tool(query)
    # Pure domain queries (no recognized trip_type AND no price filter
    # AND a domain intent) skip the SQL search entirely - ILIKE-ing on
    # A cross-category comparison with converted prices matches no catalog text
    # and the user only cares about the domain tool's answer anyway.
    pure_domain = (
        use_custom_mcp
        and not params.matched_trip_type
        and params.price_filter is None
    )

    # ----- Generic MCP server (awslabs.postgres-mcp-server) -----
    # Only traced when the turn actually opens a generic session.  A
    # pure-domain turn (currency, loyalty) never enters mcp_session(), so
    # emitting discovery and connect here would put a session in the trace
    # that no code opened.
    results: List[Dict[str, Any]] = []
    if not pure_domain:
        results = await _postgres_mcp_query(params, limit, activities)

    # ----- Custom MCP server (meridian-concierge) -----
    domain_text: Optional[str] = None
    custom_answered = False
    if use_custom_mcp:
        results, domain_text, custom_answered = await _concierge_mcp_turn(
            query, traveler_id, results, activities,
        )

    execution_time = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

    log_search(phase=2, query=query, results_count=len(results),
               execution_time_ms=execution_time, search_type="mcp")

    # Count the servers that answered this turn, not the ones it meant to use.
    # Detecting a domain intent is not the same as the concierge server
    # returning: counting the intent let a call that raised, or came back
    # empty, still appear in the proof as a server the turn had used.
    servers_used = (0 if pure_domain else 1) + (1 if custom_answered else 0)
    activities.append(create_activity(
        activity_type="mcp",
        title=(
            f"MCP turn complete · {servers_used} server"
            f"{'' if servers_used == 1 else 's'}"
        ),
        details=f"Retrieved {len(results)} rows in {execution_time}ms",
        execution_time_ms=execution_time,
        agent_name="MCPAgent",
        agent_file="backend/routers/chat.py",
    ))

    product_dicts = results_to_packages(results)
    products = [Product(**row_to_api_product(p)) for p in product_dicts]

    return products, activities, domain_text


def _format_domain_reply(tool: str, result: Any) -> str:
    """Format a custom-MCP tool result as a human-readable bot reply."""
    if not isinstance(result, (list, dict)):
        return ""
    try:
        if tool == "compare_packages" and isinstance(result, list):
            if not result:
                return "No packages to compare."
            lines = [f"Compared {len(result)} packages via meridian-concierge MCP:", ""]
            for p in result:
                price = p.get("price_per_person")
                price_s = f"${price:,.0f}" if isinstance(price, (int, float)) else "-"
                lines.append(
                    f"- {p.get('name')} - {p.get('destination') or p.get('region') or ''} "
                    f"- {p.get('trip_type', '')} - {price_s}"
                )
            return "\n".join(lines)
        if tool == "currency_convert" and isinstance(result, dict):
            conversions = result.get("conversions")
            if isinstance(conversions, list):
                if not conversions:
                    return "No package prices were available to convert."
                lines = [
                    f"Package prices converted to {result.get('to')} via "
                    "meridian-concierge MCP:",
                    "",
                ]
                for item in conversions:
                    amount = item.get("amount")
                    converted = item.get("converted")
                    amount_s = f"{amount:,.0f}" if isinstance(amount, (int, float)) else "-"
                    converted_s = (
                        f"{converted:,.2f}"
                        if isinstance(converted, (int, float))
                        else "-"
                    )
                    lines.append(
                        f"- {item.get('name')} - {amount_s} {item.get('from')} "
                        f"≈ {converted_s} {item.get('to')}"
                    )
                lines.extend(["", "Indicative rates; not for settlement."])
                return "\n".join(lines)
            amt = result.get("amount")
            converted = result.get("converted")
            rate = result.get("rate")
            return (
                f"FX via meridian-concierge MCP: "
                f"{amt} {result.get('from')} ≈ {converted} {result.get('to')} "
                f"(rate {rate})."
            )
        if tool == "loyalty_balance" and isinstance(result, dict):
            if result.get("error") == "loyalty_balance_unavailable":
                return (
                    "Loyalty balance unavailable: no recorded balance for this "
                    "program in the traveler's profile. No points total was inferred."
                )
            if result.get("error"):
                return (
                    "Loyalty lookup refused by meridian-concierge MCP: the workload "
                    f"holds no grant for traveler {result.get('traveler_id')}. "
                    "No balance was read."
                )
            pts = result.get("points_balance", 0)
            tier = result.get("tier", "—")
            program = result.get("program", "")
            to_next = result.get("points_to_next_tier", 0) or 0
            tail = f" · {to_next:,} pts to next tier" if to_next else ""
            return (
                f"Loyalty (via meridian-concierge MCP): "
                f"{pts:,} pts on {program} · tier {tier}{tail}."
            )
        if tool == "price_range" and isinstance(result, dict):
            dest = result.get("destination", "—")
            low = result.get("low")
            avg = result.get("average")
            high = result.get("high")
            n = result.get("sample_size", 0)
            if not n:
                return f"No pricing data for {dest}."
            note = result.get("note", "")
            return (
                f"Price range for {dest} (via meridian-concierge MCP, sample={n}): "
                f"low ${low:,.0f} · average ${avg:,.0f} · high ${high:,.0f}. {note}"
            ).rstrip()
        if tool == "region_inventory" and isinstance(result, dict):
            region = result.get("region", "—")
            count = result.get("package_count", 0)
            slots = result.get("total_departure_slots", 0)
            by_type = result.get("package_count_by_trip_type") or {}
            by_type_s = ", ".join(f"{k}: {v}" for k, v in by_type.items()) if by_type else "—"
            return (
                f"Inventory in {region} (via meridian-concierge MCP): "
                f"{count} packages · {slots} departure slots · by trip type — {by_type_s}."
            )
    except Exception:
        pass
    return ""


def _summarize_domain_result(tool: str, result: Any) -> str:
    """One-line summary of a custom MCP tool call for the trace span.

    Falls back to `type(result).__name__` when the shape doesn't match
    the expected tool contract - that immediately surfaces decode bugs
    in the activity panel instead of silently saying 'ok'.
    """
    try:
        if tool == "compare_packages":
            if isinstance(result, list):
                return f"compared {len(result)} packages"
            return f"unexpected shape: {type(result).__name__}"
        if tool == "currency_convert" and isinstance(result, dict):
            conversions = result.get("conversions")
            if isinstance(conversions, list):
                return f"converted {len(conversions)} package prices to {result.get('to')}"
            return f"{result.get('amount')} {result.get('from')} = {result.get('converted')} {result.get('to')}"
        if tool == "loyalty_balance" and isinstance(result, dict):
            if result.get("error"):
                return f"refused · {result.get('error')}"
            pts = result.get("points_balance", 0) or 0
            return f"{pts:,} pts · tier={result.get('tier')}"
        if tool == "price_range" and isinstance(result, dict):
            return f"range low={result.get('low')} · high={result.get('high')}"
        if tool == "region_inventory" and isinstance(result, dict):
            return f"{result.get('package_count')} packages · {result.get('total_departure_slots')} slots"
    except Exception as exc:
        return f"summarize failed: {exc.__class__.__name__}"
    return f"shape={type(result).__name__}"


# =============================================================================
# Search-response prose helper. Phase 4 returns its managed Runtime answer;
# Phase 5 returns its checkpointed operational result without another rewrite.
# Search prose receives the returned catalog facts and a grounding instruction.
# That instruction is guidance, not a guarantee; structured receipts remain
# authoritative for actions and inventory.
# =============================================================================


async def _polish_phase_reply(
    phase: int,
    user_query: str,
    raw_message: str,
    products: List[Product],
    activities: List[ActivityEntry],
    memory_facts: Optional[List[Any]] = None,  # MemoryFact pydantic OR dict
) -> PolishResult:
    """Run the Bedrock polish over a search reply.

    Returns the polish result: the final message, the model that wrote it and
    how long its call took, or no model and a note. On failure (no model
    access, all fallbacks blocked) the raw_message is returned verbatim as a
    graceful fallback.
    """
    if not products and not raw_message:
        return PolishResult(text=raw_message, model_id=None, note="no content to polish")

    # Build a deterministic, fact-only context block. The system prompt
    # in llm_polish forbids inventing anything outside this block.
    lines: List[str] = [f"Mode: phase {phase}", f"Tool reply: {raw_message}"]

    if products:
        lines.append("")
        lines.append(f"Top {len(products)} trips returned by the agent:")
        for rank, p in enumerate(products[:5], start=1):
            facts = [
                f"rank={rank}",
                f"name={p.name}",
                f"trip_type={p.category}",
                f"operator={p.brand}",
                f"price=${p.price:,.0f}",
            ]
            if p.available_sizes:
                facts.append(f"durations={', '.join(p.available_sizes[:3])}")
            if p.highlights:
                facts.append(f"highlights={', '.join(p.highlights[:3])}")
            if p.description:
                facts.append(f"description={p.description[:180]}")
            lines.append("- " + " · ".join(facts))

    if memory_facts:
        lines.append("")
        # Pass the full For-you fact set (the same one the right-rail panel
        # shows) so the polish has the widest material to weave from. The
        # system prompt caps how many it actually mentions so this stays
        # natural — but giving the model 12 facts beats giving it 2 and
        # forcing it to repeat shellfish + no_red_eye in every reply.
        lines.append("Traveler preferences applied to this turn:")
        for fact in memory_facts[:12]:
            # Phase 4 passes Pydantic MemoryFact objects; the SearchAgent /
            # legacy paths pass plain dicts. Support both shapes.
            if hasattr(fact, "key"):
                key = str(getattr(fact, "key", "") or "").strip()
                val = str(getattr(fact, "value", "") or "").strip()
            else:
                key = str(fact.get("key", "") if isinstance(fact, dict) else "").strip()
                val = str(fact.get("value", "") if isinstance(fact, dict) else "").strip()
            if key and val:
                lines.append(f"- {key}: {val}")

    # Surface a couple of distinctive trace spans so the polish can mention
    # what the agent actually did (rerank, RLS scope, LangGraph node, etc.).
    # Filter to *successful* spans only — we never want the concierge to
    # narrate degraded paths ("our reranker was unavailable") at the user.
    # If a step failed, fall back silently and let the model talk about
    # results, not infrastructure hiccups. We also drop the "unavailable"
    # / "failed" / "fallback" titles explicitly so a span without a
    # status field still can't leak.
    def _is_healthy_span(a: ActivityEntry) -> bool:
        title = (a.title or "").lower()
        if any(k in title for k in ("unavailable", "failed", "fallback", "error")):
            return False
        status = (getattr(a, "status", None) or "").lower()
        if status and status != "ok":
            return False
        return True

    notable_spans = [
        a for a in activities
        if a.title
        and _is_healthy_span(a)
        and any(
            k in a.title.lower()
            for k in ("rerank applied", "rls", "agentcore", "langgraph", "memory-grounded", "checkpoint", "snapshot saved", "node:")
        )
    ]
    if notable_spans:
        lines.append("")
        lines.append("Notable trace spans for this turn:")
        for a in notable_spans[:5]:
            lines.append(f"- {a.title}")

    raw = "\n".join(lines)

    return await polish_concierge_reply(user_query, raw)
#
# AWS docs:
#   Cohere Embed v4: https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html
#   Aurora pgvector: https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraPostgreSQL.Extensions.html#AuroraPostgreSQL.Extensions.pgvector

async def _polish_and_record(
    *,
    phase: int,
    mode_label: str,
    agent_name: str,
    user_query: str,
    raw_message: str,
    products: List[Product],
    activities: List[ActivityEntry],
    memory_facts: Optional[List[Any]] = None,
) -> tuple[str, Optional[str]]:
    """Polish a reply and append the matching success/failure span.

    Phase 3 ends a turn this way: run the Bedrock rewrite, record whether it
    succeeded, and fall back to the deterministic reply if it did not.

    Returns:
        (message, model_label). `model_label` names whichever model in the
        Sonnet 5 -> Haiku 4.5 -> Opus 5 chain actually wrote `message` -
        which may not be the configured primary - or None when polish did
        not run or every model in the chain failed, so the raw tool result
        is returned unpolished and no model wrote any part of it.
    """
    polish = await _polish_phase_reply(
        phase=phase,
        user_query=user_query,
        raw_message=raw_message,
        products=products,
        activities=activities,
        memory_facts=memory_facts,
    )
    if polish.model_id:
        activities.append(create_activity(
            activity_type="reasoning",
            title=f"Bedrock · concierge polish ({polish.model_id})",
            details=f"Wrapping {mode_label} reply in concierge tone",
            execution_time_ms=polish.elapsed_ms,
            agent_name=agent_name,
            agent_file="backend/llm_polish.py",
        ))
        return polish.text, bedrock_model_label(polish.model_id)
    activities.append(create_activity(
        activity_type="error",
        title="Bedrock polish unavailable",
        details=polish.note or "unknown",
        agent_name=agent_name,
        agent_file="backend/llm_polish.py",
    ))
    return raw_message, None


# =============================================================================
# PHASE 3 (live): Strands RetrievalAgent driving Bedrock tool delegation.
# =============================================================================

_MEMORY_RECALL_PATTERNS = re.compile(
    r"\b(what did we (discuss|talk about|cover|see|look at)|"
    r"pick up where we left off|"
    r"continue our (conversation|chat|discussion)|"
    r"last time|previous (chat|turn|session|conversation)|"
    r"remind me (what|of|about) (we|i|you)|"
    r"earlier (you|we|i) (said|told|mentioned|discussed)|"
    r"as (we|i) (discussed|talked|mentioned) (earlier|before|previously)|"
    r"my (saved|past|prior|previous|earlier) (trips?|searches?|preferences?|favorites?)|"
    r"what (have|did) (we|i) (look|check|search|browse)ed?|"
    r"do you remember|recall (our|my|the))\b",
    re.IGNORECASE,
)


def _is_memory_recall_query(query: str) -> bool:
    """Phase 3 has no persistent memory — detect prompts that depend on it.

    Phase 3 is pure retrieval: pgvector + Cohere rerank on a fresh query.
    There's no conversation store, no traveler profile, no prior-turn
    awareness. When a user asks "what did we discuss" or "pick up where
    we left off", embedding-and-searching is exactly the wrong thing to
    do — it pulls whatever's vector-closest to that phrase (which tends
    to be City Breaks at ~37% match because "discuss" + "look at" land
    near generic exploration packages). That dilutes the demo punchline.

    The honest answer is "I can't see prior turns from here." Phase 4
    (Production — AgentCore Memory + Aurora RLS over conversation_messages
    and trip_interactions) is exactly the fix. So we short-circuit, return
    zero products, and let the polish reply explain the gap.
    """
    return bool(_MEMORY_RECALL_PATTERNS.search(query or ""))


# Availability-intent: the supervisor should delegate these to the PackageAgent
# specialist (departure slots / open dates on a *named* package), not run a
# fresh hybrid search. This is what makes "supervised multi-agent" visible in
# the demo — a different specialist, a different tool, a different trace.
_AVAILABILITY_PATTERNS = re.compile(
    r"\b(availability|available|open dates?|departure dates?|departures?|"
    r"what dates?|when can (we|i)|free dates?|open slots?|slots?)\b",
    re.IGNORECASE,
)


def _is_availability_query(query: str) -> bool:
    """Detect departure/availability questions that belong to the PackageAgent.

    Guard against overlap with memory-recall ("what did we discuss") — those
    are handled separately and must take precedence, so callers check
    _is_memory_recall_query first.
    """
    return bool(_AVAILABILITY_PATTERNS.search(query or ""))


_PHASE4_WORKFLOW_TRANSITION_MESSAGE = (
    "I can carry forward your Tokyo context, but this needs two dependent "
    "steps: rework the itinerary, then check duration availability for the "
    "best three options. "
    "Switch to Workflow so each step is explicit, checkpointed, and resumable."
)


def _needs_checkpointed_workflow(query: str) -> bool:
    """Detect Phase 4 prompts that should bridge to the LangGraph workflow.

    Production mode can recall memory, search, and persist a turn, but a prompt
    that asks for multiple dependent travel-planning steps should not be framed
    as completed in one fluent paragraph. Phase 5 owns that story because the
    graph can checkpoint search -> availability -> synthesis/resume.

    A cancelled-flight replan qualifies as its own multi-step plan: reworking
    the itinerary is a re-search step and confirming which departures are still
    open is an availability step, so it bridges to Workflow just like the
    explicit "plan ... extension" phrasing does.
    """
    q = (query or "").lower()
    if not q:
        return False

    is_disruption_replan = any(
        m in q for m in ("cancel", "disrupt", "rebook", "stranded", "rerouted")
    ) and any(m in q for m in ("rework", "replan", "re-plan", "redo", "rebuild"))

    has_plan_intent = is_disruption_replan or any(
        marker in q
        for marker in (
            "plan ",
            "plan our",
            "plan a",
            "plan me",
            "end to end",
            "end-to-end",
        )
    )
    has_availability_step = any(
        marker in q
        for marker in (
            "open date",
            "open dates",
            "availability",
            "available",
            "departure",
            "departures",
            "what dates",
            "when can",
            "still open",
        )
    )
    dependent_steps = sum(
        bool(any(marker in q for marker in markers))
        for markers in (
            ("shortlist", "candidate", "find ", "rework", "replan"),
            ("pick ", "choose ", "select "),
            ("marriott", "bonvoy"),
            ("kyoto", "side trip", "extension"),
            ("hold", "stage", "reserve", "book"),
            ("departure", "departures", "still open"),
            ("availability", "available", "duration"),
        )
    )
    return has_plan_intent and has_availability_step and dependent_steps >= 2


async def retrieval_supervisor_search(
    query: str,
    limit: int = 5,
) -> tuple[List[Product], List[ActivityEntry]]:
    """Phase 3 via live Strands supervisor — Bedrock LLM picks the SearchAgent tool."""
    from backend.agents.phase_03_retrieval import create_retrieval_system

    activities: List[ActivityEntry] = []

    def collect(entry: Any) -> None:
        try:
            act = _memory_activity_to_entry(entry)
            activities.append(act)
            log_activity_entry(act)
        except Exception:
            # Best-effort: keep going if the entry shape is unexpected.
            pass

    # Memory-recall short-circuit. We deliberately do not embed-and-search
    # these prompts — Phase 3 has no conversation history, so any pgvector
    # match would be coincidental. Surface the limitation in the trace so
    # the polish reply can name the gap and point at Phase 4 as the fix.
    if _is_memory_recall_query(query):
        activities.append(create_activity(
            activity_type="reasoning",
            title="Memory-recall prompt detected — Retrieval mode has no conversation store",
            details=(
                "Retrieval mode is pure search (pgvector + Cohere rerank). "
                "Prior-turn memory is intentionally not in scope — that's "
                "Production mode (AgentCore Memory + Aurora RLS over "
                "conversation_messages / trip_interactions). Returning "
                "zero results so the concierge can explain the gap."
            ),
            agent_name="RetrievalAgent",
            agent_file="agents/phase_03_retrieval/supervisor.py",
        ))
        activities.append(create_activity(
            activity_type="result",
            title="Retrieval mode cannot resolve memory-recall queries",
            details="Routed to honest-failure path; Production mode is the upgrade.",
            agent_name="RetrievalAgent",
            agent_file="agents/phase_03_retrieval/supervisor.py",
        ))
        return [], activities

    activities.append(create_activity(
        activity_type="reasoning",
        title="RetrievalAgent invoked (Strands + Bedrock)",
        details="Bedrock will choose which specialist tool to call",
        agent_name="RetrievalAgent",
        agent_file="agents/phase_03_retrieval/supervisor.py",
    ))

    supervisor = create_retrieval_system(activity_callback=collect)

    packages, _llm_reply = await supervisor.process_search(query, activity_callback=collect)

    # Belt-and-suspenders: if Bedrock chose to answer conversationally
    # without calling the SearchAgent tool, we'd have an empty package
    # list even though the user clearly asked for a trip. Fall back to
    # calling SearchAgent.hybrid_search() directly so Phase 3 always
    # surfaces hybrid results for trip-shaped prompts.
    if not packages:
        activities.append(create_activity(
            activity_type="reasoning",
            title="Supervisor returned no packages — fallback to direct hybrid search",
            details=(
                "Bedrock did not invoke _delegate_to_search; calling "
                "SearchAgent.hybrid_search directly to guarantee "
                "pgvector + tsvector + Cohere rerank coverage."
            ),
            agent_name="RetrievalAgent",
            agent_file="agents/phase_03_retrieval/supervisor.py",
        ))
        try:
            direct = await supervisor.search_agent.hybrid_search(query, limit=limit)
            packages = direct.get("packages", [])
        except Exception as exc:
            log_error("retrieval_direct_fallback", error=str(exc))

    products: List[Product] = []
    for pkg in packages[:limit]:
        # SearchAgent returns dicts with package_id; map to API Product shape.
        products.append(Product(
            product_id=pkg.get("package_id", ""),
            name=pkg.get("name", ""),
            brand=pkg.get("operator", ""),
            price=float(pkg.get("price_per_person", 0.0)),
            description=pkg.get("description", "") or "",
            image_url=pkg.get("image_url", "") or "",
            category=pkg.get("trip_type", "") or "",
            destination=pkg.get("destination"),
            region=pkg.get("region"),
            available_sizes=pkg.get("durations") or [],
            availability=pkg.get("availability") or {},
            highlights=pkg.get("highlights") or [],
            similarity=pkg.get("similarity"),
            pre_rerank_position=pkg.get("pre_rerank_position"),
            pre_rerank_similarity=pkg.get("pre_rerank_similarity"),
            rank_delta=pkg.get("rank_delta"),
        ))

    if not products:
        activities.append(create_activity(
            activity_type="result",
            title="Supervisor search returned no trips",
            details="Strands delegation + direct fallback both empty",
            agent_name="RetrievalAgent",
            agent_file="agents/phase_03_retrieval/supervisor.py",
        ))
        return products, activities

    activities.append(create_activity(
        activity_type="result",
        title=f"Supervisor returned {len(products)} trips",
        details="Bedrock-driven delegation completed",
        agent_name="RetrievalAgent",
        agent_file="agents/phase_03_retrieval/supervisor.py",
    ))

    return products, activities


# =============================================================================
# PHASE 4: ProductionAgent + AgentCore (Runtime/Gateway/Memory/Identity) + Aurora RLS
#
# AWS docs:
#   AgentCore overview:
#     https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html
#   Gateway MCP:
#     https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html
#   RDS Data API transactions (RLS):
#     https://docs.aws.amazon.com/rdsdataservice/latest/APIReference/API_BeginTransaction.html
# =============================================================================

def _memory_activity_to_entry(entry: Any) -> ActivityEntry:
    """Convert MemoryAgent/ProductionAgent activity to API ActivityEntry."""
    if isinstance(entry, ActivityEntry):
        return entry
    data = entry.model_dump() if hasattr(entry, "model_dump") else dict(entry)
    telemetry = data.pop("telemetry", None)
    return ActivityEntry(
        **data,
        telemetry=TraceTelemetry(**telemetry) if telemetry else None,
    )


async def _load_workflow_memory_facts(traveler_id: str) -> List[MemoryFact]:
    """Read recovery context in one short, authorized RLS transaction."""
    from backend.memory.store import get_memory_store

    store = get_memory_store()
    async with store.db.scoped_session(
        traveler_id=traveler_id,
        agent_type="orchestration_agent",
        authorization=get_agentcore_identity().authorization_context(),
    ) as tx:
        facts = await store.recall_preferences(
            traveler_id,
            limit=20,
            transaction_id=tx,
        )
    return [
        MemoryFact(
            key=str(fact.get("key") or ""),
            value=str(fact.get("value") or ""),
            source=fact.get("source"),
            confidence=fact.get("confidence"),
        )
        for fact in facts
        if fact.get("key") and fact.get("value")
    ]


def _append_recovery_memory_receipt(
    message: str,
    query: str,
    memory_facts: List[MemoryFact],
) -> str:
    """Show which Aurora facts materially shaped a disruption recovery."""
    is_disruption = re.search(
        r"\b(cancelled|canceled|disrupt|rebook|stranded)\b",
        query,
        re.I,
    )
    is_trip = re.search(r"\b(flight|departure|itinerary|trip)\b", query, re.I)
    if not (is_disruption and is_trip):
        return message

    by_key = {fact.key: fact.value for fact in memory_facts}
    receipt: List[str] = []
    home_airport = by_key.get("home_airport")
    if home_airport:
        receipt.append(f"- **Home airport:** {home_airport}")
    shellfish = by_key.get("shellfish_allergy")
    if shellfish:
        receipt.append(
            f"- **Dietary safety:** shellfish allergy — {shellfish}"
        )
    if not receipt:
        return message
    return (
        f"{message.rstrip()}\n\n"
        "**Traveler context applied from Aurora**\n"
        + "\n".join(receipt)
    )


async def orchestration_workflow(
    query: str,
    traveler_id: str,
    conversation_id: Optional[str] = None,
    *,
    resume: bool = False,
    travelers_count: int = 1,
    review_only: bool = False,
) -> tuple[List[Product], List[ActivityEntry], str, str, str, bool]:
    """Phase 5: the MeridianWorkflow AgentCore Runtime runs a Strands Graph.

    The graph saves each step in Aurora.

    Reuses Phase 3's retrieval and availability as graph steps, so the workflow
    story is "explicit edges and saved steps" rather than different search code.
    """
    from backend.agentcore.errors import AgentCoreNotConfiguredError
    from backend.agentcore.workflow_runtime import get_workflow_runtime
    from backend.agents.phase_05_workflow.runner import (
        WorkflowCommand,
        WorkflowConflictError,
        WorkflowRequestError,
    )
    from backend.agents.phase_05_workflow.state import WorkflowAuthorizationError
    from backend.db.journey_store import ExecutionLeaseLostError

    command = WorkflowCommand(
        query=query,
        traveler_id=traveler_id,
        thread_id=conversation_id or f"phase5-{uuid.uuid4().hex[:12]}",
        resume=resume,
        travelers_count=travelers_count,
        review_only=review_only,
    )
    try:
        final_state = await get_workflow_runtime().run(command)
    except AgentCoreNotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail=("The workflow Runtime is not configured: set AGENTCORE_WORKFLOW_RUNTIME_ARN "
                    "or deploy MeridianWorkflow."),
        ) from exc
    except WorkflowAuthorizationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except WorkflowRequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (WorkflowConflictError, ExecutionLeaseLostError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    raw_activities = final_state.get("activities", []) or []
    activities = [_dict_to_activity_entry(a) for a in raw_activities]
    for act in activities:
        log_activity_entry(act)
    packages = [
        package
        if isinstance(package, Product)
        else Product(**package)
        for package in (final_state.get("packages", []) or [])
    ]
    response = final_state.get("response") or "Workflow finished."
    conv_id = final_state.get("conversation_id") or ""
    workflow_status = final_state.get("workflow_status") or "complete"
    resumed_after_restart = bool(
        final_state.get("resumed_after_restart", False)
    )
    return (
        packages,
        activities,
        response,
        conv_id,
        workflow_status,
        resumed_after_restart,
    )


def _dict_to_activity_entry(activity: Any) -> ActivityEntry:
    if isinstance(activity, ActivityEntry):
        return activity
    if hasattr(activity, "model_dump"):
        activity = activity.model_dump()
    if not isinstance(activity, dict):
        return ActivityEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            activity_type="reasoning",
            title=str(activity),
        )
    telemetry = activity.pop("telemetry", None)
    activity.setdefault("id", str(uuid.uuid4()))
    activity.setdefault("timestamp", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    activity.setdefault("activity_type", "reasoning")
    activity.setdefault("title", "(unnamed)")
    return ActivityEntry(
        **activity,
        telemetry=TraceTelemetry(**telemetry) if telemetry else None,
    )


async def production_search(
    query: str,
    customer_id: str,
    conversation_id: Optional[str] = None,
    limit: int = 5,
    travelers_count: int = 1,
) -> tuple[List[Product], List[ActivityEntry], str, str, List[MemoryFact]]:
    """
    Production mode: identity + RLS recall, then the AgentCore Runtime owns the tool loop.

    Requires deployed AgentCore Runtime, Gateway, and Memory — see ``agentcore/README.md``.
    """
    from backend.agents.phase_04_production.concierge import create_production_agent
    from backend.memory.store import DEMO_TRAVELER_ID

    tid = customer_id or DEMO_TRAVELER_ID
    runtime = create_production_agent()
    packages, raw_activities, message, conv_id, facts = await runtime.process_turn(
        query,
        tid,
        conversation_id,
        limit,
        travelers_count=travelers_count,
    )
    activities = [_memory_activity_to_entry(a) for a in raw_activities]
    for act in activities:
        log_activity_entry(act)
    memory_facts = [
        MemoryFact(
            key=f["key"],
            value=f["value"],
            source=f.get("source"),
            confidence=f.get("confidence"),
        )
        for f in facts
    ]
    api_products = [
        Product(
            product_id=(getattr(pkg, "package_id", "") or ""),
            name=(getattr(pkg, "name", "") or ""),
            brand=(getattr(pkg, "operator", "") or ""),
            price=float(getattr(pkg, "price_per_person", 0.0) or 0.0),
            description=(getattr(pkg, "description", "") or ""),
            image_url=(getattr(pkg, "image_url", "") or ""),
            category=(getattr(pkg, "trip_type", "") or ""),
            destination=getattr(pkg, "destination", None),
            region=getattr(pkg, "region", None),
            available_sizes=getattr(pkg, "durations", None),
            availability=getattr(pkg, "availability", None),
            highlights=getattr(pkg, "highlights", None),
            similarity=getattr(pkg, "similarity", None),
        )
        for pkg in packages
    ]
    return api_products, activities, message, conv_id, memory_facts


# =============================================================================
# PHASE 3: PackageAgent — duration inventory and package details
# =============================================================================

def is_availability_query(query: str) -> bool:
    """Check if the query is asking about availability or departure slots."""
    query_lower = query.lower()
    availability_patterns = [
        'available', 'do you have', 'check availability',
        'availability', 'how many', 'what dates', 'dates available',
        'departure', 'departures', 'is the', 'is there', 'got any', 'slots'
    ]
    return any(pattern in query_lower for pattern in availability_patterns)


def _is_workflow_resume_query(query: str) -> bool:
    normalized = " ".join((query or "").lower().split())
    return normalized in {
        "resume",
        "resume workflow",
        "resume workflow from the saved step",
        "resume workflow from checkpoint",
        "continue workflow",
        "continue from the saved step",
        "continue from checkpoint",
    }


# Retain disconnected read-only chat turns until memory persistence finishes.
# Disconnecting never retries a request or claims to cancel an in-flight tool.
_stream_tasks: set[asyncio.Task] = set()


@router.post("/stream")
async def stream_chat(
    request: ChatRequest,
    principal: HttpPrincipal = Depends(require_http_principal),
) -> StreamingResponse:
    if request.phase != 4:
        raise HTTPException(status_code=422, detail="Streaming is available for the concierge.")
    # Authorize before sending headers, including when context is disabled.
    authorize_traveler(principal, request.customer_id)
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    connected = True
    started = clock()
    first_delta = False
    handoff = _needs_checkpointed_workflow(request.message)

    def enqueue(event):
        nonlocal first_delta
        if event.get("type") == "delta" and not first_delta:
            first_delta = True
            logger.info("Concierge first text received in %s ms", elapsed_ms(started))
        if connected:
            queue.put_nowait(event)

    def publish(event):
        # A workflow handoff intentionally replaces Runtime prose. Never briefly
        # show an answer the ordinary chat route would withhold.
        if handoff and event.get("type") == "delta":
            return
        loop.call_soon_threadsafe(enqueue, event)

    async def produce():
        token = chat_event_sink.set(publish)
        try:
            response = await chat(request, principal)
            if any(entry.activity_type == "error" for entry in response.activities):
                publish({"type": "error", "message": response.message})
            else:
                publish({"type": "complete", "response": response.model_dump()})
        except HTTPException as exc:
            publish({"type": "error", "message": str(exc.detail)})
        except Exception:
            logger.exception("Concierge stream failed")
            publish({"type": "error", "message": "The response was interrupted. Check the connection before trying again."})
        finally:
            chat_event_sink.reset(token)

    async def events():
        nonlocal connected
        task = asyncio.create_task(produce())
        _stream_tasks.add(task)
        task.add_done_callback(_stream_tasks.discard)
        try:
            yield 'data: {"type":"status","text":"Connecting to your concierge…"}\n\n'
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=10)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in ("complete", "error"):
                    break
        finally:
            connected = False

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
    })


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    principal: HttpPrincipal = Depends(require_http_principal),
) -> ChatResponse:
    """
    Process a chat message with the AI travel concierge.

    Routes to the appropriate search implementation based on phase:
    - Phase 1: Direct RDS Data API filters
    - Phase 2: MCP-backed SQL
    - Phase 3: Hybrid retrieval + Cohere rerank; PackageAgent for slot checks
    - Phase 4: Production concierge with AgentCore Runtime/Gateway/Memory
    """
    traveler_id = authorize_traveler(principal, request.customer_id)
    request = request.model_copy(update={"customer_id": traveler_id})

    turn_started = log_turn_start(
        request.phase,
        request.message,
        traveler_id=request.customer_id,
        conversation_id=request.conversation_id,
    )
    activities = []

    # The finished product offers a reviewable recovery, never a teaching-mode
    # instruction. Opening the plan is a separate explicit action; this reply
    # does not run a workflow or authorize an inventory write.
    if request.experience == "concierge" and request.phase == 4 and _needs_checkpointed_workflow(request.message):
        return _complete_chat_turn(ChatResponse(
            message="We can find alternatives and check their availability. Review a recovery plan before deciding whether to request a hold.",
            activities=[create_activity(
                activity_type="reasoning", title="Recovery review offered",
                details="The traveler can open a saved recovery plan. No recovery or hold has run.",
                agent_name="Concierge", agent_file="backend/routers/chat.py",
            )], conversation_id=request.conversation_id,
            recovery_request=request.message,
        ), request.phase, turn_started)

    # Only Phase 3 uses the local PackageAgent shortcut. Phase 4 must apply
    # the context guard and execute through the managed Runtime below. Multi-step
    # planning prompts that also ask for availability should NOT be collapsed
    # into this single specialist path; Phase 4 uses them as the bridge to the
    # checkpointed Workflow mode.
    if (
        request.phase == 3
        and is_availability_query(request.message)
        and not _needs_checkpointed_workflow(request.message)
    ):
        activities.append(create_activity(
            activity_type="reasoning",
            title="Processing with Multi-Agent Orchestration",
            details=f"Query: {request.message[:80]}{'...' if len(request.message) > 80 else ''}",
            agent_name="RetrievalAgent",
            agent_file="agents/phase_03_retrieval/supervisor.py"
        ))

        try:
            products, availability_activities, message = await retrieval_availability_search(request.message)
            activities.extend(availability_activities)

            follow_ups = ["Show similar trips", "What other durations are available?", "Find alternatives"]

            return _complete_chat_turn(
                ChatResponse(
                message=message,
                products=products if products else None,
                order=None,
                activities=activities,
                follow_ups=follow_ups,
                memory_facts=None,
            ),
                request.phase,
                turn_started,
            )
        except TravelerAuthorizationError as e:
            log_error("production_authorization", error=str(e))
            raise HTTPException(status_code=403, detail=str(e)) from e
        except Exception:
            error_ref = log_exception("package_agent")
            activities.append(create_activity(
                activity_type="error",
                title="PackageAgent error",
                details=f"The availability lookup failed. Reference {error_ref}.",
                agent_name="PackageAgent",
                agent_file="agents/phase_03_retrieval/package_agent.py"
            ))
            # Fall through to regular search

    # Phase 4: ProductionAgent + AgentCore Runtime/Gateway/Memory
    if request.phase == 4:
        from backend.memory.store import DEMO_TRAVELER_ID

        if not request.memory_enabled:
            activities.append(create_activity(
                activity_type="reasoning",
                title="Traveler memory disabled for this run",
                details=(
                    "Production did not read Aurora traveler preferences, prior "
                    "conversations, or AgentCore Memory. No memory writeback occurred."
                ),
                agent_name="ProductionAgent",
                agent_file="agents/phase_04_production/concierge.py",
            ))
            return _complete_chat_turn(
                ChatResponse(
                    message=(
                        "Traveler context is off for this run. Enable **Use traveler "
                        "context** to let Production recall Jordan's saved preferences "
                        "and prior Tokyo plan. Nothing was read from or written to memory."
                    ),
                    products=None,
                    order=None,
                    activities=activities,
                    follow_ups=[
                        "Enable traveler context",
                        "Recall my Tokyo plan",
                        "Find a Tokyo culture trip using my preferences",
                    ],
                    memory_facts=None,
                ),
                request.phase,
                turn_started,
            )

        activities.append(create_activity(
            activity_type="reasoning",
            title="Processing with Production concierge (Runtime + Gateway + Memory)",
            details=f"Query: {request.message[:80]}{'...' if len(request.message) > 80 else ''}",
            agent_name="ProductionAgent",
            agent_file="agents/phase_04_production/concierge.py",
        ))
        try:
            products, search_activities, raw_message, conv_id, memory_facts = await production_search(
                request.message,
                customer_id=request.customer_id or DEMO_TRAVELER_ID,
                conversation_id=request.conversation_id,
                limit=5,
                travelers_count=request.travelers_count,
            )
            activities.extend(search_activities)
            needs_workflow = _needs_checkpointed_workflow(request.message)
            if needs_workflow:
                if "tokyo" in request.message.lower():
                    tokyo_products = [
                        product
                        for product in products
                        if "tokyo" in " ".join(
                            filter(
                                None,
                                (
                                    product.name,
                                    product.destination,
                                    product.region,
                                ),
                            )
                        ).lower()
                    ]
                    if tokyo_products:
                        products = tokyo_products
                activities.append(create_activity(
                    activity_type="reasoning",
                    title="Checkpointed workflow required",
                    details=(
                        "Production mode recalled traveler context and found candidate "
                        "trips, but this prompt has dependent planning steps. "
                        "Hand off to Workflow mode so package search and duration "
                        "availability can checkpoint separately."
                    ),
                    agent_name="ProductionAgent",
                    agent_file="agents/phase_04_production/concierge.py",
                ))
                message = _PHASE4_WORKFLOW_TRANSITION_MESSAGE
            else:
                # ProductionAgent persists the managed Runtime decision. Return
                # that same decision unchanged so the user-facing response
                # is authored by AgentCore Runtime rather than a second local
                # model pass.
                message = raw_message
            # The badge names the model the Runtime reported for this turn.
            # The workflow handoff is fixed text, so no model wrote it.
            runtime_model = None if needs_workflow else runtime_model_id(search_activities)
            follow_ups = (
                [
                    "Run this in Workflow",
                    "Rework the itinerary",
                    "Check duration availability for the best three options",
                ]
                if needs_workflow
                else generate_follow_ups(request.message, products, request.phase)
            )
            return _complete_chat_turn(
                ChatResponse(
                message=message,
                products=products if products else None,
                order=None,
                activities=activities,
                follow_ups=follow_ups,
                conversation_id=conv_id,
                memory_facts=memory_facts,
                model_label=bedrock_model_label(runtime_model) if runtime_model else None,
            ),
                request.phase,
                turn_started,
            )
        except TravelerAuthorizationError as e:
            log_error("production_authorization", error=str(e))
            raise HTTPException(status_code=403, detail=str(e)) from e
        except Exception as e:
            error_ref = log_exception("production_search")
            from backend.agentcore.errors import AgentCoreNotConfiguredError

            is_agentcore_config_error = isinstance(e, AgentCoreNotConfiguredError)
            activities.append(create_activity(
                activity_type="error",
                title=(
                    "AgentCore platform not configured"
                    if is_agentcore_config_error
                    else "Concierge error"
                ),
                details=(
                    "AgentCore Runtime, Gateway or Memory is not configured. "
                    if is_agentcore_config_error
                    else "The Production concierge failed. "
                ) + f"Reference {error_ref}.",
                agent_name="ProductionAgent",
                agent_file="agents/phase_04_production/concierge.py",
            ))
            return _complete_chat_turn(
                ChatResponse(
                message=(
                    "Production mode requires deployed AgentCore Runtime, Gateway, "
                    "and Memory resources. Run `agentcore deploy -y` from "
                    "`meridian/meridian_agentcore`, then run "
                    "`python scripts/sync_agentcore_env.py --write` from `meridian`."
                    if is_agentcore_config_error
                    else "I encountered an error in Production mode. Please try again."
                ),
                products=None,
                order=None,
                activities=activities,
                follow_ups=["Romantic week in Europe", "Family-friendly beach resort", "Tokyo culture trip"],
            ),
                request.phase,
                turn_started,
                error=str(e),
            )

    # Phase 5: Strands Graph workflow with snapshots saved in Aurora.
    if request.phase == 5:
        from backend.memory.store import DEMO_TRAVELER_ID
        try:
            resume_workflow = request.resume or _is_workflow_resume_query(
                request.message
            )
            if request.review_only and resume_workflow:
                raise HTTPException(status_code=422, detail="A review starts a new plan. Read the saved recovery before choosing to resume it.")
            (
                workflow_packages,
                workflow_activities,
                raw_message,
                conv_id,
                workflow_status,
                resumed_after_restart,
            ) = await orchestration_workflow(
                request.message,
                traveler_id=request.customer_id or DEMO_TRAVELER_ID,
                conversation_id=request.conversation_id,
                resume=resume_workflow,
                travelers_count=request.travelers_count,
                **({"review_only": True} if request.review_only else {}),
            )
            activities.extend(workflow_activities)
            workflow_memory_facts: List[MemoryFact] = []
            if workflow_status != "paused":
                try:
                    workflow_memory_facts = await _load_workflow_memory_facts(
                        request.customer_id or DEMO_TRAVELER_ID
                    )
                    if workflow_memory_facts:
                        activities.append(create_activity(
                            activity_type="search",
                            title="Aurora recall: recovery context",
                            details=(
                                "Saved traveler preferences read under RLS "
                                "for review alongside the recorded recovery"
                            ),
                            agent_name="OrchestrationAgent",
                            agent_file="backend/memory/store.py",
                        ))
                except Exception as exc:
                    log_error("workflow_memory_fetch", error=str(exc))
            # Return the checkpointed operational result verbatim. A second
            # model rewrite can contradict the hold/lease result and adds an
            # avoidable wait after the durable workflow already completed.
            message = raw_message
            follow_ups = (
                ["Resume workflow from the saved step"]
                if workflow_status == "paused"
                else generate_follow_ups(
                    request.message, workflow_packages, request.phase
                )
            )
            return _complete_chat_turn(
                ChatResponse(
                message=message,
                products=workflow_packages if workflow_packages else None,
                order=None,
                activities=activities,
                follow_ups=follow_ups,
                conversation_id=conv_id,
                memory_facts=workflow_memory_facts or None,
                workflow_status=workflow_status,
                workflow_resumed_after_restart=resumed_after_restart,
            ),
                request.phase,
                turn_started,
            )
        except HTTPException:
            # Preserve authorization/conflict HTTP status from the workflow.
            raise
        except HoldOutcomeUnknown as e:
            log_error("orchestration_workflow", error=str(e))
            raise HTTPException(
                status_code=503,
                detail=f"{e} Re-read the saved journey before retrying.",
            ) from e
        except TravelerAuthorizationError as e:
            log_error("workflow_authorization", error=str(e))
            raise HTTPException(status_code=403, detail=str(e)) from e
        except Exception as e:
            # Keep the stack in the log; show the user a stable reference,
            # not a Python exception string.
            error_ref = uuid.uuid4().hex[:8]
            logger.exception(
                "orchestration_workflow failed (ref=%s)", error_ref
            )
            log_error("orchestration_workflow", error=str(e), ref=error_ref)
            raise HTTPException(
                status_code=503,
                detail=("Recovery was interrupted. Re-read the saved journey before retrying. "
                        f"Reference {error_ref}."),
            ) from e

    # Phase 3: live Strands supervisor (Bedrock-driven tool delegation).
    phase3_fn = retrieval_supervisor_search
    phase3_method = "Hybrid (pgvector + tsvector) + Cohere Rerank via Strands Supervisor"

    phase_configs = {
        1: ("SQLAgent", "Direct RDS Data API", sql_search, "backend/routers/chat.py"),
        2: ("MCPAgent", "MCP tool routing", mcp_search, "backend/routers/chat.py"),
        3: ("RetrievalAgent", phase3_method, phase3_fn, "agents/phase_03_retrieval/supervisor.py"),
    }

    agent_name, method, search_fn, agent_file = phase_configs[request.phase]

    activities.append(create_activity(
        activity_type="reasoning",
        title=f"Processing with {method}",
        details=f"Query: {request.message[:80]}{'...' if len(request.message) > 80 else ''}",
        agent_name=agent_name,
        agent_file=agent_file
    ))

    if request.phase == 1 and _wants_domain_tool(request.message):
        activities.append(create_activity(
            activity_type="result",
            title="Boundary reached: comparison and FX need MCP tools",
            details=(
                "SQL mode owns direct catalog filters. Side-by-side comparison "
                "and currency conversion are explicit domain operations."
            ),
            agent_name=agent_name,
            agent_file=agent_file,
        ))
        return _complete_chat_turn(
            ChatResponse(
                message=(
                    "SQL can filter catalog rows by trip type, destination, and "
                    "price, but this request combines comparison with currency "
                    "conversion. Switch to MCP, where `compare_packages` and "
                    "`currency_convert` are explicit tools."
                ),
                products=None,
                order=None,
                activities=activities,
                follow_ups=[
                    "Show me city trips under $2,000 per traveler.",
                    "Show me beach trips under $2,500 per traveler.",
                ],
            ),
            request.phase,
            turn_started,
        )

    if (
        request.phase == 2
        and not _wants_domain_tool(request.message)
        and _is_semantic_intent_query(request.message)
    ):
        activities.append(create_activity(
            activity_type="result",
            title="Boundary reached: mood intent needs semantic retrieval",
            details=(
                "MCP mode can invoke explicit tools and structured queries, but "
                "this prompt describes meaning that is not a catalog field."
            ),
            agent_name=agent_name,
            agent_file=agent_file,
        ))
        return _complete_chat_turn(
            ChatResponse(
                message=(
                    "MCP can invoke explicit tools and structured queries, but "
                    "this request describes a mood rather than a catalog field "
                    "or tool operation. Switch to Retrieval, which uses semantic "
                    "search and reranking to understand the intent."
                ),
                products=None,
                order=None,
                activities=activities,
                follow_ups=[
                    "Compare three trip types side by side and convert their prices to euros.",
                    "What is the price range for Tokyo trips?",
                ],
            ),
            request.phase,
            turn_started,
        )
    
    try:
        # Phase 2 returns a 3-tuple (products, activities, domain_text)
        # because its custom MCP path can produce a non-product reply.
        # All other phases stay on the 2-tuple shape.
        domain_text: Optional[str] = None
        # Set only when a real Bedrock call wrote this reply. A pure tool
        # result (SQL, MCP, or the raw search prose) carries no model and
        # the frontend must show no model badge for it.
        model_label: Optional[str] = None
        if request.phase == 2:
            products, search_activities, domain_text = await mcp_search(
                request.message,
                traveler_id=request.customer_id,
                limit=5,
            )
        elif (
            request.phase == 3
            and not _is_memory_recall_query(request.message)
            and _is_availability_query(request.message)
        ):
            # Supervised multi-agent: an availability question routes to the
            # PackageAgent specialist (departure slots on a named package),
            # not a fresh hybrid search. Different specialist, different tool,
            # different trace — this is where "supervised multi-agent search"
            # becomes visible. Memory-recall is checked first so it keeps
            # precedence (its honest-failure path handles those prompts).
            products, search_activities, domain_text = await retrieval_availability_search(
                request.message
            )
        else:
            products, search_activities = await search_fn(request.message, limit=5)
        activities.extend(search_activities)

        # Generate personalized response message
        if domain_text:
            # Custom MCP produced a domain readout (compare / FX / loyalty
            # / price range / inventory) - that IS the answer. It
            # names the specific trips it surfaced, so we don't tack on a
            # generic "I also found N trips" suffix; the recommendation grid
            # speaks for itself.
            message = domain_text
        elif products:
            if request.phase in (3, 4):
                top_similarity = products[0].similarity
                if top_similarity and top_similarity > 0.8:
                    raw_message = f"I found {len(products)} trips that closely match what you're looking for:"
                else:
                    raw_message = f"Here are {len(products)} trips that might interest you:"
            else:
                prices = [product.price for product in products]
                lowest_price = min(prices)
                highest_price = max(prices)
                lowest = [
                    product.name
                    for product in products
                    if product.price == lowest_price
                ]
                lowest_names = " and ".join(lowest[:2])
                raw_message = (
                    f"I found {len(products)} trips within your filters, from "
                    f"${lowest_price:,.0f} to ${highest_price:,.0f} per traveler. "
                    f"{lowest_names} {'are' if len(lowest) > 1 else 'is'} the "
                    f"lowest-priced {'options' if len(lowest) > 1 else 'option'} "
                    f"at ${lowest_price:,.0f}; compare duration and live inventory "
                    "on the cards below."
                )

            # Phase 3 specifically benefits from polish: similarity scores
            # in the product list let the model name *why* each trip
            # matched (intent, vibe, dates) instead of just listing.
            if request.phase == 3:
                message, model_label = await _polish_and_record(
                    phase=3,
                    mode_label="Retrieval",
                    agent_name="RetrievalAgent",
                    user_query=request.message,
                    raw_message=raw_message,
                    products=products,
                    activities=activities,
                )
            else:
                message = raw_message
        else:
            if request.phase == 2:
                message = (
                    "MCP ran the available explicit catalog tools but found no "
                    "literal match. This request expresses intent rather than a "
                    "catalog field or typed operation. Switch to Retrieval for "
                    "semantic search and reranking."
                )
            elif request.phase == 1:
                # Explain the actual boundary the prompt crossed. The canonical
                # Phase 1 stretch is a compare + FX operation, while free-form
                # intent prompts fail for a different reason.
                if _wants_domain_tool(request.message):
                    raw = (
                        "Search summary:\n"
                        "- SQLAgent only owns direct catalog filters and returned "
                        "0 rows for this business operation.\n"
                        "- Comparing packages side by side and converting each "
                        "price requires reusable domain contracts, not another "
                        "WHERE clause.\n"
                        "- Next step: switch to MCP mode, where compare_packages "
                        "and currency_convert are typed, auditable tools."
                    )
                else:
                    raw = (
                        "Search summary:\n"
                        "- SQLAgent ran a keyword ILIKE on name/description/"
                        "destination/operator in trip_packages and returned 0 rows.\n"
                        "- The prompt expresses intent rather than a literal keyword "
                        "that lives in a trip_packages column.\n"
                        "- SQL filters match exact tokens, not meaning, so a "
                        "natural-language wish slips straight through.\n"
                        "- Next step: switch to Retrieval mode, which uses Cohere "
                        "Embed v4 + pgvector + Cohere Rerank to read intent."
                    )
                message = raw
            elif request.phase == 3 and _is_memory_recall_query(request.message):
                # Keep the staged failure concise and deterministic. The
                # absence of a memory tool is the lesson, not a model-generated
                # apology for returning zero search results.
                message = (
                    "Retrieval understands the request, but it only searches the "
                    "current prompt; it has no traveler profile or prior-turn "
                    "memory. Switch to Production, where AgentCore Memory and "
                    "Aurora RLS can recall the October Tokyo plan and saved "
                    "preferences."
                )
            else:
                message = "I couldn't find exact matches. Try different destinations, trip types, or travel dates."

        # Generate contextual follow-up suggestions
        follow_ups = generate_follow_ups(request.message, products, request.phase)
        
        return _complete_chat_turn(
            ChatResponse(
            message=message,
            products=products if products else None,
            order=None,
            activities=activities,
            follow_ups=follow_ups,
            model_label=model_label,
        ),
            request.phase,
            turn_started,
        )

    except HTTPException:
        raise
    except TravelerAuthorizationError as e:
        log_error(context="traveler_authorization", error=str(e), phase=request.phase)
        raise HTTPException(status_code=403, detail=str(e)) from e
    except Exception as e:
        error_ref = log_exception("chat_search")
        activities.append(create_activity(
            activity_type="error",
            title="Error processing request",
            details=f"The request failed. Reference {error_ref}.",
            agent_name=agent_name,
            agent_file=agent_file
        ))

        return _complete_chat_turn(
            ChatResponse(
            message="I encountered an error. Please try again or browse featured trips.",
            products=None,
            order=None,
            activities=activities,
            follow_ups=["Tokyo culture trip", "Beach resort for two", "City trips in Europe"]
        ),
            request.phase,
            turn_started,
            error=str(e),
        )


# =============================================================================
# BOOKING PROCESSING — demonstrates agentic booking flow
# =============================================================================

class OrderRequest(BaseModel):
    """Request model for a no-payment courtesy hold."""
    product_id: str = Field(min_length=1, max_length=50)
    size: Optional[str] = Field(default=None, min_length=1, max_length=50)
    quantity: int = Field(default=1, gt=0, le=12)
    phase: Literal[1, 2, 3, 4, 5]
    traveler_id: str = Field(default="trv_meridian_demo", min_length=1, max_length=50)
    action: Literal["hold"] = "hold"
    # Phase 4 holds run inside the conversation's AgentCore Memory session, so
    # the showcase passes the active conversation id along with the hold.
    conversation_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


class OrderResponse(BaseModel):
    """Response model for order processing."""
    message: str
    order: Optional[Order] = None
    activities: List[ActivityEntry]


HOLD_PACKAGE_SQL = """
    SELECT package_id, name, operator, price_per_person, description,
           image_url, trip_type, durations, availability
    FROM trip_packages
    WHERE package_id = %s
"""


async def _package_for_hold(product_id: str) -> tuple[dict, dict]:
    """Return (catalog row, API product) for a hold, or raise 404."""
    rows = await get_rds_data_client().execute(HOLD_PACKAGE_SQL, (product_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="That trip package is no longer available.")
    return rows[0], row_to_api_product(rows[0])


def _requested_duration(row: dict, requested: Optional[str]) -> str:
    durations = row.get("durations") or []
    duration = requested or (durations[0] if durations else None)
    if not duration or duration not in durations:
        raise HTTPException(
            status_code=422, detail="Select one of the package's published durations."
        )
    return duration


def _order_from_hold(pkg: dict, request: "OrderRequest", duration: str, hold: dict) -> Order:
    total = round(float(pkg["price"]) * request.quantity, 2)
    return Order(
        order_id=str(hold["bookingId"]),
        items=[OrderItem(product_id=pkg["product_id"], name=pkg["name"], size=duration,
                         quantity=request.quantity, unit_price=pkg["price"])],
        subtotal=total,
        tax=0.0,
        shipping=0.0,
        total=total,
        status=str(hold.get("status") or "held"),
        estimated_delivery=None,
        departure_date=None,
        hold_expires_at=hold.get("expiresAt"),
        hold_created_at=hold.get("createdAt"),
        seats_remaining=hold.get("seatsRemaining"),
        payment_required=False,
    )


async def production_hold(request: "OrderRequest") -> OrderResponse:
    """Phase 4 hold: the runtime asks the gateway, Cedar decides, the Lambda writes.

    The traveler's click is the confirmation. The backend passes it to the runtime
    as ``hold_confirmed``; the runtime pins it onto the tool call; the gateway's
    Cedar policy permits the hold only with it. Nothing here writes to Aurora.
    """
    from backend.agents.phase_04_production.concierge import HoldTarget, create_production_agent

    row, pkg = await _package_for_hold(request.product_id)
    duration = _requested_duration(row, request.size)
    target = HoldTarget(
        package_id=pkg["product_id"],
        duration=duration,
        travelers=request.quantity,
        unit_price_cents=int(round(float(pkg["price"]) * 100)),
    )
    try:
        outcome = await create_production_agent().process_hold(
            request.traveler_id, request.conversation_id, target
        )
    except TravelerAuthorizationError as e:
        log_error(context="hold_authorization", error=str(e), phase=4)
        raise HTTPException(status_code=403, detail=str(e)) from e
    except Exception as e:
        log_error(context="production_hold", error=str(e), phase=4)
        raise HTTPException(
            status_code=503,
            detail="The governed hold could not be completed. Check the AgentCore platform.",
        ) from e
    activities = [_memory_activity_to_entry(a) for a in outcome.activities]
    for act in activities:
        log_activity_entry(act)
    if not outcome.hold:
        return OrderResponse(message=outcome.message, order=None, activities=activities)
    order = _order_from_hold(pkg, request, duration, outcome.hold)
    log_order(phase=4, order_id=order.order_id, product_id=request.product_id,
              total=order.total, status=order.status)
    return OrderResponse(message=outcome.message, order=order, activities=activities)


class BookingRequest(BaseModel):
    """Request model for confirming a held package. Catalog inventory only, no payment."""
    booking_id: str = Field(min_length=1, max_length=50)
    phase: Literal[4] = 4
    traveler_id: str = Field(default="trv_meridian_demo", min_length=1, max_length=50)
    conversation_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


class BookingResponse(BaseModel):
    """Response model for a booking confirmation."""
    message: str
    order: Optional[Order] = None
    activities: List[ActivityEntry]


TRAVELER_BOOKING_SQL = """
    SELECT b.booking_id, b.status, b.total_amount, b.hold_expires_at, b.confirmed_at,
           b.created_at AS hold_created_at,
           bl.package_id, bl.duration, bl.travelers_count, bl.unit_price
      FROM bookings b
      JOIN booking_lines bl ON bl.booking_id = b.booking_id
     WHERE b.booking_id = %s AND b.traveler_id = %s
"""


async def _traveler_booking(traveler_id: str, booking_id: str) -> dict:
    """The booking and its line as Aurora holds them, read under the traveler's RLS scope.

    The grant is checked for the same identity envelope the concierge turn uses,
    so the read authorizes exactly where the hold and the confirmation do.
    """
    db = get_rds_data_client()
    async with db.scoped_session(
        traveler_id=traveler_id,
        agent_type="concierge_agent",
        authorization=get_agentcore_identity().scope_for_turn().authorization,
    ) as transaction_id:
        rows = await db.execute(
            TRAVELER_BOOKING_SQL, (booking_id, traveler_id), transaction_id=transaction_id
        )
    if not rows:
        raise HTTPException(status_code=404, detail="That booking is not on record for you.")
    return rows[0]


def _order_from_booking(pkg: dict, line: dict, booking: dict) -> Order:
    quantity = int(line["travelers_count"])
    unit_price = float(line["unit_price"])
    total = float(booking.get("totalAmount") or line["total_amount"])
    return Order(
        order_id=str(booking["bookingId"]),
        items=[OrderItem(product_id=pkg["product_id"], name=pkg["name"],
                         size=str(line["duration"]), quantity=quantity, unit_price=unit_price)],
        subtotal=total,
        tax=0.0,
        shipping=0.0,
        total=total,
        status=str(booking.get("status") or "confirmed"),
        estimated_delivery=None,
        departure_date=None,
        hold_expires_at=booking.get("expiresAt"),
        hold_created_at=booking.get("createdAt"),
        payment_required=False,
        confirmed_at=booking.get("confirmedAt"),
    )


def _recorded_order(line: dict) -> Order:
    """Render recorded amounts and status, never today's catalog price."""
    def utc_instant(value):
        if not value:
            return None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Data API returns PostgreSQL UTC timestamps without an offset.
        # Explicit UTC prevents a browser in London/Tokyo shifting the TTL.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    expires = utc_instant(line.get("hold_expires_at"))
    created = utc_instant(line.get("hold_created_at"))
    confirmed = utc_instant(line.get("confirmed_at"))
    state = str(line["status"])
    if state == "held" and expires:
        if expires <= datetime.now(timezone.utc):
            state = "expired"
    return _order_from_booking(
        {"product_id": line["package_id"], "name": line.get("name") or line["package_id"]},
        line,
        {"bookingId": line["booking_id"], "status": state,
         "expiresAt": expires.isoformat() if expires else None,
         "createdAt": created.isoformat() if created else None,
         "confirmedAt": confirmed.isoformat() if confirmed else None},
    )


@router.get("/bookings/{booking_id}", response_model=OrderResponse)
async def read_booking(
    booking_id: str,
    principal: HttpPrincipal = Depends(require_http_principal),
) -> OrderResponse:
    """Reconcile an acknowledgement loss without invoking a write or model."""
    line = await _traveler_booking(principal.traveler_id, booking_id)
    return OrderResponse(message="Booking read from Aurora.", order=_recorded_order(line), activities=[])


@router.get("/holds", response_model=OrderResponse)
async def read_hold(
    conversation_id: str = Query(min_length=1, max_length=128),
    product_id: str = Query(min_length=1, max_length=50),
    duration: str = Query(min_length=1, max_length=50),
    quantity: int = Query(ge=1, le=12),
    principal: HttpPrincipal = Depends(require_http_principal),
) -> OrderResponse:
    """Look up this exact direct-hold intent under the authenticated traveler."""
    db = get_rds_data_client()
    async with db.scoped_session(
        traveler_id=principal.traveler_id, agent_type="concierge_agent",
        authorization=get_agentcore_identity().scope_for_turn().authorization,
    ) as tx:
        rows = await db.execute(
            """SELECT b.booking_id, b.status, b.total_amount, b.hold_expires_at,
                      b.created_at AS hold_created_at, b.confirmed_at,
                      bl.package_id, bl.duration, bl.travelers_count, bl.unit_price
                 FROM journeys j
                 JOIN journey_threads jt ON jt.journey_id = j.journey_id
                 JOIN hold_requests hr ON hr.journey_id = j.journey_id
                 JOIN bookings b ON b.booking_id = hr.booking_id
                 JOIN booking_lines bl ON bl.booking_id = b.booking_id
                WHERE j.traveler_id = %s AND b.traveler_id = %s
                  AND jt.thread_id = %s AND bl.package_id = %s
                  AND bl.duration = %s AND bl.travelers_count = %s""",
            (principal.traveler_id, principal.traveler_id, f"concierge:{conversation_id}",
             product_id, duration, quantity), transaction_id=tx,
        )
    if len(rows) > 1:
        raise HTTPException(409, "Multiple receipts match this intent. Inspect the saved journey before continuing.")
    return OrderResponse(
        message="Hold read from Aurora." if rows else "No committed hold is recorded for this intent yet.",
        order=_recorded_order(rows[0]) if rows else None, activities=[],
    )


async def production_booking(request: "BookingRequest") -> BookingResponse:
    """Phase 4 confirmation: the runtime asks the gateway, Cedar decides, Aurora confirms.

    The traveler's click is the confirmation. The backend reads the held booking
    under RLS so the total the policy judges is the total Aurora holds, then
    passes the confirmation to the runtime as ``booking_confirmed``. Nothing
    here writes to Aurora: the SQL function behind the gateway tool flips the
    booking from held to confirmed, or refuses by name.
    """
    from backend.agents.phase_04_production.concierge import BookingTarget, create_production_agent

    line = await _traveler_booking(request.traveler_id, request.booking_id)
    _row, pkg = await _package_for_hold(str(line["package_id"]))
    target = BookingTarget(
        booking_id=str(line["booking_id"]),
        total_cents=int(round(float(line["total_amount"]) * 100)),
        package_id=pkg["product_id"],
        duration=str(line["duration"]),
        travelers=int(line["travelers_count"]),
    )
    try:
        outcome = await create_production_agent().process_booking(
            request.traveler_id, request.conversation_id, target
        )
    except TravelerAuthorizationError as e:
        log_error(context="booking_authorization", error=str(e), phase=4)
        raise HTTPException(status_code=403, detail=str(e)) from e
    except Exception as e:
        log_error(context="production_booking", error=str(e), phase=4)
        raise HTTPException(
            status_code=503,
            detail="The governed booking could not be completed. Check the AgentCore platform.",
        ) from e
    activities = [_memory_activity_to_entry(a) for a in outcome.activities]
    for act in activities:
        log_activity_entry(act)
    if not outcome.booking:
        return BookingResponse(message=outcome.message, order=None, activities=activities)
    order = _order_from_booking(pkg, line, outcome.booking)
    log_order(phase=4, order_id=order.order_id, product_id=pkg["product_id"],
              total=order.total, status=order.status)
    return BookingResponse(message=outcome.message, order=order, activities=activities)


@router.post("/book", response_model=BookingResponse)
async def confirm_booking(
    request: BookingRequest,
    principal: HttpPrincipal = Depends(require_http_principal),
) -> BookingResponse:
    """
    Confirm a held trip package for the authorized traveler.

    Books catalog inventory in the Meridian database. No supplier is contacted
    and no payment is authorized or captured. The gateway's Cedar policy decides
    on the traveler's confirmation and the saved budget before Aurora confirms.
    """
    traveler_id = authorize_traveler(principal, request.traveler_id)
    request = request.model_copy(update={"traveler_id": traveler_id})
    return await production_booking(request)


@router.post("/order", response_model=OrderResponse)
async def process_order(
    request: OrderRequest,
    principal: HttpPrincipal = Depends(require_http_principal),
) -> OrderResponse:
    """Place every clicked hold through Runtime, Gateway, Cedar and the holds Lambda.

    The phase selects the demonstration view, never the authorization path.
    Missing AgentCore configuration fails closed; no direct SQL write fallback.
    Phase 5 automatic holds still use their checkpointed workflow intent.
    """
    traveler_id = authorize_traveler(principal, request.traveler_id)
    request = request.model_copy(update={"traveler_id": traveler_id})
    return await production_hold(request)
