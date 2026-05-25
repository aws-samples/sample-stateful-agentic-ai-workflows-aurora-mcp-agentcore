"""
Chat API Router for Meridian.

Handles chat interactions with the AI travel concierge across four phases:
- Phase 1: Direct RDS Data API connection (SQL filters on trip_packages)
- Phase 2: Via MCP (awslabs.postgres-mcp-server) abstraction
- Phase 3: Hybrid search — semantic (pgvector) + lexical (tsvector/tsrank)
- Phase 4: Strands concierge + Aurora traveler memory
"""

import os
import re
import uuid
from datetime import datetime
from typing import Literal, Optional, List, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.rds_data_client import get_rds_data_client
from backend.db.embedding_service import get_embedding_service
from backend.config import config
from backend.logging_config import log_search, log_order, log_error
from backend.search_utils import (
    parse_search_query,
    execute_keyword_search,
    build_search_sql,
    results_to_packages,
)
from backend.catalog_compat import row_to_api_product, rows_to_api_products

# MCP client import with graceful fallback
try:
    from backend.mcp.mcp_client import get_mcp_client, mcp_session
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


# Orchestration mode controls whether the live API drives Bedrock-powered
# Strands tool routing (`full`) or runs the procedural fallback path
# (`fallback`). Used by Phase 3 supervisor and Phase 4 concierge.
def strands_mode() -> str:
    return os.getenv("STRANDS_ORCHESTRATION", "full").lower().strip()


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
    return ActivityEntry(
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


def generate_follow_ups(query: str, products: List[Product], phase: int) -> List[str]:
    """Generate contextual follow-up suggestions based on the query and results.

    Phase 1 & 2: SQL filter search (trip_type, operator, price filters)
    Phase 3: Hybrid semantic + lexical search (understands natural language)

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

async def phase1_search(query: str, limit: int = 5) -> tuple[List[Product], List[ActivityEntry]]:
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
        agent_name="Phase1Agent",
        agent_file="agents/phase1/agent.py"
    ))

    # Use shared search utilities
    params = parse_search_query(query)
    results, display_sql, search_title = await execute_keyword_search(db, params, limit)

    activities.append(create_activity(
        activity_type="search",
        title=search_title,
        sql_query=display_sql,
        agent_name="Phase1Agent",
        agent_file="agents/phase1/agent.py"
    ))

    execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

    # Log for monitoring
    log_search(phase=1, query=query, results_count=len(results),
               execution_time_ms=execution_time, search_type="keyword")

    activities.append(create_activity(
        activity_type="result",
        title=f"Found {len(results)} trips",
        execution_time_ms=execution_time,
        agent_name="Phase1Agent",
        agent_file="agents/phase1/agent.py"
    ))

    # Convert results to Product models
    product_dicts = results_to_packages(results)
    products = [Product(**row_to_api_product(p)) for p in product_dicts]

    return products, activities


# =============================================================================
# PHASE 2: MCP (Model Context Protocol) Abstraction
# Uses awslabs.postgres-mcp-server for database operations
#
# This phase uses the REAL MCP protocol to connect to Aurora PostgreSQL:
# 1. Connects to awslabs.postgres-mcp-server via stdio transport
# 2. Uses the MCP SDK to invoke database tools (run_query, connect_to_database)
# 3. Falls back to RDS Data API if MCP is not available
#
# Configuration:
# - Set MCP_CONNECTION_METHOD=rdsapi (default) or pgwire/pgwire_iam
# - Set AURORA_CLUSTER_IDENTIFIER for rdsapi method
# - Set AURORA_DATABASE_ENDPOINT for pgwire methods
# - Ensure AWS credentials are configured
# =============================================================================

async def phase2_search(query: str, limit: int = 5) -> tuple[List[Product], List[ActivityEntry]]:
    """
    Phase 2: Search via MCP abstraction layer.

    Uses the same search logic as Phase 1, but through the MCP protocol.
    This demonstrates progressive architecture: same tools, different interface.

    The MCP client connects to awslabs.postgres-mcp-server which provides:
    - connect_to_database: Establish connection to Aurora PostgreSQL
    - run_query: Execute SQL queries
    - get_schema: Inspect database schema
    """
    activities = []
    start_time = datetime.utcnow()

    # Parse search query using shared utilities
    params = parse_search_query(query)

    sql, display_sql, search_title = build_search_sql(params, limit)
    results: List[dict] = []
    used_mcp = False

    if MCP_AVAILABLE:
        activities.append(create_activity(
            activity_type="mcp",
            title="MCP: connect_to_database",
            details="Connecting to Aurora PostgreSQL via postgres-mcp-server",
            agent_name="Phase2Agent",
            agent_file="agents/phase2/agent.py"
        ))
        try:
            async with mcp_session() as client:
                results = await client.run_query(sql)
            used_mcp = True
            activities.append(create_activity(
                activity_type="mcp",
                title="MCP: run_query",
                details=f"Executing via MCP: {search_title}",
                sql_query=display_sql,
                agent_name="Phase2Agent",
                agent_file="agents/phase2/agent.py"
            ))
        except Exception as e:
            log_error("phase2_mcp_fallback", error=str(e))
            activities.append(create_activity(
                activity_type="error",
                title="MCP failed — falling back to RDS Data API",
                details=str(e),
                agent_name="Phase2Agent",
                agent_file="agents/phase2/agent.py"
            ))
    else:
        activities.append(create_activity(
            activity_type="database",
            title="MCP SDK unavailable — using RDS Data API",
            details="Install MCP dependencies to enable postgres-mcp-server",
            agent_name="Phase2Agent",
            agent_file="agents/phase2/agent.py"
        ))

    if not used_mcp:
        db = get_rds_data_client()
        results, display_sql, search_title = await execute_keyword_search(db, params, limit)
        activities.append(create_activity(
            activity_type="database",
            title=f"RDS Data API: {search_title}",
            sql_query=display_sql,
            agent_name="Phase2Agent",
            agent_file="agents/phase2/agent.py"
        ))

    execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

    log_search(phase=2, query=query, results_count=len(results),
               execution_time_ms=execution_time, search_type="mcp")

    activities.append(create_activity(
        activity_type="mcp",
        title="MCP: Query completed",
        details=f"Retrieved {len(results)} rows",
        execution_time_ms=execution_time,
        agent_name="Phase2Agent",
        agent_file="agents/phase2/agent.py"
    ))

    product_dicts = results_to_packages(results)
    products = [Product(**row_to_api_product(p)) for p in product_dicts]

    return products, activities


# =============================================================================
# PHASE 3: Hybrid Search - Semantic (pgvector) + Lexical (tsvector/tsrank)
# Combines embedding similarity with PostgreSQL full-text search
# =============================================================================

async def phase3_lexical_search(query: str, limit: int = 5) -> tuple[List[Product], List[ActivityEntry]]:
    """Lexical-only fallback when Bedrock embeddings are unavailable."""
    activities = []
    start_time = datetime.utcnow()
    db = get_rds_data_client()

    activities.append(create_activity(
        activity_type="search",
        title="Lexical fallback (tsvector)",
        details="Embeddings unavailable — ranking with search_vector only",
        agent_name="SearchAgent",
        agent_file="agents/phase3/search_agent.py",
    ))

    sql = """
        SELECT package_id, name, operator, price_per_person, description,
               image_url, trip_type, durations,
               ts_rank(search_vector, plainto_tsquery('english', %s)) AS lexical_score
        FROM trip_packages
        WHERE search_vector @@ plainto_tsquery('english', %s)
        ORDER BY lexical_score DESC
        LIMIT %s
    """
    results = await db.execute(sql, (query, query, limit))
    search_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

    activities.append(create_activity(
        activity_type="search",
        title=f"Lexical search found {len(results)} packages",
        execution_time_ms=search_time,
        agent_name="SearchAgent",
        agent_file="agents/phase3/search_agent.py",
    ))

    products = [Product(**row_to_api_product(row)) for row in results]
    return products, activities


async def phase3_search(query: str, limit: int = 5) -> tuple[List[Product], List[ActivityEntry]]:
    """
    Phase 3: Hybrid search combining semantic and lexical approaches.
    - Semantic: Cohere Embed v4 embeddings with pgvector cosine similarity
    - Lexical: PostgreSQL tsvector/tsrank full-text search
    - Final ranking: Weighted combination of both scores
    """
    activities = []
    start_time = datetime.utcnow()
    
    db = get_rds_data_client()
    
    # Parse price filter
    price_filter = None
    price_match = re.search(r'(?:under|below|less than|<)\s*\$?(\d+(?:\.\d{2})?)', query.lower())
    if price_match:
        price_filter = float(price_match.group(1))
    
    # Step 0: Supervisor delegates to SearchAgent
    activities.append(create_activity(
        activity_type="reasoning",
        title="Delegating to SearchAgent",
        details="Supervisor routing search request to specialized agent",
        agent_name="SupervisorAgent",
        agent_file="agents/phase3/supervisor.py"
    ))
    
    # Step 1: Generate query embedding
    activities.append(create_activity(
        activity_type="embedding",
        title="Generating query embedding",
        details="Cohere Embed v4 Embeddings (1024d)",
        agent_name="SearchAgent",
        agent_file="agents/phase3/search_agent.py"
    ))
    
    try:
        embedding_service = get_embedding_service()
        query_embedding = embedding_service.generate_text_embedding(query)
        embedding_str = '[' + ','.join(str(x) for x in query_embedding) + ']'
        
        embedding_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        activities.append(create_activity(
            activity_type="embedding",
            title="Embedding generated",
            execution_time_ms=embedding_time,
            agent_name="SearchAgent",
            agent_file="agents/phase3/search_agent.py"
        ))
        
        # Step 2: Hybrid search - Semantic + Lexical
        activities.append(create_activity(
            activity_type="search",
            title="Hybrid search: Semantic + Lexical",
            details="pgvector cosine + tsvector/tsrank",
            agent_name="SearchAgent",
            agent_file="agents/phase3/search_agent.py"
        ))
        
        # Hybrid query combining vector similarity and full-text search
        # Semantic score: 1 - cosine distance (higher = more similar)
        # Lexical score: ts_rank with plainto_tsquery
        # Combined score: 0.7 * semantic + 0.3 * lexical
        
        if price_filter:
            sql = """
                WITH semantic_search AS (
                    SELECT package_id, name, operator, price_per_person, description, 
                           image_url, trip_type, durations,
                           1 - (embedding <=> %s::vector) as semantic_score
                    FROM trip_packages
                    WHERE price_per_person <= %s
                ),
                lexical_search AS (
                    SELECT package_id,
                           ts_rank(
                               to_tsvector('english', name || ' ' || COALESCE(description, '') || ' ' || COALESCE(operator, '')),
                               plainto_tsquery('english', %s)
                           ) as lexical_score
                    FROM trip_packages
                    WHERE price_per_person <= %s
                )
                SELECT s.package_id, s.name, s.operator, s.price_per_person, s.description,
                       s.image_url, s.trip_type, s.durations,
                       s.semantic_score,
                       COALESCE(l.lexical_score, 0) as lexical_score,
                       (0.7 * s.semantic_score + 0.3 * COALESCE(l.lexical_score, 0)) as combined_score
                FROM semantic_search s
                LEFT JOIN lexical_search l ON s.package_id = l.package_id
                ORDER BY combined_score DESC
                LIMIT %s
            """
            results = await db.execute(sql, (embedding_str, price_filter, query, price_filter, limit))
        else:
            sql = """
                WITH semantic_search AS (
                    SELECT package_id, name, operator, price_per_person, description, 
                           image_url, trip_type, durations,
                           1 - (embedding <=> %s::vector) as semantic_score
                    FROM trip_packages
                ),
                lexical_search AS (
                    SELECT package_id,
                           ts_rank(
                               to_tsvector('english', name || ' ' || COALESCE(description, '') || ' ' || COALESCE(operator, '')),
                               plainto_tsquery('english', %s)
                           ) as lexical_score
                    FROM trip_packages
                )
                SELECT s.package_id, s.name, s.operator, s.price_per_person, s.description,
                       s.image_url, s.trip_type, s.durations,
                       s.semantic_score,
                       COALESCE(l.lexical_score, 0) as lexical_score,
                       (0.7 * s.semantic_score + 0.3 * COALESCE(l.lexical_score, 0)) as combined_score
                FROM semantic_search s
                LEFT JOIN lexical_search l ON s.package_id = l.package_id
                ORDER BY combined_score DESC
                LIMIT %s
            """
            results = await db.execute(sql, (embedding_str, query, limit))
        
        search_time = int((datetime.utcnow() - start_time).total_seconds() * 1000) - embedding_time

        # Build display SQL based on whether price filter was used
        if price_filter:
            display_sql = f"WITH semantic_search AS (SELECT ..., 1 - (embedding <=> query_vector) as score FROM trip_packages WHERE price_per_person <= {price_filter}), lexical_search AS (SELECT ..., ts_rank(...) FROM trip_packages) SELECT ... ORDER BY (0.7 * semantic + 0.3 * lexical) DESC LIMIT {limit}"
        else:
            display_sql = f"WITH semantic_search AS (SELECT ..., 1 - (embedding <=> query_vector) as score FROM trip_packages), lexical_search AS (SELECT ..., ts_rank(...) FROM trip_packages) SELECT ... ORDER BY (0.7 * semantic + 0.3 * lexical) DESC LIMIT {limit}"

        activities.append(create_activity(
            activity_type="search",
            title="pgvector HNSW + tsrank search",
            details=f"Found {len(results)} trips",
            sql_query=display_sql,
            execution_time_ms=search_time,
            agent_name="SearchAgent",
            agent_file="agents/phase3/search_agent.py"
        ))

        # SearchAgent returns results to Supervisor
        activities.append(create_activity(
            activity_type="result",
            title=f"SearchAgent returned {len(results)} results",
            details="Returning ranked trips to SupervisorAgent",
            agent_name="SupervisorAgent",
            agent_file="agents/phase3/supervisor.py"
        ))
        
        products = [Product(**row_to_api_product(row)) for row in results]
        
        return products, activities
        
    except Exception as e:
        activities.append(create_activity(
            activity_type="error",
            title="Hybrid search failed",
            details=str(e),
            agent_name="SearchAgent",
            agent_file="agents/phase3/search_agent.py"
        ))

        # Fallback to lexical search, then keyword search
        lexical_products, lexical_activities = await phase3_lexical_search(query, limit)
        activities.extend(lexical_activities)
        if lexical_products:
            return lexical_products, activities
        fallback_products, fallback_activities = await phase1_search(query, limit)
        activities.extend(fallback_activities)
        return fallback_products, activities


# =============================================================================
# PHASE 3 (live): Strands SupervisorAgent driving Bedrock tool delegation.
# Falls back to procedural phase3_search on any error.
# =============================================================================

async def phase3_supervisor_search(
    query: str,
    limit: int = 5,
) -> tuple[List[Product], List[ActivityEntry]]:
    """Phase 3 via live Strands supervisor — Bedrock LLM picks the SearchAgent tool."""
    from backend.agents.phase3 import create_phase3_system

    activities: List[ActivityEntry] = []

    def collect(entry: Any) -> None:
        try:
            activities.append(_memory_activity_to_entry(entry))
        except Exception:
            # Best-effort: keep going if the entry shape is unexpected.
            pass

    activities.append(create_activity(
        activity_type="reasoning",
        title="SupervisorAgent invoked (Strands + Bedrock)",
        details="Bedrock will choose which specialist tool to call",
        agent_name="SupervisorAgent",
        agent_file="agents/phase3/supervisor.py",
    ))

    supervisor = create_phase3_system(activity_callback=collect)

    try:
        packages, _llm_reply = await supervisor.process_search(query, activity_callback=collect)
    except Exception as exc:
        log_error(context="phase3_supervisor_search", error=str(exc))
        activities.append(create_activity(
            activity_type="error",
            title="Strands supervisor unavailable — falling back to procedural search",
            details=str(exc)[:200],
            agent_name="SupervisorAgent",
            agent_file="agents/phase3/supervisor.py",
        ))
        fallback_products, fallback_activities = await phase3_search(query, limit)
        activities.extend(fallback_activities)
        return fallback_products, activities

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

    activities.append(create_activity(
        activity_type="result",
        title=f"Supervisor returned {len(products)} trips",
        details="Bedrock-driven delegation completed",
        agent_name="SupervisorAgent",
        agent_file="agents/phase3/supervisor.py",
    ))

    return products, activities


# =============================================================================
# PHASE 4: Strands ConciergeOrchestrator + MemoryAgent (@tool) + Aurora memory
# =============================================================================

def _memory_activity_to_entry(entry: Any) -> ActivityEntry:
    """Convert MemoryAgent/ConciergeOrchestrator activity to API ActivityEntry."""
    if isinstance(entry, ActivityEntry):
        return entry
    data = entry.model_dump() if hasattr(entry, "model_dump") else dict(entry)
    telemetry = data.pop("telemetry", None)
    return ActivityEntry(
        **data,
        telemetry=TraceTelemetry(**telemetry) if telemetry else None,
    )


async def phase5_workflow(
    query: str,
    traveler_id: str,
    conversation_id: Optional[str] = None,
) -> tuple[List[Product], List[ActivityEntry], str, str]:
    """
    Phase 5: LangGraph StateGraph orchestrates classify → branch → synthesize.

    Reuses Phase 3's hybrid search and availability check as graph nodes so the
    workflow story is "explicit edges + checkpoints" rather than "different
    search code."  Checkpointer is PostgresSaver when LANGGRAPH_CHECKPOINT_DSN
    is set, otherwise MemorySaver.
    """
    from backend.agents.phase5.workflow import Phase5Workflow

    workflow = Phase5Workflow(
        search_fn=phase3_search,
        availability_fn=phase3_availability_check,
    )
    final_state = await workflow.run(
        query,
        traveler_id=traveler_id,
        conversation_id=conversation_id or "",
    )

    raw_activities = final_state.get("activities", []) or []
    activities = [_dict_to_activity_entry(a) for a in raw_activities]
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


async def phase4_search(
    query: str,
    customer_id: str,
    conversation_id: Optional[str] = None,
    limit: int = 5,
) -> tuple[List[Product], List[ActivityEntry], str, str, List[MemoryFact]]:
    """
    Phase 4: Recall Aurora memory via Strands @tool, hybrid search, persist turn.
    """
    from backend.agents.phase4.concierge import create_concierge
    from backend.memory.store import DEMO_TRAVELER_ID

    tid = customer_id or DEMO_TRAVELER_ID
    runtime = create_concierge()
    # In `full` orchestration mode the concierge calls the live Strands supervisor
    # for search; in `fallback` mode it uses the procedural hybrid query.
    search_fn = phase3_supervisor_search if strands_mode() == "full" else phase3_search
    products, raw_activities, message, conv_id, facts = await runtime.process_turn(
        query,
        tid,
        conversation_id,
        limit,
        search_fn=search_fn,
    )
    activities = [_memory_activity_to_entry(a) for a in raw_activities]
    memory_facts = [
        MemoryFact(
            key=f["key"],
            value=f["value"],
            source=f.get("source"),
            confidence=f.get("confidence"),
        )
        for f in facts
    ]
    return products, activities, message, conv_id, memory_facts


# =============================================================================
# PHASE 3: AvailabilityAgent — departure slots and package details
# =============================================================================

async def phase3_availability_check(query: str) -> tuple[List[Product], List[ActivityEntry], str]:
    """
    Phase 3: AvailabilityAgent handles departure and slot queries.
    Supervisor delegates availability questions to the specialist agent.

    Returns: (products, activities, message)
    """
    activities = []
    start_time = datetime.utcnow()

    db = get_rds_data_client()

    activities.append(create_activity(
        activity_type="reasoning",
        title="Delegating to AvailabilityAgent",
        details="Supervisor routing availability request to specialist agent",
        agent_name="SupervisorAgent",
        agent_file="agents/phase3/supervisor.py"
    ))

    query_lower = query.lower()

    activities.append(create_activity(
        activity_type="search",
        title="AvailabilityAgent: Finding package",
        details="Searching for mentioned trip package",
        agent_name="AvailabilityAgent",
        agent_file="agents/phase3/product_agent.py"
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
            title="AvailabilityAgent: Package not found",
            execution_time_ms=search_time,
            agent_name="AvailabilityAgent",
            agent_file="agents/phase3/product_agent.py"
        ))

        activities.append(create_activity(
            activity_type="result",
            title="AvailabilityAgent returned to Supervisor",
            details="No matching package found",
            agent_name="SupervisorAgent",
            agent_file="agents/phase3/supervisor.py"
        ))

        return [], activities, "I couldn't find that trip package. Try searching by destination, operator, or trip type."

    product = results[0]
    availability = product.get('availability', {})

    activities.append(create_activity(
        activity_type="availability",
        title="AvailabilityAgent: Checking departures",
        details=f"Package: {product['name']}",
        sql_query="SELECT availability, durations FROM trip_packages WHERE package_id = ?",
        agent_name="AvailabilityAgent",
        agent_file="agents/phase3/product_agent.py"
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
        title="AvailabilityAgent: Departures verified",
        details=f"Total: {total_stock} departure slots available",
        execution_time_ms=availability_time,
        agent_name="AvailabilityAgent",
        agent_file="agents/phase3/product_agent.py"
    ))

    activities.append(create_activity(
        activity_type="result",
        title="AvailabilityAgent returned to Supervisor",
        details=f"Availability check complete for {product['name']}",
        agent_name="SupervisorAgent",
        agent_file="agents/phase3/supervisor.py"
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
    - Phase 3: Hybrid semantic + lexical search; AvailabilityAgent for slot checks
    - Phase 4: Strands concierge with Aurora traveler memory
    """
    activities = []

    # Phase 3/4: availability query -> route to AvailabilityAgent
    if request.phase in (3, 4) and is_availability_query(request.message):
        activities.append(create_activity(
            activity_type="reasoning",
            title="Processing with Multi-Agent Orchestration",
            details=f"Query: {request.message[:80]}{'...' if len(request.message) > 80 else ''}",
            agent_name="SupervisorAgent",
            agent_file="agents/phase3/supervisor.py"
        ))
        
        try:
            products, availability_activities, message = await phase3_availability_check(request.message)
            activities.extend(availability_activities)
            
            follow_ups = ["Show similar trips", "What other durations are available?", "Find alternatives"]
            
            return ChatResponse(
                message=message,
                products=products if products else None,
                order=None,
                activities=activities,
                follow_ups=follow_ups
            )
        except Exception as e:
            activities.append(create_activity(
                activity_type="error",
                title="AvailabilityAgent error",
                details=str(e),
                agent_name="AvailabilityAgent",
                agent_file="agents/phase3/product_agent.py"
            ))
            # Fall through to regular search

    # Phase 4: Strands ConciergeOrchestrator + Aurora memory (@tool)
    if request.phase == 4:
        from backend.memory.store import DEMO_TRAVELER_ID

        activities.append(create_activity(
            activity_type="reasoning",
            title="Processing with personal concierge (Strands + Aurora memory)",
            details=f"Query: {request.message[:80]}{'...' if len(request.message) > 80 else ''}",
            agent_name="ConciergeOrchestrator",
            agent_file="agents/phase4/concierge.py",
        ))
        try:
            products, search_activities, message, conv_id, memory_facts = await phase4_search(
                request.message,
                customer_id=request.customer_id or DEMO_TRAVELER_ID,
                conversation_id=request.conversation_id,
                limit=5,
            )
            activities.extend(search_activities)
            follow_ups = generate_follow_ups(request.message, products, request.phase)
            return ChatResponse(
                message=message,
                products=products if products else None,
                order=None,
                activities=activities,
                follow_ups=follow_ups,
                conversation_id=conv_id,
                memory_facts=memory_facts,
            )
        except Exception as e:
            log_error("phase4_search", error=str(e))
            activities.append(create_activity(
                activity_type="error",
                title="Concierge error",
                details=str(e),
                agent_name="ConciergeOrchestrator",
                agent_file="agents/phase4/concierge.py",
            ))
            return ChatResponse(
                message="I encountered an error loading memory. Please try again.",
                products=None,
                order=None,
                activities=activities,
                follow_ups=["Romantic week in Europe", "Family-friendly beach resort", "Tokyo culture trip"],
            )

    # Phase 5: LangGraph workflow with explicit StateGraph + checkpointer.
    if request.phase == 5:
        from backend.memory.store import DEMO_TRAVELER_ID
        try:
            workflow_packages, workflow_activities, message, conv_id = await phase5_workflow(
                request.message,
                traveler_id=request.customer_id or DEMO_TRAVELER_ID,
                conversation_id=request.conversation_id,
            )
            activities.extend(workflow_activities)
            follow_ups = generate_follow_ups(request.message, workflow_packages, request.phase)
            return ChatResponse(
                message=message,
                products=workflow_packages if workflow_packages else None,
                order=None,
                activities=activities,
                follow_ups=follow_ups,
                conversation_id=conv_id,
            )
        except Exception as e:
            log_error("phase5_workflow", error=str(e))
            activities.append(create_activity(
                activity_type="error",
                title="LangGraph workflow error",
                details=str(e),
                agent_name="Phase5Workflow",
                agent_file="agents/phase5/workflow.py",
            ))
            return ChatResponse(
                message="The Phase 5 workflow hit an error. Please try a Phase 3 or Phase 4 query.",
                products=None,
                order=None,
                activities=activities,
                follow_ups=["Tokyo culture trip", "Family-friendly beach resort", "Romantic week in Europe"],
            )

    # Phase 3: pick the live Strands supervisor when STRANDS_ORCHESTRATION=full;
    # otherwise use the procedural hybrid search directly.
    phase3_fn = phase3_supervisor_search if strands_mode() == "full" else phase3_search
    phase3_method = (
        "Hybrid Search via Strands Supervisor (Bedrock-driven)"
        if strands_mode() == "full"
        else "Hybrid Search (Semantic + Lexical, procedural)"
    )

    phase_configs = {
        1: ("Phase1Agent", "Direct RDS Data API", phase1_search, "agents/phase1/agent.py"),
        2: ("Phase2Agent", "MCP (postgres-mcp-server)", phase2_search, "agents/phase2/agent.py"),
        3: ("SupervisorAgent", phase3_method, phase3_fn, "agents/phase3/supervisor.py"),
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
        products, search_activities = await search_fn(request.message, limit=5)
        activities.extend(search_activities)
        
        # Generate personalized response message
        if products:
            if request.phase in (3, 4):
                top_similarity = products[0].similarity
                if top_similarity and top_similarity > 0.8:
                    message = f"Great match! I found {len(products)} trips that closely match what you're looking for:"
                else:
                    message = f"Here are {len(products)} trips that might interest you:"
            else:
                message = f"I found {len(products)} trips for you:"
        else:
            if request.phase in [1, 2]:
                message = "No results found. Phase 1/2 uses keyword filters only. Try destination or operator names like 'Tokyo' or 'ANA Holidays', or switch to Phase 3 for natural language search."
            else:
                message = "I couldn't find exact matches. Try different destinations, trip types, or travel dates."

        # Generate contextual follow-up suggestions
        follow_ups = generate_follow_ups(request.message, products, request.phase)
        
        return ChatResponse(
            message=message,
            products=products if products else None,
            order=None,
            activities=activities,
            follow_ups=follow_ups
        )
        
    except Exception as e:
        activities.append(create_activity(
            activity_type="error",
            title="Error processing request",
            details=str(e),
            agent_name=agent_name,
            agent_file=agent_file
        ))

        return ChatResponse(
            message="I encountered an error. Please try again or browse featured trips.",
            products=None,
            order=None,
            activities=activities,
            follow_ups=["Tokyo culture trip", "Beach resort for two", "City breaks in Europe"]
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
        1: ("Phase1Agent", "agents/phase1/agent.py"),
        2: ("Phase2Agent", "agents/phase2/agent.py"),
        3: ("OrderAgent", "agents/phase3/order_agent.py"),
        # Phase 4 booking is driven by the concierge orchestrator; the OrderAgent
        # in phase 3 is reused as the booking specialist.
        4: ("OrderAgent", "agents/phase3/order_agent.py"),
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
