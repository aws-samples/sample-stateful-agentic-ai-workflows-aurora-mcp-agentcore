"""
Chat API Router for Meridian.

Handles chat interactions with the AI travel concierge across five phases:
- Phase 1: Direct RDS Data API connection (SQL filters on trip_packages)
- Phase 2: Via MCP (awslabs.postgres-mcp-server) abstraction
- Phase 3: Hybrid retrieval (semantic + lexical) + Cohere rerank
- Phase 4: Production concierge with AgentCore Runtime, Gateway, Memory
- Phase 5: LangGraph workflow orchestration

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

import os
import re
import uuid
from datetime import datetime
from typing import Literal, Optional, List, Any, Dict
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.rds_data_client import get_rds_data_client
from backend.db.embedding_service import get_embedding_service
from backend.config import config
from backend.logging_config import log_search, log_order, log_error, log_turn_start, log_turn_complete, log_activity_entry
from backend.search_utils import (
    parse_search_query,
    execute_keyword_search,
    build_search_sql,
    results_to_packages,
)
from backend.catalog_compat import row_to_api_product, rows_to_api_products

# MCP clients — Phase 2 demos two distinct MCP servers side-by-side:
#   1. awslabs.postgres-mcp-server (generic SQL transport, public AWS server)
#   2. backend.mcp.concierge_server (custom domain tools - compare,
#      seasonal pricing, region inventory, FX, loyalty)
#
# Memory tools live in Phase 4 by design (Aurora RLS + AgentCore) - the
# Phase 2 narrative is "what does a CUSTOM MCP get you that the public
# one can't?" so domain logic, not memory, is what we showcase here.
from backend.mcp.mcp_client import mcp_session
from backend.mcp.concierge_mcp_client import concierge_mcp_session
from backend.llm_polish import polish_concierge_reply


router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    """Request model for chat endpoint."""
    message: str
    phase: Literal[1, 2, 3, 4, 5]
    customer_id: Optional[str] = None
    conversation_id: Optional[str] = None


class TraceTelemetry(BaseModel):
    """Optional rich telemetry for trace UI."""
    category: Optional[str] = None
    component: Optional[str] = None
    status: Optional[str] = None
    fields: Optional[List[dict]] = None
    memory: Optional[dict] = None
    tokens: Optional[dict] = None


class ActivityEntry(BaseModel):
    """Model for agent activity entries."""
    id: str
    timestamp: str
    activity_type: str
    title: str
    details: Optional[str] = None
    sql_query: Optional[str] = None
    execution_time_ms: Optional[int] = None
    agent_name: Optional[str] = None
    agent_file: Optional[str] = None
    telemetry: Optional[TraceTelemetry] = None


class Product(BaseModel):
    """Trip package in API shape (legacy field names for frontend)."""
    product_id: str
    name: str
    brand: str
    price: float
    description: str
    image_url: str
    category: str
    available_sizes: Optional[List[str]] = None
    similarity: Optional[float] = None


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


def create_activity(
    activity_type: str,
    title: str,
    details: Optional[str] = None,
    sql_query: Optional[str] = None,
    execution_time_ms: Optional[int] = None,
    agent_name: Optional[str] = None,
    agent_file: Optional[str] = None
) -> ActivityEntry:
    """Create an activity entry."""
    entry = ActivityEntry(
        id=str(uuid.uuid4()),
        timestamp=datetime.utcnow().isoformat() + "Z",
        activity_type=activity_type,
        title=title,
        details=details,
        sql_query=sql_query,
        execution_time_ms=execution_time_ms,
        agent_name=agent_name,
        agent_file=agent_file
    )
    log_activity_entry(entry)
    return entry


def _complete_chat_turn(
    response: ChatResponse,
    phase: int,
    started_at: float,
    *,
    error: Optional[str] = None,
) -> ChatResponse:
    log_turn_complete(
        phase,
        products_count=len(response.products) if response.products else 0,
        activities_count=len(response.activities),
        started_at=started_at,
        error=error,
    )
    return response


def generate_follow_ups(query: str, products: List[Product], phase: int) -> List[str]:
    """Generate contextual follow-up suggestions based on the query and results.

    Phase 1 & 2: SQL filter search (trip_type, operator, price filters)
    Phase 3: Hybrid retrieval + Cohere rerank (understands natural language)

    Suggestions are designed to:
    1. Show queries that work in the current phase
    2. For Phase 1/2, include semantic queries that will fail to demonstrate limitations
    """
    follow_ups = []
    query_lower = query.lower()

    if products:
        # Get categories and brands from results
        categories = list(set(p.category for p in products))
        brands = list(set(p.brand for p in products if p.brand))
        prices = [p.price for p in products]
        primary_category = categories[0] if categories else None

        category_keywords_map = {
            "City Breaks": "city breaks",
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
                    follow_ups.append(f"{category_keyword} under $2000")

            if brands:
                for brand in brands:
                    if brand.lower() not in query_lower:
                        follow_ups.append(f"{brand} {category_keyword}")
                        break

            if "City Breaks" in categories:
                follow_ups.append("Beach & Resort")
            elif "Beach & Resort" in categories:
                follow_ups.append("Adventure & Outdoors")
            elif "Adventure & Outdoors" in categories:
                follow_ups.append("Wellness & Luxury")
            else:
                follow_ups.append("Show me city breaks")

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
                follow_ups.append("Weekend city break under $2k")
            elif "Beach & Resort" not in categories:
                follow_ups.append("Relaxing beach vacation")

    else:
        if phase in [1, 2]:
            follow_ups = [
                "City breaks",
                "Beach & Resort",
                "Business travel",
            ]
        else:
            follow_ups = [
                "Romantic week in Europe",
                "Family-friendly beach resort",
                "Adventure trip with guided hikes",
            ]

    # Limit to 3 unique suggestions
    seen = set()
    unique_follow_ups = []
    for fu in follow_ups:
        fu_lower = fu.lower()
        if fu_lower not in seen:
            seen.add(fu_lower)
            unique_follow_ups.append(fu)

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
    start_time = datetime.utcnow()

    db = get_rds_data_client()

    activities.append(create_activity(
        activity_type="database",
        title="Direct RDS Data API connection",
        details="Executing SQL query via HTTP endpoint",
        agent_name="SQLAgent",
        agent_file="agents/sql_01/agent.py"
    ))

    # Use shared search utilities
    params = parse_search_query(query)
    results, display_sql, search_title = await execute_keyword_search(db, params, limit)

    activities.append(create_activity(
        activity_type="search",
        title=search_title,
        sql_query=display_sql,
        agent_name="SQLAgent",
        agent_file="agents/sql_01/agent.py"
    ))

    execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

    # Log for monitoring
    log_search(phase=1, query=query, results_count=len(results),
               execution_time_ms=execution_time, search_type="keyword")

    activities.append(create_activity(
        activity_type="result",
        title=f"Found {len(results)} trips",
        execution_time_ms=execution_time,
        agent_name="SQLAgent",
        agent_file="agents/sql_01/agent.py"
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
# transport (compare these / what's the off-season price / how many
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
    "off-season",
    "off season",
    "shoulder season",
    "peak season",
    "cheapest month",
    "best month",
    "how many",
    "inventory",
)


def _wants_domain_tool(query: str) -> bool:
    q = query.lower()
    return any(k in q for k in _DOMAIN_INTENT_KEYWORDS)


async def _call_domain_tool(query: str) -> Optional[Dict[str, Any]]:
    """Pick the most relevant custom-MCP tool(s) for a domain-flavored
    prompt and call them. Returns a single dict (legacy single-call path)
    OR a dict with `tool='multi'` and a `calls` list when more than one
    tool intent was detected (e.g. "compare ... in EUR" fires BOTH
    compare_packages and currency_convert)."""
    q = query.lower()
    calls: List[Dict[str, Any]] = []

    async with concierge_mcp_session() as cli:
        if any(k in q for k in ("compare", "comparison", "side by side")):
            # Pick 3 packages to compare. Prefer "top trips" (highest-priced
            # on the assumption they're flagship offerings); if that returns
            # nothing fall back to ANY 3 rows. The tool should always have
            # something to compare or it confuses the LLM polish step.
            ids: List[str] = []
            try:
                row_sql = (
                    "SELECT package_id FROM trip_packages "
                    "ORDER BY price_per_person DESC LIMIT 3"
                )
                rows = await get_rds_data_client().execute(row_sql)
                ids = [r["package_id"] for r in rows if r.get("package_id")]
                if not ids:
                    rows = await get_rds_data_client().execute(
                        "SELECT package_id FROM trip_packages LIMIT 3"
                    )
                    ids = [r["package_id"] for r in rows if r.get("package_id")]
            except Exception as exc:
                log_error("compare_packages_row_pull", error=str(exc))
            if ids:
                result = await cli.call("compare_packages", {"package_ids": ids})
                calls.append({"tool": "compare_packages", "args": {"package_ids": ids}, "result": result})

        if any(k in q for k in ("convert", "in eur", "in euro", "in gbp", "in pounds", "in jpy", "in yen")):
            target = (
                "EUR" if "eur" in q
                else "GBP" if "gbp" in q or "pound" in q
                else "JPY" if "jpy" in q or "yen" in q
                else "EUR"
            )
            result = await cli.call(
                "currency_convert",
                {"amount": 2500.0, "from_ccy": "USD", "to_ccy": target},
            )
            calls.append({
                "tool": "currency_convert",
                "args": {"amount": 2500.0, "to": target},
                "result": result,
            })

        if any(k in q for k in ("loyalty", "bonvoy", "skymiles")):
            program = "Marriott Bonvoy" if "bonvoy" in q else "Delta SkyMiles" if "skymiles" in q else "Marriott Bonvoy"
            result = await cli.call(
                "loyalty_balance",
                {"traveler_id": "trv_meridian_demo", "program": program},
            )
            calls.append({
                "tool": "loyalty_balance",
                "args": {"program": program},
                "result": result,
            })

        if any(k in q for k in ("off-season", "off season", "shoulder season", "peak season", "cheapest month", "best month")):
            destination = "Europe"
            for d in ("Tokyo", "Paris", "Bali", "Lisbon", "Porto", "Iceland", "Rome", "Kyoto"):
                if d.lower() in q:
                    destination = d
                    break
            month = 11 if "off" in q or "cheapest" in q else 5 if "shoulder" in q else 7
            result = await cli.call(
                "seasonal_price_band",
                {"destination": destination, "month": month},
            )
            calls.append({
                "tool": "seasonal_price_band",
                "args": {"destination": destination, "month": month},
                "result": result,
            })

        if any(k in q for k in ("how many", "inventory")):
            region = "Europe"
            for r in ("Asia", "Europe", "Americas", "Africa", "Oceania"):
                if r.lower() in q:
                    region = r
                    break
            result = await cli.call("region_inventory", {"region": region})
            calls.append({
                "tool": "region_inventory",
                "args": {"region": region},
                "result": result,
            })

    if not calls:
        return None
    if len(calls) == 1:
        return calls[0]
    return {"tool": "multi", "calls": calls}


async def mcp_search(
    query: str, limit: int = 5
) -> tuple[List[Product], List[ActivityEntry], Optional[str]]:
    """
    Phase 2: Search via MCP abstraction layer.

    Routes through TWO MCP servers in one agent turn:
      - awslabs.postgres-mcp-server  → generic SQL transport
      - meridian-concierge (custom)  → travel-domain tools

    Returns (products, activities, domain_text). When the prompt is a
    pure domain query (compare/FX/seasonal/inventory/loyalty), the
    domain_text contains a markdown-style readout that the caller uses
    as the bot reply instead of the generic "I found N trips" message.
    """
    activities: List[ActivityEntry] = []
    start_time = datetime.utcnow()

    params = parse_search_query(query)
    use_custom_mcp = _wants_domain_tool(query)
    # Pure domain queries (no recognized trip_type AND no price filter
    # AND a domain intent) skip the SQL search entirely - ILIKE-ing on
    # "compare top trips and show prices in EUR" matches nothing useful
    # and the user only cares about the domain tool's answer anyway.
    pure_domain = (
        use_custom_mcp
        and not params.matched_trip_type
        and params.price_filter is None
    )

    # ----- Generic MCP server (awslabs.postgres-mcp-server) -----
    activities.append(create_activity(
        activity_type="mcp",
        title="MCP server discovered: awslabs.postgres-mcp-server",
        details=(
            "Generic SQL transport · tools/list returned "
            "run_query, connect_to_database, get_table_schema"
        ),
        agent_name="MCPAgent",
        agent_file="agents/mcp_02/agent.py",
    ))
    activities.append(create_activity(
        activity_type="mcp",
        title="postgres-mcp · connect_to_database",
        details="Aurora PostgreSQL via RDS Data API (rdsapi)",
        agent_name="MCPAgent",
        agent_file="agents/mcp_02/agent.py",
    ))

    results: List[Dict[str, Any]] = []
    if not pure_domain:
        sql, display_sql, search_title = build_search_sql(params, limit)
        async with mcp_session() as client:
            results = await client.run_query(sql)
        activities.append(create_activity(
            activity_type="mcp",
            title="postgres-mcp · run_query",
            details=f"Generic SQL tool: {search_title}",
            sql_query=display_sql,
            agent_name="MCPAgent",
            agent_file="agents/mcp_02/agent.py",
        ))

    # ----- Custom MCP server (meridian-concierge) -----
    domain_text: Optional[str] = None
    if use_custom_mcp:
        activities.append(create_activity(
            activity_type="mcp",
            title="MCP server discovered: meridian-concierge (custom)",
            details=(
                "Custom domain server · tools/list returned "
                "compare_packages, seasonal_price_band, region_inventory, "
                "currency_convert, loyalty_balance"
            ),
            agent_name="MCPAgent",
            agent_file="backend/mcp/concierge_server.py",
        ))
        try:
            domain_call = await _call_domain_tool(query)
            if domain_call:
                # Normalize single-call and multi-call shapes into a list
                # so we can log + format both uniformly.
                if domain_call.get("tool") == "multi":
                    sub_calls = domain_call["calls"]
                else:
                    sub_calls = [domain_call]

                reply_parts: List[str] = []
                for sub in sub_calls:
                    tool_name = sub["tool"]
                    tool_args = sub["args"]
                    tool_result = sub["result"]
                    summary = _summarize_domain_result(tool_name, tool_result)
                    activities.append(create_activity(
                        activity_type="mcp",
                        title=f"meridian-concierge · {tool_name}",
                        details=f"args={tool_args} · {summary}",
                        agent_name="MCPAgent",
                        agent_file="backend/mcp/concierge_server.py",
                    ))
                    reply = _format_domain_reply(tool_name, tool_result)
                    if reply:
                        reply_parts.append(reply)
                if reply_parts:
                    raw_text = "\n\n".join(reply_parts)
                    # Polish the deterministic readout through Opus 4.7
                    # (with a Sonnet/Haiku fallback chain) so the user
                    # sees a longer, narrative concierge reply instead
                    # of a dry one-liner. The polish never invents facts
                    # (system prompt forbids it) - it just adds context.
                    polish = await polish_concierge_reply(query, raw_text)
                    if polish.model_id:
                        activities.append(create_activity(
                            activity_type="reasoning",
                            title=f"Bedrock · concierge polish ({polish.model_id})",
                            details="Wrapping deterministic tool output in concierge tone",
                            agent_name="MCPAgent",
                            agent_file="backend/llm_polish.py",
                        ))
                    else:
                        # Every model in the fallback chain failed - tell
                        # the user what blocked us instead of pretending
                        # the dry deterministic text was the polished one.
                        activities.append(create_activity(
                            activity_type="error",
                            title="Bedrock polish unavailable",
                            details=polish.note or "unknown",
                            agent_name="MCPAgent",
                            agent_file="backend/llm_polish.py",
                        ))
                    domain_text = polish.text
                    if not polish.model_id and polish.note:
                        domain_text = (
                            f"{polish.text}\n\n"
                            f"_(Concierge polish unavailable: {polish.note}. "
                            f"Showing raw tool output above.)_"
                        )
            else:
                # The intent matched but no tool branch picked it up.
                domain_text = (
                    "I recognized this as a domain-tool query but couldn't pick a "
                    "matching meridian-concierge tool. Try keywords like 'compare', "
                    "'in EUR', 'cheapest month', 'inventory', or 'loyalty'."
                )
        except Exception as exc:
            err_msg = str(exc)[:200] or repr(exc)
            activities.append(create_activity(
                activity_type="error",
                title="meridian-concierge MCP error",
                details=err_msg,
                agent_name="MCPAgent",
                agent_file="backend/mcp/concierge_server.py",
            ))
            # Surface the failure to the user instead of letting the
            # generic "Phase 1/2 keyword filters" message take over.
            domain_text = (
                "Custom MCP server (meridian-concierge) failed to execute the "
                f"domain tool: {err_msg}\n\n"
                "Confirm the FastAPI process can spawn the server "
                "(`python -m backend.mcp.concierge_server`) and that "
                "AURORA_CLUSTER_ARN/AURORA_SECRET_ARN/AWS creds are set."
            )

    execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

    log_search(phase=2, query=query, results_count=len(results),
               execution_time_ms=execution_time, search_type="mcp")

    activities.append(create_activity(
        activity_type="mcp",
        title=(
            "MCP turn complete · 2 servers"
            if use_custom_mcp
            else "MCP turn complete · 1 server"
        ),
        details=f"Retrieved {len(results)} rows in {execution_time}ms",
        execution_time_ms=execution_time,
        agent_name="MCPAgent",
        agent_file="agents/mcp_02/agent.py",
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
            lines = [f"Compared {len(result)} packages via meridian-concierge MCP:"]
            for p in result:
                price = p.get("price_per_person")
                price_s = f"${price:,.0f}" if isinstance(price, (int, float)) else "—"
                lines.append(
                    f"• {p.get('name')} — {p.get('destination') or p.get('region') or ''} "
                    f"· {p.get('trip_type', '')} · {price_s}"
                )
            return "\n".join(lines)
        if tool == "currency_convert" and isinstance(result, dict):
            amt = result.get("amount")
            converted = result.get("converted")
            rate = result.get("rate")
            return (
                f"FX via meridian-concierge MCP: "
                f"{amt} {result.get('from')} ≈ {converted} {result.get('to')} "
                f"(rate {rate})."
            )
        if tool == "loyalty_balance" and isinstance(result, dict):
            pts = result.get("points_balance", 0)
            tier = result.get("tier", "—")
            program = result.get("program", "")
            to_next = result.get("points_to_next_tier", 0) or 0
            tail = f" · {to_next:,} pts to next tier" if to_next else ""
            return (
                f"Loyalty (via meridian-concierge MCP): "
                f"{pts:,} pts on {program} · tier {tier}{tail}."
            )
        if tool == "seasonal_price_band" and isinstance(result, dict):
            dest = result.get("destination", "—")
            month = result.get("month", "—")
            season = result.get("season", "—")
            low = result.get("low")
            med = result.get("median")
            high = result.get("high")
            n = result.get("sample_size", 0)
            if not n:
                return f"No pricing data for {dest} in month {month}."
            return (
                f"Seasonal price band for {dest} (month {month}, {season}, "
                f"sample={n}): low ${low:,.0f} · median ${med:,.0f} · high ${high:,.0f}."
            )
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
    """One-line summary of a custom MCP tool call for the trace span."""
    if not isinstance(result, (list, dict)):
        return "ok"
    try:
        if tool == "compare_packages" and isinstance(result, list):
            return f"compared {len(result)} packages"
        if tool == "currency_convert" and isinstance(result, dict):
            return f"{result.get('amount')} {result.get('from')} = {result.get('converted')} {result.get('to')}"
        if tool == "loyalty_balance" and isinstance(result, dict):
            return f"{result.get('points_balance'):,} pts · tier={result.get('tier')}"
        if tool == "seasonal_price_band" and isinstance(result, dict):
            return f"{result.get('season')} band · low={result.get('low')} · high={result.get('high')}"
        if tool == "region_inventory" and isinstance(result, dict):
            return f"{result.get('package_count')} packages · {result.get('total_departure_slots')} slots"
    except Exception:
        pass
    return "ok"


# =============================================================================
# PHASE 3: Retrieval - semantic + lexical candidates + Cohere rerank
#
# AWS docs:
#   Cohere Embed v4: https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html
#   Aurora pgvector: https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraPostgreSQL.Extensions.html#AuroraPostgreSQL.Extensions.pgvector
# =============================================================================

async def retrieval_search(query: str, limit: int = 5) -> tuple[List[Product], List[ActivityEntry]]:
    """
    Retrieval mode: hybrid candidates + Cohere rerank.
    - Candidate retrieval: pgvector semantic + tsvector lexical search on Aurora
    - Ranking: Cohere Rerank on Bedrock
    """
    activities = []
    start_time = datetime.utcnow()

    db = get_rds_data_client()

    # Optional budget filter parsed from natural language.
    price_filter = None
    price_match = re.search(r'(?:under|below|less than|<)\s*\$?(\d+(?:\.\d{2})?)', query.lower())
    if price_match:
        price_filter = float(price_match.group(1))

    activities.append(create_activity(
        activity_type="reasoning",
        title="Delegating to SearchAgent",
        details="Supervisor routing search request to specialized agent",
        agent_name="RetrievalAgent",
        agent_file="agents/retrieval_03/supervisor.py"
    ))

    activities.append(create_activity(
        activity_type="embedding",
        title="Generating query embedding",
        details="Cohere Embed v4 Embeddings (1024d)",
        agent_name="SearchAgent",
        agent_file="agents/retrieval_03/search_agent.py"
    ))
    
    embedding_service = get_embedding_service()
    query_embedding = embedding_service.generate_text_embedding(query)
    embedding_str = '[' + ','.join(str(x) for x in query_embedding) + ']'

    embedding_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
    activities.append(create_activity(
        activity_type="embedding",
        title="Embedding generated",
        execution_time_ms=embedding_time,
        agent_name="SearchAgent",
        agent_file="agents/retrieval_03/search_agent.py"
    ))

    # Step 2: Hybrid candidate retrieval (semantic + lexical).
    activities.append(create_activity(
        activity_type="search",
        title="Hybrid candidate retrieval",
        details="pgvector cosine + tsvector/ts_rank",
        agent_name="SearchAgent",
        agent_file="agents/retrieval_03/search_agent.py"
    ))
    candidate_limit = max(limit * config.search.rerank_candidate_multiplier, 25)
    semantic_sql = """
        SELECT * FROM semantic_trip_search(%s::vector, %s)
    """
    semantic_rows = await db.execute(semantic_sql, (embedding_str, candidate_limit))
    if price_filter is not None:
        semantic_rows = [r for r in semantic_rows if float(r["price_per_person"]) <= price_filter]

    lexical_sql = """
        SELECT
            package_id,
            name,
            operator,
            price_per_person,
            description,
            image_url,
            trip_type,
            destination,
            region,
            durations,
            ts_rank(search_vector, websearch_to_tsquery('english', %s)) AS lexical_score
        FROM trip_packages
        WHERE search_vector @@ websearch_to_tsquery('english', %s)
    """
    lexical_params: list[Any] = [query, query]
    if price_filter is not None:
        lexical_sql += " AND price_per_person <= %s"
        lexical_params.append(price_filter)
    lexical_sql += " ORDER BY lexical_score DESC LIMIT %s"
    lexical_params.append(candidate_limit)
    lexical_rows = await db.execute(lexical_sql, tuple(lexical_params))

    # Merge candidates by package_id so rerank sees one entry per trip package.
    merged_by_package: dict[str, dict[str, Any]] = {}
    for row in semantic_rows:
        merged_by_package[row["package_id"]] = dict(row)
    for row in lexical_rows:
        existing = merged_by_package.get(row["package_id"])
        if existing:
            existing["lexical_score"] = float(row.get("lexical_score", 0.0))
        else:
            merged_by_package[row["package_id"]] = dict(row)
    candidate_rows = list(merged_by_package.values())

    search_time = int((datetime.utcnow() - start_time).total_seconds() * 1000) - embedding_time
    activities.append(create_activity(
        activity_type="search",
        title="Hybrid candidates fetched",
        details=(
            f"{len(candidate_rows)} unique candidates "
            f"(semantic={len(semantic_rows)}, lexical={len(lexical_rows)})"
        ),
        sql_query=(
            "SELECT * FROM semantic_trip_search(query_vector, candidate_limit); "
            "SELECT ... ts_rank(search_vector, websearch_to_tsquery(...)) ..."
        ),
        execution_time_ms=search_time,
        agent_name="SearchAgent",
        agent_file="agents/retrieval_03/search_agent.py"
    ))

    # Step 3: Cohere rerank over semantic candidates.
    rerank_start = datetime.utcnow()
    embedding_service = get_embedding_service()
    docs = [
        " | ".join([
            row.get("name", "") or "",
            row.get("destination", "") or "",
            row.get("trip_type", "") or "",
            row.get("operator", "") or "",
            row.get("description", "") or "",
        ])
        for row in candidate_rows
    ]
    ranked_rows = candidate_rows
    rerank_failed = False
    try:
        ranked = embedding_service.rerank_documents(query, docs, top_n=limit)
        ranked_rows = [candidate_rows[item["index"]] for item in ranked if item["index"] < len(candidate_rows)]
    except Exception:
        rerank_failed = True
        ranked_rows = candidate_rows[:limit]

    rerank_time = int((datetime.utcnow() - rerank_start).total_seconds() * 1000)
    activities.append(create_activity(
        activity_type="search",
        title="Cohere rerank applied" if not rerank_failed else "Cohere rerank unavailable",
        details=(
            f"Reranked to top {len(ranked_rows[:limit])} trips"
            if not rerank_failed
            else "Falling back to semantic rank order"
        ),
        execution_time_ms=rerank_time,
        agent_name="SearchAgent",
        agent_file="agents/retrieval_03/search_agent.py"
    ))

    activities.append(create_activity(
        activity_type="result",
        title=f"SearchAgent returned {len(ranked_rows[:limit])} results",
        details="Returning ranked trips to RetrievalAgent",
        agent_name="RetrievalAgent",
        agent_file="agents/retrieval_03/supervisor.py"
    ))

    products = [Product(**row_to_api_product(row)) for row in ranked_rows[:limit]]
    return products, activities


# =============================================================================
# PHASE 3 (live): Strands RetrievalAgent driving Bedrock tool delegation.
# =============================================================================

async def retrieval_supervisor_search(
    query: str,
    limit: int = 5,
) -> tuple[List[Product], List[ActivityEntry]]:
    """Phase 3 via live Strands supervisor — Bedrock LLM picks the SearchAgent tool."""
    from backend.agents.retrieval_03 import create_retrieval_system

    activities: List[ActivityEntry] = []

    def collect(entry: Any) -> None:
        try:
            act = _memory_activity_to_entry(entry)
            activities.append(act)
            log_activity_entry(act)
        except Exception:
            # Best-effort: keep going if the entry shape is unexpected.
            pass

    activities.append(create_activity(
        activity_type="reasoning",
        title="RetrievalAgent invoked (Strands + Bedrock)",
        details="Bedrock will choose which specialist tool to call",
        agent_name="RetrievalAgent",
        agent_file="agents/retrieval_03/supervisor.py",
    ))

    supervisor = create_retrieval_system(activity_callback=collect)

    packages, _llm_reply = await supervisor.process_search(query, activity_callback=collect)

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
            similarity=pkg.get("similarity"),
        ))

    if not products:
        activities.append(create_activity(
            activity_type="result",
            title="Supervisor search returned no trips",
            details="Strands delegation completed without package results",
            agent_name="RetrievalAgent",
            agent_file="agents/retrieval_03/supervisor.py",
        ))
        return products, activities

    activities.append(create_activity(
        activity_type="result",
        title=f"Supervisor returned {len(products)} trips",
        details="Bedrock-driven delegation completed",
        agent_name="RetrievalAgent",
        agent_file="agents/retrieval_03/supervisor.py",
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


async def workflow_memory_recall(
    query: str,
    *,
    traveler_id: str,
    conversation_id: str,
) -> tuple[List[Product], List[ActivityEntry]]:
    """
    LangGraph memory_recall branch — Aurora reads without a Strands loop.

    Reuses the same MemoryStore tables as Phase 4 @tools so the graph node
    story stays consistent: Phase 4 = Strands-driven recall; Phase 5 = explicit
    workflow node calling the same data plane.
    """
    from backend.memory.store import get_memory_store, DEMO_TRAVELER_ID

    tid = traveler_id or DEMO_TRAVELER_ID
    store = get_memory_store()
    activities: List[ActivityEntry] = []

    prefs = await store.recall_preferences(tid)
    activities.append(create_activity(
        activity_type="tool_call",
        title="Aurora recall: traveler_preferences",
        details=f"{len(prefs)} durable preference facts",
        agent_name="MemoryAgent",
        agent_file="agents/production_04/memory_agent.py",
    ))

    if conversation_id:
        session = await store.recall_short_term(conversation_id, limit=6)
        activities.append(create_activity(
            activity_type="tool_call",
            title="Aurora recall: conversation_messages",
            details=f"{len(session)} recent session turns",
            agent_name="MemoryAgent",
            agent_file="agents/production_04/memory_agent.py",
        ))

    similar = await store.recall_similar_interactions(tid, query, limit=3)
    activities.append(create_activity(
        activity_type="tool_call",
        title="Aurora recall: trip_interactions (pgvector)",
        details=f"{len(similar)} semantically similar past interactions",
        agent_name="MemoryAgent",
        agent_file="agents/production_04/memory_agent.py",
    ))

    products, search_activities = await retrieval_search(query, limit=5)
    activities.extend(search_activities)
    return products, activities


async def orchestration_workflow(
    query: str,
    traveler_id: str,
    conversation_id: Optional[str] = None,
) -> tuple[List[Product], List[ActivityEntry], str, str]:
    """
    Phase 5: LangGraph StateGraph orchestrates classify → branch → synthesize.

    Reuses Phase 3's retrieval search and availability check as graph nodes so the
    workflow story is "explicit edges + checkpoints" rather than "different
    search code."  Checkpointer is PostgresSaver when LANGGRAPH_CHECKPOINT_DSN
    is set, otherwise MemorySaver.
    """
    from backend.agents.orchestration_05.workflow import OrchestrationAgent

    workflow = OrchestrationAgent(
        search_fn=retrieval_search,
        availability_fn=retrieval_availability_search,
        memory_recall_fn=workflow_memory_recall,
    )
    final_state = await workflow.run(
        query,
        traveler_id=traveler_id,
        conversation_id=conversation_id or "",
    )

    raw_activities = final_state.get("activities", []) or []
    activities = [_dict_to_activity_entry(a) for a in raw_activities]
    for act in activities:
        log_activity_entry(act)
    packages = final_state.get("packages", []) or []
    response = final_state.get("response") or "Workflow finished."
    conv_id = final_state.get("conversation_id") or ""
    return packages, activities, response, conv_id


def _dict_to_activity_entry(activity: Any) -> ActivityEntry:
    if isinstance(activity, ActivityEntry):
        return activity
    if hasattr(activity, "model_dump"):
        activity = activity.model_dump()
    if not isinstance(activity, dict):
        return ActivityEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.utcnow().isoformat() + "Z",
            activity_type="reasoning",
            title=str(activity),
        )
    telemetry = activity.pop("telemetry", None)
    activity.setdefault("id", str(uuid.uuid4()))
    activity.setdefault("timestamp", datetime.utcnow().isoformat() + "Z")
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
) -> tuple[List[Product], List[ActivityEntry], str, str, List[MemoryFact]]:
    """
    Production mode: Runtime session + Memory recall + Gateway search + persist turn.

    Requires deployed AgentCore Runtime, Gateway, and Memory — see ``agentcore/README.md``.
    """
    from backend.agents.production_04.concierge import create_production_agent
    from backend.memory.store import DEMO_TRAVELER_ID

    tid = customer_id or DEMO_TRAVELER_ID
    runtime = create_production_agent()
    packages, raw_activities, message, conv_id, facts = await runtime.process_turn(
        query,
        tid,
        conversation_id,
        limit,
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
            similarity=getattr(pkg, "similarity", None),
        )
        for pkg in packages
    ]
    return api_products, activities, message, conv_id, memory_facts


# =============================================================================
# PHASE 3: PackageAgent — departure slots and package details
# =============================================================================

async def retrieval_availability_search(query: str) -> tuple[List[Product], List[ActivityEntry], str]:
    """
    Phase 3: PackageAgent handles departure and slot queries.
    Supervisor delegates availability questions to the specialist agent.

    Returns: (products, activities, message)
    """
    activities = []
    start_time = datetime.utcnow()

    db = get_rds_data_client()

    activities.append(create_activity(
        activity_type="reasoning",
        title="Delegating to PackageAgent",
        details="Supervisor routing availability request to specialist agent",
        agent_name="RetrievalAgent",
        agent_file="agents/retrieval_03/supervisor.py"
    ))

    query_lower = query.lower()

    activities.append(create_activity(
        activity_type="search",
        title="PackageAgent: Finding package",
        details="Searching for mentioned trip package",
        agent_name="PackageAgent",
        agent_file="agents/retrieval_03/package_agent.py"
    ))

    sql = """
        SELECT package_id, name, operator, price_per_person, description,
               image_url, trip_type, durations, availability
        FROM trip_packages
        WHERE LOWER(name) LIKE %s OR LOWER(operator) LIKE %s
        LIMIT 1
    """
    
    # Extract key terms from query
    search_terms = []
    stopwords = {'is', 'the', 'in', 'available', 'do', 'you', 'have', 'check', 'what',
                 'durations', 'duration', 'for', 'a', 'an', 'any', 'package', 'trip'}
    for word in query_lower.split():
        if word not in stopwords:
            search_terms.append(word)
    
    results = []
    for term in search_terms:
        results = await db.execute(sql, (f'%{term}%', f'%{term}%'))
        if results:
            break
    
    search_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
    
    if not results:
        activities.append(create_activity(
            activity_type="result",
            title="PackageAgent: Package not found",
            execution_time_ms=search_time,
            agent_name="PackageAgent",
            agent_file="agents/retrieval_03/package_agent.py"
        ))

        activities.append(create_activity(
            activity_type="result",
            title="PackageAgent returned to Supervisor",
            details="No matching package found",
            agent_name="RetrievalAgent",
            agent_file="agents/retrieval_03/supervisor.py"
        ))

        return [], activities, "I couldn't find that trip package. Try searching by destination, operator, or trip type."

    product = results[0]
    availability = product.get('availability', {})

    activities.append(create_activity(
        activity_type="availability",
        title="PackageAgent: Checking departures",
        details=f"Package: {product['name']}",
        sql_query="SELECT availability, durations FROM trip_packages WHERE package_id = ?",
        agent_name="PackageAgent",
        agent_file="agents/retrieval_03/package_agent.py"
    ))
    
    # Calculate total stock
    if isinstance(availability, dict):
        if 'quantity' in availability:
            total_stock = availability['quantity']
        else:
            total_stock = sum(availability.values()) if availability else 0
    else:
        total_stock = 0
    
    availability_time = int((datetime.utcnow() - start_time).total_seconds() * 1000) - search_time
    
    activities.append(create_activity(
        activity_type="result",
        title="PackageAgent: Departures verified",
        details=f"Total: {total_stock} departure slots available",
        execution_time_ms=availability_time,
        agent_name="PackageAgent",
        agent_file="agents/retrieval_03/package_agent.py"
    ))

    activities.append(create_activity(
        activity_type="result",
        title="PackageAgent returned to Supervisor",
        details=f"Availability check complete for {product['name']}",
        agent_name="RetrievalAgent",
        agent_file="agents/retrieval_03/supervisor.py"
    ))

    durations = product.get('durations', [])
    if total_stock > 0:
        if durations:
            durations_str = ', '.join(durations[:5])
            message = f"**{product['name']}** has availability! {total_stock} departure slots across: {durations_str}."
        else:
            message = f"**{product['name']}** has {total_stock} departure slots available."
    else:
        message = f"**{product['name']}** is currently sold out. Would you like similar alternatives?"
    
    # Return the product
    products = [Product(**row_to_api_product(product))]
    
    return products, activities, message


def is_availability_query(query: str) -> bool:
    """Check if the query is asking about availability or departure slots."""
    query_lower = query.lower()
    availability_patterns = [
        'available', 'do you have', 'check availability',
        'availability', 'how many', 'what dates', 'dates available',
        'departure', 'departures', 'is the', 'is there', 'got any', 'slots'
    ]
    return any(pattern in query_lower for pattern in availability_patterns)


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """
    Process a chat message with the AI travel concierge.

    Routes to the appropriate search implementation based on phase:
    - Phase 1: Direct RDS Data API filters
    - Phase 2: MCP-backed SQL
    - Phase 3: Hybrid retrieval + Cohere rerank; PackageAgent for slot checks
    - Phase 4: Production concierge with AgentCore Runtime/Gateway/Memory
    """
    turn_started = log_turn_start(
        request.phase,
        request.message,
        traveler_id=request.customer_id,
        conversation_id=request.conversation_id,
    )
    activities = []

    # Phase 3/4: availability query -> route to PackageAgent
    if request.phase in (3, 4) and is_availability_query(request.message):
        activities.append(create_activity(
            activity_type="reasoning",
            title="Processing with Multi-Agent Orchestration",
            details=f"Query: {request.message[:80]}{'...' if len(request.message) > 80 else ''}",
            agent_name="RetrievalAgent",
            agent_file="agents/retrieval_03/supervisor.py"
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
                follow_ups=follow_ups
            ),
                request.phase,
                turn_started,
            )
        except Exception as e:
            activities.append(create_activity(
                activity_type="error",
                title="PackageAgent error",
                details=str(e),
                agent_name="PackageAgent",
                agent_file="agents/retrieval_03/package_agent.py"
            ))
            # Fall through to regular search

    # Phase 4: ProductionAgent + AgentCore Runtime/Gateway/Memory
    if request.phase == 4:
        from backend.memory.store import DEMO_TRAVELER_ID

        activities.append(create_activity(
            activity_type="reasoning",
            title="Processing with Production concierge (Runtime + Gateway + Memory)",
            details=f"Query: {request.message[:80]}{'...' if len(request.message) > 80 else ''}",
            agent_name="ProductionAgent",
            agent_file="agents/production_04/concierge.py",
        ))
        try:
            products, search_activities, message, conv_id, memory_facts = await production_search(
                request.message,
                customer_id=request.customer_id or DEMO_TRAVELER_ID,
                conversation_id=request.conversation_id,
                limit=5,
            )
            activities.extend(search_activities)
            follow_ups = generate_follow_ups(request.message, products, request.phase)
            return _complete_chat_turn(
                ChatResponse(
                message=message,
                products=products if products else None,
                order=None,
                activities=activities,
                follow_ups=follow_ups,
                conversation_id=conv_id,
                memory_facts=memory_facts,
            ),
                request.phase,
                turn_started,
            )
        except Exception as e:
            log_error("production_search", error=str(e))
            activities.append(create_activity(
                activity_type="error",
                title="Concierge error",
                details=str(e),
                agent_name="ProductionAgent",
                agent_file="agents/production_04/concierge.py",
            ))
            return _complete_chat_turn(
                ChatResponse(
                message="I encountered an error in Production mode. Please try again.",
                products=None,
                order=None,
                activities=activities,
                follow_ups=["Romantic week in Europe", "Family-friendly beach resort", "Tokyo culture trip"],
            ),
                request.phase,
                turn_started,
                error=str(e),
            )

    # Phase 5: LangGraph workflow with explicit StateGraph + checkpointer.
    if request.phase == 5:
        from backend.memory.store import DEMO_TRAVELER_ID
        try:
            workflow_packages, workflow_activities, message, conv_id = await orchestration_workflow(
                request.message,
                traveler_id=request.customer_id or DEMO_TRAVELER_ID,
                conversation_id=request.conversation_id,
            )
            activities.extend(workflow_activities)
            follow_ups = generate_follow_ups(request.message, workflow_packages, request.phase)
            return _complete_chat_turn(
                ChatResponse(
                message=message,
                products=workflow_packages if workflow_packages else None,
                order=None,
                activities=activities,
                follow_ups=follow_ups,
                conversation_id=conv_id,
            ),
                request.phase,
                turn_started,
            )
        except Exception as e:
            log_error("orchestration_workflow", error=str(e))
            activities.append(create_activity(
                activity_type="error",
                title="LangGraph workflow error",
                details=str(e),
                agent_name="OrchestrationAgent",
                agent_file="agents/orchestration_05/workflow.py",
            ))
            return _complete_chat_turn(
                ChatResponse(
                message="The Phase 5 workflow hit an error. Please try a Phase 3 or Phase 4 query.",
                products=None,
                order=None,
                activities=activities,
                follow_ups=["Tokyo culture trip", "Family-friendly beach resort", "Romantic week in Europe"],
            ),
                request.phase,
                turn_started,
                error=str(e),
            )

    # Phase 3: live Strands supervisor (Bedrock-driven tool delegation).
    phase3_fn = retrieval_supervisor_search
    phase3_method = "Hybrid (pgvector + tsvector) + Cohere Rerank via Strands Supervisor"

    phase_configs = {
        1: ("SQLAgent", "Direct RDS Data API", sql_search, "agents/sql_01/agent.py"),
        2: ("MCPAgent", "MCP (postgres-mcp-server)", mcp_search, "agents/mcp_02/agent.py"),
        3: ("RetrievalAgent", phase3_method, phase3_fn, "agents/retrieval_03/supervisor.py"),
    }

    agent_name, method, search_fn, agent_file = phase_configs[request.phase]

    activities.append(create_activity(
        activity_type="reasoning",
        title=f"Processing with {method}",
        details=f"Query: {request.message[:80]}{'...' if len(request.message) > 80 else ''}",
        agent_name=agent_name,
        agent_file=agent_file
    ))
    
    try:
        # Phase 2 returns a 3-tuple (products, activities, domain_text)
        # because its custom MCP path can produce a non-product reply.
        # All other phases stay on the 2-tuple shape.
        domain_text: Optional[str] = None
        if request.phase == 2:
            products, search_activities, domain_text = await mcp_search(request.message, limit=5)
        else:
            products, search_activities = await search_fn(request.message, limit=5)
        activities.extend(search_activities)

        # Generate personalized response message
        if domain_text:
            # Custom MCP produced a domain readout (compare / FX / loyalty
            # / seasonal pricing / inventory) - that IS the answer.
            if products:
                message = f"{domain_text}\n\nI also found {len(products)} matching trips:"
            else:
                message = domain_text
        elif products:
            if request.phase in (3, 4):
                top_similarity = products[0].similarity
                if top_similarity and top_similarity > 0.8:
                    message = f"Great match! I found {len(products)} trips that closely match what you're looking for:"
                else:
                    message = f"Here are {len(products)} trips that might interest you:"
            else:
                message = f"I found {len(products)} trips for you:"
        else:
            if request.phase == 2:
                # Phase 2 fallback - SQL came up empty AND no domain tool
                # matched. Polish the explanation through Opus so the
                # stretch-query narrative reads naturally.
                raw = (
                    "Search summary:\n"
                    "- postgres-mcp ran a keyword ILIKE on name/description/destination "
                    "  and returned 0 rows.\n"
                    "- meridian-concierge (custom MCP) was loaded but no domain tool "
                    "  matched the query.\n"
                    "- Reason: the prompt expresses intent (vibe, mood, openness) "
                    "  rather than a literal keyword that lives in trip_packages.\n"
                    "- Next step the system supports: switch to Phase 3 (Retrieval) "
                    "  which uses pgvector + Cohere Rerank to read intent."
                )
                polish = await polish_concierge_reply(request.message, raw)
                if polish.model_id:
                    activities.append(create_activity(
                        activity_type="reasoning",
                        title=f"Bedrock · concierge polish ({polish.model_id})",
                        details="Explaining the stretch-query miss in concierge tone",
                        agent_name="MCPAgent",
                        agent_file="backend/llm_polish.py",
                    ))
                    message = polish.text
                else:
                    activities.append(create_activity(
                        activity_type="error",
                        title="Bedrock polish unavailable",
                        details=polish.note or "unknown",
                        agent_name="MCPAgent",
                        agent_file="backend/llm_polish.py",
                    ))
                    message = (
                        f"{polish.text}\n\n"
                        f"_(Concierge polish unavailable: {polish.note}. "
                        f"Check Bedrock model access for "
                        f"`global.anthropic.claude-opus-4-7` "
                        f"and `global.anthropic.claude-sonnet-4-6` "
                        f"in this region.)_"
                    )
            elif request.phase == 1:
                message = (
                    "No results found. Phase 1 uses keyword filters only. Try "
                    "destination or operator names like 'Tokyo' or 'ANA Holidays', "
                    "or switch to Phase 3 for natural language search."
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
            follow_ups=follow_ups
        ),
            request.phase,
            turn_started,
        )
        
    except Exception as e:
        activities.append(create_activity(
            activity_type="error",
            title="Error processing request",
            details=str(e),
            agent_name=agent_name,
            agent_file=agent_file
        ))

        return _complete_chat_turn(
            ChatResponse(
            message="I encountered an error. Please try again or browse featured trips.",
            products=None,
            order=None,
            activities=activities,
            follow_ups=["Tokyo culture trip", "Beach resort for two", "City breaks in Europe"]
        ),
            request.phase,
            turn_started,
            error=str(e),
        )


# =============================================================================
# BOOKING PROCESSING — demonstrates agentic booking flow
# =============================================================================

class OrderRequest(BaseModel):
    """Request model for booking processing."""
    product_id: str
    size: Optional[str] = None
    quantity: int = 1
    phase: Literal[1, 2, 3, 4]


class OrderResponse(BaseModel):
    """Response model for order processing."""
    message: str
    order: Optional[Order] = None
    activities: List[ActivityEntry]


@router.post("/order", response_model=OrderResponse)
async def process_order(request: OrderRequest) -> OrderResponse:
    """
    Process an order for a product - demonstrates agentic workflow capabilities.

    Simulates:
    1. Product lookup
    2. Inventory check
    3. Payment processing (mock)
    4. Order confirmation
    """
    import asyncio
    import random

    activities = []
    start_time = datetime.utcnow()

    # Determine agent config based on phase
    phase_configs = {
        1: ("SQLAgent", "agents/sql_01/agent.py"),
        2: ("MCPAgent", "agents/mcp_02/agent.py"),
        3: ("BookingAgent", "agents/retrieval_03/booking_agent.py"),
        # Phase 4 booking is driven by the concierge orchestrator; the BookingAgent
        # in phase 3 is reused as the booking specialist.
        4: ("BookingAgent", "agents/retrieval_03/booking_agent.py"),
    }
    agent_name, agent_file = phase_configs[request.phase]

    try:
        db = get_rds_data_client()

        # Step 1: Product lookup
        activities.append(create_activity(
            activity_type="search",
            title="Looking up package details",
            details=f"Package ID: {request.product_id}",
            agent_name=agent_name,
            agent_file=agent_file
        ))

        # Simulate processing time
        await asyncio.sleep(0.3)

        sql = """
            SELECT package_id, name, operator, price_per_person, description,
                   image_url, trip_type, durations
            FROM trip_packages
            WHERE package_id = %s
        """
        results = await db.execute(sql, (request.product_id,))

        if not results:
            activities.append(create_activity(
                activity_type="error",
                title="Package not found",
                details=f"No package with ID {request.product_id}",
                agent_name=agent_name,
                agent_file=agent_file
            ))
            return OrderResponse(
                message="Sorry, I couldn't find that trip package. It may no longer be available.",
                order=None,
                activities=activities
            )

        product = results[0]
        pkg = row_to_api_product(product)
        lookup_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        activities.append(create_activity(
            activity_type="result",
            title=f"Found: {pkg['name']}",
            details=f"${pkg['price']:.2f} pp — {pkg['brand']}",
            execution_time_ms=lookup_time,
            agent_name=agent_name,
            agent_file=agent_file
        ))

        # Step 2: Inventory check
        activities.append(create_activity(
            activity_type="availability",
            title="Checking availability",
            details=f"Duration: {request.size or 'default'}, Travelers: {request.quantity}",
            agent_name=agent_name,
            agent_file=agent_file
        ))

        await asyncio.sleep(0.2)

        # Mock availability check - always available for demo
        departures_available = True
        seats_available = random.randint(5, 50)

        availability_time = int((datetime.utcnow() - start_time).total_seconds() * 1000) - lookup_time

        activities.append(create_activity(
            activity_type="availability",
            title="Departure available" if departures_available else "No departures available",
            details=f"{seats_available} seats available on requested dates",
            execution_time_ms=availability_time,
            agent_name=agent_name,
            agent_file=agent_file
        ))

        if not departures_available:
            return OrderResponse(
                message=f"Sorry, {product['name']} has no departures on your requested dates. Would you like me to notify you when seats open up?",
                order=None,
                activities=activities
            )

        # Step 3: Booking authorization (mock — demo only)
        activities.append(create_activity(
            activity_type="order",
            title="Booking authorization (demo)",
            details="Holding seats with stored traveler profile",
            agent_name=agent_name,
            agent_file=agent_file
        ))

        await asyncio.sleep(0.4)

        # Calculate order totals using config values
        subtotal = pkg['price'] * request.quantity
        tax = round(subtotal * config.order.tax_rate, 2)
        shipping = 0.0 if subtotal >= config.order.free_shipping_threshold else config.order.shipping_fee
        total = round(subtotal + tax + shipping, 2)

        payment_time = int((datetime.utcnow() - start_time).total_seconds() * 1000) - lookup_time - availability_time

        activities.append(create_activity(
            activity_type="order",
            title="Booking authorized (demo)",
            details=f"Authorized ${total:.2f} hold on traveler profile",
            execution_time_ms=payment_time,
            agent_name=agent_name,
            agent_file=agent_file
        ))

        # Step 4: Create order
        order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"

        activities.append(create_activity(
            activity_type="order",
            title="Booking confirmed",
            details=f"Booking #{order_id}",
            agent_name=agent_name,
            agent_file=agent_file
        ))

        # Estimate departure using config values
        from datetime import timedelta
        days_to_departure = random.randint(config.order.min_delivery_days, config.order.max_delivery_days)
        departure_date = (datetime.utcnow() + timedelta(days=days_to_departure)).strftime("%B %d, %Y")

        order = Order(
            order_id=order_id,
            items=[
                OrderItem(
                    product_id=pkg['product_id'],
                    name=pkg['name'],
                    size=request.size,
                    quantity=request.quantity,
                    unit_price=pkg['price']
                )
            ],
            subtotal=subtotal,
            tax=tax,
            shipping=shipping,
            total=total,
            status="confirmed",
            estimated_delivery=departure_date
        )

        total_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        activities.append(create_activity(
            activity_type="result",
            title="Booking complete",
            details=f"Departure: {departure_date}",
            execution_time_ms=total_time,
            agent_name=agent_name,
            agent_file=agent_file
        ))

        service_fee_label = "Service fee"
        message = f"Great choice! I've placed your booking for **{pkg['name']}**.\n\n" \
                  f"**Booking #{order_id}**\n" \
                  f"- Subtotal: ${subtotal:.2f}\n" \
                  f"- Tax: ${tax:.2f}\n" \
                  f"- {service_fee_label}: {'FREE' if shipping == 0 else f'${shipping:.2f}'}\n" \
                  f"- **Total: ${total:.2f}**\n\n" \
                  f"Departure: {departure_date}"

        # Log successful order
        log_order(
            phase=request.phase,
            order_id=order_id,
            product_id=request.product_id,
            total=total,
            status="confirmed"
        )

        return OrderResponse(
            message=message,
            order=order,
            activities=activities
        )

    except Exception as e:
        log_error(context="order_processing", error=str(e), phase=request.phase)
        activities.append(create_activity(
            activity_type="error",
            title="Order processing failed",
            details=str(e),
            agent_name=agent_name,
            agent_file=agent_file
        ))
        return OrderResponse(
            message="Sorry, I encountered an error processing your order. Please try again.",
            order=None,
            activities=activities
        )
