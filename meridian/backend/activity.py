"""Trace spans and trip packages in the API's shape, with no web framework.

The Phase 5 workflow runs inside AgentCore Runtime as well as the FastAPI app,
so the models its steps emit cannot live in the router module.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from backend.logging_config import log_activity_entry


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
    destination: Optional[str] = None
    region: Optional[str] = None
    available_sizes: Optional[List[str]] = None
    availability: Optional[Dict[str, Any]] = None
    highlights: Optional[List[str]] = None
    similarity: Optional[float] = None
    # Phase 3 rerank-visualization metadata (optional; only the Retrieval
    # path populates these). Lets the UI animate the hybrid→reranked reorder.
    pre_rerank_position: Optional[int] = None
    pre_rerank_similarity: Optional[float] = None
    rank_delta: Optional[int] = None


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
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
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
